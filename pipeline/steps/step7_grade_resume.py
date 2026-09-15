"""Step 7 (per-job): Grade resume — LLM grades the customized resume.

This is a LangGraph node. The grader LLM extracts requirements and assigns
per-criterion verdicts (Call 1, reasoning); the score is then computed
deterministically in code (pipeline.infrastructure.scoring — the protocol's
formula is arithmetic, not judgment). Setting config.deterministic_scoring
to False restores the legacy two-call path (a second LLM call does the
arithmetic) for A/B comparison (ADR-0011).

The orchestrator captures stdout, persists the reasoning JSON, writes the
grade JSON, parses it into a ResumeGrade, renames the resume file with the
grade prefix, appends to .grades.log, and cleans up the grading workspace.

The grader call uses `normal` permission mode (ADR-0010) — it cannot write
files or exec commands; it returns JSON via stdout only.

The grade is stored in state.latest_grade (not on the ResumeVersion) to
avoid list-reducer complexity — see the "Resume version grade update" note
in the refactor plan.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.file_ops import rename_with_grade
from pipeline.infrastructure.grades_log import append_grades_log
from pipeline.infrastructure.llm_interface import get_deps, parse_llm_json
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.protocol_constants import GRADING_REASONING_PROTOCOL, GRADING_SCORING_PROTOCOL
from pipeline.infrastructure.scoring import compute_grade
from pipeline.infrastructure.state import JobState, ResumeGrade


# ─── Two-call prompt builders (ADR-0011) ─────────────────────────────────────


def build_reasoning_prompt(
    slug: str, resume_path: str, jd_path: str, version: int,
    location_policy: str = "",
) -> str:
    """Build the Call 1 (reasoning) prompt for the grader model.

    This prompt covers Steps 1-2 of the grading protocol: extract requirements
    from the JD and assess each against the resume. Returns reasoning JSON
    via stdout. The protocol is inlined directly — no file-read tool call.
    """
    location_note = (
        f"Location note: {location_policy}\n\n" if location_policy else ""
    )
    return f"""Be realistically harsh — as harsh as a recruiter taking only 30 seconds to skim the resume.

{GRADING_REASONING_PROTOCOL}

{location_note}

Read the resume at {resume_path} and the JD at {jd_path}.

Extract requirements from qualifications sections only. Assign verdicts (DIRECT_HIT / ADDRESSED / PARTIAL / GAP) with one-sentence comments citing specific resume evidence.

Output ONLY valid JSON to stdout (format: {{"per_criterion": [{{"requirement": "...", "tier": "core", "assessment": "DIRECT_HIT", "comment": "..."}}], "judgment_calls": [{{"requirement": "...", "phrase": "...", "classified_as": "preferred", "reason": "..."}}], "summary": "one paragraph — overall assessment, the decisive hits and gaps"}}). No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. Do NOT compute a score — the orchestrator applies the scoring formula deterministically.

If the JD requires security clearance, output CLEARANCE instead of the reasoning JSON."""


def build_scoring_prompt(
    slug: str, version: int, reasoning_json: str
) -> str:
    """Build the Call 2 (scoring) prompt for the grader model.

    This prompt covers Steps 3-4 of the grading protocol: compute the score
    from the fixed reasoning JSON produced by Call 1, and output the final
    grade JSON via stdout. The protocol is inlined directly.
    """
    return f"""{GRADING_SCORING_PROTOCOL}

You are grading resume v{version} for {slug}. The reasoning from Call 1 is provided below as fixed, committed input. You CANNOT change these verdicts — your job is to compute the score from them.

Reasoning JSON from Call 1:
{reasoning_json}

Compute the score using the protocol formula (ceiling, core score, preferred bonus, quantitative base, subjective adjustment, final score). Pass through the per_criterion list unchanged. If judgment_calls was provided in the reasoning JSON, pass it through unchanged as well.

This is arithmetic — count the verdicts, apply the formula, output the number. You have all the input above. Do NOT run commands, write scripts, search files, or use any tools. Do NOT read files — everything you need is in this prompt. Such actions will be blocked by a hook and waste time. If you spend more than a few seconds on this, you are overthinking. Just compute and output.

Output ONLY valid JSON to stdout. No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. Do NOT rename anything. Do NOT append to any log file. The orchestrator handles all file operations from your stdout output."""


def build_tier_retry_prompt(
    slug: str, resume_path: str, jd_path: str, version: int,
    prior_reasoning_json: str,
    location_policy: str = "",
) -> str:
    """Build a retry prompt when the LLM classified zero requirements as core.

    Re-runs the reasoning call with an explicit instruction to classify
    requirements into core and preferred tiers.
    """
    location_note = (
        f"Location note: {location_policy}\n\n" if location_policy else ""
    )
    return f"""You are re-grading resume v{version} for {slug} because your previous response classified every requirement as "preferred" — none as "core". This is almost always wrong: job descriptions have core requirements (qualifications the candidate must meet) and preferred requirements (nice-to-haves).

{GRADING_REASONING_PROTOCOL}

{location_note}

Read the resume at {resume_path} and the JD at {jd_path}.

## Your previous response (correct the tier assignments)

```json
{prior_reasoning_json}
```

## Your task

Re-evaluate your tier classifications. Requirements listed under "qualifications", "requirements", "what you'll do", or "what we'd like you to have" headings are CORE — they are the job's must-have qualifications. Requirements that are explicitly labeled as "preferred", "nice to have", "bonus", or "a plus" are PREFERRED. Re-assess each requirement's tier accordingly. Keep your assessments (DIRECT_HIT/ADDRESSED/PARTIAL/GAP) unchanged unless the tier change reveals an error.

Output ONLY valid JSON to stdout (same format as before). No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files.

If the JD requires security clearance, output CLEARANCE instead of the reasoning JSON."""


def parse_resume_grade_json(grade_file: Path) -> dict | None:
    """Read and parse a resume grade JSON file from .grading/."""
    if not grade_file.exists():
        return None
    try:
        with open(grade_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None





def _find_jd_in_drafts(slug: str, paths: Paths) -> Path | None:
    """Find the JD file in drafts/<slug>/."""
    job_dir = paths.drafts / slug
    if not job_dir.exists():
        return None
    jd_files = list(job_dir.glob("*job-description.md"))
    return jd_files[0] if jd_files else None


def step7_grade_resume_node(state: JobState, config: RunnableConfig) -> dict:
    """Grade the latest resume using the grader LLM.

    Returns partial state with latest_grade set. Raises on LLM failure,
    missing files, or unparseable grade JSON.

    In dry_run, skips grading entirely (returns empty dict).
    """
    deps = get_deps(config)
    slug = state.slug
    latest = state.latest_resume

    if not latest:
        raise RuntimeError(f"{slug}: no resume to grade")

    version = latest.version
    resume_path = latest.path

    if deps.config.dry_run:
        deps.logger.info(f"  {slug}: dry-run grade v{version} (skipped)")
        return {}

    # Find the JD file
    jd_path = _find_jd_in_drafts(slug, deps.paths)
    if not jd_path:
        raise RuntimeError(f"{slug}: JD file not found for resume grading")

    # Create grading workspace
    grading_dir = deps.paths.grading / slug
    grading_dir.mkdir(parents=True, exist_ok=True)

    # Build relative paths for prompts
    resume_rel = str(resume_path.relative_to(deps.paths.hunter_dir))
    jd_rel = str(jd_path.relative_to(deps.paths.hunter_dir))

    # ─── Call 1: Reasoning (ADR-0011) ──────────────────────────────────
    # alive_check_seconds=<timeout> disables the 10s liveness probe (see step6).
    grade_timeout = deps.config.timeout_for("grade-resume")
    reasoning_prompt = build_reasoning_prompt(
        slug, resume_rel, jd_rel, version,
        location_policy=deps.config.location_policy,
    )
    reasoning_output, reasoning_error = deps.llm(
        reasoning_prompt,
        model=deps.config.models.grader,
        timeout=grade_timeout,
        alive_check_seconds=grade_timeout,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
        permission_mode="normal",
        job_slug=slug, step="grade-resume",
    )

    if reasoning_error:
        raise RuntimeError(
            f"{slug}: resume grading reasoning call failed: {reasoning_error}"
        )

    # Check for clearance detection.
    # Per the reasoning protocol, the LLM outputs the literal string "CLEARANCE"
    # (and nothing else) when a JD requires security clearance. A naive substring
    # check fires on "No security clearance required" inside the JSON summary —
    # so we check if the stripped response IS "CLEARANCE", not if it CONTAINS it.
    if reasoning_output.strip().upper() == "CLEARANCE":
        deps.logger.info(f"  {slug}: clearance detected during grading → trash")
        resume_grade = ResumeGrade(grade=0, per_criterion=[])
        # Still need to handle the resume file — leave ungraded
        return {"latest_grade": resume_grade}

    # Parse reasoning JSON from stdout
    reasoning_data = parse_llm_json(reasoning_output)
    if reasoning_data is None:
        raise RuntimeError(
            f"{slug}: could not parse reasoning JSON from Call 1 stdout"
        )

    # If the LLM classified zero requirements as core, retry once with an
    # explicit instruction to classify tiers correctly. Job descriptions
    # always have core requirements; zero-core means the LLM misclassified.
    per_criterion = reasoning_data.get("per_criterion", [])
    has_core = any(c.get("tier") == "core" for c in per_criterion) if per_criterion else False
    if not has_core and per_criterion:
        deps.logger.warning(
            f"  {slug}: zero core criteria in reasoning — retrying with tier correction prompt"
        )
        retry_prompt = build_tier_retry_prompt(
            slug, resume_rel, jd_rel, version,
            reasoning_output.strip(),
            location_policy=deps.config.location_policy,
        )
        retry_output, retry_error = deps.llm(
            retry_prompt,
            model=deps.config.models.grader,
            timeout=grade_timeout,
            alive_check_seconds=grade_timeout,
            retries=deps.config.llm_retries,
            retry_delay=deps.config.llm_retry_delay,
            workspace=str(deps.paths.hunter_dir),
            permission_mode="normal",
            job_slug=slug, step="grade-resume",
        )
        if retry_error:
            deps.logger.warning(
                f"  {slug}: tier retry call failed ({retry_error}) — falling back to all-core"
            )
        else:
            retry_data = parse_llm_json(retry_output)
            if retry_data and any(c.get("tier") == "core" for c in retry_data.get("per_criterion", [])):
                reasoning_data = retry_data
            else:
                deps.logger.warning(
                    f"  {slug}: tier retry still has zero core criteria — falling back to all-core"
                )

    # Persist reasoning JSON for auditability (ADR-0011)
    reasoning_file = deps.paths.grading / slug / f"reasoning-v{version}.json"
    reasoning_file.parent.mkdir(parents=True, exist_ok=True)
    with open(reasoning_file, "w") as f:
        json.dump(reasoning_data, f, indent=2)

    # ─── Scoring ─────────────────────────────────────────────────────
    grade_file = deps.paths.grading / slug / f"grade-v{version}.json"
    grade_file.parent.mkdir(parents=True, exist_ok=True)

    if deps.config.deterministic_scoring:
        # Compute the score in code — the formula is spec'd in
        # _config/grading-scoring-protocol.md and implemented in
        # pipeline.infrastructure.scoring.
        grade_data = compute_grade(reasoning_data.get("per_criterion", []))
        grade_data["justification"] = str(reasoning_data.get("summary", ""))
        grade_data["judgment_calls"] = reasoning_data.get("judgment_calls", [])
    else:
        # Legacy path: a second LLM call applies the formula (ADR-0011).
        reasoning_json_str = json.dumps(reasoning_data)
        scoring_prompt = build_scoring_prompt(slug, version, reasoning_json_str)
        scoring_output, scoring_error = deps.llm(
            scoring_prompt,
            model=deps.config.models.grader,
            timeout=grade_timeout,
            alive_check_seconds=grade_timeout,
            retries=deps.config.llm_retries,
            retry_delay=deps.config.llm_retry_delay,
            workspace=str(deps.paths.hunter_dir),
            permission_mode="normal",
            job_slug=slug, step="grade-resume",
        )

        if scoring_error:
            raise RuntimeError(
                f"{slug}: resume grading scoring call failed: {scoring_error}"
            )

        # Parse grade JSON from stdout (primary path — ADR-0010)
        grade_data = parse_llm_json(scoring_output)

        # Fallback: read from file (transitional compat for any LLM that still writes)
        if grade_data is None:
            grade_data = parse_resume_grade_json(grade_file)

        if grade_data is None:
            raise RuntimeError(
                f"{slug}: could not parse grade JSON from scoring call stdout"
                f" or read from .grading/{slug}/grade-v{version}.json"
            )

        # Clamp out-of-range grades before validation — transport success
        # but validation failure; record the raw value for review.
        raw_grade = grade_data.get("grade")
        if raw_grade is not None:
            raw_grade_float = float(raw_grade)
            if not (0 <= raw_grade_float <= 10):
                clamped = max(0.0, min(10.0, raw_grade_float))
                deps.logger.warning(
                    f"  {slug}: resume grade {raw_grade_float} out of range [0, 10] — "
                    f"clamped to {clamped} (flagged for review)"
                )
                grade_data["grade"] = clamped
                orig_just = grade_data.get("justification", "")
                grade_data["justification"] = (
                    f"[VALIDATION: raw grade {raw_grade_float} clamped to {clamped}] {orig_just}"
                )

    # Orchestrator writes the grade JSON file (ADR-0010)
    with open(grade_file, "w") as f:
        json.dump(grade_data, f, indent=2)

    # Parse into ResumeGrade (validates grade range, enum values)
    try:
        resume_grade = ResumeGrade.model_validate(grade_data)
    except Exception as e:
        raise RuntimeError(f"{slug}: could not parse grade JSON: {e}") from e

    grade = resume_grade.grade
    deps.logger.info(f"  {slug}: resume v{version} grade = {grade}")

    # Append to audit log — include the justification so the "why" is
    # traceable in plain English, not just the score.
    append_grades_log(
        resume_rel,
        grade,
        deps.config.models.grader,
        resume_grade.per_criterion,
        deps.paths.grades_log,
        deps.logger,
        note=resume_grade.justification,
    )

    # Rename the resume file with the grade
    if resume_path.exists():
        rename_with_grade(resume_path, grade)

    # Clean up grading workspace
    shutil.rmtree(grading_dir, ignore_errors=True)

    return {"latest_grade": resume_grade}
