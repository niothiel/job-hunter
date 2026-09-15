"""Unit tests for pipeline.infrastructure.scoring.compute_grade.

The deterministic formula replaces LLM Call 2 — these tests pin the exact
arithmetic from _config/grading-scoring-protocol.md so prompt/protocol
drift can't silently change scores.
"""
from pipeline.infrastructure.scoring import compute_grade
from pipeline.infrastructure.state import Criterion


def _crit(tier, assessment, req="x"):
    return {"requirement": req, "tier": tier, "assessment": assessment, "comment": ""}


def test_all_direct_hits_scores_10():
    out = compute_grade([_crit("core", "DIRECT_HIT"), _crit("core", "DIRECT_HIT")])
    assert out["grade"] == 10.0
    assert out["ceiling"] == 10.0
    assert out["subjective_adjustment"] == 0.3


def test_core_gap_floors_at_zero():
    out = compute_grade([_crit("core", "GAP")])
    # core_score 0 -> adjustment -1.0 -> floored at 0
    assert out["grade"] == 0.0
    assert out["ceiling"] == 8.0
    assert out["subjective_adjustment"] == -1.0


def test_single_addressed():
    out = compute_grade([_crit("core", "ADDRESSED")])
    # core_score 5.0, no GAPs -> +0.3
    assert out["grade"] == 5.3


def test_job_fit_band():
    """1 HIT + 2 ADDRESSED -> 6.97 — the [6, 7.0) rejected_job_fit band."""
    out = compute_grade([
        _crit("core", "DIRECT_HIT"),
        _crit("core", "ADDRESSED"),
        _crit("core", "ADDRESSED"),
    ])
    assert out["grade"] == 6.97


def test_core_partial_ceiling_and_adjustment():
    out = compute_grade([_crit("core", "PARTIAL")])
    # core_score 2.5 -> -0.5 -> 2.0; ceiling 9.0
    assert out["ceiling"] == 9.0
    assert out["grade"] == 2.0
    assert out["subjective_adjustment"] == -0.5


def test_preferred_gap_ceiling():
    out = compute_grade([
        _crit("core", "DIRECT_HIT"),
        _crit("preferred", "GAP"),
    ])
    # ceiling 9.5, adjustment -0.2 -> 9.3
    assert out["ceiling"] == 9.5
    assert out["grade"] == 9.3


def test_preferred_bonus_capped_at_one():
    out = compute_grade([
        _crit("core", "DIRECT_HIT"),
        *[_crit("preferred", "DIRECT_HIT") for _ in range(10)],
    ])
    assert out["preferred_bonus"] == 1.0
    assert out["grade"] == 10.0  # still capped at ceiling


def test_mixed_core_gap_pulls_down():
    out = compute_grade([_crit("core", "DIRECT_HIT"), _crit("core", "GAP")])
    # core_score 5.0, ceiling 8.0, adjustment -1.0 -> 4.0
    assert out["ceiling"] == 8.0
    assert out["grade"] == 4.0


def test_empty_criteria_scores_zero():
    # Empty core -> core_score 0; no GAPs -> +0.3 polish -> 0.3 (trash either way).
    out = compute_grade([])
    assert out["grade"] == 0.3
    assert out["ceiling"] == 10.0


def test_preferred_only_falls_back_to_core():
    # Zero core criteria → compute_grade treats preferred as core (fallback).
    # A DIRECT_HIT as core → core_score 10.0, no GAPs → +0.3 → 10.0.
    out = compute_grade([_crit("preferred", "DIRECT_HIT")])
    assert out["grade"] == 10.0
    assert out["ceiling"] == 10.0


def test_accepts_criterion_models():
    out = compute_grade([
        Criterion(requirement="Python", tier="core",
                  assessment="DIRECT_HIT", comment="x"),
    ])
    assert out["grade"] == 10.0


def test_per_criterion_passed_through():
    crits = [_crit("core", "DIRECT_HIT", req="Python")]
    out = compute_grade(crits)
    assert out["per_criterion"] == crits


def test_output_shape():
    out = compute_grade([_crit("core", "DIRECT_HIT")])
    for key in ("grade", "ceiling", "core_score", "preferred_bonus",
                "quantitative_base", "subjective_adjustment",
                "subjective_justification", "per_criterion"):
        assert key in out
