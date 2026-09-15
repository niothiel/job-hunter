"""Tests for graph.py — routing functions and full graph topology.

Tests the routing functions in isolation, then exercises the full
compiled graph with FakeLLM to verify edges and conditional routing work
end-to-end.
"""
import json
import re
from pathlib import Path

import pytest
from langgraph.graph import START, END, StateGraph

from pipeline.infrastructure.graph import (
    build_job_graph,
    route_after_customize,
    route_after_finalize,
    route_after_jd_grade,
    route_should_continue,
    route_triage,
)
from pipeline.infrastructure.state import (
    Assessment,
    Criterion,
    JdGrade,
    JobState,
    ResumeGrade,
    ResumeVersion,
    TriageDestination,
    Verification,
)

from pipeline.infrastructure.config import DEFAULT_LLM_MODEL


# ─── Routing function tests ──────────────────────────────────────────────────


def test_route_after_jd_grade_clearance():
    state = JobState(slug="co", jd_grade=JdGrade(grade=0, is_clearance=True))
    assert route_after_jd_grade(state) == "clearance"


def test_route_after_jd_grade_normal():
    state = JobState(slug="co", jd_grade=JdGrade(grade=8.5))
    assert route_after_jd_grade(state) == "graded"


def test_route_after_jd_grade_no_grade():
    state = JobState(slug="co")
    assert route_after_jd_grade(state) == "graded"


def test_route_triage_trash():
    state = JobState(slug="co", triage=TriageDestination.TRASH)
    assert route_triage(state) == "trash"


def test_route_triage_rejected_job_fit():
    state = JobState(slug="co", triage=TriageDestination.REJECTED_JOB_FIT)
    assert route_triage(state) == "rejected_job_fit"


def test_route_triage_drafts():
    state = JobState(slug="co", triage=TriageDestination.DRAFTS)
    assert route_triage(state) == "drafts"


def test_route_triage_none_defaults_trash():
    state = JobState(slug="co")
    assert route_triage(state) == "trash"


def test_route_after_customize_evaluate():
    state = JobState(slug="co", customizer_draft_unchanged=False)
    assert route_after_customize(state) == "evaluate"


def test_route_after_customize_skip_on_identical_draft():
    """Byte-identical revision + NO -> skip grade/veracity for the duplicate."""
    state = JobState(slug="co", customizer_draft_unchanged=True)
    assert route_after_customize(state) == "skip_evaluation"


def test_route_after_customize_default_evaluates():
    state = JobState(slug="co")
    assert route_after_customize(state) == "evaluate"


def test_route_should_continue_continue():
    state = JobState(slug="co", should_continue=True)
    assert route_should_continue(state) == "continue"


def test_route_should_continue_done():
    state = JobState(slug="co", should_continue=False)
    assert route_should_continue(state) == "done"


def test_route_should_continue_none_defaults_done():
    state = JobState(slug="co")
    assert route_should_continue(state) == "done"


def test_route_after_finalize_selected():
    state = JobState(slug="co", selected_version=2)
    assert route_after_finalize(state) == "ready"


def test_route_after_finalize_no_selection():
    state = JobState(slug="co")
    assert route_after_finalize(state) == "rejected"


# ─── Full graph topology tests ───────────────────────────────────────────────
#
# These use FakeLLM with canned responses to exercise the graph end-to-end.
# We use dry_run=True to avoid needing real base resumes / file I/O for
# customize/grade steps, but we DO create the necessary folder structures
# for the terminal nodes to move.


def _make_node_config(tmp_paths, fake_llm, logger, dry_run=True):
    """Build a LangGraph RunnableConfig for full-graph tests."""
    from pipeline.infrastructure.config import PipelineConfig
    config = PipelineConfig(dry_run=dry_run)
    return {
        "configurable": {
            "llm": fake_llm,
            "logger": logger,
            "config": config,
            "paths": tmp_paths,
        }
    }


def _grade_json(grade, assessments=None):
    """Build a grade JSON string for FakeLLM."""
    if assessments is None:
        assessments = [("Python", "core", "DIRECT_HIT", "Strong")]
    per_criterion = [
        {"requirement": req, "tier": tier, "assessment": a, "comment": c}
        for req, tier, a, c in assessments
    ]
    return json.dumps({"grade": grade, "per_criterion": per_criterion, "model": DEFAULT_LLM_MODEL})


def _jd_reasoning_json(assessments=None):
    """Build JD grading reasoning JSON for FakeLLM."""
    if assessments is None:
        assessments = [("Python", "core", "DIRECT_HIT", "Strong")]
    per_criterion = [
        {"requirement": req, "tier": tier, "assessment": a, "comment": c}
        for req, tier, a, c in assessments
    ]
    return json.dumps({"per_criterion": per_criterion})


def _jd_scoring_json(grade, assessments=None, justification="Fit."):
    """Build JD grading scoring JSON for FakeLLM."""
    if assessments is None:
        assessments = [("Python", "core", "DIRECT_HIT", "Strong")]
    per_criterion = [
        {"requirement": req, "tier": tier, "assessment": a, "comment": c}
        for req, tier, a, c in assessments
    ]
    return json.dumps({
        "grade": grade, "ceiling": 10.0, "core_score": grade,
        "preferred_bonus": 0, "quantitative_base": grade,
        "subjective_adjustment": 0,
        "per_criterion": per_criterion,
        "subjective_justification": "No gaps.",
        "justification": justification,
    })


def _create_source_files(tmp_paths):
    """Create minimal base resume + LinkedIn for prompt inlining."""
    tmp_paths.base_resume.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.base_resume.write_text("# Squall Leonhart\n\nSoftware Engineer\n", encoding="utf-8")
    tmp_paths.linkedin_experience.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.linkedin_experience.write_text("# Squall Leonhart — Full Experience\n\n## Experience\n", encoding="utf-8")


def _verification_json(verified=True, claims=None):
    """Build verification JSON. Normalizes string claims to objects."""
    if claims is None:
        claims = []
    normalized = []
    for c in claims:
        if isinstance(c, str):
            normalized.append({"claim": c, "location": "", "bucket": "FABRICATED", "source_checked": "both", "reason": ""})
        else:
            normalized.append(c)
    return json.dumps({"verified": verified, "unverifiable_claims": normalized})


def _reasoning_json(assessments=None):
    """Build a reasoning JSON string for FakeLLM Call 1."""
    if assessments is None:
        assessments = [("Python", "core", "DIRECT_HIT", "Strong")]
    per_criterion = [
        {"requirement": req, "tier": tier, "assessment": a, "comment": c}
        for req, tier, a, c in assessments
    ]
    return json.dumps({"per_criterion": per_criterion})


def _classification_json(claims=None):
    """Build a classification JSON string for FakeLLM Call 1."""
    return json.dumps({"per_claim": claims or []})


def test_graph_compiles_without_checkpointer():
    """build_job_graph(None) should compile successfully."""
    graph = build_job_graph(checkpointer=None)
    assert graph is not None


def test_full_graph_clearance_to_trash(tmp_paths, fake_llm, logger):
    """A clearance-required JD -> trashed at step4."""
    _create_source_files(tmp_paths)
    fake_llm.add_response("JD Grading Reasoning", "CLEARANCE")
    # Create the listing folder (step3_ingest would do this, but we need it
    # for the trash node to move it)
    (tmp_paths.listings / "defense-co").mkdir(parents=True)
    (tmp_paths.listings / "defense-co" / "[TBD] job-description.md").write_text("TS/SCI required")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)
    state = JobState(
        slug="defense-co",
        url="https://linkedin.com/123",
        jd_text="Must hold active TS/SCI clearance",
    )
    result = graph.invoke(state, config=config)
    final = JobState(**result)
    assert final.final_destination == TriageDestination.TRASH
    assert final.jd_grade.is_clearance is True


def test_full_graph_low_jd_grade_to_trash(tmp_paths, fake_llm, logger):
    """JD grade < 6 -> trashed at step5. A core GAP deterministically
    scores 0.0."""
    _create_source_files(tmp_paths)
    fake_llm.add_response("JD Grading Reasoning", _jd_reasoning_json([("Rust", "core", "GAP", "No Rust")]))
    (tmp_paths.listings / "bad-co").mkdir(parents=True)
    (tmp_paths.listings / "bad-co" / "[TBD] job-description.md").write_text("JD text")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="bad-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)
    assert final.final_destination == TriageDestination.TRASH
    assert final.jd_grade.grade == 0.0


def test_full_graph_jd_legacy_scoring_path(tmp_paths, fake_llm, logger):
    """deterministic_scoring=False: the legacy two-call path is used —
    the second LLM call's grade drives routing."""
    _create_source_files(tmp_paths)
    fake_llm.add_response("JD Grading Reasoning", _jd_reasoning_json([("Rust", "core", "GAP", "No Rust")]))
    fake_llm.add_response("JD Grading Scoring", _jd_scoring_json(3.0, [("Rust", "core", "GAP", "No Rust")], "Poor fit."))
    (tmp_paths.listings / "bad-co").mkdir(parents=True)
    (tmp_paths.listings / "bad-co" / "[TBD] job-description.md").write_text("JD text")

    from pipeline.infrastructure.config import PipelineConfig
    config = {
        "configurable": {
            "llm": fake_llm,
            "logger": logger,
            "config": PipelineConfig(dry_run=False, deterministic_scoring=False),
            "paths": tmp_paths,
        }
    }
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="bad-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)
    assert final.final_destination == TriageDestination.TRASH
    assert final.jd_grade.grade == 3.0


def test_full_graph_mid_jd_grade_to_rejected_job_fit(tmp_paths, fake_llm, logger):
    """JD grade in [6, 7.0) -> rejected/[JOB-FIT]. 1 DIRECT_HIT + 2 ADDRESSED
    deterministically scores 6.97 — below the 7.0 proceed threshold."""
    _create_source_files(tmp_paths)
    fake_llm.add_response("JD Grading Reasoning", _jd_reasoning_json([
        ("Python", "core", "DIRECT_HIT", "Has Python"),
        ("Go", "core", "ADDRESSED", "Adjacent experience"),
        ("AWS", "core", "ADDRESSED", "Some cloud"),
    ]))
    (tmp_paths.listings / "mid-co").mkdir(parents=True)
    (tmp_paths.listings / "mid-co" / "[TBD] job-description.md").write_text("JD text")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="mid-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)
    assert final.final_destination == TriageDestination.REJECTED_JOB_FIT


def test_full_graph_pass_to_ready(tmp_paths, fake_llm, logger):
    """Full pass: JD grade 8+ -> customize -> grade 9+ -> verified -> ready.

    The customizer answers NO (byte-identical v1 is a valid first draft),
    veracity verifies, should_continue routes done, the final truthfulness
    gate re-verifies v1, and finalize ships it.
    """
    # FakeLLM responses:
    # 1. JD grading reasoning -> all DIRECT_HIT -> deterministic grade 10.0
    # 2. Customize -> "Done\nNO" (FakeLLM doesn't edit; unchanged v1 is fine)
    # 3. Resume grading reasoning -> all DIRECT_HIT -> grade 10.0
    # 4. In-loop truthfulness -> verified=True JSON
    # 5. Final truthfulness gate -> verified=True JSON
    fake_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    fake_llm.add_response("customizing", "Done.\n\nNO")
    fake_llm.add_response("Be realistically harsh", _reasoning_json())
    fake_llm.add_response("You are a truthfulness", _classification_json([]))
    fake_llm.add_response("You are verifying resume", _verification_json(verified=True))
    fake_llm.add_response("You are a truthfulness", _classification_json([]))
    fake_llm.add_response("You are verifying resume", _verification_json(verified=True))

    # Pre-create base resume + LinkedIn (needed by step4 and step6)
    _create_source_files(tmp_paths)

    # Pre-create the listing folder (step3 creates it, but we need it before)
    job_dir = tmp_paths.listings / "good-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nWe need Python.")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)
    state = JobState(
        slug="good-co",
        url="https://x.com",
        jd_text="We need Python.",
    )
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.READY
    assert final.jd_grade.grade == 10.0
    assert final.latest_grade is not None
    assert final.latest_grade.grade == 10.0
    assert final.verification is not None
    assert final.verification.verified is True
    assert len(final.resume_versions) == 1
    assert len(final.version_history) == 1
    assert final.version_history[0].verified is True
    assert final.selected_version == 1
    assert final.final_verification is not None
    assert final.final_verification.verified is True
    # Folder moved to ready/
    assert (tmp_paths.ready / "good-co").exists()


class SequentialEditingLLM:
    """Sequential FakeLLM that also edits the draft file on customize calls.

    Returns canned responses in sequence (first-match, advancing). When a
    prompt contains a "stages/2_drafts/.../resume-vN.md" path, appends a
    marker line to that file — simulating the customizer's in-place edit
    so the YES/NO outcome validation sees a real diff.
    """

    def __init__(self, hunter_dir):
        self.hunter_dir = Path(hunter_dir)
        self.responses: list[tuple[str, str, bool]] = []
        self.calls: list[str] = []
        self._idx = 0

    def add_response(self, key: str, response: str, *, edit: bool = True):
        self.responses.append((key, response, edit))

    def __call__(self, prompt, *, model, timeout, workspace, retries=2, retry_delay=5,
                 export_path=None, permission_mode="dangerous", config_path=None,
                 job_slug=None, step=None, alive_check_seconds=None):
        self.calls.append(prompt)
        for i, (key, resp, do_edit) in enumerate(self.responses):
            if i < self._idx:
                continue
            if key in prompt:
                self._idx = i + 1
                if do_edit:
                    m = re.search(r"Edit only (stages/2_drafts/[^\n]+?resume-v\d+\.md)", prompt)
                    if m:
                        p = self.hunter_dir / m.group(1)
                        p.write_text(
                            p.read_text(encoding="utf-8") + f"\n<!-- edit {i} -->\n",
                            encoding="utf-8",
                        )
                return resp, None
        return None, "No matching response"


def test_full_graph_customize_loop(tmp_paths, logger):
    """Customize loop: v1 grades below threshold (veracity skipped) -> YES
    -> v2 grades passing -> verified -> ready. Veracity runs inside the
    loop on every version."""
    seq_llm = SequentialEditingLLM(tmp_paths.hunter_dir)
    # 1. JD grading reasoning -> all DIRECT_HIT -> deterministic 10.0
    seq_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    # 2. Customize v1 -> edits + YES
    seq_llm.add_response("customizing", "Done.\n\nYES")
    # 3. Resume grading v1 -> core GAP -> deterministic 0.0 (veracity skipped)
    seq_llm.add_response("Be realistically harsh", _reasoning_json([("Rust", "core", "GAP", "Missing")]))
    # 4. should_continue: YES -> customize v2 -> edits + NO
    seq_llm.add_response("customizing", "Done.\n\nNO")
    # 5. Resume grading v2 -> all DIRECT_HIT -> deterministic 10.0
    seq_llm.add_response("Be realistically harsh", _reasoning_json())
    # 6. Veracity v2 -> verified; should_continue: NO + verified -> done
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))
    # 7. Final truthfulness gate on v2 -> verified
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))

    # Pre-create base resume + LinkedIn
    _create_source_files(tmp_paths)

    # Pre-create listing
    job_dir = tmp_paths.listings / "cycle-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nWe need Python + Rust.")

    from pipeline.infrastructure.config import PipelineConfig
    config = {
        "configurable": {
            "llm": seq_llm,
            "logger": logger,
            "config": PipelineConfig(dry_run=False),
            "paths": tmp_paths,
        }
    }
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="cycle-co", url="https://x.com", jd_text="We need Python + Rust.")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.READY
    assert len(final.resume_versions) == 2
    assert final.resume_versions[0].version == 1
    assert final.resume_versions[1].version == 2
    assert final.latest_grade.grade == 10.0
    # v1 evaluated (grade 0.0, veracity skipped) + v2 evaluated (verified)
    assert len(final.version_history) == 2
    assert final.version_history[0].grade == 0.0
    assert final.version_history[0].verified is None
    assert final.version_history[1].verified is True
    assert final.selected_version == 2
    assert final.final_verification.verified is True


def test_full_graph_final_veracity_cascade(tmp_paths, logger):
    """The selected version fails the FINAL gate -> the next-best passing
    version is verified and ships instead (ADR-0018 fallback)."""
    seq_llm = SequentialEditingLLM(tmp_paths.hunter_dir)
    seq_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    # v1: customize + YES -> grade 9.3 (preferred GAP) -> in-loop verified
    seq_llm.add_response("customizing", "Done.\n\nYES")
    seq_llm.add_response("Be realistically harsh", _reasoning_json([
        ("Python", "core", "DIRECT_HIT", "Strong"),
        ("Kubernetes", "preferred", "GAP", "Not mentioned"),
    ]))
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))
    # v2: customize + NO -> grade 10.0 (all DIRECT_HIT) -> in-loop verified
    seq_llm.add_response("customizing", "Done.\n\nNO")
    seq_llm.add_response("Be realistically harsh", _reasoning_json())
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))
    # Final gate: v2 (best grade) fails; v1 passes -> ships v1
    seq_llm.add_response("You are a truthfulness", _classification_json([
        {"claim": "Fabricated skill", "location": "Skills", "bucket": "FABRICATED",
         "source_checked": "both", "reason": "Not in either source."}
    ]))
    seq_llm.add_response("You are verifying resume",
                         _verification_json(verified=False, claims=["Fabricated skill"]))
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))

    _create_source_files(tmp_paths)
    job_dir = tmp_paths.listings / "cascade-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nJD text")

    from pipeline.infrastructure.config import PipelineConfig
    config = {
        "configurable": {
            "llm": seq_llm,
            "logger": logger,
            "config": PipelineConfig(dry_run=False),
            "paths": tmp_paths,
        }
    }
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="cascade-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.READY
    assert final.selected_version == 1
    assert final.final_failed_versions == [2]
    # The shipped folder contains v1's resume, not v2's
    ready_files = [p.name for p in (tmp_paths.ready / "cascade-co").glob("*resume-v*.md")]
    assert ready_files == ["[9.3] resume-v1.md"]


def test_full_graph_veracity_failure_held_for_review(tmp_paths, logger):
    """Versions that grade >= 9 but fail veracity: repair revisions are
    forced (NO + unverified -> continue) until budget exhausts, then the
    job is held for human review (error) rather than silently rejected."""
    seq_llm = SequentialEditingLLM(tmp_paths.hunter_dir)
    seq_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    unverified = _verification_json(verified=False, claims=["Fabricated skill"])
    classification = _classification_json([
        {"claim": "Fabricated skill", "location": "Skills", "bucket": "FABRICATED",
         "source_checked": "both", "reason": "Not in either source."}
    ])
    for _ in range(3):
        # Each version: customize edits + NO -> grade 10.0 -> veracity fails
        seq_llm.add_response("customizing", "Done.\n\nNO")
        seq_llm.add_response("Be realistically harsh", _reasoning_json())
        seq_llm.add_response("You are a truthfulness", classification)
        seq_llm.add_response("You are verifying resume", unverified)

    _create_source_files(tmp_paths)
    job_dir = tmp_paths.listings / "unverified-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nJD text")

    from pipeline.infrastructure.config import PipelineConfig
    config = {
        "configurable": {
            "llm": seq_llm,
            "logger": logger,
            "config": PipelineConfig(dry_run=False),
            "paths": tmp_paths,
        }
    }
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="unverified-co", url="https://x.com", jd_text="JD text")
    with pytest.raises(RuntimeError, match="human review"):
        graph.invoke(state, config=config)
    # Held for review: the folder stays in drafts/
    assert (tmp_paths.drafts / "unverified-co").exists()


def test_full_graph_low_grades_to_rejected_resume(tmp_paths, logger):
    """All versions grade below threshold (veracity skipped each time) ->
    budget exhausts -> no passing version, no veracity failures ->
    rejected/[RESUME]."""
    seq_llm = SequentialEditingLLM(tmp_paths.hunter_dir)
    seq_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    for _ in range(3):
        seq_llm.add_response("customizing", "Done.\n\nYES")
        # core GAP -> deterministic 0.0 -> below threshold
        seq_llm.add_response("Be realistically harsh", _reasoning_json([("Rust", "core", "GAP", "Missing")]))

    _create_source_files(tmp_paths)
    job_dir = tmp_paths.listings / "lowgrade-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nJD text")

    from pipeline.infrastructure.config import PipelineConfig
    config = {
        "configurable": {
            "llm": seq_llm,
            "logger": logger,
            "config": PipelineConfig(dry_run=False),
            "paths": tmp_paths,
        }
    }
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="lowgrade-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.REJECTED_RESUME
    assert len(final.resume_versions) == 3
    assert len(final.version_history) == 3
    assert all(v.verified is None for v in final.version_history)
    assert (tmp_paths.rejected / "[RESUME] lowgrade-co").exists()


def test_full_graph_unchanged_stop_skips_evaluation(tmp_paths, logger):
    """Customizer v2 says NO without changing the draft -> the byte-identical
    duplicate is skipped (not re-graded/verified) and v1's evaluation stands."""
    seq_llm = SequentialEditingLLM(tmp_paths.hunter_dir)
    seq_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    seq_llm.add_response("customizing", "Done.\n\nYES")   # v1: edits + YES
    seq_llm.add_response("Be realistically harsh", _reasoning_json())
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))
    # v2: NO and no edit -> byte-identical -> skip_evaluation -> done
    seq_llm.add_response("customizing", "Done.\n\nNO", edit=False)
    # Final truthfulness gate on v1 -> verified
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))

    _create_source_files(tmp_paths)
    job_dir = tmp_paths.listings / "skip-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nJD text")

    from pipeline.infrastructure.config import PipelineConfig
    config = {
        "configurable": {
            "llm": seq_llm,
            "logger": logger,
            "config": PipelineConfig(dry_run=False),
            "paths": tmp_paths,
        }
    }
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="skip-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.READY
    assert len(final.resume_versions) == 1  # identical v2 dropped
    assert len(final.version_history) == 1
    assert final.selected_version == 1


def test_full_graph_dry_run_no_moves(tmp_paths, fake_llm, logger):
    """Dry run: graph executes but no files are moved."""
    _create_source_files(tmp_paths)
    fake_llm.add_response("JD Grading Reasoning", _jd_reasoning_json([("Rust", "core", "GAP", "No Rust")]))
    (tmp_paths.listings / "dry-co").mkdir(parents=True)
    (tmp_paths.listings / "dry-co" / "[TBD] job-description.md").write_text("JD text")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=True)
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="dry-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    # In dry_run, step5_triage sets triage but doesn't move.
    # The trash node also doesn't move. But final_destination is still set.
    assert final.final_destination == TriageDestination.TRASH
    # Listing not moved (dry_run)
    assert (tmp_paths.listings / "dry-co").exists()
    assert not (tmp_paths.trash / "dry-co").exists()


def test_full_graph_all_final_gate_failures_held_for_review(tmp_paths, logger):
    """All candidates pass in-loop veracity but ALL fail the final gate ->
    held for human review (not silently rejected).

    This is distinct from test_full_graph_veracity_failure_held_for_review
    (which tests in-loop veracity failure) — here every version passes
    in-loop, so they're all final candidates, but the independent final
    gate rejects them all.
    """
    seq_llm = SequentialEditingLLM(tmp_paths.hunter_dir)
    seq_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    # v1: customize + YES -> grade 10.0 -> in-loop verified
    seq_llm.add_response("customizing", "Done.\n\nYES")
    seq_llm.add_response("Be realistically harsh", _reasoning_json())
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))
    # v2: customize + NO -> grade 10.0 -> in-loop verified
    seq_llm.add_response("customizing", "Done.\n\nNO")
    seq_llm.add_response("Be realistically harsh", _reasoning_json())
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))
    # Final gate: v2 (best grade) fails, v1 also fails -> all fail -> human review
    fail_classification = _classification_json([
        {"claim": "Fabricated skill", "location": "Skills", "bucket": "FABRICATED",
         "source_checked": "both", "reason": "Not in either source."}
    ])
    fail_verification = _verification_json(verified=False, claims=["Fabricated skill"])
    # v2 final gate: fail
    seq_llm.add_response("You are a truthfulness", fail_classification)
    seq_llm.add_response("You are verifying resume", fail_verification)
    # v1 final gate: also fail
    seq_llm.add_response("You are a truthfulness", fail_classification)
    seq_llm.add_response("You are verifying resume", fail_verification)

    _create_source_files(tmp_paths)
    job_dir = tmp_paths.listings / "all-final-fail-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nJD text")

    from pipeline.infrastructure.config import PipelineConfig
    config = {
        "configurable": {
            "llm": seq_llm,
            "logger": logger,
            "config": PipelineConfig(dry_run=False),
            "paths": tmp_paths,
        }
    }
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="all-final-fail-co", url="https://x.com", jd_text="JD text")
    with pytest.raises(RuntimeError, match="human review"):
        graph.invoke(state, config=config)
    # Held for review: the folder stays in drafts/
    assert (tmp_paths.drafts / "all-final-fail-co").exists()


def test_full_graph_multiple_verified_picks_best(tmp_paths, logger):
    """Two versions both pass in-loop veracity; the final gate picks the
    higher-grade v2 directly (no cascade needed).

    This is the normal multi-candidate path — distinct from the cascade
    test where the best candidate fails and falls back.
    """
    seq_llm = SequentialEditingLLM(tmp_paths.hunter_dir)
    seq_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    # v1: customize + YES -> grade 9.3 (preferred GAP) -> in-loop verified
    seq_llm.add_response("customizing", "Done.\n\nYES")
    seq_llm.add_response("Be realistically harsh", _reasoning_json([
        ("Python", "core", "DIRECT_HIT", "Strong"),
        ("Kubernetes", "preferred", "GAP", "Not mentioned"),
    ]))
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))
    # v2: customize + NO -> grade 10.0 -> in-loop verified
    seq_llm.add_response("customizing", "Done.\n\nNO")
    seq_llm.add_response("Be realistically harsh", _reasoning_json())
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))
    # Final gate: v2 (best grade) passes directly
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))

    _create_source_files(tmp_paths)
    job_dir = tmp_paths.listings / "multi-verified-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nJD text")

    from pipeline.infrastructure.config import PipelineConfig
    config = {
        "configurable": {
            "llm": seq_llm,
            "logger": logger,
            "config": PipelineConfig(dry_run=False),
            "paths": tmp_paths,
        }
    }
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="multi-verified-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.READY
    assert final.selected_version == 2  # higher grade
    assert final.final_failed_versions == []
    assert final.final_verification.verified is True
    # v1 was pruned, v2 shipped
    ready_files = [p.name for p in (tmp_paths.ready / "multi-verified-co").glob("*resume-v*.md")]
    assert ready_files == ["[10.0] resume-v2.md"]
