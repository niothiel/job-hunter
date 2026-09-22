"""Candidate-specific search policy shared by filtering and grading prompts."""

from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent.parent / "_config" / "search-preferences.json"


def load_search_preferences() -> dict:
    with open(_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def search_policy_prompt() -> str:
    p = load_search_preferences()
    return (
        "Candidate search policy (hard constraints unless labeled preference):\n"
        "- Pure individual-contributor track; reject people-management roles.\n"
        "- Prefer Staff, Senior Staff, and Principal; retain strong Senior or equivalent IC roles.\n"
        "- Optimize first for mission/domain fit in application security, SCA, developer security, vulnerability remediation, backend/platform systems, developer tooling, distributed systems, workflow automation, and agentic AI.\n"
        "- Eligible arrangements are remote and hybrid only. Remote roles must explicitly permit working from Virginia; a generic remote label is insufficient when the posting restricts states or regions.\n"
        "- Hybrid roles must be in Northern Virginia or Washington, DC. Maryland is not eligible. The candidate will not relocate.\n"
        "- Reject roles requiring an active clearance, eligibility to obtain a clearance, or clearance sponsorship. Do not reject a role saying no clearance is required.\n"
        f"- Minimum annual total compensation is ${p['minimum_total_compensation_usd']:,}. Reject only when maximum attainable total compensation is known to be below the floor. Base salary alone is not total compensation; keep unknown or ambiguous compensation eligible and flag it."
    )


def is_hard_constraint_violation(output: str | None) -> bool:
    """Recognize the grader's sentinel without matching words inside JSON."""
    normalized = (output or "").lstrip().upper()
    return normalized.startswith(("INELIGIBLE", "CLEARANCE"))


SEARCH_POLICY_PROMPT = search_policy_prompt()
