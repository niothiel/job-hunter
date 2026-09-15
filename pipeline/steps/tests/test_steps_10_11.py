"""Tests for step10_final_veracity, step11_finalize, and terminal nodes."""
import json
from pathlib import Path

import pytest

from pipeline.infrastructure.state import (
    JobState,
    ResumeVersion,
    TriageDestination,
    Verification,
)
from pipeline.infrastructure.state_store import JobStatus, StateStore
from pipeline.steps.step10_final_veracity import step10_final_veracity_node
from pipeline.steps.step11_finalize import (
    rejected_job_fit_node,
    rejected_resume_node,
    step11_finalize_node,
    step11_ready_node,
    trash_node,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _setup_drafts(tmp_paths, slug="test-co", resume_grade_prefix="[9.5]"):
    """Create drafts/<slug>/ with a graded JD + graded resume + source files. Returns job_dir."""
    job_dir = tmp_paths.drafts / slug
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "[8.5] job-description.md").write_text(
        "<!-- url: https://linkedin.com/123 -->\n\nWe are looking for a Python engineer.",
        encoding="utf-8",
    )
    (job_dir / f"{resume_grade_prefix} resume-v1.md").write_text(
        "# Squall Leonhart\n\nSoftware Engineer\n", encoding="utf-8"
    )
    # Create source files needed for prompt inlining
    tmp_paths.base_resume.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.base_resume.write_text("# Squall Leonhart\n\nSoftware Engineer\n", encoding="utf-8")
    tmp_paths.linkedin_experience.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.linkedin_experience.write_text("# Squall Leonhart — Full Experience\n\n## Experience\n", encoding="utf-8")
    return job_dir


def _setup_listings(tmp_paths, slug="test-co", jd_grade_prefix="[3.0]"):
    """Create listings/<slug>/ with a graded JD. Returns job_dir."""
    job_dir = tmp_paths.listings / slug
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"{jd_grade_prefix} job-description.md").write_text("JD text", encoding="utf-8")
    return job_dir


def _make_verification_json(verified=True, claims=None):
    """Build a verification JSON dict for FakeLLM responses."""
    normalized = []
    for c in (claims or []):
        if isinstance(c, str):
            normalized.append({"claim": c, "location": "", "bucket": "FABRICATED", "source_checked": "both", "reason": ""})
        else:
            normalized.append(c)
    return json.dumps({"verified": verified, "unverifiable_claims": normalized})


def _make_classification_json(claims=None):
    """Build a classification JSON dict for FakeLLM Call 1 responses."""
    return json.dumps({"per_claim": claims or []})


def _history(*records):
    """Build version_history entries: (version, grade, verified) tuples."""
    return [
        ResumeVersion(
            version=v,
            path=Path(f"drafts/test-co/resume-v{v}.md"),
            grade=g,
            verified=ok,
        )
        for v, g, ok in records
    ]


def _expect_final_veracity_calls(fake_llm, verified=True, claims=None):
    """Queue the two canned responses the final gate consumes."""
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", _make_verification_json(verified=verified, claims=claims))


class _SequentialLLM:
    """Returns canned responses in call order, ignoring prompt keys.

    Needed when the same prompt key must produce different outcomes across
    calls (e.g. a first candidate failing the final gate, then the fallback
    passing) — FakeLLM's key-matched responses can't express that.
    """

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, prompt, **kwargs):
        self.calls.append(prompt)
        if self._responses:
            return self._responses.pop(0), None
        return None, "SequentialLLM: responses exhausted"


def _verification_pair(verified=True, claims=None):
    """The two responses (classification, synthesis) one gate run consumes."""
    return [
        _make_classification_json([]),
        _make_verification_json(verified=verified, claims=claims),
    ]


# ─── step10_final_veracity_node ───────────────────────────────────────────────


def test_step10_selects_and_verifies_winner(node_config, fake_llm, tmp_paths):
    """Best passing candidate is re-verified; winner + final_verification set."""
    _setup_drafts(tmp_paths)
    _expect_final_veracity_calls(fake_llm, verified=True)
    state = JobState(
        slug="test-co",
        version_history=_history((1, 9.5, True)),
    )
    result = step10_final_veracity_node(state, node_config)
    assert result["selected_version"] == 1
    assert result["final_verification"].verified is True
    assert result["final_failed_versions"] == []
    assert len(fake_llm.calls) == 2  # classification + synthesis


def test_step10_picks_highest_grade(node_config, fake_llm, tmp_paths):
    """Among multiple passing candidates, the highest grade is checked first."""
    job_dir = _setup_drafts(tmp_paths)
    (job_dir / "[9.8] resume-v2.md").write_text("# v2 content", encoding="utf-8")
    _expect_final_veracity_calls(fake_llm, verified=True)
    state = JobState(
        slug="test-co",
        version_history=_history((1, 9.5, True), (2, 9.8, True)),
    )
    result = step10_final_veracity_node(state, node_config)
    assert result["selected_version"] == 2
    assert "v2 content" in fake_llm.calls[0]  # v2's file was verified


def test_step10_cascade_on_final_failure(node_config, tmp_paths):
    """Winner fails the final gate -> next-best candidate is verified and wins."""
    job_dir = _setup_drafts(tmp_paths)
    (job_dir / "[9.8] resume-v2.md").write_text("# v2 content", encoding="utf-8")
    seq_llm = _SequentialLLM(
        # First candidate (v2, higher grade) fails final; fallback (v1) passes
        *_verification_pair(verified=False, claims=["Fabricated title"]),
        *_verification_pair(verified=True),
    )
    node_config["configurable"]["llm"] = seq_llm
    state = JobState(
        slug="test-co",
        version_history=_history((1, 9.5, True), (2, 9.8, True)),
    )
    result = step10_final_veracity_node(state, node_config)
    assert result["selected_version"] == 1
    assert result["final_failed_versions"] == [2]
    assert result["final_verification"].verified is True
    assert len(seq_llm.calls) == 4  # two candidates x two calls


def test_step10_all_candidates_fail(node_config, tmp_paths):
    """Every candidate fails the final gate -> no winner, all recorded failed."""
    job_dir = _setup_drafts(tmp_paths)
    (job_dir / "[9.8] resume-v2.md").write_text("# v2 content", encoding="utf-8")
    seq_llm = _SequentialLLM(
        *_verification_pair(verified=False, claims=["c1"]),
        *_verification_pair(verified=False, claims=["c2"]),
    )
    node_config["configurable"]["llm"] = seq_llm
    state = JobState(
        slug="test-co",
        version_history=_history((1, 9.5, True), (2, 9.8, True)),
    )
    result = step10_final_veracity_node(state, node_config)
    assert result["selected_version"] is None
    assert sorted(result["final_failed_versions"]) == [1, 2]
    assert result["final_verification"].verified is False


def test_step10_no_candidates_no_llm(node_config, fake_llm, tmp_paths):
    """No passing versions -> no LLM calls, no selection."""
    _setup_drafts(tmp_paths)
    state = JobState(
        slug="test-co",
        version_history=_history((1, 7.0, None), (2, 8.0, None)),
    )
    result = step10_final_veracity_node(state, node_config)
    assert result["selected_version"] is None
    assert len(fake_llm.calls) == 0


def test_step10_in_loop_failure_not_a_candidate(node_config, fake_llm, tmp_paths):
    """A version that failed in-loop veracity is never a final candidate."""
    job_dir = _setup_drafts(tmp_paths)
    (job_dir / "[9.9] resume-v2.md").write_text("# v2", encoding="utf-8")
    _expect_final_veracity_calls(fake_llm, verified=True)
    state = JobState(
        slug="test-co",
        version_history=_history((1, 9.5, True), (2, 9.9, False)),
    )
    result = step10_final_veracity_node(state, node_config)
    assert result["selected_version"] == 1
    assert len(fake_llm.calls) == 2  # only v1 was checked


def test_step10_missing_candidate_file_falls_back(node_config, fake_llm, tmp_paths):
    """A candidate whose file vanished is excluded; the next one is tried."""
    _setup_drafts(tmp_paths)  # only v1's file exists
    _expect_final_veracity_calls(fake_llm, verified=True)
    state = JobState(
        slug="test-co",
        version_history=_history((1, 9.5, True), (2, 9.9, True)),  # v2 file missing
    )
    result = step10_final_veracity_node(state, node_config)
    assert result["selected_version"] == 1
    assert result["final_failed_versions"] == [2]


def test_step10_skips_prior_final_failures(node_config, fake_llm, tmp_paths):
    """Versions already in final_failed_versions are not re-checked."""
    _setup_drafts(tmp_paths)
    _expect_final_veracity_calls(fake_llm, verified=True)
    state = JobState(
        slug="test-co",
        version_history=_history((1, 9.5, True), (2, 9.9, True)),
        final_failed_versions=[2],
    )
    result = step10_final_veracity_node(state, node_config)
    assert result["selected_version"] == 1
    assert result["final_failed_versions"] == [2]
    assert len(fake_llm.calls) == 2


def test_step10_dry_run(dry_run_node_config, fake_llm, tmp_paths):
    """Dry run: no LLM calls, no selection."""
    _setup_drafts(tmp_paths)
    state = JobState(
        slug="test-co",
        version_history=_history((1, 9.5, True)),
    )
    result = step10_final_veracity_node(state, dry_run_node_config)
    assert result["selected_version"] is None
    assert len(fake_llm.calls) == 0


def test_step10_llm_error_raises(node_config, fake_llm, tmp_paths):
    """LLM failure on the final check raises — no silent fallback."""
    _setup_drafts(tmp_paths)
    state = JobState(
        slug="test-co",
        version_history=_history((1, 9.5, True)),
    )
    with pytest.raises(RuntimeError, match="classification"):
        step10_final_veracity_node(state, node_config)


# ─── step11_finalize_node ─────────────────────────────────────────────────────


def _winner_state(**kwargs):
    """State as left by a successful step10 run."""
    defaults = dict(
        slug="test-co",
        selected_version=1,
        final_verification=Verification(verified=True),
        version_history=_history((1, 9.5, True)),
    )
    defaults.update(kwargs)
    return JobState(**defaults)


def test_finalize_routes_winner(node_config, tmp_paths):
    """Confirmed winner -> prunes non-selected files, JD untouched."""
    job_dir = _setup_drafts(tmp_paths)
    (job_dir / "[9.8] resume-v2.md").write_text("# v2", encoding="utf-8")
    state = _winner_state(
        version_history=_history((1, 9.5, True), (2, 9.8, True)),
    )
    result = step11_finalize_node(state, node_config)
    assert result == {}
    assert (job_dir / "[9.5] resume-v1.md").exists()
    assert not (job_dir / "[9.8] resume-v2.md").exists()
    assert (job_dir / "[8.5] job-description.md").exists()


def test_finalize_winner_without_final_verification_raises(node_config, tmp_paths):
    """selected_version set but no final_verification -> inconsistent, held."""
    _setup_drafts(tmp_paths)
    state = _winner_state(final_verification=None)
    with pytest.raises(RuntimeError, match="inconsistent"):
        step11_finalize_node(state, node_config)


def test_finalize_winner_with_failed_final_verification_raises(node_config, tmp_paths):
    """selected_version set but final_verification failed -> held."""
    _setup_drafts(tmp_paths)
    state = _winner_state(final_verification=Verification(verified=False))
    with pytest.raises(RuntimeError, match="inconsistent"):
        step11_finalize_node(state, node_config)


def test_finalize_no_winner_no_failures_rejects(node_config, tmp_paths):
    """All versions below grade (veracity skipped) -> normal reject."""
    _setup_drafts(tmp_paths)
    state = JobState(
        slug="test-co",
        selected_version=None,
        version_history=_history((1, 7.0, None), (2, 8.0, None), (3, 8.5, None)),
    )
    result = step11_finalize_node(state, node_config)
    assert result == {}


def test_finalize_in_loop_failure_raises_for_review(node_config, tmp_paths):
    """No winner + a failed in-loop review -> held for human review."""
    _setup_drafts(tmp_paths)
    state = JobState(
        slug="test-co",
        selected_version=None,
        version_history=_history((1, 9.5, False), (2, 9.7, False)),
    )
    with pytest.raises(RuntimeError, match="human review"):
        step11_finalize_node(state, node_config)


def test_finalize_final_gate_failure_raises_for_review(node_config, tmp_paths):
    """No winner + a failed FINAL review -> held for human review."""
    _setup_drafts(tmp_paths)
    state = JobState(
        slug="test-co",
        selected_version=None,
        version_history=_history((1, 9.5, True), (2, 9.8, True)),
        final_failed_versions=[1, 2],
        final_verification=Verification(verified=False),
    )
    with pytest.raises(RuntimeError, match="human review"):
        step11_finalize_node(state, node_config)


def test_finalize_dry_run_no_pruning(dry_run_node_config, tmp_paths):
    """Dry run: no files are pruned."""
    job_dir = _setup_drafts(tmp_paths)
    (job_dir / "[9.8] resume-v2.md").write_text("# v2", encoding="utf-8")
    state = _winner_state(
        version_history=_history((1, 9.5, True), (2, 9.8, True)),
    )
    result = step11_finalize_node(state, dry_run_node_config)
    assert result == {}
    assert (job_dir / "[9.8] resume-v2.md").exists()  # not pruned


# ─── step11_ready_node ────────────────────────────────────────────────────────


def test_step11_ready_moves_to_ready(node_config, tmp_paths):
    """Passing resume: drafts/<slug>/ moved to ready/<slug>/."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(slug="test-co")
    result = step11_ready_node(state, node_config)
    assert result["final_destination"] == TriageDestination.READY
    assert (tmp_paths.ready / "test-co").exists()
    assert not (tmp_paths.drafts / "test-co").exists()


def test_step11_ready_dry_run(dry_run_node_config, tmp_paths):
    """Dry run: no move, but final_destination still set."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(slug="test-co")
    result = step11_ready_node(state, dry_run_node_config)
    assert result["final_destination"] == TriageDestination.READY
    assert (tmp_paths.drafts / "test-co").exists()  # not moved
    assert not (tmp_paths.ready / "test-co").exists()


def test_step11_ready_missing_folder_ok(node_config, tmp_paths):
    """If drafts/<slug>/ doesn't exist, node still succeeds (idempotent)."""
    state = JobState(slug="nonexistent")
    result = step11_ready_node(state, node_config)
    assert result["final_destination"] == TriageDestination.READY


# ─── trash_node ───────────────────────────────────────────────────────────────


def test_trash_node_moves_to_trash(node_config, tmp_paths):
    """Clearance/low-grade job: listings/<slug>/ moved to trash/<slug>/."""
    _setup_listings(tmp_paths, slug="bad-co", jd_grade_prefix="[3.0]")
    state = JobState(slug="bad-co")
    result = trash_node(state, node_config)
    assert result["final_destination"] == TriageDestination.TRASH
    assert (tmp_paths.trash / "bad-co").exists()
    assert not (tmp_paths.listings / "bad-co").exists()


def test_trash_node_dry_run(dry_run_node_config, tmp_paths):
    """Dry run: no move, but final_destination set."""
    _setup_listings(tmp_paths, slug="bad-co", jd_grade_prefix="[3.0]")
    state = JobState(slug="bad-co")
    result = trash_node(state, dry_run_node_config)
    assert result["final_destination"] == TriageDestination.TRASH
    assert (tmp_paths.listings / "bad-co").exists()


def test_trash_node_missing_folder_ok(node_config, tmp_paths):
    """If listings/<slug>/ doesn't exist, node still succeeds."""
    state = JobState(slug="nonexistent")
    result = trash_node(state, node_config)
    assert result["final_destination"] == TriageDestination.TRASH


# ─── rejected_job_fit_node ────────────────────────────────────────────────────


def test_rejected_job_fit_moves_with_prefix(node_config, tmp_paths):
    """Grade 6-7.9: listings/<slug>/ moved to rejected/[JOB-FIT] <slug>/."""
    _setup_listings(tmp_paths, slug="mid-co", jd_grade_prefix="[7.0]")
    state = JobState(slug="mid-co")
    result = rejected_job_fit_node(state, node_config)
    assert result["final_destination"] == TriageDestination.REJECTED_JOB_FIT
    assert (tmp_paths.rejected / "[JOB-FIT] mid-co").exists()
    assert not (tmp_paths.listings / "mid-co").exists()


def test_rejected_job_fit_dry_run(dry_run_node_config, tmp_paths):
    """Dry run: no move, but final_destination set."""
    _setup_listings(tmp_paths, slug="mid-co", jd_grade_prefix="[7.0]")
    state = JobState(slug="mid-co")
    result = rejected_job_fit_node(state, dry_run_node_config)
    assert result["final_destination"] == TriageDestination.REJECTED_JOB_FIT
    assert (tmp_paths.listings / "mid-co").exists()


# ─── rejected_resume_node ─────────────────────────────────────────────────────


def test_rejected_resume_moves_with_prefix(node_config, tmp_paths):
    """Grade < 9 or unverified: drafts/<slug>/ moved to rejected/[RESUME] <slug>/."""
    _setup_drafts(tmp_paths, slug="fail-co", resume_grade_prefix="[7.5]")
    state = JobState(slug="fail-co")
    result = rejected_resume_node(state, node_config)
    assert result["final_destination"] == TriageDestination.REJECTED_RESUME
    assert (tmp_paths.rejected / "[RESUME] fail-co").exists()
    assert not (tmp_paths.drafts / "fail-co").exists()


def test_rejected_resume_dry_run(dry_run_node_config, tmp_paths):
    """Dry run: no move, but final_destination set."""
    _setup_drafts(tmp_paths, slug="fail-co", resume_grade_prefix="[7.5]")
    state = JobState(slug="fail-co")
    result = rejected_resume_node(state, dry_run_node_config)
    assert result["final_destination"] == TriageDestination.REJECTED_RESUME
    assert (tmp_paths.drafts / "fail-co").exists()


def test_rejected_resume_missing_folder_ok(node_config, tmp_paths):
    """If drafts/<slug>/ doesn't exist, node still succeeds."""
    state = JobState(slug="nonexistent")
    result = rejected_resume_node(state, node_config)
    assert result["final_destination"] == TriageDestination.REJECTED_RESUME


# ─── State transition audit (E2) ──────────────────────────────────────────────


def test_step11_ready_records_transition(node_config, tmp_paths):
    """Move to ready/ records a state_transitions row."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(slug="test-co")
    step11_ready_node(state, node_config)

    store = StateStore(str(tmp_paths.jobs_db))
    history = store.query_history("test-co")
    store.close()
    assert len(history) == 1
    assert history[0]["from_status"] == JobStatus.DRAFTS.value
    assert history[0]["to_status"] == JobStatus.READY.value


def test_trash_node_records_transition(node_config, tmp_paths):
    """Move to trash/ records a state_transitions row."""
    _setup_listings(tmp_paths, slug="bad-co", jd_grade_prefix="[3.0]")
    state = JobState(slug="bad-co")
    trash_node(state, node_config)

    store = StateStore(str(tmp_paths.jobs_db))
    history = store.query_history("bad-co")
    store.close()
    assert len(history) == 1
    assert history[0]["from_status"] == JobStatus.LISTINGS.value
    assert history[0]["to_status"] == JobStatus.TRASH.value


def test_rejected_job_fit_records_transition(node_config, tmp_paths):
    """Move to rejected/[JOB-FIT] records a state_transitions row."""
    _setup_listings(tmp_paths, slug="mid-co", jd_grade_prefix="[7.0]")
    state = JobState(slug="mid-co")
    rejected_job_fit_node(state, node_config)

    store = StateStore(str(tmp_paths.jobs_db))
    history = store.query_history("mid-co")
    store.close()
    assert len(history) == 1
    assert history[0]["from_status"] == JobStatus.LISTINGS.value
    assert history[0]["to_status"] == JobStatus.REJECTED_JOB_FIT.value


def test_rejected_resume_records_transition(node_config, tmp_paths):
    """Move to rejected/[RESUME] records a state_transitions row."""
    _setup_drafts(tmp_paths, slug="fail-co", resume_grade_prefix="[7.5]")
    state = JobState(slug="fail-co")
    rejected_resume_node(state, node_config)

    store = StateStore(str(tmp_paths.jobs_db))
    history = store.query_history("fail-co")
    store.close()
    assert len(history) == 1
    assert history[0]["from_status"] == JobStatus.DRAFTS.value
    assert history[0]["to_status"] == JobStatus.REJECTED_RESUME.value


def test_step11_ready_dry_run_no_transition(dry_run_node_config, tmp_paths):
    """Dry run: no move, no transition recorded."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(slug="test-co")
    step11_ready_node(state, dry_run_node_config)

    store = StateStore(str(tmp_paths.jobs_db))
    history = store.query_history("test-co")
    store.close()
    assert history == []
