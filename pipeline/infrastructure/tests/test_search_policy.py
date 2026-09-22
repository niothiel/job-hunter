from pipeline.infrastructure.search_policy import (
    SEARCH_POLICY_PROMPT,
    is_hard_constraint_violation,
    load_search_preferences,
)


def test_search_preferences_capture_candidate_constraints():
    p = load_search_preferences()
    assert p["employment_track"] == "individual_contributor"
    assert p["hybrid_locations"] == ["Northern Virginia", "Washington, DC"]
    assert p["excluded_hybrid_locations"] == ["Maryland"]
    assert p["relocation"] is False
    assert p["minimum_total_compensation_usd"] == 300_000


def test_search_policy_prompt_is_unambiguous():
    assert "working from Virginia" in SEARCH_POLICY_PROMPT
    assert "Maryland is not eligible" in SEARCH_POLICY_PROMPT
    assert "will not relocate" in SEARCH_POLICY_PROMPT
    assert "Pure individual-contributor" in SEARCH_POLICY_PROMPT
    assert "Base salary alone is not total compensation" in SEARCH_POLICY_PROMPT
    assert "no clearance is required" in SEARCH_POLICY_PROMPT


def test_hard_constraint_guard_only_accepts_leading_sentinel():
    assert is_hard_constraint_violation("INELIGIBLE: hybrid role in Maryland")
    assert is_hard_constraint_violation("CLEARANCE")
    assert not is_hard_constraint_violation(
        '{"per_criterion": [{"comment": "No clearance is required."}]}'
    )
