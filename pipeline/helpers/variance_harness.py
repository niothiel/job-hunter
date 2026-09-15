"""Variance harness — A/B compare deterministic vs legacy LLM grading.

Runs the same JD (or resume) through the grader N times in each scoring
mode (`deterministic_scoring=True` vs `False`) and reports the score
distribution per mode, so you can see whether the two approaches are in
the same ballpark and aligned with expectations.

Usage (real LLM calls — takes minutes per JD):

    # Auto-pick 8 JDs across grade bands, 5 runs each mode:
    python3 -m pipeline.helpers.variance_harness --sample 8 --runs 5

    # Specific slugs:
    python3 -m pipeline.helpers.variance_harness --slugs acme-senior-director,foo-bar --runs 5

    # Resume grading (needs customized resumes in stages/2_drafts/):
    python3 -m pipeline.helpers.variance_harness --mode resume --sample 4 --runs 5

    # Both JD and resume grading:
    python3 -m pipeline.helpers.variance_harness --mode both --sample 6 --runs 5

    # Dry-run (FakeLLM — verifies harness logic, no real calls):
    python3 -m pipeline.helpers.variance_harness --dry-run --sample 3 --runs 2

Output:
    variance-report.json  — raw per-run data (grades, verdicts, timings)
    variance-report.md    — human-readable summary with per-JD stats and
                            side-by-side mode comparison

The report lives in --output-dir (default: cwd).
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from pipeline.infrastructure.config import PipelineConfig, load_config
from pipeline.infrastructure.llm_interface import RealLLM, FakeLLM
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.state import JobState, ResumeVersion


# ─── JD selection ────────────────────────────────────────────────────────────

GRADE_BANDS = [
    ("trash (<6)", 0.0, 6.0),
    ("job-fit (6-7.9)", 6.0, 8.0),
    ("proceed (8-8.9)", 8.0, 9.0),
    ("strong (9-10)", 9.0, 10.1),
]


def _extract_grade_from_filename(name: str) -> float | None:
    """Extract the numeric grade from a graded file like '[8.5] job-description.md'."""
    if not name.startswith("["):
        return None
    end = name.find("]")
    if end <= 0:
        return None
    try:
        return float(name[1:end])
    except ValueError:
        return None


def pick_jds_across_bands(listings_dir: Path, n: int) -> list[tuple[str, float, Path]]:
    """Pick ~n JDs spread across grade bands.

    Returns list of (slug, known_grade, jd_file_path).
    """
    candidates: dict[str, list[tuple[str, float, Path]]] = {
        label: [] for label, _, _ in GRADE_BANDS
    }

    for job_dir in sorted(listings_dir.iterdir()):
        if not job_dir.is_dir():
            continue
        for f in job_dir.iterdir():
            if not f.name.endswith("job-description.md"):
                continue
            grade = _extract_grade_from_filename(f.name)
            if grade is None:
                continue
            for label, lo, hi in GRADE_BANDS:
                if lo <= grade < hi:
                    candidates[label].append((job_dir.name, grade, f))
                    break

    # Spread n across bands that have candidates, round-robin
    picked: list[tuple[str, float, Path]] = []
    bands_with_data = [(label, items) for label, items in candidates.items() if items]
    if not bands_with_data:
        return []

    # Pick roughly n / len(bands) from each band
    per_band = max(1, n // len(bands_with_data))
    for label, items in bands_with_data:
        # Pick evenly spaced within the band for diversity
        step = max(1, len(items) // per_band)
        picked.extend(items[::step][:per_band])

    return picked[:n]


def pick_resumes_for_grading(drafts_dir: Path, n: int) -> list[tuple[str, Path, Path]]:
    """Pick customized resumes from stages/2_drafts/ for resume grading.

    Returns list of (slug, resume_file, jd_file_in_drafts).
    """
    picked: list[tuple[str, Path, Path]] = []
    for job_dir in sorted(drafts_dir.iterdir()):
        if not job_dir.is_dir():
            continue
        resumes = sorted(job_dir.glob("*resume-v*.md"))
        jds = sorted(job_dir.glob("*job-description.md"))
        if resumes and jds:
            picked.append((job_dir.name, resumes[-1], jds[0]))
        if len(picked) >= n:
            break
    return picked[:n]


# ─── Run execution ──────────────────────────────────────────────────────────


@dataclass
class RunResult:
    """One grading run — one JD × one mode × one iteration."""
    slug: str
    mode: str  # "deterministic" | "legacy"
    iteration: int
    grade: float
    is_clearance: bool
    justification: str
    per_criterion: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    error: str | None = None


def _build_temp_workspace(real_paths: Paths, real_config: PipelineConfig,
                          tmp_root: Path) -> Paths:
    """Create a temp workspace with profile files copied in.

    The temp workspace mirrors the real layout so step nodes find files
    where they expect them (base_resume, linkedin_experience, etc.).
    """
    tmp_paths = Paths.from_hunter_dir(
        tmp_root,
        base_resume_filename=real_config.profile.base_resume_file,
        linkedin_experience_filename=real_config.profile.linkedin_experience_file,
    )
    # Create profile dirs and copy files
    tmp_paths.base_resume.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.linkedin_experience.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(real_paths.base_resume, tmp_paths.base_resume)
    shutil.copy2(real_paths.linkedin_experience, tmp_paths.linkedin_experience)
    # Create stage dirs the nodes touch
    tmp_paths.listings.mkdir(parents=True, exist_ok=True)
    tmp_paths.drafts.mkdir(parents=True, exist_ok=True)
    tmp_paths.grading.mkdir(parents=True, exist_ok=True)
    return tmp_paths


def _make_node_config(llm, logger, config: PipelineConfig, paths: Paths) -> dict:
    """Build a RunnableConfig for direct node invocation."""
    return {
        "configurable": {
            "llm": llm,
            "logger": logger,
            "config": config,
            "paths": paths,
        }
    }


def run_jd_grading(slug: str, jd_text: str, known_grade: float,
                   mode: str, iteration: int,
                   real_paths: Paths, base_config: PipelineConfig,
                   llm, logger, tmp_root: Path) -> RunResult:
    """Run step4 (JD grading) once in the given mode."""
    from pipeline.steps.step4_grade_jd import step4_grade_jd_node

    deterministic = (mode == "deterministic")
    config = base_config.model_copy(update={"deterministic_scoring": deterministic})

    tmp_workspace = _build_temp_workspace(real_paths, config, tmp_root / f"{slug}-{mode}-{iteration}")
    # Write JD to a temp file so step4 can rename it
    job_dir = tmp_workspace.listings / slug
    job_dir.mkdir(parents=True, exist_ok=True)
    jd_file = job_dir / "[TBD] job-description.md"
    jd_file.write_text(f"<!-- url: https://example.com/{slug} -->\n\n{jd_text}", encoding="utf-8")

    node_config = _make_node_config(llm, logger, config, tmp_workspace)
    state = JobState(
        slug=slug,
        url=f"https://example.com/{slug}",
        jd_text=jd_text,
        jd_path=jd_file,
    )

    start = time.monotonic()
    try:
        result = step4_grade_jd_node(state, node_config)
        elapsed = time.monotonic() - start
        jd_grade = result.get("jd_grade")
        if jd_grade is None:
            return RunResult(slug, mode, iteration, grade=-1, is_clearance=False,
                            justification="", elapsed_seconds=elapsed,
                            error="No jd_grade in result")
        return RunResult(
            slug=slug, mode=mode, iteration=iteration,
            grade=jd_grade.grade,
            is_clearance=jd_grade.is_clearance,
            justification=jd_grade.justification,
            per_criterion=[c.model_dump() for c in jd_grade.per_criterion],
            elapsed_seconds=elapsed,
        )
    except Exception as e:
        elapsed = time.monotonic() - start
        return RunResult(slug, mode, iteration, grade=-1, is_clearance=False,
                        justification="", elapsed_seconds=elapsed, error=str(e))


def run_resume_grading(slug: str, resume_text: str, jd_text: str,
                        mode: str, iteration: int,
                        real_paths: Paths, base_config: PipelineConfig,
                        llm, logger, tmp_root: Path) -> RunResult:
    """Run step7 (resume grading) once in the given mode."""
    from pipeline.steps.step7_grade_resume import step7_grade_resume_node

    deterministic = (mode == "deterministic")
    config = base_config.model_copy(update={"deterministic_scoring": deterministic})

    tmp_workspace = _build_temp_workspace(real_paths, config, tmp_root / f"{slug}-resume-{mode}-{iteration}")
    # Copy resume + JD into drafts/<slug>/
    job_dir = tmp_workspace.drafts / slug
    job_dir.mkdir(parents=True, exist_ok=True)
    resume_file = job_dir / "[TBD] resume-v1.md"
    resume_file.write_text(resume_text, encoding="utf-8")
    jd_file = job_dir / "[TBD] job-description.md"
    jd_file.write_text(jd_text, encoding="utf-8")

    node_config = _make_node_config(llm, logger, config, tmp_workspace)
    state = JobState(
        slug=slug,
        url=f"https://example.com/{slug}",
        jd_text=jd_text,
        version_history=[
            ResumeVersion(version=1, path=resume_file, grade=0.0),
        ],
    )

    start = time.monotonic()
    try:
        result = step7_grade_resume_node(state, node_config)
        elapsed = time.monotonic() - start
        latest_grade = result.get("latest_grade")
        if latest_grade is None:
            return RunResult(slug, mode, iteration, grade=-1, is_clearance=False,
                            justification="", elapsed_seconds=elapsed,
                            error="No latest_grade in result")
        return RunResult(
            slug=slug, mode=mode, iteration=iteration,
            grade=latest_grade.grade,
            is_clearance=False,
            justification=latest_grade.justification or "",
            per_criterion=[c.model_dump() for c in latest_grade.per_criterion],
            elapsed_seconds=elapsed,
        )
    except Exception as e:
        elapsed = time.monotonic() - start
        return RunResult(slug, mode, iteration, grade=-1, is_clearance=False,
                        justification="", elapsed_seconds=elapsed, error=str(e))


# ─── Stats + reporting ───────────────────────────────────────────────────────


def _stats(grades: list[float]) -> dict[str, float]:
    if not grades:
        return {"mean": 0, "stdev": 0, "min": 0, "max": 0, "n": 0}
    return {
        "mean": round(statistics.mean(grades), 2),
        "stdev": round(statistics.stdev(grades), 2) if len(grades) > 1 else 0.0,
        "min": round(min(grades), 2),
        "max": round(max(grades), 2),
        "n": len(grades),
    }


def _read_jd_text(jd_file: Path) -> str:
    """Read JD text, stripping the grade prefix and URL metadata comment."""
    text = jd_file.read_text(encoding="utf-8")
    # Strip leading <!-- url: ... --> comment
    if text.startswith("<!--"):
        end = text.find("-->")
        if end > 0:
            text = text[end + 2:].strip()
    return text


def build_report(results: list[RunResult], known_grades: dict[str, float],
                 mode_label: str) -> dict[str, Any]:
    """Build the JSON-serializable report structure."""
    report: dict[str, Any] = {"mode_label": mode_label, "items": []}

    # Group by slug
    by_slug: dict[str, list[RunResult]] = {}
    for r in results:
        by_slug.setdefault(r.slug, []).append(r)

    for slug, runs in sorted(by_slug.items()):
        det_runs = [r for r in runs if r.mode == "deterministic" and r.error is None]
        leg_runs = [r for r in runs if r.mode == "legacy" and r.error is None]
        det_errors = [r for r in runs if r.mode == "deterministic" and r.error is not None]
        leg_errors = [r for r in runs if r.mode == "legacy" and r.error is not None]

        det_grades = [r.grade for r in det_runs]
        leg_grades = [r.grade for r in leg_runs]

        item = {
            "slug": slug,
            "known_grade": known_grades.get(slug),
            "deterministic": {
                "stats": _stats(det_grades),
                "grades": det_grades,
                "errors": [f"iter {r.iteration}: {r.error}" for r in det_errors],
                "mean_elapsed_seconds": round(
                    statistics.mean([r.elapsed_seconds for r in det_runs]), 1
                ) if det_runs else 0,
                # Include per-criterion verdicts from the first successful run
                # so divergences are debuggable without re-running.
                "sample_verdicts": det_runs[0].per_criterion if det_runs else [],
                "sample_justification": det_runs[0].justification if det_runs else "",
            },
            "legacy": {
                "stats": _stats(leg_grades),
                "grades": leg_grades,
                "errors": [f"iter {r.iteration}: {r.error}" for r in leg_errors],
                "mean_elapsed_seconds": round(
                    statistics.mean([r.elapsed_seconds for r in leg_runs]), 1
                ) if leg_runs else 0,
                "sample_verdicts": leg_runs[0].per_criterion if leg_runs else [],
                "sample_justification": leg_runs[0].justification if leg_runs else "",
            },
        }

        # Delta between modes
        if det_grades and leg_grades:
            item["delta_mean"] = round(
                statistics.mean(det_grades) - statistics.mean(leg_grades), 2
            )
        report["items"].append(item)

    return report


def write_markdown_report(report: dict[str, Any], output_path: Path) -> None:
    """Write a human-readable Markdown summary."""
    lines: list[str] = [
        "# Variance Harness Report",
        "",
        f"Mode: {report['mode_label']}",
        "",
        "## Per-JD Summary",
        "",
        "| Slug | Known | Det mean ± stdev | Leg mean ± stdev | Δ mean | Det range | Leg range | Det errs | Leg errs | Det time | Leg time |",
        "|------|-------|-------------------|------------------|--------|-----------|-----------|----------|----------|----------|----------|",
    ]

    for item in report["items"]:
        d = item["deterministic"]
        l = item["legacy"]
        known = item.get("known_grade")
        known_str = f"{known:.1f}" if known is not None else "?"
        det_str = f"{d['stats']['mean']} ± {d['stats']['stdev']}" if d["stats"]["n"] else "—"
        leg_str = f"{l['stats']['mean']} ± {l['stats']['stdev']}" if l["stats"]["n"] else "—"
        delta = item.get("delta_mean", "—")
        det_range = f"{d['stats']['min']}–{d['stats']['max']}" if d["stats"]["n"] else "—"
        leg_range = f"{l['stats']['min']}–{l['stats']['max']}" if l["stats"]["n"] else "—"
        lines.append(
            f"| {item['slug']} | {known_str} | {det_str} | {leg_str} | {delta} | "
            f"{det_range} | {leg_range} | {len(d['errors'])} | {len(l['errors'])} | "
            f"{d['mean_elapsed_seconds']}s | {l['mean_elapsed_seconds']}s |"
        )

    lines.extend(["", "## Detailed Grades", ""])
    for item in report["items"]:
        lines.append(f"### {item['slug']} (known: {item.get('known_grade', '?')})")
        d = item["deterministic"]
        l = item["legacy"]
        if d["grades"]:
            lines.append(f"- Deterministic: {d['grades']}")
        if l["grades"]:
            lines.append(f"- Legacy:        {l['grades']}")
        if d["errors"]:
            lines.append(f"- Det errors: {d['errors']}")
        if l["errors"]:
            lines.append(f"- Leg errors: {l['errors']}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


# ─── Main ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="A/B compare deterministic vs legacy LLM grading."
    )
    parser.add_argument("--slugs", type=str, default=None,
                        help="Comma-separated JD slugs to grade (overrides --sample)")
    parser.add_argument("--sample", type=int, default=8,
                        help="Auto-pick N JDs across grade bands (default: 8)")
    parser.add_argument("--runs", type=int, default=5,
                        help="Runs per mode per JD (default: 5)")
    parser.add_argument("--mode", choices=["jd", "resume", "both"], default="jd",
                        help="What to grade (default: jd)")
    parser.add_argument("--output-dir", type=str, default=".",
                        help="Where to write reports (default: cwd)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use FakeLLM (no real calls — for testing the harness)")
    parser.add_argument("--hunter-dir", type=str, default=None,
                        help="Hunter dir (default: auto-detect from this script)")
    args = parser.parse_args(argv)

    # Locate the hunter dir
    if args.hunter_dir:
        hunter_dir = Path(args.hunter_dir)
    else:
        # This script is at pipeline/helpers/variance_harness.py
        hunter_dir = Path(__file__).resolve().parent.parent.parent

    # Load real config + paths
    config_path = hunter_dir / ".devin" / "pipeline-config.json"
    real_config = load_config(config_path) if config_path.exists() else PipelineConfig()
    real_paths = Paths.from_hunter_dir(
        hunter_dir,
        base_resume_filename=real_config.profile.base_resume_file,
        linkedin_experience_filename=real_config.profile.linkedin_experience_file,
    )

    # Logger — quiet the structlog noise from step nodes so harness output is clean
    logging.basicConfig(level=logging.WARNING)
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    stdlib_log = logging.getLogger("variance_harness")
    stdlib_log.handlers.clear()
    stdlib_log.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    stdlib_log.addHandler(handler)
    logger = structlog.get_logger("variance_harness")

    # Base config — no retries, long timeout. We want raw variance, not
    # retry recovery. In dry-run, FakeLLM ignores timeouts/retries anyway.
    base_config = real_config.model_copy(update={
        "dry_run": False,
        "llm_retries": 0,
        "llm_retry_delay": 0,
        "llm_timeout_seconds": 600,
    })

    # LLM
    if args.dry_run:
        llm = FakeLLM()
        _wire_fake_llm(llm)
    else:
        llm = RealLLM(db_path=str(real_paths.jobs_db))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[RunResult] = []
    known_grades: dict[str, float] = {}

    tmp_root = Path(tempfile.mkdtemp(prefix="variance-harness-"))
    try:
        # ─── JD grading ────────────────────────────────────────────────
        if args.mode in ("jd", "both"):
            if args.slugs:
                slug_list = [s.strip() for s in args.slugs.split(",")]
                jd_targets: list[tuple[str, float, Path]] = []
                for slug in slug_list:
                    job_dir = real_paths.listings / slug
                    if not job_dir.exists():
                        print(f"  SKIP: {slug} — not found in {real_paths.listings}")
                        continue
                    jds = sorted(job_dir.glob("*job-description.md"))
                    if not jds:
                        print(f"  SKIP: {slug} — no JD file")
                        continue
                    grade = _extract_grade_from_filename(jds[0].name) or 0.0
                    jd_targets.append((slug, grade, jds[0]))
            else:
                jd_targets = pick_jds_across_bands(real_paths.listings, args.sample)

            if not jd_targets:
                print("No JDs found to grade.")
                return 1

            print(f"\n=== JD Grading: {len(jd_targets)} JDs × {args.runs} runs × 2 modes ===\n")
            for slug, known_grade, jd_file in jd_targets:
                known_grades[slug] = known_grade
                jd_text = _read_jd_text(jd_file)
                print(f"  {slug} (known: {known_grade:.1f})")

                for mode in ("deterministic", "legacy"):
                    for i in range(args.runs):
                        print(f"    {mode} run {i + 1}/{args.runs}...", end="", flush=True)
                        r = run_jd_grading(
                            slug, jd_text, known_grade, mode, i,
                            real_paths, base_config, llm, logger, tmp_root,
                        )
                        all_results.append(r)
                        if r.error:
                            print(f" ERROR: {r.error[:80]}")
                        else:
                            print(f" grade={r.grade:.1f} ({r.elapsed_seconds:.0f}s)")

        # ─── Resume grading ────────────────────────────────────────────
        if args.mode in ("resume", "both"):
            resume_targets = pick_resumes_for_grading(real_paths.drafts, args.sample)
            if resume_targets:
                print(f"\n=== Resume Grading: {len(resume_targets)} resumes × {args.runs} runs × 2 modes ===\n")
                for slug, resume_file, jd_file in resume_targets:
                    resume_text = resume_file.read_text(encoding="utf-8")
                    jd_text = _read_jd_text(jd_file)
                    # Try to get known grade from resume filename
                    known = _extract_grade_from_filename(resume_file.name)
                    if known is not None:
                        known_grades[slug] = known
                    print(f"  {slug} (known: {known_grades.get(slug, '?')})")

                    for mode in ("deterministic", "legacy"):
                        for i in range(args.runs):
                            print(f"    {mode} run {i + 1}/{args.runs}...", end="", flush=True)
                            r = run_resume_grading(
                                slug, resume_text, jd_text, mode, i,
                                real_paths, base_config, llm, logger, tmp_root,
                            )
                            all_results.append(r)
                            if r.error:
                                print(f" ERROR: {r.error[:80]}")
                            else:
                                print(f" grade={r.grade:.1f} ({r.elapsed_seconds:.0f}s)")
            elif args.mode == "resume":
                print("No customized resumes found in stages/2_drafts/.")

        # ─── Reports ───────────────────────────────────────────────────
        mode_label = f"{args.runs} runs per mode"
        report = build_report(all_results, known_grades, mode_label)

        json_path = output_dir / "variance-report.json"
        json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

        md_path = output_dir / "variance-report.md"
        write_markdown_report(report, md_path)

        print(f"\nReports written to:")
        print(f"  {json_path}")
        print(f"  {md_path}")

        # Quick summary to stdout
        print(f"\n=== Summary ===")
        for item in report["items"]:
            d = item["deterministic"]["stats"]
            l = item["legacy"]["stats"]
            known = item.get("known_grade", "?")
            delta = item.get("delta_mean", "—")
            print(f"  {item['slug']}: known={known}  "
                  f"det={d['mean']}±{d['stdev']}  leg={l['mean']}±{l['stdev']}  Δ={delta}")

        return 0
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _wire_fake_llm(llm: FakeLLM) -> None:
    """Wire FakeLLM with grading JSON for dry-run testing.

    FakeLLM matches by prompt substring (first match wins, insertion order).
    The scoring prompt contains 'Compute the score' (unique to Call 2);
    the reasoning prompt contains the reasoning protocol text. We key the
    scoring response on 'Compute the score' and the reasoning response on
    a broad substring that appears in any reasoning prompt.
    """
    reasoning_response = json.dumps({
        "per_criterion": [
            {"requirement": "10+ years experience", "tier": "core", "assessment": "DIRECT_HIT", "comment": "yes"},
            {"requirement": "Leadership experience", "tier": "core", "assessment": "DIRECT_HIT", "comment": "yes"},
            {"requirement": "Distributed systems", "tier": "core", "assessment": "ADDRESSED", "comment": "partial"},
            {"requirement": "Kubernetes", "tier": "preferred", "assessment": "GAP", "comment": "no"},
        ],
        "summary": "Strong candidate with relevant experience.",
        "judgment_calls": [],
    })
    scoring_response = json.dumps({
        "grade": 7.5,
        "justification": "Legacy scoring call result.",
        "per_criterion": [
            {"requirement": "10+ years experience", "tier": "core", "assessment": "DIRECT_HIT", "comment": "yes"},
            {"requirement": "Leadership experience", "tier": "core", "assessment": "DIRECT_HIT", "comment": "yes"},
            {"requirement": "Distributed systems", "tier": "core", "assessment": "ADDRESSED", "comment": "partial"},
            {"requirement": "Kubernetes", "tier": "preferred", "assessment": "GAP", "comment": "no"},
        ],
    })
    # FakeLLM matches by prompt substring (first match wins, insertion order).
    # "Scoring Protocol" appears only in the scoring prompt (Call 2);
    # "Reasoning Protocol" appears only in the reasoning prompt (Call 1).
    # Scoring key is checked first so legacy mode's Call 2 matches correctly.
    llm.add_response("Scoring Protocol", scoring_response)
    llm.add_response("Reasoning Protocol", reasoning_response)


if __name__ == "__main__":
    sys.exit(main())
