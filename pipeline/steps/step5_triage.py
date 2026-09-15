"""Step 5 (per-job): Triage — route to trash / rejected / drafts based on JD grade.

This is a LangGraph node. It moves the listing folder based on the JD grade:
    - grade < 6 → trash/
    - grade 6–7.9 → rejected/[JOB-FIT] <slug>/
    - grade ≥ 8 → drafts/ (proceed to customization)

The conditional edge (route_triage in graph.py) reads state.triage to
determine the next node. This node sets state.triage and performs the
filesystem move. After each move, it records a state_transitions audit
row (E2, ADR-0012).
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.grades_log import append_decision_log
from pipeline.infrastructure.llm_interface import get_deps
from pipeline.infrastructure.state import JobState, TriageDestination
from pipeline.infrastructure.state_store import JobStatus, StateStore
from pipeline.infrastructure.file_ops import move_dir


# Map TriageDestination → JobStatus for the audit log.
_TRIAGE_TO_STATUS = {
    TriageDestination.TRASH: JobStatus.TRASH,
    TriageDestination.REJECTED_JOB_FIT: JobStatus.REJECTED_JOB_FIT,
    TriageDestination.DRAFTS: JobStatus.DRAFTS,
    TriageDestination.READY: JobStatus.READY,
    TriageDestination.REJECTED_RESUME: JobStatus.REJECTED_RESUME,
}


def step5_triage_node(state: JobState, config: RunnableConfig) -> dict:
    """Triage the JD based on its grade. Move the folder and set triage.

    Returns partial state with triage set. The conditional edge reads
    this to route to the correct terminal node or to step6_customize.
    """
    deps = get_deps(config)
    slug = state.slug

    if not state.jd_grade or state.jd_grade.is_clearance:
        # Clearance is handled by the conditional edge routing to trash,
        # but if we somehow get here, route to trash.
        deps.logger.info(f"  {slug}: no grade or clearance → trash")
        triage = TriageDestination.TRASH
    else:
        grade = state.jd_grade.grade
        threshold = deps.config.jd_grade_threshold

        if grade < 6:
            triage = TriageDestination.TRASH
            deps.logger.info(f"  {slug}: grade {grade} < 6 → trash")
        elif grade < threshold:
            triage = TriageDestination.REJECTED_JOB_FIT
            deps.logger.info(
                f"  {slug}: grade {grade} < {threshold} → rejected/[JOB-FIT]"
            )
        else:
            triage = TriageDestination.DRAFTS
            deps.logger.info(
                f"  {slug}: grade {grade} ≥ {threshold} → drafts"
            )

    # Perform the filesystem move (unless dry_run)
    if not deps.config.dry_run:
        src = deps.paths.listings / slug
        if src.exists():
            if triage == TriageDestination.TRASH:
                move_dir(src, deps.paths.trash)
            elif triage == TriageDestination.REJECTED_JOB_FIT:
                move_dir(src, deps.paths.rejected, prefix="[JOB-FIT]")
            elif triage == TriageDestination.DRAFTS:
                move_dir(src, deps.paths.drafts)

            # Record state transition for audit (E2, ADR-0012), with the
            # full why: threshold that fired + the grader's justification
            # and per-criterion detail in metadata for later debugging.
            # Constructed lazily so dry_run / missing-folder paths don't
            # touch the DB. Failure-isolated — never raises.
            grade_val = state.jd_grade.grade if state.jd_grade else None
            justification = state.jd_grade.justification if state.jd_grade else ""
            threshold = deps.config.jd_grade_threshold
            if state.jd_grade and state.jd_grade.is_clearance:
                reason = "security clearance required (LLM-detected during JD grading)"
            elif grade_val is None:
                reason = "no JD grade produced"
            elif grade_val < 6:
                reason = f"JD grade {grade_val} < 6 (trash threshold)"
            elif grade_val < threshold:
                reason = f"JD grade {grade_val} < {threshold} (proceed threshold)"
            else:
                reason = f"JD grade {grade_val} >= {threshold} — proceeding to customization"
            metadata = {
                "jd_grade": grade_val,
                "jd_threshold": threshold,
                "justification": justification,
                "per_criterion": [
                    c.model_dump() for c in state.jd_grade.per_criterion
                ] if state.jd_grade else [],
            }
            to_status = _TRIAGE_TO_STATUS[triage]
            store = StateStore(str(deps.paths.jobs_db))
            store.record_transition(
                slug,
                JobStatus.LISTINGS,
                to_status,
                reason=reason,
                grade=grade_val,
                metadata=metadata,
            )
            store.close()

            # Same decision lands in .grades.log for human review.
            append_decision_log(
                slug, f"triage->{triage.value}", reason,
                deps.paths.grades_log, deps.logger,
            )

    return {"triage": triage}
