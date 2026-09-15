#!/usr/bin/env python3
"""Grade a resume against a job description from a folder of PDFs or MDs.

Usage:
    python3 -m pipeline.helpers.grade_resume <folder> [--model <model>] [--timeout <seconds>]

The folder should contain two files:
  - A resume (PDF or MD, with "resume" or "cv" in the filename)
  - A job description (PDF or MD, with "jd", "job", or "description" in the
    filename)

Extracts text from both files, grades the resume against the JD using the
same two-call decomposition (reasoning + scoring) and grading protocol as
the pipeline's step7_grade_resume, and prints the grade + per-criterion
feedback.

This is a standalone tool — it does NOT use the candidate's profile files
(base-resume.md, full-experience.md) from _config/profile/. Only the
provided resume and JD are used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.infrastructure.config import PipelineConfig, load_config
from pipeline.infrastructure.injection_filter import scrub_jd_text, wrap_jd_content
from pipeline.infrastructure.llm_interface import RealLLM, parse_llm_json
from pipeline.infrastructure.protocol_constants import (
    GRADING_REASONING_PROTOCOL,
    GRADING_SCORING_PROTOCOL,
)


# ─── File discovery ──────────────────────────────────────────────────────────

_RESUME_KEYWORDS = ("resume", "cv")
_JD_KEYWORDS = ("jd", "job-description", "job_description", "job", "description", "posting")
_SUPPORTED_EXTS = (".pdf", ".md", ".txt")


def _classify_filename(name: str) -> str | None:
    """Classify a filename as 'resume', 'jd', or None (ambiguous).

    Checks lowercased filename (without extension) against keyword lists.
    """
    lower = name.lower()
    if any(kw in lower for kw in _RESUME_KEYWORDS):
        return "resume"
    if any(kw in lower for kw in _JD_KEYWORDS):
        return "jd"
    return None


def find_input_files(folder: Path) -> tuple[Path, Path]:
    """Find the resume and JD files in a folder.

    Scans for .pdf, .md, and .txt files. Classifies each by filename
    keywords. If exactly two files are found and one is classified, the
    other is assigned the remaining role.

    Returns:
        (resume_path, jd_path)

    Raises:
        FileNotFoundError: If the folder doesn't exist or no supported files found.
        ValueError: If files can't be unambiguously classified.
    """
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")

    candidates = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTS
    )

    if not candidates:
        raise FileNotFoundError(
            f"No supported files ({', '.join(_SUPPORTED_EXTS)}) found in {folder}"
        )

    resumes: list[Path] = []
    jds: list[Path] = []
    unclassified: list[Path] = []

    for f in candidates:
        role = _classify_filename(f.name)
        if role == "resume":
            resumes.append(f)
        elif role == "jd":
            jds.append(f)
        else:
            unclassified.append(f)

    # If we have unclassified files, assign them to the missing role
    # (only when the other role is already filled).
    if unclassified and not resumes and len(jds) == 1:
        resumes.append(unclassified.pop(0))
    if unclassified and not jds and len(resumes) == 1:
        jds.append(unclassified.pop(0))

    # If still unclassified files remain and we have neither role filled,
    # try: exactly 2 files → assign by order (first = resume, second = JD).
    if not resumes and not jds and len(candidates) == 2:
        resumes.append(candidates[0])
        jds.append(candidates[1])

    if not resumes or not jds:
        found = [f.name for f in candidates]
        raise ValueError(
            f"Could not identify resume and JD files in {folder}.\n"
            f"Found: {found}\n"
            f"Name the resume file with 'resume' or 'cv' in the filename, "
            f"and the JD with 'jd', 'job', or 'description'."
        )

    if len(resumes) > 1:
        raise ValueError(
            f"Multiple resume files found: {[f.name for f in resumes]}. "
            f"Put only one resume in the folder."
        )
    if len(jds) > 1:
        raise ValueError(
            f"Multiple JD files found: {[f.name for f in jds]}. "
            f"Put only one job description in the folder."
        )

    return resumes[0], jds[0]


# ─── Text extraction ─────────────────────────────────────────────────────────


def extract_text(path: Path) -> str:
    """Extract text from a PDF, Markdown, or plain text file.

    Uses pymupdf (fitz) for PDFs. Reads .md/.txt files directly as UTF-8.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    return path.read_text(encoding="utf-8")


def _extract_pdf_text(path: Path) -> str:
    """Extract text from a PDF using pymupdf."""
    import pymupdf

    doc = pymupdf.open(str(path))
    try:
        pages = [page.get_text() for page in doc]
    finally:
        doc.close()
    return "\n".join(pages).strip()


# ─── Prompt builders ─────────────────────────────────────────────────────────


def build_reasoning_prompt(resume_content: str, jd_text: str) -> str:
    """Build the Call 1 (reasoning) prompt for the grader model.

    Inlines both the resume and JD text directly into the prompt — the
    LLM does not need to read any files. JD text is scrubbed of injection
    patterns and wrapped in isolation tags (same as step4/step6).
    """
    scrubbed = scrub_jd_text(jd_text)
    wrapped = wrap_jd_content(scrubbed)
    return f"""Be realistically harsh — as harsh as a recruiter taking only 30 seconds to skim the resume.

{GRADING_REASONING_PROTOCOL}

## Source documents (pre-loaded — do NOT read any files)

### Resume

```markdown
{resume_content}
```

### Job Description

{wrapped}

## Your task

Extract requirements from the JD's qualifications sections only. Assign verdicts (DIRECT_HIT / ADDRESSED / PARTIAL / GAP) with one-sentence comments citing specific resume evidence.

Output ONLY valid JSON to stdout (format: {{"per_criterion": [{{"requirement": "...", "tier": "core", "assessment": "DIRECT_HIT", "comment": "..."}}]}}). No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. Do NOT compute a score — that is Call 2's job.

If the JD requires security clearance, output CLEARANCE instead of the reasoning JSON."""


def build_scoring_prompt(reasoning_json: str) -> str:
    """Build the Call 2 (scoring) prompt for the grader model.

    Computes the score from the fixed reasoning JSON produced by Call 1.
    """
    return f"""{GRADING_SCORING_PROTOCOL}

The reasoning from Call 1 is provided below as fixed, committed input. You CANNOT change these verdicts — your job is to compute the score from them.

Reasoning JSON from Call 1:
{reasoning_json}

Compute the score using the protocol formula (ceiling, core score, preferred bonus, quantitative base, subjective adjustment, final score). Pass through the per_criterion list unchanged.

This is arithmetic — count the verdicts, apply the formula, output the number. You have all the input above. Do NOT run commands, write scripts, search files, or use any tools. Do NOT read files — everything you need is in this prompt. Such actions will be blocked by a hook and waste time. If you spend more than a few seconds on this, you are overcomplicating this. Just compute and output.

Output ONLY valid JSON to stdout. No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. Do NOT rename anything. Do NOT append to any log file."""


# ─── Grading ─────────────────────────────────────────────────────────────────


def grade_resume(
    resume_content: str,
    jd_text: str,
    llm: RealLLM,
    config: PipelineConfig,
) -> dict:
    """Grade a resume against a JD using the two-call decomposition.

    Call 1 (reasoning): extract requirements + assess each against the resume.
    Call 2 (scoring): compute the score from the fixed reasoning.

    Returns the parsed grade JSON dict (with "grade" and "per_criterion").
    Raises RuntimeError on LLM failure or unparseable JSON.
    """
    model = config.models.grader
    timeout = config.timeout_for("grade-resume")
    workspace = str(Path(__file__).resolve().parent.parent.parent)

    # ─── Call 1: Reasoning ─────────────────────────────────────────────
    reasoning_prompt = build_reasoning_prompt(resume_content, jd_text)
    reasoning_output, reasoning_error = llm(
        reasoning_prompt,
        model=model,
        timeout=timeout,
        retries=config.llm_retries,
        retry_delay=config.llm_retry_delay,
        workspace=workspace,
        permission_mode="normal",
        job_slug="grade-only",
        step="grade-resume",
    )

    if reasoning_error:
        raise RuntimeError(f"Reasoning call failed: {reasoning_error}")

    # Check for clearance detection
    if "CLEARANCE" in (reasoning_output or "").upper():
        return {
            "grade": 0,
            "per_criterion": [],
            "justification": "Security clearance required — auto-rejected.",
        }

    reasoning_data = parse_llm_json(reasoning_output)
    if reasoning_data is None:
        raise RuntimeError(
            f"Could not parse reasoning JSON from Call 1 stdout: "
            f"{(reasoning_output or '')[:200]}"
        )

    # ─── Call 2: Scoring ───────────────────────────────────────────────
    reasoning_json_str = json.dumps(reasoning_data)
    scoring_prompt = build_scoring_prompt(reasoning_json_str)
    scoring_output, scoring_error = llm(
        scoring_prompt,
        model=model,
        timeout=timeout,
        retries=config.llm_retries,
        retry_delay=config.llm_retry_delay,
        workspace=workspace,
        permission_mode="normal",
        job_slug="grade-only",
        step="grade-resume",
    )

    if scoring_error:
        raise RuntimeError(f"Scoring call failed: {scoring_error}")

    grade_data = parse_llm_json(scoring_output)
    if grade_data is None:
        raise RuntimeError(
            f"Could not parse grade JSON from scoring call stdout: "
            f"{(scoring_output or '')[:200]}"
        )

    return grade_data


# ─── Output formatting ───────────────────────────────────────────────────────

_ASSESSMENT_LABELS = {
    "DIRECT_HIT": "DIRECT HIT",
    "ADDRESSED": "ADDRESSED",
    "PARTIAL": "PARTIAL",
    "GAP": "GAP",
}


def format_result(grade_data: dict) -> str:
    """Format the grading result as a human-readable summary."""
    grade = grade_data.get("grade", "?")
    per_criterion = grade_data.get("per_criterion", [])
    justification = grade_data.get("justification", "")

    lines = []
    lines.append("=" * 60)
    lines.append(f"  GRADE: {grade}/10")
    if justification:
        lines.append(f"  {justification}")
    lines.append("=" * 60)
    lines.append("")

    if not per_criterion:
        lines.append("(no per-criterion breakdown available)")
        return "\n".join(lines)

    # Group by tier
    core = [c for c in per_criterion if c.get("tier", "").lower() == "core"]
    nice = [c for c in per_criterion if c.get("tier", "").lower() != "core"]

    if core:
        lines.append("── Core Requirements ──")
        for c in core:
            assessment = _ASSESSMENT_LABELS.get(
                c.get("assessment", ""), c.get("assessment", "")
            )
            lines.append(f"  [{assessment}] {c.get('requirement', '')}")
            comment = c.get("comment", "")
            if comment:
                lines.append(f"      {comment}")
        lines.append("")

    if nice:
        lines.append("── Nice-to-Have ──")
        for c in nice:
            assessment = _ASSESSMENT_LABELS.get(
                c.get("assessment", ""), c.get("assessment", "")
            )
            lines.append(f"  [{assessment}] {c.get('requirement', '')}")
            comment = c.get("comment", "")
            if comment:
                lines.append(f"      {comment}")
        lines.append("")

    # Summary stats
    hits = sum(1 for c in per_criterion if c.get("assessment") == "DIRECT_HIT")
    addressed = sum(1 for c in per_criterion if c.get("assessment") == "ADDRESSED")
    partial = sum(1 for c in per_criterion if c.get("assessment") == "PARTIAL")
    gaps = sum(1 for c in per_criterion if c.get("assessment") == "GAP")
    total = len(per_criterion)

    lines.append("── Summary ──")
    lines.append(f"  {total} criteria: {hits} direct hits, {addressed} addressed, "
                 f"{partial} partial, {gaps} gaps")
    lines.append("")

    return "\n".join(lines)


# ─── CLI ─────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grade a resume against a job description from a folder of PDFs/MDs.",
    )
    parser.add_argument(
        "folder",
        type=str,
        help="Path to a folder containing a resume file and a JD file.",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Override the grader model name (default: from config.json).",
    )
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="Override the LLM timeout in seconds (default: from config.json).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output the raw grade JSON instead of a formatted summary.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    folder = Path(args.folder).resolve()

    # Load config for model name and LLM settings
    hunter_dir = Path(__file__).resolve().parent.parent.parent
    config = load_config(hunter_dir / "config.json")

    # Apply CLI overrides
    if args.model or args.timeout:
        overrides = {}
        if args.model:
            overrides["models"] = config.models.model_copy(
                update={"grader": args.model}
            )
        if args.timeout:
            overrides["llm_timeout_seconds"] = args.timeout
        config = config.model_copy(update=overrides)

    # Find and extract text from the input files
    try:
        resume_path, jd_path = find_input_files(folder)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Resume: {resume_path.name}")
    print(f"JD:     {jd_path.name}")
    print()

    resume_content = extract_text(resume_path)
    jd_text = extract_text(jd_path)

    if not resume_content.strip():
        print(f"Error: No text could be extracted from {resume_path.name}", file=sys.stderr)
        sys.exit(1)
    if not jd_text.strip():
        print(f"Error: No text could be extracted from {jd_path.name}", file=sys.stderr)
        sys.exit(1)

    # Grade
    from pipeline.infrastructure.paths import Paths
    paths = Paths.from_hunter_dir(hunter_dir)
    llm = RealLLM(db_path=str(paths.jobs_db), export_dir=str(paths.exports))
    try:
        grade_data = grade_resume(resume_content, jd_text, llm, config)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Output
    if args.json:
        print(json.dumps(grade_data, indent=2))
    else:
        print(format_result(grade_data))


if __name__ == "__main__":
    main()
