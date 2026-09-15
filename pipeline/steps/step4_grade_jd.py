"""Step 4 (per-job): Grade JD — LLM grades the JD + detects clearance.

This is a LangGraph node. A single LLM call extracts requirements and
assesses each criterion against the candidate's full background (base
resume + LinkedIn); the score is then computed deterministically in code
(pipeline.infrastructure.scoring). Setting config.deterministic_scoring
to False restores the legacy two-call path (a second LLM call applies the
scoring formula) for A/B comparison.

Source documents (base resume, LinkedIn) are pre-fed into the prompt to
eliminate file-read tool calls.

Clearance detection is LLM-only (ADR-0007 — no regex). If the LLM returns
CLEARANCE, the jd_grade.is_clearance flag is set and the conditional edge
routes to trash.
"""
from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.llm_interface import get_deps, parse_llm_json
from pipeline.infrastructure.state import Criterion, JobState, JdGrade, JudgmentCall
from pipeline.infrastructure.file_ops import rename_with_grade, strip_url_metadata
from pipeline.infrastructure.grades_log import append_grades_log
from pipeline.infrastructure.injection_filter import scrub_jd_text, wrap_jd_content
from pipeline.infrastructure.protocol_constants import (
    JD_GRADING_REASONING_PROTOCOL,
    JD_GRADING_SCORING_PROTOCOL,
)
from pipeline.infrastructure.scoring import compute_grade


# ─── Two-call prompt builders ────────────────────────────────────────────────


def build_jd_reasoning_prompt(
    slug: str, jd_text: str,
    base_resume_content: str, linkedin_content: str,
    location_policy: str = "",
) -> str:
    """Build the Call 1 (reasoning) prompt for the JD grader.

    Evaluates the candidate's full background (base resume + LinkedIn)
    against the JD requirements. Source documents are pre-fed.

    JD text is scrubbed of injection patterns and wrapped in structural
    isolation tags before being interpolated into the prompt (E4).
    """
    scrubbed = scrub_jd_text(jd_text)
    wrapped = wrap_jd_content(scrubbed)
    location_note = (
        f"Location note: {location_policy}\n\n" if location_policy else ""
    )
    return f"""You are grading a job description to determine if it's worth customizing a resume for.

{JD_GRADING_REASONING_PROTOCOL}

## Source documents (pre-loaded — do NOT read any files)

### Base resume (the customization base — every tailored resume starts here)

```markdown
{base_resume_content}
```

### Full LinkedIn experience (reference pool — the candidate's complete career history)

```markdown
{linkedin_content}
```

{location_note}

## Job

Job: {slug}
{wrapped}

## Your task

Extract requirements from the JD's qualifications sections only. Assess each against the candidate's full background (base resume + LinkedIn above). A requirement is a DIRECT_HIT if the experience appears in either source.

Output ONLY valid JSON to stdout (format: {{"per_criterion": [{{"requirement": "...", "tier": "core", "assessment": "DIRECT_HIT", "comment": "..."}}], "judgment_calls": [{{"requirement": "...", "phrase": "...", "classified_as": "preferred", "reason": "..."}}], "summary": "one paragraph — overall fit, the decisive hits and gaps"}}). No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. Do NOT compute a score — the orchestrator applies the scoring formula deterministically.

If the JD requires security clearance, output CLEARANCE instead of the reasoning JSON."""


def build_jd_scoring_prompt(slug: str, reasoning_json: str) -> str:
    """Build the Call 2 (scoring) prompt for the JD grader.

    Computes the score from the fixed reasoning JSON produced by Call 1.
    Legacy path — used only when config.deterministic_scoring is False.
    """
    return f"""{JD_GRADING_SCORING_PROTOCOL}

You are grading the JD for {slug}. The reasoning from Call 1 is provided below as fixed, committed input. You CANNOT change these verdicts — your job is to compute the score from them.

Reasoning JSON from Call 1:
{reasoning_json}

Compute the score using the protocol formula (ceiling, core score, preferred bonus, quantitative base, subjective adjustment, final score). Pass through the per_criterion list unchanged. If judgment_calls was provided in the reasoning JSON, pass it through unchanged as well.

This is arithmetic — count the verdicts, apply the formula, output the number. You have all the input above. Do NOT run commands, write scripts, search files, or use any tools. Do NOT read files — everything you need is in this prompt. Such actions will be blocked by a hook and waste time. If you spend more than a few seconds on this, you are overthinking. Just compute and output.

Output ONLY valid JSON to stdout. No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. The orchestrator handles all file operations from your stdout output."""


def build_jd_tier_retry_prompt(
    slug: str, jd_text: str,
    base_resume_content: str, linkedin_content: str,
    prior_reasoning_json: str,
    location_policy: str = "",
) -> str:
    """Build a retry prompt when the LLM classified zero requirements as core.

    Re-runs the reasoning call with an explicit instruction to classify
    requirements into core and preferred tiers. The prior reasoning is
    provided so the LLM can correct its tier assignments without re-extracting.
    """
    scrubbed = scrub_jd_text(jd_text)
    wrapped = wrap_jd_content(scrubbed)
    location_note = (
        f"Location note: {location_policy}\n\n" if location_policy else ""
    )
    return f"""You are re-grading a job description because your previous response classified every requirement as "preferred" — none as "core". This is almost always wrong: job descriptions have core requirements (qualifications the candidate must meet) and preferred requirements (nice-to-haves).

{JD_GRADING_REASONING_PROTOCOL}

## Source documents (pre-loaded — do NOT read any files)

### Base resume (the customization base — every tailored resume starts here)

```markdown
{base_resume_content}
```

### Full LinkedIn experience (reference pool — the candidate's complete career history)

```markdown
{linkedin_content}
```

{location_note}

## Job

Job: {slug}
{wrapped}

## Your previous response (correct the tier assignments)

```json
{prior_reasoning_json}
```

## Your task

Re-evaluate your tier classifications. Requirements listed under "qualifications", "requirements", "what you'll do", or "what we'd like you to have" headings are CORE — they are the job's must-have qualifications. Requirements that are explicitly labeled as "preferred", "nice to have", "bonus", or "a plus" are PREFERRED. Re-assess each requirement's tier accordingly. Keep your assessments (DIRECT_HIT/ADDRESSED/PARTIAL/GAP) unchanged unless the tier change reveals an error.

Output ONLY valid JSON to stdout (same format as before). No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files.

If the JD requires security clearance, output CLEARANCE instead of the reasoning JSON."""


# ─── Node ────────────────────────────────────────────────────────────────────


def step4_grade_jd_node(state: JobState, config: RunnableConfig) -> dict:
    """Grade the JD using the LLM. Detects clearance (ADR-0007).

    Single LLM call produces per-criterion verdicts (DIRECT_HIT/ADDRESSED/
    PARTIAL/GAP) plus a summary; the score is then computed deterministically
    in code (pipeline.infrastructure.scoring) — the scoring protocol's formula
    is arithmetic, not judgment, so no second LLM call is needed.

    Returns partial state with jd_grade set. If clearance is detected,
    jd_grade.is_clearance is True and the conditional edge routes to trash.
    If the LLM call fails or the reasoning can't be parsed, raises an
    exception (caught by the orchestrator's per-job error boundary).
    """
    deps = get_deps(config)
    slug = state.slug

    # Read JD text from state or from file
    jd_text = state.jd_text
    if not jd_text and state.jd_path and state.jd_path.exists():
        jd_text = state.jd_path.read_text(encoding="utf-8")

    # Strip URL metadata comment for grading
    jd_text_clean = strip_url_metadata(jd_text)

    # Pre-read source files for prompt inlining
    base_resume_content = deps.paths.base_resume.read_text(encoding="utf-8")
    linkedin_content = deps.paths.linkedin_experience.read_text(encoding="utf-8")

    # ─── Reasoning call (verdicts only — score is computed in code) ────
    reasoning_prompt = build_jd_reasoning_prompt(
        slug, jd_text_clean, base_resume_content, linkedin_content,
        location_policy=deps.config.location_policy,
    )
    # alive_check_seconds=<timeout> disables the 10s liveness probe (see step6).
    grade_jd_timeout = deps.config.timeout_for("grade-jd")
    reasoning_output, reasoning_error = deps.llm(
        reasoning_prompt,
        model=deps.config.models.grader,
        timeout=grade_jd_timeout,
        alive_check_seconds=grade_jd_timeout,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
        job_slug=slug, step="grade-jd",
    )

    if reasoning_error:
        raise RuntimeError(f"{slug}: JD grading reasoning call failed: {reasoning_error}")

    # Check for clearance detection.
    # Per the reasoning protocol, the LLM outputs the literal string "CLEARANCE"
    # (and nothing else) when a JD requires security clearance. A naive substring
    # check fires on "No security clearance required" inside the JSON summary —
    # so we check if the stripped response IS "CLEARANCE", not if it CONTAINS it.
    if (reasoning_output or "").strip().upper() == "CLEARANCE":
        deps.logger.info(f"  {slug}: clearance detected by LLM → trash")
        jd_grade = JdGrade(grade=0, justification="CLEARANCE", is_clearance=True)
        return {"jd_grade": jd_grade}

    # Parse reasoning JSON from stdout
    reasoning_data = parse_llm_json(reasoning_output)
    if reasoning_data is None:
        raise RuntimeError(
            f"{slug}: could not parse reasoning JSON from reasoning call stdout: {reasoning_output[:200]}"
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
        retry_prompt = build_jd_tier_retry_prompt(
            slug, jd_text_clean, base_resume_content, linkedin_content,
            reasoning_output.strip(),
            location_policy=deps.config.location_policy,
        )
        retry_output, retry_error = deps.llm(
            retry_prompt,
            model=deps.config.models.grader,
            timeout=grade_jd_timeout,
            alive_check_seconds=grade_jd_timeout,
            retries=deps.config.llm_retries,
            retry_delay=deps.config.llm_retry_delay,
            workspace=str(deps.paths.hunter_dir),
            job_slug=slug, step="grade-jd",
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

    if deps.config.deterministic_scoring:
        # Score in code — the formula is spec'd in
        # _config/jd-grading-scoring-protocol.md and implemented in
        # pipeline.infrastructure.scoring.
        grade_data = compute_grade(reasoning_data.get("per_criterion", []))
        grade_data["justification"] = str(reasoning_data.get("summary", ""))
        grade_data["judgment_calls"] = reasoning_data.get("judgment_calls", [])
        grade = float(grade_data["grade"])
        justification = grade_data["justification"]
    else:
        # Legacy two-call path — a second LLM call applies the formula.
        reasoning_json_str = json.dumps(reasoning_data)
        scoring_prompt = build_jd_scoring_prompt(slug, reasoning_json_str)
        scoring_output, scoring_error = deps.llm(
            scoring_prompt,
            model=deps.config.models.grader,
            timeout=grade_jd_timeout,
            alive_check_seconds=grade_jd_timeout,
            retries=deps.config.llm_retries,
            retry_delay=deps.config.llm_retry_delay,
            workspace=str(deps.paths.hunter_dir),
            job_slug=slug, step="grade-jd",
        )
        if scoring_error:
            raise RuntimeError(f"{slug}: JD grading scoring call failed: {scoring_error}")
        grade_data = parse_llm_json(scoring_output)
        if grade_data is None:
            raise RuntimeError(
                f"{slug}: could not parse grade JSON from scoring call stdout: {scoring_output[:200]}"
            )
        grade = float(grade_data.get("grade", -1))
        justification = str(grade_data.get("justification", ""))
        if not (0 <= grade <= 10):
            # Transport success but validation failure — clamp and warn.
            raw_grade = grade
            grade = max(0.0, min(10.0, grade))
            deps.logger.warning(
                f"  {slug}: JD grade {raw_grade} out of range [0, 10] — "
                f"clamped to {grade} (flagged for review)"
            )
            justification = f"[VALIDATION: raw grade {raw_grade} clamped to {grade}] {justification}"

    deps.logger.info(f"  {slug}: JD grade = {grade}")
    jd_grade = JdGrade(
        grade=grade,
        justification=justification,
        is_clearance=False,
        per_criterion=[
            Criterion.model_validate(c)
            for c in grade_data.get("per_criterion", reasoning_data.get("per_criterion", []))
        ],
        judgment_calls=[
            JudgmentCall.model_validate(j)
            for j in grade_data.get("judgment_calls", reasoning_data.get("judgment_calls", []))
        ],
    )

    # Log the JD grade to .grades.log (audit trail — same file the resume
    # grader appends to).
    append_grades_log(
        str(state.jd_path) if state.jd_path else slug,
        grade,
        deps.config.models.grader,
        jd_grade.per_criterion,
        deps.paths.grades_log,
        deps.logger,
        note=justification,
    )

    # Rename the JD file with the grade
    if not deps.config.dry_run and state.jd_path and state.jd_path.exists():
        rename_with_grade(state.jd_path, grade)

    return {"jd_grade": jd_grade}
