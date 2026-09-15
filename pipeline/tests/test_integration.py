"""Integration tests for the full per-job graph flow.

These tests exercise the complete pipeline end-to-end with FakeLLM,
covering scenarios that the unit tests in test_graph.py don't reach:
  - Multiple jobs in one run
  - Customize loop with iteration budget exhausted
  - Error boundary (node throws, other jobs continue)
  - State accumulation across the customize loop
"""
import json
import re
from pathlib import Path

import pytest

from pipeline.infrastructure.config import PipelineConfig, DEFAULT_LLM_MODEL
from pipeline.infrastructure.graph import build_job_graph
from pipeline.infrastructure.state import (
    JobState,
    TriageDestination,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


class SequentialFakeLLM:
    """FakeLLM that returns responses in sequence (first-match, advancing).

    Unlike FakeLLM (which always returns the first match), this advances
    past consumed responses so successive calls to the same prompt substring
    get different responses.

    When `hunter_dir` is set and `edit=True` on a response, the draft file
    referenced by an "Edit only stages/2_drafts/..." prompt is appended to —
    simulating the customizer's in-place edit so the YES/NO outcome
    validation sees a real diff.
    """

    def __init__(self, hunter_dir=None):
        self.hunter_dir = Path(hunter_dir) if hunter_dir else None
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
                if do_edit and self.hunter_dir is not None:
                    m = re.search(r"Edit only (stages/2_drafts/[^\n]+?resume-v\d+\.md)", prompt)
                    if m:
                        p = self.hunter_dir / m.group(1)
                        p.write_text(
                            p.read_text(encoding="utf-8") + f"\n<!-- edit {i} -->\n",
                            encoding="utf-8",
                        )
                return resp, None
        return None, "SequentialFakeLLM: no matching response"


def _grade_json(grade, assessments=None):
    if assessments is None:
        assessments = [("Python", "core", "DIRECT_HIT", "Strong")]
    per_criterion = [
        {"requirement": req, "tier": tier, "assessment": a, "comment": c}
        for req, tier, a, c in assessments
    ]
    return json.dumps({"grade": grade, "per_criterion": per_criterion, "model": DEFAULT_LLM_MODEL})


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


def _make_config(tmp_paths, llm, logger, dry_run=False, deterministic_scoring=True):
    """Build a LangGraph RunnableConfig for integration tests."""
    return {
        "configurable": {
            "llm": llm,
            "logger": logger,
            "config": PipelineConfig(
                dry_run=dry_run, deterministic_scoring=deterministic_scoring),
            "paths": tmp_paths,
        }
    }


def _setup_listing(tmp_paths, slug, jd_text="We need Python."):
    """Create listings/<slug>/ with a [TBD] JD file."""
    job_dir = tmp_paths.listings / slug
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "[TBD] job-description.md").write_text(
        f"<!-- url: https://linkedin.com/{slug} -->\n\n{jd_text}",
        encoding="utf-8",
    )
    return job_dir


def _setup_base_resume(tmp_paths):
    """Create a minimal base resume + LinkedIn for prompt inlining."""
    tmp_paths.base_resume.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.base_resume.write_text("# Squall Leonhart\n\nSoftware Engineer\n", encoding="utf-8")
    tmp_paths.linkedin_experience.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.linkedin_experience.write_text("# Squall Leonhart — Full Experience\n\n## Experience\n", encoding="utf-8")


# ─── Integration tests ────────────────────────────────────────────────────────


def test_integration_multiple_jobs(tmp_paths, logger):
    """Two jobs in one run: one trashed (grade < 6), one rejected_job_fit
    (grade in [6, 7.0) — below the proceed threshold).

    Both exit before the customize step, so no base resume is needed.
    """
    _setup_base_resume(tmp_paths)

    llm = SequentialFakeLLM()
    # good-co: 1 DIRECT_HIT + 2 ADDRESSED -> deterministic 6.97 (rejected_job_fit)
    llm.add_response("JD Grading Reasoning", '{"per_criterion": [{"requirement": "Python", "tier": "core", "assessment": "DIRECT_HIT", "comment": "Has Python"}, {"requirement": "Go", "tier": "core", "assessment": "ADDRESSED", "comment": "Adjacent"}, {"requirement": "AWS", "tier": "core", "assessment": "ADDRESSED", "comment": "Some cloud"}], "summary": "Decent fit."}')
    # bad-co: core GAP -> deterministic 0.0 (trash)
    llm.add_response("JD Grading Reasoning", '{"per_criterion": [{"requirement": "Rust", "tier": "core", "assessment": "GAP", "comment": "No Rust"}], "summary": "Poor fit."}')

    _setup_listing(tmp_paths, "good-co", "We need a Python engineer.")
    _setup_listing(tmp_paths, "bad-co", "We need a Rust engineer with 20 years experience.")

    config = _make_config(tmp_paths, llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)

    results = []
    for slug in ("good-co", "bad-co"):
        state = JobState(slug=slug, url=f"https://linkedin.com/{slug}",
                         jd_text="We need an engineer.")
        result = graph.invoke(state, config=config)
        results.append(JobState(**result))

    # good-co: rejected_job_fit (grade 6.97, between 6 and 7.0)
    assert results[0].final_destination == TriageDestination.REJECTED_JOB_FIT
    assert (tmp_paths.rejected / "[JOB-FIT] good-co").exists()

    # bad-co: trashed (grade 0.0 < 6)
    assert results[1].final_destination == TriageDestination.TRASH
    assert (tmp_paths.trash / "bad-co").exists()


def test_integration_budget_exhaustion(tmp_paths, logger):
    """Customize loop hits max iterations (3) without reaching the passing
    threshold.

    Every version grades below the veracity threshold, so veracity is
    skipped each iteration (recorded verified=None). With no passing
    version and no failed review, the job goes to rejected/[RESUME].
    """
    seq_llm = SequentialFakeLLM(tmp_paths.hunter_dir)
    # JD grading reasoning -> all DIRECT_HIT -> deterministic 10.0
    seq_llm.add_response("JD Grading Reasoning", _reasoning_json([("Python", "core", "DIRECT_HIT", "Strong")]))
    for _ in range(3):
        # customize: edits the draft + says YES
        seq_llm.add_response("customizing", "Done.\n\nYES")
        # core GAP -> deterministic 0.0 -> below threshold -> veracity skipped
        seq_llm.add_response("Be realistically harsh", _reasoning_json([("Rust", "core", "GAP", "Missing")]))

    _setup_base_resume(tmp_paths)
    _setup_listing(tmp_paths, "max-iter-co")

    config = _make_config(tmp_paths, seq_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)

    state = JobState(slug="max-iter-co", jd_text="We need Python + Rust.")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert len(final.resume_versions) == 3
    assert len(final.version_history) == 3
    assert all(v.verified is None for v in final.version_history)
    assert final.latest_grade.grade == 0.0
    assert final.selected_version is None
    assert final.final_destination == TriageDestination.REJECTED_RESUME
    assert (tmp_paths.rejected / "[RESUME] max-iter-co").exists()


def test_integration_error_boundary(tmp_paths, logger):
    """If a node throws, the graph.invoke raises (orchestrator catches)."""
    from pipeline.infrastructure.llm_interface import FakeLLM

    llm = FakeLLM()
    # No responses set up — any LLM call will fail

    _setup_base_resume(tmp_paths)
    _setup_listing(tmp_paths, "error-co")

    config = _make_config(tmp_paths, llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)

    state = JobState(slug="error-co", jd_text="JD text")
    # step4 will fail (LLM error) — graph.invoke raises
    with pytest.raises(RuntimeError, match="JD grading reasoning call failed"):
        graph.invoke(state, config=config)


def test_integration_full_pass_with_file_based_grading(tmp_paths, logger):
    """Full pass using file-based grade JSON (legacy path, not LLM output).

    Exercises deterministic_scoring=False: the legacy grader writes grade
    JSON to .grading/<slug>/grade-vN.json. This test pre-creates that file
    to simulate the grader's file write.
    """
    from pipeline.infrastructure.llm_interface import FakeLLM

    llm = FakeLLM()
    # JD grading two-call
    llm.add_response("JD Grading Reasoning", _reasoning_json())
    llm.add_response("JD Grading Scoring", '{"grade": 8.5, "ceiling": 10.0, "core_score": 10.0, "preferred_bonus": 0, "quantitative_base": 10.0, "subjective_adjustment": 0.3, "per_criterion": [{"requirement": "Python", "tier": "core", "assessment": "DIRECT_HIT", "comment": "Strong"}], "subjective_justification": "No gaps.", "justification": "Good fit."}')
    # Customize — FakeLLM doesn't edit; an unchanged v1 is a valid first draft
    llm.add_response("customizing", "Done customizing.\n\nNO")
    # Resume grading Call 1 (reasoning) — return valid reasoning JSON
    llm.add_response("Be realistically harsh", _reasoning_json())
    # Resume grading Call 2 (scoring) — return non-JSON (simulates file fallback)
    llm.add_response("You are grading resume v", "Grade written to file.")
    # Truthfulness Call 1 (classification) — return valid classification JSON
    llm.add_response("You are a truthfulness", _classification_json([]))
    # Truthfulness Call 2 (synthesis) — return non-JSON (simulates file fallback)
    # Same canned responses serve both the in-loop and the final truthfulness check.
    llm.add_response("You are verifying resume", "Verification written to file.")

    _setup_base_resume(tmp_paths)
    _setup_listing(tmp_paths, "file-grade-co")

    config = _make_config(tmp_paths, llm, logger, dry_run=False,
                          deterministic_scoring=False)
    graph = build_job_graph(checkpointer=None)

    state = JobState(slug="file-grade-co", jd_text="We need Python.")

    # We need to intercept the grading step to write the grade JSON file.
    # Since we can't intercept mid-graph, we'll use a wrapper LLM that
    # writes the file as a side effect.
    class FileWritingLLM:
        def __init__(self, base_llm, paths):
            self.base = base_llm
            self.paths = paths
            self.calls = []

        def __call__(self, prompt, *, model, timeout, workspace, retries=2, retry_delay=5,
                     export_path=None, permission_mode="dangerous", config_path=None,
                     job_slug=None, step=None, alive_check_seconds=None):
            self.calls.append(prompt)
            resp, err = self.base(prompt, model=model, timeout=timeout, workspace=workspace)
            # If this is a scoring prompt and Call 2 returned non-JSON, write the grade file
            if "You are grading resume v" in prompt and resp and "file" in resp.lower():
                grading_dir = self.paths.grading / "file-grade-co"
                grading_dir.mkdir(parents=True, exist_ok=True)
                (grading_dir / "grade-v1.json").write_text(_grade_json(9.5))
            # If this is a synthesis prompt and Call 2 returned non-JSON, write the verification file
            if "You are verifying resume" in prompt and resp and "file" in resp.lower():
                veracity_dir = self.paths.veracity / "file-grade-co"
                veracity_dir.mkdir(parents=True, exist_ok=True)
                (veracity_dir / "verification.json").write_text(
                    _verification_json(verified=True)
                )
            return resp, err

    file_llm = FileWritingLLM(llm, tmp_paths)
    config["configurable"]["llm"] = file_llm

    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.READY
    assert final.latest_grade.grade == 9.5
    assert final.verification.verified is True
    assert (tmp_paths.ready / "file-grade-co").exists()


def test_integration_state_accumulates_versions(tmp_paths, logger):
    """Verify resume_versions + version_history accumulate through the loop."""
    seq_llm = SequentialFakeLLM(tmp_paths.hunter_dir)
    seq_llm.add_response("JD Grading Reasoning", _reasoning_json([("Python", "core", "DIRECT_HIT", "Strong")]))
    # v1: customize edits + YES -> core GAP -> 0.0 -> veracity skipped -> continue
    seq_llm.add_response("customizing", "Done.\n\nYES")
    seq_llm.add_response("Be realistically harsh", _reasoning_json([("Rust", "core", "GAP", "Missing")]))
    # v2: customize edits + NO -> all DIRECT_HIT -> 10.0 -> verified -> done
    seq_llm.add_response("customizing", "Done.\n\nNO")
    seq_llm.add_response("Be realistically harsh", _reasoning_json())
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))
    # Final truthfulness gate on v2 -> verified
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))

    _setup_base_resume(tmp_paths)
    _setup_listing(tmp_paths, "accum-co")

    config = _make_config(tmp_paths, seq_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)

    state = JobState(slug="accum-co", jd_text="We need Python + Rust.")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    # Two versions created (v1 + v2), accumulated via Annotated[list, add]
    assert len(final.resume_versions) == 2
    assert final.resume_versions[0].version == 1
    assert final.resume_versions[1].version == 2
    # version_history records both evaluations
    assert len(final.version_history) == 2
    assert final.version_history[0].grade == 0.0
    assert final.version_history[0].verified is None  # below veracity threshold
    assert final.version_history[1].grade == 10.0
    assert final.version_history[1].verified is True
    # latest_grade is the grade of the latest version (v2 = 10.0)
    assert final.latest_grade.grade == 10.0
    # is_passing should be True (grade >= 9 + verified)
    assert final.is_passing is True
    assert final.selected_version == 2
