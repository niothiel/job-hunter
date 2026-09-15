"""Tests for step8_veracity and step9_should_continue."""
import json
from pathlib import Path

import pytest

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
from pipeline.steps.step8_veracity import (
    find_resume_version_file,
    parse_verification_json,
    step8_veracity_node,
    verify_resume_file,
)
from pipeline.steps.step9_should_continue import step9_should_continue_node


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
    """Build a verification JSON dict for FakeLLM responses.

    claims should be a list of dicts with claim/location/bucket/source_checked/reason,
    matching the veracity protocol spec. If strings are passed, they're wrapped
    as minimal claim objects.
    """
    if claims is None:
        claims = []
    # Normalize string claims to objects (backward compat for older tests)
    normalized = []
    for c in claims:
        if isinstance(c, str):
            normalized.append({"claim": c, "location": "", "bucket": "FABRICATED", "source_checked": "both", "reason": ""})
        else:
            normalized.append(c)
    return json.dumps({
        "verified": verified,
        "unverifiable_claims": normalized,
    })


def _make_classification_json(claims=None):
    """Build a classification JSON dict for FakeLLM Call 1 responses."""
    return json.dumps({
        "per_claim": claims or [],
    })


# ─── parse_verification_json ─────────────────────────────────────────────────


def test_parse_verification_json_valid(tmp_path):
    f = tmp_path / "verification.json"
    f.write_text(json.dumps({"verified": True, "unverifiable_claims": []}))
    data = parse_verification_json(f)
    assert data["verified"] is True


def test_parse_verification_json_missing(tmp_path):
    assert parse_verification_json(tmp_path / "nope.json") is None


def test_parse_verification_json_invalid(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("not json")
    assert parse_verification_json(f) is None


# ─── step8_veracity_node ─────────────────────────────────────────────────


def test_step8_verified(node_config, fake_llm, tmp_paths):
    """Grade >= 9, two-call truthfulness returns verified=True + history record."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", _make_verification_json(verified=True))

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    result = step8_veracity_node(state, node_config)
    assert result["verification"].verified is True
    assert result["verification"].unverifiable_claims == []

    record = result["version_history"][0]
    assert record.version == 1
    assert record.grade == 9.5
    assert record.verified is True
    assert len(record.draft_hash) == 12
    assert record.truthfulness_issues == []


def test_step8_unverified(node_config, fake_llm, tmp_paths):
    """Grade >= 9, verified=False with claims — recorded on version_history."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.0]")
    fake_llm.add_response("You are a truthfulness", _make_classification_json([
        {"claim": "Claim 1", "location": "x", "bucket": "FABRICATED",
         "source_checked": "both", "reason": "r1"},
        {"claim": "Claim 2", "location": "y", "bucket": "MATERIAL_OVERSTATEMENT",
         "source_checked": "LinkedIn", "reason": "r2"},
    ]))
    fake_llm.add_response("You are verifying resume", _make_verification_json(
        verified=False, claims=["Claim 1", "Claim 2"]
    ))

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.0] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.0, per_criterion=[]),
    )
    result = step8_veracity_node(state, node_config)
    assert result["verification"].verified is False
    assert len(result["verification"].unverifiable_claims) == 2

    record = result["version_history"][0]
    assert record.verified is False
    assert record.truthfulness_issues == ["Claim 1", "Claim 2"]


def test_step8_targets_latest_version(node_config, fake_llm, tmp_paths):
    """In-loop veracity reviews the LATEST version, not the best-graded file."""
    job_dir = _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    # A higher-graded v1 and a lower-graded v2 — veracity must review v2.
    (job_dir / "[8.0] resume-v2.md").write_text("# v2 content", encoding="utf-8")
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", _make_verification_json(verified=True))

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[
            ResumeVersion(version=1, path=job_dir / "[9.5] resume-v1.md"),
            ResumeVersion(version=2, path=job_dir / "[8.0] resume-v2.md"),
        ],
        latest_grade=ResumeGrade(grade=9.2, per_criterion=[]),
    )
    result = step8_veracity_node(state, node_config)

    record = result["version_history"][0]
    assert record.version == 2
    assert "v2 content" in fake_llm.calls[0]  # v2 content inlined in classification prompt


def test_step8_from_file(node_config, fake_llm, tmp_paths):
    """Verification JSON read from file (fallback after two-call)."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    # Call 1 returns classification, Call 2 returns non-JSON — simulates the
    # LLM writing verification.json itself, so the double writes the file as
    # a side effect during the synthesis call.
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", "Done. Verification written to file.")

    base_llm = node_config["configurable"]["llm"]

    class _FileWritingLLM:
        def __call__(self, prompt, **kwargs):
            resp, err = base_llm(prompt, **kwargs)
            if "You are verifying resume" in prompt:
                veracity_dir = tmp_paths.veracity / "test-co"
                veracity_dir.mkdir(parents=True, exist_ok=True)
                (veracity_dir / "verification.json").write_text(
                    _make_verification_json(verified=True)
                )
            return resp, err

    node_config["configurable"]["llm"] = _FileWritingLLM()

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    result = step8_veracity_node(state, node_config)
    assert result["verification"].verified is True


def test_step8_grade_below_threshold_skips_llm(node_config, fake_llm, tmp_paths):
    """Grade < 9 -> no LLM call, review skipped (record verified=None)."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[7.5]")
    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[7.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=7.5, per_criterion=[]),
    )
    result = step8_veracity_node(state, node_config)
    assert result["verification"].verified is False
    assert len(fake_llm.calls) == 0  # no LLM call

    record = result["version_history"][0]
    assert record.verified is None  # review not run — distinct from failed
    assert record.grade == 7.5


def test_step8_no_grade_skips_llm(node_config, fake_llm, tmp_paths):
    """No latest_grade -> no LLM call, review skipped (verified=None)."""
    _setup_drafts(tmp_paths)
    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
    )
    result = step8_veracity_node(state, node_config)
    assert result["verification"].verified is False
    assert len(fake_llm.calls) == 0
    assert result["version_history"][0].verified is None


def test_step8_dry_run(dry_run_node_config, tmp_paths):
    """Dry run: no LLM call, review skipped (verified=None)."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    result = step8_veracity_node(state, dry_run_node_config)
    assert result["verification"].verified is False
    assert result["version_history"][0].verified is None


def test_step8_llm_error_raises(node_config, fake_llm, tmp_paths):
    """LLM failure on Call 1 (classification) should raise."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    with pytest.raises(RuntimeError, match="classification"):
        step8_veracity_node(state, node_config)


def test_step8_parse_error_raises(node_config, fake_llm, tmp_paths):
    """Unparseable verification from Call 2 (no file, no JSON) should raise."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    # Call 1 returns valid classification, Call 2 returns unparseable
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", "I could not verify this resume.")

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    with pytest.raises(RuntimeError, match="could not parse verification JSON"):
        step8_veracity_node(state, node_config)


def test_step8_no_resume_raises(node_config, fake_llm, tmp_paths):
    """No resume version in state should raise."""
    state = JobState(
        slug="test-co",
        jd_text="JD text",
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    with pytest.raises(RuntimeError, match="no resume to verify"):
        step8_veracity_node(state, node_config)


def test_step8_two_call_verified(node_config, fake_llm, tmp_paths):
    """Two-call truthfulness: Call 1 classifies → Call 2 synthesizes verified=True."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", _make_verification_json(verified=True))

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    result = step8_veracity_node(state, node_config)
    assert result["verification"].verified is True


def test_step8_two_call_unverified(node_config, fake_llm, tmp_paths):
    """Two-call truthfulness: Call 2 returns verified=False with claims."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.0]")
    fake_llm.add_response("You are a truthfulness", _make_classification_json([
        {"claim": "Led 100-person org", "location": "Experience > Oracle",
         "bucket": "MATERIAL_OVERSTATEMENT", "source_checked": "LinkedIn",
         "reason": "LinkedIn says 50."}
    ]))
    fake_llm.add_response("You are verifying resume", _make_verification_json(
        verified=False, claims=["Led 100-person org"]
    ))

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.0] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.0, per_criterion=[]),
    )
    result = step8_veracity_node(state, node_config)
    assert result["verification"].verified is False
    assert len(result["verification"].unverifiable_claims) == 1


def test_step8_two_call_classification_error_raises(node_config, fake_llm, tmp_paths):
    """If Call 1 (classification) fails, the node should raise — not proceed to Call 2."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    with pytest.raises(RuntimeError, match="classification"):
        step8_veracity_node(state, node_config)


# ─── step9_should_continue_node ───────────────────────────────────────────────


def _loop_state(**kwargs):
    """JobState for should_continue tests with one version graded."""
    defaults = dict(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=Path("drafts/test-co/resume-v1.md"))],
    )
    defaults.update(kwargs)
    return JobState(**defaults)


def test_step9_yes_continues(node_config):
    """Customizer said YES -> continue regardless of veracity."""
    state = _loop_state(customizer_can_improve=True)
    result = step9_should_continue_node(state, node_config)
    assert result["should_continue"] is True


def test_step9_no_verified_done(node_config):
    """NO + latest review verified -> done."""
    state = _loop_state(
        customizer_can_improve=False,
        version_history=[
            ResumeVersion(version=1, path=Path("a"), grade=9.5, verified=True),
        ],
        verification=Verification(verified=True),
    )
    result = step9_should_continue_node(state, node_config)
    assert result["should_continue"] is False


def test_step9_no_failed_review_continues(node_config):
    """NO + latest review FAILED -> truthfulness override forces a repair."""
    state = _loop_state(
        customizer_can_improve=False,
        version_history=[
            ResumeVersion(version=1, path=Path("a"), grade=9.5, verified=False),
        ],
        verification=Verification(verified=False),
    )
    result = step9_should_continue_node(state, node_config)
    assert result["should_continue"] is True


def test_step9_no_skipped_review_done(node_config):
    """NO + review SKIPPED (below grade -> verified=None) -> done.

    A skipped review is not a failed review — the customizer's NO stands.
    """
    state = _loop_state(
        customizer_can_improve=False,
        version_history=[
            ResumeVersion(version=1, path=Path("a"), grade=7.0, verified=None),
        ],
        verification=Verification(verified=False),  # step8 sets False on skip
    )
    result = step9_should_continue_node(state, node_config)
    assert result["should_continue"] is False


def test_step9_no_verification_done(node_config):
    """NO + no verification result at all -> done (nothing failed to repair)."""
    state = _loop_state(customizer_can_improve=False)
    result = step9_should_continue_node(state, node_config)
    assert result["should_continue"] is False


def test_step9_legacy_verification_false_continues(node_config):
    """Legacy state (no version_history) + verification.verified=False ->
    treated as a failed review -> continue."""
    state = _loop_state(
        customizer_can_improve=False,
        verification=Verification(verified=False),
    )
    result = step9_should_continue_node(state, node_config)
    assert result["should_continue"] is True


def test_step9_budget_exhausted_done(node_config):
    """3 versions created = budget exhausted -> done even if YES."""
    state = JobState(
        slug="test-co",
        resume_versions=[
            ResumeVersion(version=1, path=Path("a")),
            ResumeVersion(version=2, path=Path("b")),
            ResumeVersion(version=3, path=Path("c")),
        ],
        customizer_can_improve=True,
        verification=Verification(verified=False),
    )
    result = step9_should_continue_node(state, node_config)
    assert result["should_continue"] is False


def test_step9_no_signal_done(node_config):
    """No YES/NO signal (dry run, legacy state) -> done."""
    state = _loop_state()
    result = step9_should_continue_node(state, node_config)
    assert result["should_continue"] is False


def test_step9_unchanged_draft_done(node_config):
    """Byte-identical revision + failed prior review -> done, NOT continue.

    Identical drafts never increment resume_versions, so letting the
    truthfulness override continue here would loop forever — the customizer
    keeps returning the same draft. Finalize holds the job for review.
    """
    state = _loop_state(
        customizer_can_improve=False,
        customizer_draft_unchanged=True,
        version_history=[
            ResumeVersion(version=1, path=Path("a"), grade=9.5, verified=False),
        ],
        verification=Verification(verified=False),
    )
    result = step9_should_continue_node(state, node_config)
    assert result["should_continue"] is False


def test_verify_resume_file_clears_stale_fallback(node_config, fake_llm, tmp_paths):
    """A stale verification.json from a prior run is cleared before the
    synthesis call — preventing a previous candidate's file from being
    misattributed to the next candidate when stdout isn't parseable.

    Regression test for the cascade-stale-file bug: if v2's check left a
    verification.json and v3's synthesis returned non-JSON, the fallback
    would read v2's file and inherit v2's verdict.
    """
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    veracity_dir = tmp_paths.veracity / "test-co"
    veracity_dir.mkdir(parents=True, exist_ok=True)
    # Pre-create a STALE verification.json (simulates a prior candidate's file)
    stale_json = _make_verification_json(verified=False, claims=["Stale claim"])
    (veracity_dir / "verification.json").write_text(stale_json)

    # Call 1 returns valid classification; Call 2 returns non-JSON
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", "Done. No JSON in stdout.")

    from pipeline.infrastructure.llm_interface import get_deps
    deps = get_deps(node_config)
    resume_path = tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md"

    with pytest.raises(RuntimeError, match="could not parse verification JSON"):
        verify_resume_file(
            deps, "test-co", resume_path,
            step_label="truthfulness", file_prefix="",
        )
    # The stale file was deleted
    assert not (veracity_dir / "verification.json").exists()


def test_find_resume_version_file_finds_grade_prefixed(tmp_paths):
    """find_resume_version_file matches grade-prefixed files like
    '[9.5] resume-v1.md' by globbing '*resume-v{version}.md'."""
    job_dir = tmp_paths.drafts / "test-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[9.5] resume-v1.md").write_text("v1", encoding="utf-8")
    (job_dir / "[9.8] resume-v2.md").write_text("v2", encoding="utf-8")

    p1 = find_resume_version_file("test-co", 1, tmp_paths)
    assert p1 is not None
    assert p1.name == "[9.5] resume-v1.md"

    p2 = find_resume_version_file("test-co", 2, tmp_paths)
    assert p2 is not None
    assert p2.name == "[9.8] resume-v2.md"

    # Missing version
    assert find_resume_version_file("test-co", 3, tmp_paths) is None
    # Missing job dir
    assert find_resume_version_file("no-such-co", 1, tmp_paths) is None


