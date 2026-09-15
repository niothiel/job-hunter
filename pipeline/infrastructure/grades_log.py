"""Append-only .grades.log writer — the human-readable grading audit trail.

One line per grading or routing event, so the whole pipeline's decisions are
traceable in plain English after the run. Format:

    <timestamp> | <path-or-slug> | grade=<N> | model=<M> | hits=<h> gaps=<g> [| note=...]

`note` carries the LLM's justification / the routing reason — the "why"
behind the numbers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def append_grades_log(
    path: str,
    grade: float,
    model: str,
    per_criterion: list,
    grades_log: Path,
    logger,
    note: str = "",
) -> None:
    """Append a grading event to .grades.log (audit trail).

    hits = count of DIRECT_HIT verdicts, gaps = count of GAP verdicts.
    note = free-text justification/reason (newlines collapsed).
    """
    def _verdict(c) -> str:
        a = c["assessment"] if isinstance(c, dict) else c.assessment
        return getattr(a, "value", a)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hits = sum(1 for c in per_criterion if _verdict(c) == "DIRECT_HIT")
    gaps = sum(1 for c in per_criterion if _verdict(c) == "GAP")
    line = (
        f"{timestamp} | {path} | grade={grade} | model={model} "
        f"| hits={hits} gaps={gaps}"
    )
    if note:
        line += f" | note={note.replace(chr(10), ' ')}"
    with open(grades_log, "a") as f:
        f.write(line + "\n")
    logger.debug(f"Grades log appended: {line}")


def append_decision_log(
    slug: str,
    decision: str,
    reason: str,
    grades_log: Path,
    logger,
) -> None:
    """Append a routing decision to .grades.log.

    Format: <timestamp> | decision | <slug> | <decision> | reason=...
    reason = plain-English why (newlines collapsed).
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (
        f"{timestamp} | decision | {slug} | {decision} "
        f"| reason={reason.replace(chr(10), ' ')}"
    )
    with open(grades_log, "a") as f:
        f.write(line + "\n")
    logger.debug(f"Decision log appended: {line}")
