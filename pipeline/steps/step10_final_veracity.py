"""Step 10 (per-job): Final truthfulness review — the post-loop gate (ADR-0018).

Runs AFTER the customize-grade-veracity loop exits, as its own stage. The
in-loop review (step8) verifies each version to steer the loop; this node
re-verifies the version actually selected to ship — an independent,
authoritative gate before anything reaches ready/.

Selection + fallback cascade:
  1. Gather evaluated versions passing both gates (grade >= threshold AND
     verified is True), best first.
  2. Re-run the full two-call verification on the best candidate — always,
     even though it passed in-loop (verification is non-deterministic; the
     final check exists to catch a wrong call before shipping).
  3. If it fails, try the next-best candidate; repeat until one passes.
  4. If every candidate fails, no winner is selected — step11_finalize then
     holds the job for human review (a truthfulness failure is never a
     silent rejection).

sets selected_version ONLY on a confirmed pass — downstream, a non-None
selected_version means "verified winner", and step11_finalize additionally
requires final_verification.verified before shipping (never ship unverified).
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.llm_interface import get_deps
from pipeline.infrastructure.state import JobState, passing_versions
from pipeline.steps.step8_veracity import (
    find_resume_version_file,
    verify_resume_file,
)


def step10_final_veracity_node(state: JobState, config: RunnableConfig) -> dict:
    """Select the best passing version and run the final truthfulness gate.

    Returns partial state:
      - selected_version: the confirmed winner's version, or None
      - final_verification: the last Verification produced (winner's on
        success, last attempt's on total failure), or None if no check ran
      - final_failed_versions: cumulative list of versions that failed the
        final gate (or whose file was missing)

    No LLM calls when there are no candidates or in dry_run — both return
    selected_version=None and step11_finalize routes accordingly.
    """
    deps = get_deps(config)
    slug = state.slug
    threshold = deps.config.resume_grade_threshold

    failed = list(state.final_failed_versions)
    attempted = set(failed)
    candidates = [
        v
        for v in passing_versions(state.evaluated_versions, threshold)
        if v.version not in attempted
    ]
    latest_num = (
        max(v.version for v in state.evaluated_versions)
        if state.evaluated_versions
        else None
    )

    if not candidates:
        deps.logger.info(
            f"  {slug}: no passing candidate for final truthfulness review"
        )
        return {"selected_version": None, "final_failed_versions": failed}

    if deps.config.dry_run:
        deps.logger.info(f"  {slug}: dry-run final truthfulness (skipped)")
        return {"selected_version": None, "final_failed_versions": failed}

    last_verification = None
    for candidate in candidates:
        if candidate.version in attempted:
            continue  # duplicate history record for an already-checked version
        attempted.add(candidate.version)

        resume_path = find_resume_version_file(slug, candidate.version, deps.paths)
        if resume_path is None:
            resume_path = candidate.path
        if not resume_path.exists():
            deps.logger.warning(
                f"  {slug}: v{candidate.version} file missing "
                f"({resume_path.name}) — excluded from final selection"
            )
            failed.append(candidate.version)
            continue

        verification = verify_resume_file(
            deps,
            slug,
            resume_path,
            step_label="final-truthfulness",
            file_prefix=f"final-v{candidate.version}-",
        )
        last_verification = verification

        if verification.verified:
            if candidate.version != latest_num:
                deps.logger.warning(
                    f"  {slug}: selected earlier v{candidate.version} "
                    f"(grade {candidate.grade}) over latest v{latest_num} — "
                    f"revision regressed, review in audit trail"
                )
            deps.logger.info(
                f"  {slug}: final truthfulness VERIFIED — selected "
                f"v{candidate.version} (grade {candidate.grade})"
            )
            return {
                "selected_version": candidate.version,
                "final_verification": verification,
                "final_failed_versions": failed,
            }

        failed.append(candidate.version)
        deps.logger.warning(
            f"  {slug}: v{candidate.version} FAILED final truthfulness "
            f"(passed in-loop) — trying next candidate"
        )

    deps.logger.info(
        f"  {slug}: all {len(attempted - set(state.final_failed_versions))} "
        f"candidate(s) failed final truthfulness"
    )
    return {
        "selected_version": None,
        "final_verification": last_verification,
        "final_failed_versions": failed,
    }
