"""Unit tests for the variance harness.

Tests the harness logic (JD selection, stats, report generation, dry-run
end-to-end) without real LLM calls. Real-LLM variance testing is done by
running the harness directly, not via pytest.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.helpers.variance_harness import (
    RunResult,
    _extract_grade_from_filename,
    _stats,
    build_report,
    pick_jds_across_bands,
    write_markdown_report,
)


# ─── Filename grade extraction ───────────────────────────────────────────────


class TestExtractGrade:
    def test_valid_grade(self):
        assert _extract_grade_from_filename("[8.5] job-description.md") == 8.5

    def test_integer_grade(self):
        assert _extract_grade_from_filename("[7] job-description.md") == 7.0

    def test_no_prefix(self):
        assert _extract_grade_from_filename("job-description.md") is None

    def test_tbd(self):
        assert _extract_grade_from_filename("[TBD] job-description.md") is None

    def test_non_numeric(self):
        assert _extract_grade_from_filename("[CLEARANCE] job-description.md") is None


# ─── JD selection across grade bands ────────────────────────────────────────


class TestPickJDsAcrossBands:
    def test_picks_from_multiple_bands(self, tmp_path):
        listings = tmp_path / "stages" / "1_listings"
        listings.mkdir(parents=True)
        # Create JDs in different grade bands
        for slug, grade in [
            ("trash-1", 2.0), ("trash-2", 4.5),
            ("jobfit-1", 6.5), ("jobfit-2", 7.5),
            ("proceed-1", 8.0), ("proceed-2", 8.5),
            ("strong-1", 9.0), ("strong-2", 9.5),
        ]:
            job_dir = listings / slug
            job_dir.mkdir()
            (job_dir / f"[{grade}] job-description.md").write_text("JD text")

        picked = pick_jds_across_bands(listings, n=8)
        assert len(picked) <= 8
        assert len(picked) >= 4  # at least one per band

        # Verify grades span multiple bands
        grades = [g for _, g, _ in picked]
        assert min(grades) < 6.0  # has a trash
        assert max(grades) >= 9.0  # has a strong

    def test_empty_listings(self, tmp_path):
        listings = tmp_path / "stages" / "1_listings"
        listings.mkdir(parents=True)
        picked = pick_jds_across_bands(listings, n=8)
        assert picked == []

    def test_skips_ungraded(self, tmp_path):
        listings = tmp_path / "stages" / "1_listings"
        listings.mkdir(parents=True)
        job_dir = listings / "ungraded-job"
        job_dir.mkdir()
        (job_dir / "[TBD] job-description.md").write_text("JD")
        picked = pick_jds_across_bands(listings, n=8)
        assert picked == []


# ─── Stats computation ──────────────────────────────────────────────────────


class TestStats:
    def test_basic_stats(self):
        s = _stats([7.0, 8.0, 9.0])
        assert s["mean"] == 8.0
        assert s["stdev"] == 1.0
        assert s["min"] == 7.0
        assert s["max"] == 9.0
        assert s["n"] == 3

    def test_single_value(self):
        s = _stats([7.5])
        assert s["mean"] == 7.5
        assert s["stdev"] == 0.0  # can't compute stdev with 1 value
        assert s["n"] == 1

    def test_empty(self):
        s = _stats([])
        assert s["mean"] == 0
        assert s["n"] == 0


# ─── Report generation ──────────────────────────────────────────────────────


class TestReport:
    def test_build_report_with_results(self):
        results = [
            RunResult("acme-1", "deterministic", 0, grade=8.0, is_clearance=False, justification="good"),
            RunResult("acme-1", "deterministic", 1, grade=8.5, is_clearance=False, justification="good"),
            RunResult("acme-1", "legacy", 0, grade=7.5, is_clearance=False, justification="ok"),
            RunResult("acme-1", "legacy", 1, grade=8.0, is_clearance=False, justification="ok"),
            RunResult("acme-2", "deterministic", 0, grade=3.0, is_clearance=False, justification="bad"),
            RunResult("acme-2", "legacy", 0, grade=2.5, is_clearance=False, justification="bad"),
        ]
        known = {"acme-1": 8.0, "acme-2": 3.0}
        report = build_report(results, known, "test")

        assert len(report["items"]) == 2
        item1 = report["items"][0]
        assert item1["slug"] == "acme-1"
        assert item1["known_grade"] == 8.0
        assert item1["deterministic"]["stats"]["mean"] == 8.25
        assert item1["legacy"]["stats"]["mean"] == 7.75
        assert item1["delta_mean"] == 0.5

    def test_build_report_with_errors(self):
        results = [
            RunResult("acme-1", "deterministic", 0, grade=-1, is_clearance=False, justification="", error="LLM failed"),
            RunResult("acme-1", "legacy", 0, grade=7.5, is_clearance=False, justification="ok"),
        ]
        report = build_report(results, {}, "test")
        item = report["items"][0]
        assert len(item["deterministic"]["errors"]) == 1
        assert "LLM failed" in item["deterministic"]["errors"][0]
        assert item["deterministic"]["stats"]["n"] == 0
        assert item["legacy"]["stats"]["n"] == 1

    def test_markdown_report(self, tmp_path):
        results = [
            RunResult("acme-1", "deterministic", 0, grade=8.0, is_clearance=False, justification="good"),
            RunResult("acme-1", "legacy", 0, grade=7.5, is_clearance=False, justification="ok"),
        ]
        report = build_report(results, {"acme-1": 8.0}, "test")
        md_path = tmp_path / "report.md"
        write_markdown_report(report, md_path)
        md = md_path.read_text()
        assert "Variance Harness Report" in md
        assert "acme-1" in md
        assert "Det mean" in md
        assert "8.0" in md

    def test_json_report_serializable(self):
        results = [
            RunResult("acme-1", "deterministic", 0, grade=8.0, is_clearance=False, justification="good"),
        ]
        report = build_report(results, {"acme-1": 8.0}, "test")
        # Must be JSON-serializable (the harness writes it with json.dumps)
        text = json.dumps(report, default=str, indent=2)
        assert "acme-1" in text


# ─── Dry-run end-to-end ──────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_produces_report(self, tmp_path, monkeypatch):
        """Run the harness in dry-run mode and verify it produces reports."""
        from pipeline.helpers.variance_harness import main

        # Create a minimal hunter dir with listings
        hunter_dir = tmp_path / "hunter"
        listings = hunter_dir / "stages" / "1_listings"
        listings.mkdir(parents=True)
        job_dir = listings / "test-job"
        job_dir.mkdir()
        (job_dir / "[8.0] job-description.md").write_text("A test job description")

        # Create profile dirs (needed by Paths)
        profile = hunter_dir / "_config" / "profile"
        (profile / "base-resume").mkdir(parents=True)
        (profile / "full-experience").mkdir(parents=True)
        (profile / "base-resume" / "base-resume.md").write_text("Base resume")
        (profile / "full-experience" / "full-experience.md").write_text("LinkedIn")

        output_dir = tmp_path / "output"
        exit_code = main([
            "--dry-run", "--sample", "1", "--runs", "1",
            "--output-dir", str(output_dir),
            "--hunter-dir", str(hunter_dir),
        ])

        assert exit_code == 0
        assert (output_dir / "variance-report.json").exists()
        assert (output_dir / "variance-report.md").exists()

        report = json.loads((output_dir / "variance-report.json").read_text())
        assert len(report["items"]) == 1
        item = report["items"][0]
        assert item["deterministic"]["stats"]["n"] == 1
        assert item["legacy"]["stats"]["n"] == 1
        # Deterministic mode computes from the formula, legacy returns the fake score
        assert item["deterministic"]["stats"]["mean"] != item["legacy"]["stats"]["mean"]
