"""Step 11 + terminal nodes: finalize and move to ready/rejected/trash.

This module contains:
- step11_finalize_node: routes on the winner confirmed by
  step10_final_veracity (ADR-0018) and prunes non-selected resume files so
  only the winning version ships to ready/
- step11_ready_node: moves drafts/<slug>/ to ready/ when a version was selected
- trash_node: moves listings/<slug>/ to trash/ (clearance or grade < 6)
- rejected_job_fit_node: moves listings/<slug>/ to rejected/[JOB-FIT] (grade 6-7.9)
- rejected_resume_node: moves drafts/<slug>/ to rejected/[RESUME] (no passing version)

All are LangGraph nodes. They set final_destination on state. The
conditional edges route to these nodes based on grade/verification.
After each move, they record a state_transitions audit row (E2, ADR-0012).
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.file_ops import move_dir, parse_version_from_filename
from pipeline.infrastructure.grades_log import append_decision_log
from pipeline.infrastructure.llm_interface import get_deps
from pipeline.infrastructure.state import JobState, TriageDestination
from pipeline.infrastructure.state_store import JobStatus, StateStore


def _record(deps, slug: str, from_status: JobStatus, to_status: JobStatus,
            reason: str, grade: float | None = None,
            metadata: dict | None = None) -> None:
    """Record a state transition after a successful move (E2, ADR-0012).

    Failure-isolated — never raises. Constructed lazily so dry_run and
    missing-folder paths don't touch the DB.
    """
    store = StateStore(str(deps.paths.jobs_db))
    store.record_transition(
        slug, from_status, to_status, reason=reason,
        grade=grade, metadata=metadata,
    )
    store.close()


def _prune_non_selected(deps, slug: str, selected: int) -> None:
    """Remove resume files in drafts/<slug>/ whose version != selected.

    Only the winning version ships to ready/ — the JD and earlier grade
    prefixes keep the audit trail readable. Missing/renamed files are
    tolerated. Runs only after the final truthfulness gate confirms the
    winner: pruning earlier would destroy the fallback candidates the
    final-gate cascade needs.
    """
    job_dir = deps.paths.drafts / slug
    if not job_dir.exists():
        return
    for resume_file in job_dir.glob("*resume-v*.md"):
        vnum = parse_version_from_filename(resume_file.name)
        if vnum is not None and vnum != selected:
            deps.logger.debug(
                f"  {slug}: pruning non-selected {resume_file.name}"
            )
            resume_file.unlink()


def step11_finalize_node(state: JobState, config: RunnableConfig) -> dict:
    """Route on the winner confirmed by the final truthfulness gate (ADR-0018).

    step10_final_veracity sets selected_version only after the selected
    version PASSES the final review. This node executes the outcome:

        confirmed winner -> prune non-selected versions -> step11_ready
        no winner        -> rejected_resume (or human review on failure)

    Never ships an unverified resume: a non-None selected_version without a
    passing final_verification is an inconsistent state (legacy checkpoint,
    --step misuse) and is held for human review rather than routed to ready.

    When no version wins AND at least one version failed truthfulness —
    in-loop (verified=False) or at the final gate (final_failed_versions) —
    raises instead of rejecting: that's a signal worth human review, not a
    silent abandonment. Versions that skipped review due to a below-threshold
    grade (verified=None) do not trigger this.
    """
    deps = get_deps(config)
    slug = state.slug

    if state.selected_version is not None:
        verification = state.final_verification
        if verification is None or not verification.verified:
            reason = (
                f"v{state.selected_version} selected without a passing "
                f"final truthfulness review — inconsistent state, "
                f"held for human review"
            )
            if not deps.config.dry_run:
                _record(deps, slug, JobStatus.DRAFTS, JobStatus.HUMAN_REVIEW,
                        reason)
                append_decision_log(slug, "human_review", reason,
                                    deps.paths.grades_log, deps.logger)
            raise RuntimeError(f"{slug}: {reason}")
        if not deps.config.dry_run:
            _prune_non_selected(deps, slug, state.selected_version)
        deps.logger.info(
            f"  {slug}: v{state.selected_version} passed final gate -> ready"
        )
        return {}

    versions = state.evaluated_versions
    failed_versions = [v.version for v in versions if v.verified is False]
    all_failed = sorted(set(failed_versions) | set(state.final_failed_versions))
    if all_failed:
        reason = (
            f"no passing version and truthfulness failed for "
            f"version(s) {all_failed} — held for human review"
        )
        if not deps.config.dry_run:
            _record(
                deps, slug, JobStatus.DRAFTS, JobStatus.HUMAN_REVIEW,
                reason,
                metadata={
                    "versions": [
                        {
                            "version": v.version,
                            "grade": v.grade,
                            "verified": v.verified,
                            "truthfulness_issues": v.truthfulness_issues,
                        }
                        for v in versions
                    ],
                    "final_failed_versions": state.final_failed_versions,
                },
            )
            append_decision_log(slug, "human_review", reason,
                                deps.paths.grades_log, deps.logger)
        raise RuntimeError(f"{slug}: {reason}")

    deps.logger.info(f"  {slug}: no passing version -> rejected/[RESUME]")
    return {}


def step11_ready_node(state: JobState, config: RunnableConfig) -> dict:
    """Move the job folder from drafts/ to ready/ (grade >= 9 AND verified).

    Returns partial state with final_destination = READY.
    In dry_run, skips the move.
    """
    deps = get_deps(config)
    slug = state.slug

    if not deps.config.dry_run:
        src = deps.paths.drafts / slug
        if src.exists():
            move_dir(src, deps.paths.ready)
            sel = state.selected_version
            sel_grade = next(
                (v.grade for v in state.version_history
                 if v.version == sel), None,
            )
            reason = (
                f"v{sel} passed final truthfulness gate "
                f"(grade {sel_grade}) — moved to ready"
            )
            _record(
                deps, slug, JobStatus.DRAFTS, JobStatus.READY, reason,
                grade=sel_grade,
                metadata={
                    "selected_version": sel,
                    "final_verification": (
                        state.final_verification.model_dump()
                        if state.final_verification else None
                    ),
                },
            )
            append_decision_log(slug, "ready", reason,
                                deps.paths.grades_log, deps.logger)

    deps.logger.info(f"  {slug}: -> ready/")
    return {"final_destination": TriageDestination.READY}


# ─── Terminal nodes ──────────────────────────────────────────────────────────


def trash_node(state: JobState, config: RunnableConfig) -> dict:
    """Move listings/<slug>/ to trash/ (clearance or grade < 6).

    Returns partial state with final_destination = TRASH.
    In dry_run, skips the move.
    """
    deps = get_deps(config)
    slug = state.slug

    if not deps.config.dry_run:
        src = deps.paths.listings / slug
        if src.exists():
            move_dir(src, deps.paths.trash)
            g = state.jd_grade
            if g and g.is_clearance:
                reason = "security clearance required (LLM-detected during JD grading)"
            elif g:
                reason = f"JD grade {g.grade} < 6 (trash threshold)"
            else:
                reason = "no JD grade produced"
            _record(
                deps, slug, JobStatus.LISTINGS, JobStatus.TRASH, reason,
                grade=g.grade if g else None,
                metadata={
                    "justification": g.justification if g else "",
                    "per_criterion": [
                        c.model_dump() for c in g.per_criterion
                    ] if g else [],
                },
            )
            append_decision_log(slug, "trash", reason,
                                deps.paths.grades_log, deps.logger)

    deps.logger.info(f"  {slug}: -> trash/")
    return {"final_destination": TriageDestination.TRASH}


def rejected_job_fit_node(state: JobState, config: RunnableConfig) -> dict:
    """Move listings/<slug>/ to rejected/[JOB-FIT] <slug>/ (grade 6-7.9).

    Returns partial state with final_destination = REJECTED_JOB_FIT.
    In dry_run, skips the move.
    """
    deps = get_deps(config)
    slug = state.slug

    if not deps.config.dry_run:
        src = deps.paths.listings / slug
        if src.exists():
            move_dir(src, deps.paths.rejected, prefix="[JOB-FIT]")
            g = state.jd_grade
            threshold = deps.config.jd_grade_threshold
            if g:
                reason = (
                    f"JD grade {g.grade} below {threshold} proceed "
                    f"threshold — not worth customizing"
                )
            else:
                reason = "no JD grade produced"
            _record(
                deps, slug, JobStatus.LISTINGS, JobStatus.REJECTED_JOB_FIT,
                reason, grade=g.grade if g else None,
                metadata={
                    "jd_threshold": threshold,
                    "justification": g.justification if g else "",
                    "per_criterion": [
                        c.model_dump() for c in g.per_criterion
                    ] if g else [],
                },
            )
            append_decision_log(slug, "rejected_job_fit", reason,
                                deps.paths.grades_log, deps.logger)

    deps.logger.info(f"  {slug}: -> rejected/[JOB-FIT]")
    return {"final_destination": TriageDestination.REJECTED_JOB_FIT}


def rejected_resume_node(state: JobState, config: RunnableConfig) -> dict:
    """Move drafts/<slug>/ to rejected/[RESUME] <slug>/ (grade < 9 or unverified).

    Returns partial state with final_destination = REJECTED_RESUME.
    In dry_run, skips the move.
    """
    deps = get_deps(config)
    slug = state.slug

    if not deps.config.dry_run:
        src = deps.paths.drafts / slug
        if src.exists():
            move_dir(src, deps.paths.rejected, prefix="[RESUME]")
            versions = state.evaluated_versions
            graded = [v for v in versions if v.grade is not None]
            best = max(graded, key=lambda v: v.grade) if graded else None
            threshold = deps.config.resume_grade_threshold
            if best:
                reason = (
                    f"best resume grade {best.grade} (v{best.version}) "
                    f"below {threshold} passing threshold after "
                    f"{len(versions)} version(s)"
                )
            else:
                reason = "no graded resume version reached the passing threshold"
            _record(
                deps, slug, JobStatus.DRAFTS, JobStatus.REJECTED_RESUME,
                reason, grade=best.grade if best else None,
                metadata={
                    "resume_threshold": threshold,
                    "versions": [
                        {
                            "version": v.version,
                            "grade": v.grade,
                            "verified": v.verified,
                            "truthfulness_issues": v.truthfulness_issues,
                        }
                        for v in versions
                    ],
                    "best_version": best.version if best else None,
                    "best_justification": best.justification if best else "",
                    "gaps": [
                        {"requirement": c.requirement, "assessment": c.assessment,
                         "comment": c.comment}
                        for c in (best.per_criterion if best else [])
                        if c.assessment in ("GAP", "PARTIAL")
                    ],
                },
            )
            append_decision_log(slug, "rejected_resume", reason,
                                deps.paths.grades_log, deps.logger)

    deps.logger.info(f"  {slug}: -> rejected/[RESUME]")
    return {"final_destination": TriageDestination.REJECTED_RESUME}
