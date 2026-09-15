"""Tests for pipeline.state — Pydantic models, enums, queries, transforms."""
from pathlib import Path

import pytest

from pipeline.infrastructure.state import (
    Assessment,
    Criterion,
    FeasibilityTier,
    JdGrade,
    JobState,
    ResumeGrade,
    ResumeVersion,
    TriageDestination,
    Verification,
    build_job_state_from_filesystem,
    passing_versions,
    select_best_passing,
    with_customizer_can_improve,
    with_final_destination,
    with_jd_grade,
    with_latest_grade,
    with_resume_version,
    with_triage,
    with_verification,
)


# ─── Construction ──────────────────────────────────────────────────────────


def test_basic_construction():
    state = JobState(
        slug="google-staff-engineer",
        url="https://linkedin.com/123",
        company="Google",
        title="Staff Engineer",
    )
    assert state.slug == "google-staff-engineer"


def test_empty_state_queries():
    state = JobState(slug="test")
    assert state.latest_resume is None
    assert state.latest_resume_path is None
    assert not state.is_passing
    assert not state.has_core_gaps
    assert state.optimize_iteration_count == 0
    assert state.next_resume_version == 1


# ─── Transforms (immutability) ─────────────────────────────────────────────


def test_resume_version_transform_is_immutable():
    state = JobState(slug="test")
    v1 = ResumeVersion(version=1, path=Path("drafts/test/[TBD] resume-v1.md"))
    state2 = with_resume_version(state, v1)
    assert state2.latest_resume == v1
    assert state2.optimize_iteration_count == 1
    assert state2.next_resume_version == 2
    assert state.resume_versions == []  # original unchanged


def test_jd_grade_transform():
    state = JobState(slug="test")
    grade = JdGrade(grade=8.5, justification="Good fit")
    state2 = with_jd_grade(state, grade)
    assert state2.jd_grade.grade == 8.5
    assert state.jd_grade is None  # original unchanged


def test_latest_grade_transform():
    state = JobState(slug="test")
    grade = ResumeGrade(grade=9.5)
    state2 = with_latest_grade(state, grade)
    assert state2.latest_grade.grade == 9.5
    assert state.latest_grade is None


def test_verification_transform():
    state = JobState(slug="test")
    v = Verification(verified=True)
    state2 = with_verification(state, v)
    assert state2.verification.verified is True
    assert state.verification is None


def test_triage_transform():
    state = JobState(slug="test")
    state2 = with_triage(state, TriageDestination.DRAFTS)
    assert state2.triage == TriageDestination.DRAFTS
    assert state.triage is None


def test_final_destination_transform():
    state = JobState(slug="test")
    state2 = with_final_destination(state, TriageDestination.READY)
    assert state2.final_destination == TriageDestination.READY
    assert state.final_destination is None


def test_customizer_can_improve_transform():
    state = JobState(slug="test")
    state2 = with_customizer_can_improve(state, True)
    assert state2.customizer_can_improve is True
    assert state.customizer_can_improve is None


# ─── Pure queries ──────────────────────────────────────────────────────────


def test_is_passing_requires_grade_and_verification():
    state = JobState(slug="test")
    v1 = ResumeVersion(version=1, path=Path("drafts/test/resume-v1.md"))
    state = with_resume_version(state, v1)
    grade = ResumeGrade(grade=9.5)
    state = with_latest_grade(state, grade)

    assert not state.is_passing  # no verification yet

    state = with_verification(state, Verification(verified=True))
    assert state.is_passing


def test_is_passing_fails_with_low_grade():
    state = JobState(slug="test")
    state = with_latest_grade(state, ResumeGrade(grade=7.0))
    state = with_verification(state, Verification(verified=True))
    assert not state.is_passing


def test_is_passing_fails_with_unverified():
    state = JobState(slug="test")
    state = with_latest_grade(state, ResumeGrade(grade=9.5))
    state = with_verification(state, Verification(verified=False))
    assert not state.is_passing


def test_has_core_gaps_true():
    state = JobState(slug="test")
    grade = ResumeGrade(
        grade=7.0,
        per_criterion=[
            Criterion(requirement="Leadership", tier="core", assessment=Assessment.GAP),
        ],
    )
    state = with_latest_grade(state, grade)
    assert state.has_core_gaps


def test_has_core_gaps_false_no_gaps():
    state = JobState(slug="test")
    grade = ResumeGrade(
        grade=9.5,
        per_criterion=[
            Criterion(requirement="Leadership", tier="core", assessment=Assessment.DIRECT_HIT),
        ],
    )
    state = with_latest_grade(state, grade)
    assert not state.has_core_gaps


def test_has_core_gaps_false_no_grade():
    state = JobState(slug="test")
    assert not state.has_core_gaps


def test_has_core_gaps_ignores_non_core_tier():
    state = JobState(slug="test")
    grade = ResumeGrade(
        grade=7.0,
        per_criterion=[
            Criterion(requirement="Bonus", tier="nice-to-have", assessment=Assessment.GAP),
        ],
    )
    state = with_latest_grade(state, grade)
    assert not state.has_core_gaps


# ─── version_history / evaluated_versions / select_best_passing (ADR-0018) ──


def test_evaluated_versions_empty_without_history_or_grade():
    state = JobState(slug="test")
    assert state.evaluated_versions == []


def test_evaluated_versions_falls_back_to_current_state():
    """Pre-ADR-0018 states have no version_history — treat latest as v1."""
    state = JobState(slug="test")
    state = with_resume_version(
        state, ResumeVersion(version=1, path=Path("drafts/test/resume-v1.md"))
    )
    state = with_latest_grade(state, ResumeGrade(grade=9.5))
    state = with_verification(state, Verification(verified=True))

    versions = state.evaluated_versions
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].grade == 9.5
    assert versions[0].verified is True


def test_evaluated_versions_prefers_history():
    state = JobState(
        slug="test",
        version_history=[
            ResumeVersion(version=1, path=Path("a"), grade=8.0, verified=True),
            ResumeVersion(version=2, path=Path("b"), grade=9.5, verified=True),
        ],
    )
    assert [v.version for v in state.evaluated_versions] == [1, 2]


def test_select_best_passing_picks_highest_grade():
    versions = [
        ResumeVersion(version=1, path=Path("a"), grade=9.2, verified=True),
        ResumeVersion(version=2, path=Path("b"), grade=9.8, verified=True),
        ResumeVersion(version=3, path=Path("c"), grade=10.0, verified=False),
    ]
    best = select_best_passing(versions, 9.0)
    assert best is not None and best.version == 2


def test_select_best_passing_tie_prefers_earlier():
    versions = [
        ResumeVersion(version=1, path=Path("a"), grade=9.5, verified=True),
        ResumeVersion(version=2, path=Path("b"), grade=9.5, verified=True),
    ]
    best = select_best_passing(versions, 9.0)
    assert best is not None and best.version == 1


def test_select_best_passing_none_when_nothing_passes():
    versions = [
        ResumeVersion(version=1, path=Path("a"), grade=8.5, verified=True),
        ResumeVersion(version=2, path=Path("b"), grade=9.5, verified=False),
        ResumeVersion(version=3, path=Path("c"), grade=None, verified=None),
    ]
    assert select_best_passing(versions, 9.0) is None


def test_select_best_passing_empty_history():
    assert select_best_passing([], 9.0) is None


def test_passing_versions_orders_best_first():
    """The final-gate cascade walks candidates highest-grade-first."""
    versions = [
        ResumeVersion(version=1, path=Path("a"), grade=9.2, verified=True),
        ResumeVersion(version=2, path=Path("b"), grade=9.8, verified=True),
        ResumeVersion(version=3, path=Path("c"), grade=10.0, verified=False),
        ResumeVersion(version=4, path=Path("d"), grade=9.8, verified=True),
    ]
    ordered = passing_versions(versions, 9.0)
    assert [v.version for v in ordered] == [2, 4, 1]


def test_passing_versions_excludes_non_passing():
    versions = [
        ResumeVersion(version=1, path=Path("a"), grade=8.5, verified=True),
        ResumeVersion(version=2, path=Path("b"), grade=9.5, verified=False),
        ResumeVersion(version=3, path=Path("c"), grade=9.5, verified=None),
    ]
    assert passing_versions(versions, 9.0) == []


# ─── Validation ────────────────────────────────────────────────────────────


def test_enum_validation_rejects_invalid_assessment():
    with pytest.raises(Exception):
        Criterion(requirement="X", tier="core", assessment="INVALID")


def test_jd_grade_constraint_rejects_above_10():
    with pytest.raises(Exception):
        JdGrade(grade=11)


def test_jd_grade_constraint_rejects_below_0():
    with pytest.raises(Exception):
        JdGrade(grade=-1)


def test_resume_version_requires_positive_version():
    with pytest.raises(Exception):
        ResumeVersion(version=0, path=Path("test.md"))


# ─── build_job_state_from_filesystem ───────────────────────────────────────


def test_build_state_from_filesystem_nonexistent(tmp_paths):
    state = build_job_state_from_filesystem("nonexistent-slug", tmp_paths)
    assert state.slug == "nonexistent-slug"
    assert state.jd_text == ""
    assert state.resume_versions == []


def test_build_state_from_filesystem_with_files(tmp_paths):
    slug = "test-co-role"
    job_dir = tmp_paths.listings / slug
    job_dir.mkdir(parents=True)
    jd_path = job_dir / "[8.5] job-description.md"
    jd_path.write_text("<!-- url: https://linkedin.com/jobs/123 -->\n\nWe are looking for...")
    resume_path = job_dir / "[9.0] resume-v1.md"
    resume_path.write_text("# Resume content")

    state = build_job_state_from_filesystem(slug, tmp_paths)
    assert state.slug == slug
    assert state.url == "https://linkedin.com/jobs/123"
    assert "We are looking for" in state.jd_text
    assert state.jd_grade is not None
    assert state.jd_grade.grade == 8.5
    assert len(state.resume_versions) == 1
    assert state.resume_versions[0].version == 1
    assert state.latest_grade is not None
    assert state.latest_grade.grade == 9.0
