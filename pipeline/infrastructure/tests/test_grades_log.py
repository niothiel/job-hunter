"""Tests for grades_log.py — the append-only .grades.log audit trail."""
from pathlib import Path

import structlog

from pipeline.infrastructure.grades_log import append_grades_log, append_decision_log


def _logger():
    return structlog.get_logger("test")


# ─── append_grades_log ──────────────────────────────────────────────────────


def test_append_grades_log_basic(tmp_path):
    log = tmp_path / ".grades.log"
    criteria = [
        {"requirement": "Python", "tier": "core", "assessment": "DIRECT_HIT", "comment": "yes"},
        {"requirement": "Go", "tier": "preferred", "assessment": "GAP", "comment": "no"},
    ]
    append_grades_log("drafts/co/resume.md", 8.5, "glm-5.2-high", criteria, log, _logger())
    assert log.exists()
    line = log.read_text().strip()
    assert "grade=8.5" in line
    assert "model=glm-5.2-high" in line
    assert "hits=1" in line
    assert "gaps=1" in line
    assert "drafts/co/resume.md" in line


def test_append_grades_log_with_note(tmp_path):
    log = tmp_path / ".grades.log"
    criteria = []
    append_grades_log("drafts/co/resume.md", 7.0, "glm-5.2-high", criteria, log, _logger(),
                      note="Strong fit but missing Go.")
    line = log.read_text().strip()
    assert "note=Strong fit but missing Go." in line


def test_append_grades_log_collapses_newlines_in_note(tmp_path):
    log = tmp_path / ".grades.log"
    append_grades_log("path", 5.0, "model", [], log, _logger(),
                      note="line1\nline2\nline3")
    line = log.read_text().strip()
    assert "note=line1 line2 line3" in line
    assert "\nline2" not in line.split("note=")[1]


def test_append_grades_log_appends(tmp_path):
    log = tmp_path / ".grades.log"
    append_grades_log("a", 1.0, "m", [], log, _logger())
    append_grades_log("b", 2.0, "m", [], log, _logger())
    lines = log.read_text().strip().split("\n")
    assert len(lines) == 2
    assert "grade=1.0" in lines[0]
    assert "grade=2.0" in lines[1]


def test_append_grades_log_accepts_criterion_models(tmp_path):
    """Per-criterion can be dicts or Criterion model objects."""
    from pipeline.infrastructure.state import Criterion
    log = tmp_path / ".grades.log"
    criteria = [
        Criterion(requirement="Python", tier="core", assessment="DIRECT_HIT", comment="yes"),
        Criterion(requirement="Go", tier="preferred", assessment="GAP", comment="no"),
    ]
    append_grades_log("path", 8.0, "model", criteria, log, _logger())
    line = log.read_text().strip()
    assert "hits=1" in line
    assert "gaps=1" in line


# ─── append_decision_log ────────────────────────────────────────────────────


def test_append_decision_log_basic(tmp_path):
    log = tmp_path / ".grades.log"
    append_decision_log("acme-co", "trash", "JD grade 3.0 < 6.0 threshold", log, _logger())
    assert log.exists()
    line = log.read_text().strip()
    assert "decision" in line
    assert "acme-co" in line
    assert "trash" in line
    assert "reason=JD grade 3.0 < 6.0 threshold" in line


def test_append_decision_log_collapses_newlines(tmp_path):
    log = tmp_path / ".grades.log"
    append_decision_log("co", "rejected", "line1\nline2", log, _logger())
    line = log.read_text().strip()
    assert "reason=line1 line2" in line


def test_append_decision_log_appends(tmp_path):
    log = tmp_path / ".grades.log"
    append_decision_log("a", "trash", "r1", log, _logger())
    append_decision_log("b", "ready", "r2", log, _logger())
    lines = log.read_text().strip().split("\n")
    assert len(lines) == 2
    assert "trash" in lines[0]
    assert "ready" in lines[1]
