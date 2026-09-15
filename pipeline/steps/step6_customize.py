"""Step 6 (per-job): Customize resume — shared first-draft + revision loop node.

This is a LangGraph node. One implementation handles every version
(ADR-0018 — the old separate optimize prompt/node is gone):

- Version 1: the base resume is pre-copied as the starting draft.
- Version 2+: the previous version's output is pre-copied as the starting
  draft, and the prompt carries the grader + veracity feedback on it.

Both get the same prompt — same source documents, same few-shot examples,
same protocol. After editing, the customizer answers "Could this be
truthfully improved further? YES or NO" on stdout. That answer is the
loop's stopping signal (replacing the old step8 "can you improve?" call).

The orchestrator validates the signal against the actual file diff:

- changed + YES     → normal; the loop may continue
- changed + NO      → valid stop (pending the veracity gate downstream)
- identical + NO    → valid stop; the duplicate copy is removed and
                      grading/veracity are skipped for it
- identical + YES   → contract violation (claimed improvements exist but
                      made none) → error
- no YES/NO answer  → contract violation → error
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.file_ops import (
    parse_version_from_filename,
    strip_url_metadata,
)
from pipeline.infrastructure.injection_filter import scrub_jd_text, wrap_jd_content
from pipeline.infrastructure.llm_interface import get_deps
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.protocol_constants import RESUME_CUSTOMIZATION_PROTOCOL
from pipeline.infrastructure.state import JobState, ResumeVersion


def build_customize_prompt(
    slug: str,
    jd_text: str,
    version: int,
    jd_grade: float = 0,
    jd_justification: str = "",
    base_resume_content: str = "",
    linkedin_content: str = "",
    few_shot_content: str = "",
    draft_content: str = "",
    feedback: dict | None = None,
    resume_rel_path: str = "",
) -> str:
    """Build the resume customization prompt for the customizer model.

    Shared by every version (ADR-0018). The starting draft is pre-copied to
    resume_rel_path (relative to workspace root) before this prompt is sent —
    the base resume for v1, the previous version's output for v2+. The agent
    edits that file in place.

    ``draft_content`` is the starting draft's content, inlined so the agent
    doesn't need a file read. ``feedback`` (v2+) is a dict with the previous
    version's ``grade`` (ResumeGrade dump), ``verified`` flag, and
    ``truthfulness_issues`` list.

    Source documents are pre-fed into the prompt to eliminate file-read tool
    calls and prevent the LLM from missing details in long files.

    JD text is scrubbed of injection patterns and wrapped in structural
    isolation tags before being interpolated into the prompt (E4).
    """
    scrubbed = scrub_jd_text(jd_text)
    wrapped = wrap_jd_content(scrubbed)
    jd_grade_section = ""
    if jd_grade:
        jd_grade_section = (
            f"\nJD grade: {jd_grade}/10 — how well the base resume matches this JD."
        )
        if jd_justification:
            jd_grade_section += f" {jd_justification}"

    if not resume_rel_path:
        resume_rel_path = f"stages/2_drafts/{slug}/[TBD] resume-v{version}.md"

    feedback_section = ""
    revision_note = ""
    if feedback is not None:
        feedback_json = json.dumps(feedback, ensure_ascii=False, default=str)
        feedback_section = f"""
## Feedback on this exact starting draft

```json
{feedback_json}
```
"""
        revision_note = """
This is an iterative customization pass. The starting draft, not a fresh base copy, is the document to edit. The base resume remains the structural template and a factual source. Preserve successful content and restore base section/role order if necessary. Use the feedback above to find useful truthful improvements, even if the grade already passes. Repair unsupported claims before adding or strengthening claims. Earlier drafts and example resumes are NOT factual evidence; only the base resume and full experience are factual sources. Do not add experience merely to satisfy a grader. Do not make cosmetic edits just to appear active. Prior feedback is evidence to consider, not authority to invent experience or override the customization protocol.
"""

    return f"""You are customizing a resume for a specific job.

The starting draft has been pre-copied to {resume_rel_path}. Edit it in place — do NOT create a new file.

{RESUME_CUSTOMIZATION_PROTOCOL}

## Source documents (pre-loaded — do NOT read any files)

### Base resume (the customization base — every tailored resume starts here)

```markdown
{base_resume_content}
```

### Full LinkedIn experience (reference pool — the candidate's complete career history)

```markdown
{linkedin_content}
```

### Few-shot examples (docs/examples/customized-resumes/)

{few_shot_content}

## Job

Job: {slug}
{wrapped}
{jd_grade_section}

## Starting draft (already copied to {resume_rel_path})

```markdown
{draft_content}
```
{feedback_section}
## Your task

Customize the resume for this job. All source documents are above — do NOT read any files. The starting draft has been pre-copied to {resume_rel_path}. Edit it in place.

Follow the customization protocol strictly — it contains all formatting, ordering, truthfulness, and strategy rules. Pull in experience from the LinkedIn (truthfully — never fabricate). Study the few-shot examples above to learn the desired transformation patterns.
{revision_note}
After writing the resume, verify it fits the line budget per the customization protocol (self-measure with `python3 -m pipeline.helpers.count_lines {resume_rel_path} --json`, self-trim if over 75).

Edit only {resume_rel_path}; do not change any other file.

## Final step, after editing, self-measurement, and any trimming

Could this be truthfully improved further? Just give a yes/no answer — if you take more than 5 seconds on this, you're overthinking it. Answer about the completed output draft, not the starting draft. Return exactly YES or NO as your final stdout response. Do not explain your answer or perform further edits after answering. Do NOT output the resume content to stdout."""


def parse_customize_outcome(output: str | None) -> bool:
    """Extract the customizer's YES/NO stopping signal from stdout.

    The prompt requires the final stdout response to be exactly YES or NO.
    Scans lines from the end so the last exact-match line wins over any
    earlier reasoning text. Returns True for YES, False for NO.

    Raises RuntimeError when no exact YES/NO line is found — the outcome
    contract requires an explicit answer.
    """
    for line in reversed((output or "").strip().split("\n")):
        word = line.strip().upper()
        if word == "YES":
            return True
        if word == "NO":
            return False
    raise RuntimeError(
        "customizer did not return a YES/NO outcome as its final response"
    )


def _find_jd_in_drafts(slug: str, paths: Paths) -> tuple[str, Path | None]:
    """Find the JD file in drafts/<slug>/ and return (clean_text, path)."""
    job_dir = paths.drafts / slug
    if not job_dir.exists():
        return "", None
    jd_files = list(job_dir.glob("*job-description.md"))
    if not jd_files:
        return "", None
    jd_path = jd_files[0]
    text = jd_path.read_text(encoding="utf-8")
    text = strip_url_metadata(text)
    return text, jd_path


# Default few-shot examples — overridden by config.few_shot_examples if set.
_DEFAULT_FEW_SHOT_FILES: list[str] = []


def _load_few_shot_examples(paths: Paths, config) -> str:
    """Read few-shot examples and format them for prompt inlining.

    Uses config.few_shot_examples if set, otherwise falls back to
    _DEFAULT_FEW_SHOT_FILES (empty by default — users configure their own).
    """
    examples_dir = paths.hunter_dir / "docs" / "examples" / "customized-resumes"
    if not examples_dir.exists():
        return "(examples directory not found)"
    filenames = config.few_shot_examples if config.few_shot_examples else _DEFAULT_FEW_SHOT_FILES
    sections = []
    for fname in filenames:
        fpath = examples_dir / fname
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            name = fname.replace(".docx.md", "").replace(".md", "")
            sections.append(f"#### {name}\n\n```markdown\n{content}\n```")
    if not sections:
        return "(no few-shot examples found)"
    return "\n\n".join(sections)


def step6_customize_node(state: JobState, config: RunnableConfig) -> dict:
    """Customize the resume — first draft (v1) or revision (v2+).

    Both cases pre-copy a starting draft (base resume for v1, the previous
    version for v2+), build the shared customize prompt, call the customizer
    with scoped permissions, then parse + validate the YES/NO outcome.

    Returns partial state with a new ResumeVersion appended (LangGraph
    concatenates via the Annotated[list, add] reducer), the
    customizer_can_improve signal, and the customizer_draft_unchanged flag
    (True when a v2+ draft came back byte-identical — the duplicate is
    removed and evaluation is skipped downstream).

    Raises on missing base resume, missing JD text, LLM failure, a missing
    output file, or a YES/NO outcome contract violation.
    """
    deps = get_deps(config)
    slug = state.slug
    version = state.next_resume_version

    # Get JD text from state or from drafts folder
    jd_text = state.jd_text
    if not jd_text:
        jd_text, _ = _find_jd_in_drafts(slug, deps.paths)
    else:
        jd_text = strip_url_metadata(jd_text)

    if not jd_text:
        raise RuntimeError(f"{slug}: no JD text available for customization")

    job_dir = deps.paths.drafts / slug
    resume_path = job_dir / f"[TBD] resume-v{version}.md"
    resume_rel_path = str(resume_path.relative_to(deps.paths.hunter_dir))

    if deps.config.dry_run:
        # Dry run: skip pre-copy and LLM call, just return the version
        deps.logger.info(f"  {slug}: dry-run customize v{version} -> {resume_path.name}")
        return {"resume_versions": [ResumeVersion(version=version, path=resume_path)]}

    job_dir.mkdir(parents=True, exist_ok=True)

    # Pre-copy the starting draft: base resume for v1, previous version for v2+.
    feedback = None
    if version == 1:
        if not deps.paths.base_resume.exists():
            raise RuntimeError(
                f"{slug}: base resume not found at {deps.paths.base_resume}"
            )
        shutil.copy2(deps.paths.base_resume, resume_path)
    else:
        previous = sorted(
            job_dir.glob(f"*resume-v{version - 1}.md"),
            key=lambda p: p.name,
        )
        if not previous:
            # The immediate prior version may have been removed as a
            # byte-identical duplicate — fall back to the highest-versioned
            # resume file still on disk.
            existing = [
                p
                for p in job_dir.glob("*resume-v*.md")
                if parse_version_from_filename(p.name) is not None
            ]
            previous = sorted(
                existing,
                key=lambda p: parse_version_from_filename(p.name) or 0,
            )[-1:]
        if not previous:
            raise RuntimeError(
                f"{slug}: could not find resume-v{version - 1} to revise from"
            )
        shutil.copy2(previous[0], resume_path)
        feedback = {
            "grade": state.latest_grade.model_dump() if state.latest_grade else None,
            "verified": state.verification.verified if state.verification else None,
            "truthfulness_issues": (
                [c.claim for c in state.verification.unverifiable_claims]
                if state.verification
                else []
            ),
        }

    draft_content = resume_path.read_text(encoding="utf-8")
    before_bytes = resume_path.read_bytes()
    deps.logger.debug(f"  {slug}: pre-copied starting draft -> {resume_path.name}")

    # Pre-read source files for prompt inlining
    base_resume_content = deps.paths.base_resume.read_text(encoding="utf-8")
    linkedin_content = deps.paths.linkedin_experience.read_text(encoding="utf-8")
    few_shot_content = _load_few_shot_examples(deps.paths, deps.config)

    jd_grade = state.jd_grade.grade if state.jd_grade else 0
    jd_justification = state.jd_grade.justification if state.jd_grade else ""
    prompt = build_customize_prompt(
        slug, jd_text, version, jd_grade, jd_justification,
        base_resume_content, linkedin_content, few_shot_content,
        draft_content=draft_content, feedback=feedback,
        resume_rel_path=resume_rel_path,
    )

    # Call LLM (customizer model edits the file in place)
    # The customizer uses scoped permissions (ADR-0010, E4) — a config file
    # restricts writes to drafts/** and exec to count_lines.py only.
    customizer_config = None
    if deps.paths.agent_permissions.exists():
        customizer_config = str(deps.paths.agent_permissions)

    # alive_check_seconds=<timeout> disables the 10s liveness probe — a
    # customize turn legitimately runs minutes without stdout, and the probe
    # was killing attempt 0 and forcing a wasted retry.
    customize_timeout = deps.config.timeout_for("customize")
    output, error = deps.llm(
        prompt,
        model=deps.config.models.customizer,
        timeout=customize_timeout,
        alive_check_seconds=customize_timeout,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
        config_path=customizer_config,
        job_slug=slug, step="customize",
    )

    if error:
        raise RuntimeError(
            f"{slug}: customization v{version} LLM call failed: {error}"
        )

    # Verify the resume file exists
    if not resume_path.exists():
        # Check if it was created with a different name
        resume_files = list(job_dir.glob(f"*resume-v{version}.md"))
        if resume_files:
            resume_path = resume_files[0]
        else:
            raise RuntimeError(
                f"{slug}: customization v{version} did not produce a resume file"
            )

    # Parse the YES/NO outcome and validate it against the file diff.
    can_improve = parse_customize_outcome(output)
    changed = resume_path.read_bytes() != before_bytes

    if not changed and can_improve:
        raise RuntimeError(
            f"{slug}: customizer answered YES (further improvement possible) "
            f"but left resume-v{version} byte-identical — outcome contract violation"
        )

    if not changed and version > 1:
        # Byte-identical + NO: valid early stop. The pre-copied file isn't a
        # distinct version — remove it; grading/veracity are skipped via
        # route_after_customize and should_continue sees the NO signal.
        resume_path.unlink()
        deps.logger.info(
            f"  {slug}: v{version} byte-identical to v{version - 1} and "
            f"customizer said NO -> valid stop (evaluation skipped)"
        )
        return {"customizer_can_improve": False, "customizer_draft_unchanged": True}

    deps.logger.info(
        f"  {slug}: resume v{version} customized -> {resume_path.name} "
        f"(can_improve={'YES' if can_improve else 'NO'})"
    )
    return {
        "resume_versions": [ResumeVersion(version=version, path=resume_path)],
        "customizer_can_improve": can_improve,
        "customizer_draft_unchanged": False,
    }
