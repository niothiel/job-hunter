"""LangGraph builder — the per-job pipeline graph.

Wires all nodes (step3_ingest through step11 + terminal nodes) with
linear and conditional edges. The customize-grade-veracity loop
(step6 -> step7 -> step8 -> step9 -> back to step6 or forward to step10)
is a real graph cycle (ADR-0018).

Graph topology:

    START -> step3_ingest -> step4_grade_jd
                                |
                                +--[clearance] -> trash -> END
                                +--[graded] -> step5_triage
                                                |
                                                +--[trash] -> trash -> END
                                                +--[rejected_job_fit] -> rejected_job_fit -> END
                                                +--[drafts] -> step6_customize
                                                                |
                                                                +--[evaluate] -> step7_grade_resume
                                                                |                   v
                                                                |             step8_veracity
                                                                |                   v
                                                                +--[skip_eval] -> step9_should_continue
                                                                                    |
                                                                                    +--[continue] -> step6_customize (cycle)
                                                                                    +--[done] -> step10_final_veracity
                                                                                                    |
                                                                                                    v
                                                                                              step11_finalize
                                                                                                    |
                                                                                                    +--[ready] -> step11_ready -> END
                                                                                                    +--[rejected] -> rejected_resume -> END

step6 skips grading/veracity (skip_eval) when a revision left the draft
byte-identical — a valid early stop per the YES/NO outcome contract.

step10_final_veracity is the post-loop truthfulness gate: it selects the
best version passing both in-loop gates and re-verifies it independently,
cascading to the next-best candidate on failure (ADR-0018). step11_finalize
then prunes non-selected files and routes to ready/ or rejection.

The checkpointer (SqliteSaver) provides per-job resumability — an
interrupted hourly run resumes from the last checkpoint, keyed by
thread_id = slug.
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from pipeline.infrastructure.state import JobState, TriageDestination
from pipeline.steps.step10_final_veracity import step10_final_veracity_node
from pipeline.steps.step11_finalize import (
    rejected_job_fit_node,
    rejected_resume_node,
    step11_finalize_node,
    step11_ready_node,
    trash_node,
)
from pipeline.steps.step3_ingest import step3_ingest_node
from pipeline.steps.step4_grade_jd import step4_grade_jd_node
from pipeline.steps.step5_triage import step5_triage_node
from pipeline.steps.step6_customize import step6_customize_node
from pipeline.steps.step7_grade_resume import step7_grade_resume_node
from pipeline.steps.step8_veracity import step8_veracity_node
from pipeline.steps.step9_should_continue import step9_should_continue_node


# ─── Routing functions ──────────────────────────────────────────────────────


def route_after_jd_grade(state: JobState) -> str:
    """Route after JD grading: clearance -> trash, else -> triage."""
    if state.jd_grade and state.jd_grade.is_clearance:
        return "clearance"
    return "graded"


def route_triage(state: JobState) -> str:
    """Route after triage: read state.triage (set by step5_triage_node)."""
    if state.triage is None:
        return "trash"
    return state.triage.value


def route_after_customize(state: JobState) -> str:
    """Route after customize: evaluate the new version, or skip when the
    revision left the draft byte-identical (valid early stop, ADR-0018).

    Reads state.customizer_draft_unchanged (set by step6_customize_node).
    """
    if state.customizer_draft_unchanged:
        return "skip_evaluation"
    return "evaluate"


def route_should_continue(state: JobState) -> str:
    """Route after the should-continue decision: continue (cycle) or done.

    Reads state.should_continue (set by step9_should_continue_node).
    True  -> continue (back to step6_customize)
    False -> done (forward to step10_final_veracity)
    """
    if state.should_continue is True:
        return "continue"
    return "done"


def route_after_finalize(state: JobState) -> str:
    """Route after finalization: a version was selected -> ready, else
    -> rejected_resume.

    Reads state.selected_version (set by step10_final_veracity_node only
    after the selected version passed the final truthfulness gate).
    """
    if state.selected_version is not None:
        return "ready"
    return "rejected"


# ─── Graph builder ──────────────────────────────────────────────────────────


def build_job_graph(checkpointer: Any = None):
    """Build and compile the per-job LangGraph pipeline.

    Args:
        checkpointer: A LangGraph checkpointer (e.g. SqliteSaver) for
            per-job resumability. Pass None for tests (no checkpointing).

    Returns:
        A compiled StateGraph ready to invoke with:
            graph.invoke(initial_state, config={"configurable": {...},
                                                "thread_id": slug})
    """
    graph = StateGraph(JobState)

    # ── Add nodes ──
    graph.add_node("step3_ingest", step3_ingest_node)
    graph.add_node("step4_grade_jd", step4_grade_jd_node)
    graph.add_node("step5_triage", step5_triage_node)
    graph.add_node("step6_customize", step6_customize_node)
    graph.add_node("step7_grade_resume", step7_grade_resume_node)
    graph.add_node("step8_veracity", step8_veracity_node)
    graph.add_node("step9_should_continue", step9_should_continue_node)
    graph.add_node("step10_final_veracity", step10_final_veracity_node)
    graph.add_node("step11_finalize", step11_finalize_node)
    graph.add_node("step11_ready", step11_ready_node)
    graph.add_node("trash", trash_node)
    graph.add_node("rejected_job_fit", rejected_job_fit_node)
    graph.add_node("rejected_resume", rejected_resume_node)

    # ── Linear edges ──
    graph.add_edge(START, "step3_ingest")
    graph.add_edge("step3_ingest", "step4_grade_jd")
    graph.add_edge("step7_grade_resume", "step8_veracity")
    graph.add_edge("step8_veracity", "step9_should_continue")
    graph.add_edge("step10_final_veracity", "step11_finalize")
    graph.add_edge("step11_ready", END)
    graph.add_edge("trash", END)
    graph.add_edge("rejected_job_fit", END)
    graph.add_edge("rejected_resume", END)

    # ── Conditional edges ──
    graph.add_conditional_edges(
        "step4_grade_jd",
        route_after_jd_grade,
        {
            "clearance": "trash",
            "graded": "step5_triage",
        },
    )
    graph.add_conditional_edges(
        "step5_triage",
        route_triage,
        {
            "trash": "trash",
            "rejected_job_fit": "rejected_job_fit",
            "drafts": "step6_customize",
        },
    )
    graph.add_conditional_edges(
        "step6_customize",
        route_after_customize,
        {
            "evaluate": "step7_grade_resume",
            "skip_evaluation": "step9_should_continue",
        },
    )
    graph.add_conditional_edges(
        "step9_should_continue",
        route_should_continue,
        {
            "continue": "step6_customize",
            "done": "step10_final_veracity",
        },
    )
    graph.add_conditional_edges(
        "step11_finalize",
        route_after_finalize,
        {
            "ready": "step11_ready",
            "rejected": "rejected_resume",
        },
    )

    return graph.compile(checkpointer=checkpointer)
