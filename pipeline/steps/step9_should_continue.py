"""Step 9 (per-job): Should the customize loop continue?

Pure routing node — no LLM call (ADR-0018). Reads the customizer's YES/NO
signal (customizer_can_improve, set by step6) and the latest veracity result
(verification, set by step8), then sets state.should_continue for the
graph's conditional edge:

- Budget exhausted (max_optimization_iterations versions) → done
- YES → continue (next version)
- NO + latest review FAILED → continue (truthfulness repair is mandatory —
  the truthfulness override; a failed review forces another revision while
  budget remains)
- NO + anything else → done (verified, or the review was skipped because
  the version is below grade — a skip is not a failure to repair)
- No signal (None — dry run, legacy/checkpointed state) → done

The failed/skipped distinction comes from version_history's tri-state
``verified`` field (False = review ran and failed, None = review not run).
``state.verification.verified`` can't express it — both cases set False.
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.llm_interface import get_deps
from pipeline.infrastructure.state import JobState


def step9_should_continue_node(state: JobState, config: RunnableConfig) -> dict:
    """Decide whether the customize-grade-veracity loop continues.

    Returns {"should_continue": True|False}; the conditional edge maps it
    to step6_customize (continue) or step10_final_veracity (done).
    """
    deps = get_deps(config)
    slug = state.slug

    versions_created = len(state.resume_versions)
    max_versions = deps.config.max_optimization_iterations
    can_improve = state.customizer_can_improve

    # Latest veracity outcome as a tri-state: True = verified, False =
    # review ran and failed, None = review not run (below grade, dry run,
    # or never executed). Prefer the version_history record (always
    # appended by step8); fall back to state.verification for legacy
    # states, where False is treated as a failed review.
    if state.version_history:
        review_failed = state.version_history[-1].verified is False
    else:
        review_failed = bool(
            state.verification and state.verification.verified is False
        )

    if versions_created >= max_versions:
        deps.logger.info(
            f"  {slug}: version budget exhausted "
            f"({versions_created}/{max_versions}) -> done"
        )
        return {"should_continue": False}

    if state.customizer_draft_unchanged:
        # A byte-identical duplicate never increments resume_versions, so
        # continuing could loop forever (e.g. NO + failed veracity: the
        # customizer can't repair it but keeps returning the same draft).
        # An unchanged draft means nothing new to evaluate — done. A failed
        # review on the previous version is then handled downstream by
        # step11_finalize (held for human review).
        deps.logger.info(
            f"  {slug}: revision byte-identical -> done"
        )
        return {"should_continue": False}

    if can_improve is True:
        deps.logger.info(f"  {slug}: customizer says YES -> continue")
        return {"should_continue": True}

    if can_improve is False and review_failed:
        deps.logger.info(
            f"  {slug}: customizer says NO but latest version failed "
            f"veracity -> continue (truthfulness override)"
        )
        return {"should_continue": True}

    deps.logger.info(f"  {slug}: loop done (signal={can_improve})")
    return {"should_continue": False}
