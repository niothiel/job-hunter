"""Step 8 (per-job): Truthfulness review — verify resume claims against sources.

This is a LangGraph node, now inside the customize loop (ADR-0018): it runs
after step7_grade_resume on EVERY version, not just the final one. It uses
the two-call truthfulness decomposition (ADR-0011):
  - Call 1 (classification): loads sources + classifies each claim
  - Call 2 (synthesis): takes Call 1's verdicts as fixed input, produces
    the final verification JSON

The orchestrator captures stdout from both calls, persists the classification
JSON, writes the verification JSON, parses it into a Verification model, and
cleans up the veracity workspace.

Both calls use `normal` permission mode (ADR-0010) — the truthfulness reviewer
cannot write files or exec commands; it returns JSON via stdout only.

Every run appends a fully-populated ResumeVersion record to
state.version_history (grade, verified, draft hash, truthfulness issues) —
step10_final_veracity selects the best passing version from that history
and re-verifies it as the final gate before step11_finalize ships it.

Only reviews the latest version if grade >= threshold (resume_grade_threshold,
default 9). Below threshold the LLM calls are skipped and the history record
gets verified=None (review not run — distinct from a failed review). The
routing node (step9_should_continue) reads state.verification: a False or
skipped review combined with a NO signal forces a repair revision.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.llm_interface import get_deps, parse_llm_json
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.protocol_constants import (
    VERACITY_CLASSIFICATION_PROTOCOL,
    VERACITY_SYNTHESIS_PROTOCOL,
)
from pipeline.infrastructure.state import JobState, ResumeVersion, Verification


# ─── Two-call prompt builders (ADR-0011) ─────────────────────────────────────


def build_classification_prompt(
    slug: str,
    resume_path: str,
    resume_content: str,
    base_resume_content: str,
    linkedin_content: str,
) -> str:
    """Build the Call 1 (classification) prompt for the truthfulness model.

    All three source documents are pre-fed into the prompt — the LLM does
    not need to read any files. This eliminates tool-call round-trips and
    prevents the LLM from writing scripts to parse the files (which caused
    it to never produce output in practice).
    """
    return f"""You are a truthfulness verification subagent.

{VERACITY_CLASSIFICATION_PROTOCOL}

## Source documents (pre-loaded — do NOT read any files)

### Customized resume ({resume_path})

```markdown
{resume_content}
```

### Base resume (the customization base — every tailored resume starts here)

```markdown
{base_resume_content}
```

### Full LinkedIn experience (reference pool — the candidate's complete career history)

```markdown
{linkedin_content}
```

## Your task

All three sources are above. Do NOT run commands, write scripts, or use any tools other than reading files — they will be blocked by a hook. Classify each claim on the resume into one of 4 buckets (TRACEABLE / MINOR_VARIATION / MATERIAL_OVERSTATEMENT / FABRICATED).

This is a mechanical classification task, not an investigation. You have all three documents above — read them, classify each claim, output JSON. Do not write code, run commands, or use any tools. If you find yourself wanting to write a script, stop — you are overcomplicating this.

Output ONLY valid JSON to stdout (format: {{"per_claim": [{{"claim": "...", "location": "...", "bucket": "...", "source_checked": "...", "reason": "..."}}]}}). No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. Do NOT synthesize the final verdict — that is Call 2's job."""


def build_synthesis_prompt(slug: str, classification_json: str) -> str:
    """Build the Call 2 (synthesis) prompt for the truthfulness model.

    This prompt covers Steps 3-4 of the veracity protocol: synthesize the
    final verification result from the fixed classification JSON produced
    by Call 1, and output the verification JSON via stdout. The protocol
    is inlined directly.
    """
    return f"""{VERACITY_SYNTHESIS_PROTOCOL}

You are verifying resume for {slug}. The per-claim classifications from Call 1 are provided below as fixed, committed input. You CANNOT change these classifications — your job is to synthesize the final verification from them.

Classification JSON from Call 1:
{classification_json}

Count the MATERIAL_OVERSTATEMENT and FABRICATED verdicts (these are unverifiable). If zero → verified: true. If one or more → verified: false. Pass through the unverifiable claims unchanged.

This is a counting task — count the MATERIAL_OVERSTATEMENT and FABRICATED verdicts, collect them, output JSON. You have all the input above. Do NOT run commands, write scripts, search files, or use any tools. Do NOT read files — everything you need is in this prompt. Such actions will be blocked by a hook and waste time. If you spend more than a few seconds on this, you are overthinking. Just count and output.

Output ONLY valid JSON to stdout. No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. The orchestrator handles all file operations from your stdout output."""


def parse_verification_json(veracity_file: Path) -> dict | None:
    """Read and parse a verification JSON file from .veracity/."""
    if not veracity_file.exists():
        return None
    try:
        with open(veracity_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _draft_hash(resume_path: Path) -> str:
    """sha256[:12] of the resume file's bytes ('' if missing)."""
    try:
        return hashlib.sha256(resume_path.read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


def find_resume_version_file(slug: str, version: int, paths: Paths) -> Path | None:
    """Find the (possibly grade-renamed) file for a resume version."""
    job_dir = paths.drafts / slug
    if not job_dir.exists():
        return None
    matches = sorted(job_dir.glob(f"*resume-v{version}.md"), key=lambda p: p.name)
    return matches[0] if matches else None


def verify_resume_file(
    deps,
    slug: str,
    resume_path: Path,
    *,
    step_label: str = "truthfulness",
    file_prefix: str = "",
) -> Verification:
    """Run the two-call truthfulness verification on a resume file (ADR-0011).

    Shared by step8_veracity (in-loop) and step10_final_veracity (post-loop
    gate) — identical verification, different call sites and output names.
    Persists <file_prefix>classification.json / <file_prefix>verification.json
    under .veracity/<slug>/ for auditability.

    Raises on LLM failure or unparseable verification JSON.
    """
    veracity_dir = deps.paths.veracity / slug
    veracity_dir.mkdir(parents=True, exist_ok=True)

    # Remove a stale fallback file from a prior run — the synthesis fallback
    # reads plain "verification.json", and an old one would be misattributed
    # to this call if the LLM's stdout isn't parseable (e.g. a previous
    # cascade candidate left one behind).
    (veracity_dir / "verification.json").unlink(missing_ok=True)

    resume_rel = str(resume_path.relative_to(deps.paths.hunter_dir))

    # Pre-read source files to inline into the classification prompt
    resume_content = resume_path.read_text(encoding="utf-8")
    base_resume_content = deps.paths.base_resume.read_text(encoding="utf-8")
    linkedin_content = deps.paths.linkedin_experience.read_text(encoding="utf-8")

    # ─── Call 1: Classification (ADR-0011) ─────────────────────────────
    # alive_check_seconds=<timeout> disables the 10s liveness probe (see step6).
    truth_timeout = deps.config.timeout_for("truthfulness")
    classification_prompt = build_classification_prompt(
        slug, resume_rel, resume_content, base_resume_content, linkedin_content
    )
    classification_output, classification_error = deps.llm(
        classification_prompt,
        model=deps.config.models.truthfulness,
        timeout=truth_timeout,
        alive_check_seconds=truth_timeout,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
        permission_mode="normal",
        job_slug=slug, step=step_label,
    )

    if classification_error:
        raise RuntimeError(
            f"{slug}: truthfulness classification call failed: {classification_error}"
        )

    # Parse classification JSON from stdout
    classification_data = parse_llm_json(classification_output)
    if classification_data is None:
        raise RuntimeError(
            f"{slug}: could not parse classification JSON from Call 1 stdout"
        )

    # Persist classification JSON for auditability (ADR-0011)
    classification_file = veracity_dir / f"{file_prefix}classification.json"
    with open(classification_file, "w") as f:
        json.dump(classification_data, f, indent=2)

    # ─── Call 2: Synthesis (ADR-0011) ──────────────────────────────────
    classification_json_str = json.dumps(classification_data)
    synthesis_prompt = build_synthesis_prompt(slug, classification_json_str)
    synthesis_output, synthesis_error = deps.llm(
        synthesis_prompt,
        model=deps.config.models.truthfulness,
        timeout=truth_timeout,
        alive_check_seconds=truth_timeout,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
        permission_mode="normal",
        job_slug=slug, step=step_label,
    )

    if synthesis_error:
        raise RuntimeError(
            f"{slug}: truthfulness synthesis call failed: {synthesis_error}"
        )

    # Parse verification JSON from stdout (primary path — ADR-0010)
    verification_data = parse_llm_json(synthesis_output)

    # Fallback: read from file (transitional compat). The name is always
    # plain "verification.json" — an LLM that wrote a file would not know
    # the orchestrator's file_prefix convention.
    if verification_data is None:
        verification_data = parse_verification_json(
            veracity_dir / "verification.json"
        )

    if verification_data is None:
        raise RuntimeError(
            f"{slug}: could not parse verification JSON from synthesis call stdout"
            f" or read from {veracity_dir / 'verification.json'}"
        )

    # Orchestrator writes the verification JSON file (ADR-0010)
    veracity_file = veracity_dir / f"{file_prefix}verification.json"
    with open(veracity_file, "w") as f:
        json.dump(verification_data, f, indent=2)

    # Parse into Verification model (validates fields)
    try:
        return Verification.model_validate(verification_data)
    except Exception as e:
        raise RuntimeError(f"{slug}: could not parse verification JSON: {e}") from e


def step8_veracity_node(state: JobState, config: RunnableConfig) -> dict:
    """Run truthfulness review on the latest resume version.

    Returns partial state with verification set and a ResumeVersion record
    appended to version_history. If the grade is below threshold (or absent),
    the LLM calls are skipped and the record gets verified=None (not run).

    Raises on LLM failure or unparseable verification JSON (when grade
    >= threshold). In dry_run, skips the LLM calls.
    """
    deps = get_deps(config)
    slug = state.slug
    latest = state.latest_resume

    if not latest:
        raise RuntimeError(f"{slug}: no resume to verify")
    version = latest.version

    # Find the actual file — step7 renamed it with a [grade] prefix.
    resume_path = find_resume_version_file(slug, version, deps.paths)
    if resume_path is None:
        resume_path = latest.path

    grade = state.latest_grade.grade if state.latest_grade else None

    def _record(verified: bool | None, issues: list[str] | None = None) -> ResumeVersion:
        lg = state.latest_grade
        return ResumeVersion(
            version=version,
            path=resume_path,
            grade=grade,
            verified=verified,
            draft_hash=_draft_hash(resume_path),
            truthfulness_issues=issues or [],
            per_criterion=lg.per_criterion if lg else [],
            justification=lg.justification if lg else "",
        )

    # If grade < threshold, skip truthfulness — the version can't pass anyway.
    threshold = deps.config.resume_grade_threshold
    if grade is None or grade < threshold:
        deps.logger.info(
            f"  {slug}: grade below {threshold}, skipping truthfulness"
        )
        return {
            "verification": Verification(verified=False),
            "version_history": [_record(None)],
        }

    if deps.config.dry_run:
        deps.logger.info(f"  {slug}: dry-run truthfulness (skipped)")
        return {
            "verification": Verification(verified=False),
            "version_history": [_record(None)],
        }

    if not resume_path.exists():
        raise RuntimeError(f"{slug}: no resume found for truthfulness review")

    verification = verify_resume_file(deps, slug, resume_path)

    verified = verification.verified
    deps.logger.info(
        f"  {slug}: truthfulness {'VERIFIED' if verified else 'UNVERIFIED'}"
        + (f" ({len(verification.unverifiable_claims)} claims)" if not verified else "")
    )

    # Clean up veracity workspace
    shutil.rmtree(deps.paths.veracity / slug, ignore_errors=True)

    return {
        "verification": verification,
        "version_history": [
            _record(
                verified,
                [c.claim for c in verification.unverifiable_claims],
            )
        ],
    }
