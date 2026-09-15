"""Deterministic grading formula — replaces LLM Call 2 (scoring).

The two-call grading decomposition (ADR-0011) asked the LLM to do two jobs:
Call 1 judged each requirement (DIRECT_HIT/ADDRESSED/PARTIAL/GAP), Call 2
applied a fixed arithmetic formula to those verdicts. Call 2 was pure
computation — an LLM doing arithmetic adds only error and nondeterminism
(ADR-0015's out-of-range 10.3; see docs/research/llm-grading-variance.md §1).
The formula is now implemented here, in code, and is identical for JD grading
(step4) and resume grading (step7).

Formula (from _config/grading-scoring-protocol.md — now the spec, not a prompt):

    Ceiling (first match, hard cap):
        any core GAP -> 8.0 | any core PARTIAL -> 9.0 |
        any preferred GAP -> 9.5 | otherwise -> 10.0
    Core score       = (sum of core points / num core) * 10
                       core points: DIRECT_HIT=1.0 ADDRESSED=0.5 PARTIAL=0.25 GAP=0
    Preferred bonus  = sum of preferred points, capped at +1.0
                       preferred points: DIRECT_HIT=0.2 ADDRESSED=0.1 PARTIAL=0.05 GAP=0
    Quantitative base = min(core score + preferred bonus, ceiling)
    Adjustment (most severe only):
        core GAP -> -1.0 | core PARTIAL -> -0.5 | preferred GAP -> -0.2 |
        no GAPs -> +0.3
    Final = min(quantitative base + adjustment, ceiling), floored at 0.
"""
from __future__ import annotations

# Points per verdict — core criteria (graded against the requirement list).
CORE_POINTS = {
    "DIRECT_HIT": 1.0,
    "ADDRESSED": 0.5,
    "PARTIAL": 0.25,
    "GAP": 0.0,
}

# Points per verdict — preferred criteria (bonus pool, capped at +1.0).
PREFERRED_POINTS = {
    "DIRECT_HIT": 0.2,
    "ADDRESSED": 0.1,
    "PARTIAL": 0.05,
    "GAP": 0.0,
}


def _get(criterion, key: str, default=""):
    if isinstance(criterion, dict):
        return criterion.get(key, default)
    return getattr(criterion, key, default)


def _assessment(criterion: dict) -> str:
    """Normalized verdict string for a criterion dict."""
    a = _get(criterion, "assessment")
    return a.value if hasattr(a, "value") else str(a)


def _is_preferred(criterion: dict) -> bool:
    return str(_get(criterion, "tier", "core")).lower() == "preferred"


def compute_grade(per_criterion: list[dict]) -> dict:
    """Apply the deterministic scoring formula to Call-1 verdicts.

    Args:
        per_criterion: list of dicts (or Criterion-shaped objects) with
            requirement/tier/assessment/comment fields, straight from the
            reasoning call's parsed JSON.

    Returns the computed score fields — grade, ceiling, core_score,
    preferred_bonus, quantitative_base, subjective_adjustment,
    subjective_justification. The caller merges these with the
    pass-through fields (per_criterion, judgment_calls, justification).
    """
    core = [c for c in per_criterion if not _is_preferred(c)]
    preferred = [c for c in per_criterion if _is_preferred(c)]

    # If the LLM classified everything as "preferred" (zero core), treat
    # preferred as core. This happens when the LLM inconsistently assigns
    # tiers — the requirements are still real, just misclassified. Scoring
    # them as preferred-only produces a near-zero grade (core_score=0)
    # which is wrong: these are the job's actual requirements.
    if not core and preferred:
        core, preferred = preferred, []

    core_gaps = [c for c in core if _assessment(c) == "GAP"]
    core_partials = [c for c in core if _assessment(c) == "PARTIAL"]
    pref_gaps = [c for c in preferred if _assessment(c) == "GAP"]

    # Ceiling — first match wins.
    if core_gaps:
        ceiling = 8.0
    elif core_partials:
        ceiling = 9.0
    elif pref_gaps:
        ceiling = 9.5
    else:
        ceiling = 10.0

    # Core score — empty core list means Call 1 extracted nothing; a 0
    # tanks the grade into trash territory, which is the visible failure.
    core_score = (
        (sum(CORE_POINTS.get(_assessment(c), 0.0) for c in core) / len(core)) * 10
        if core
        else 0.0
    )

    preferred_bonus = min(
        1.0, sum(PREFERRED_POINTS.get(_assessment(c), 0.0) for c in preferred)
    )

    quantitative_base = min(core_score + preferred_bonus, ceiling)

    # Subjective adjustment — most severe verdict present, one only.
    if core_gaps:
        adjustment, adj_reason = -1.0, "core GAP present"
    elif core_partials:
        adjustment, adj_reason = -0.5, "core PARTIAL present"
    elif pref_gaps:
        adjustment, adj_reason = -0.2, "preferred GAP present"
    else:
        adjustment, adj_reason = 0.3, "no GAPs — polish bonus"

    grade = max(0.0, min(quantitative_base + adjustment, ceiling))
    grade = round(grade, 2)

    return {
        "grade": grade,
        "ceiling": ceiling,
        "core_score": round(core_score, 2),
        "preferred_bonus": round(preferred_bonus, 2),
        "quantitative_base": round(quantitative_base, 2),
        "subjective_adjustment": adjustment,
        "subjective_justification": f"{adj_reason} -> {adjustment:+.1f}",
        "per_criterion": per_criterion,
    }
