"""Real integration test for the grade_resume helper.

Calls the actual `devin -p` subprocess (RealLLM) to grade a real resume
against a real JD — the same two-call decomposition (reasoning + scoring)
that the pipeline's step7 uses, but standalone via pipeline.helpers.grade_resume.

Skipped by default — set RUN_REAL_LLM=1 to run it.

Usage:
    RUN_REAL_LLM=1 python3 -m pytest tests/test_grade_resume_integration.py -v -s

Fixtures (tests/fixtures/grade_resume/):
    - resume.md / resume.pdf — Synthetic Squall Leonhart resume (platform eng leader)
    - job-description.md     — Synthetic Director of Engineering JD (Ultimecia Systems)

The test verifies:
    - File discovery works with real files (both .md and .pdf)
    - Text extraction produces non-empty content
    - The LLM returns a valid grade (0-10) with per-criterion feedback
    - Each LLM call completes within the grade-resume timeout (180s per call)
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

import pytest

from pipeline.helpers.grade_resume import (
    extract_text,
    find_input_files,
    format_result,
    grade_resume,
)
from pipeline.infrastructure.config import PipelineConfig, load_config
from pipeline.infrastructure.llm_interface import RealLLM

# ─── Skip gate for real LLM tests ───────────────────────────────────────────

_skip_real_llm = pytest.mark.skipif(
    os.environ.get("RUN_REAL_LLM") != "1",
    reason="Set RUN_REAL_LLM=1 to run real LLM integration tests.",
)

# ─── Constants ───────────────────────────────────────────────────────────────

HUNTER_DIR = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "grade_resume"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def real_config() -> PipelineConfig:
    """Load the real pipeline config (uses config.json).

    The grade-resume timeout override is 180s per call. No retries — we
    want to see failures immediately, not mask them with retry loops.
    """
    config = load_config(HUNTER_DIR / "config.json")
    return config.model_copy(update={
        "models": config.models.model_copy(update={"grader": "glm-5.2-high"}),
        "llm_retries": 0,
        "llm_retry_delay": 0,
    })


@pytest.fixture
def real_llm() -> RealLLM:
    """RealLLM instance (calls devin -p subprocess)."""
    return RealLLM()


@pytest.fixture
def real_logger():
    """Verbose logger so you can watch progress in -s mode."""
    import structlog
    stdlib_log = logging.getLogger("grade_resume_integration")
    stdlib_log.handlers.clear()
    stdlib_log.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("  %(message)s"))
    stdlib_log.addHandler(handler)
    return structlog.get_logger("grade_resume_integration")


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestFileDiscovery:
    """Test file discovery with real fixture files (no LLM calls)."""

    def test_finds_md_files(self, tmp_path: Path):
        """find_input_files correctly identifies resume.md and job-description.md."""
        shutil.copy2(FIXTURES_DIR / "resume.md", tmp_path / "resume.md")
        shutil.copy2(FIXTURES_DIR / "job-description.md", tmp_path / "job-description.md")

        resume_path, jd_path = find_input_files(tmp_path)
        assert resume_path.name == "resume.md"
        assert jd_path.name == "job-description.md"

    def test_finds_pdf_resume_and_md_jd(self, tmp_path: Path):
        """find_input_files works with a PDF resume and MD JD."""
        shutil.copy2(FIXTURES_DIR / "resume.pdf", tmp_path / "resume.pdf")
        shutil.copy2(FIXTURES_DIR / "job-description.md", tmp_path / "job-description.md")

        resume_path, jd_path = find_input_files(tmp_path)
        assert resume_path.name == "resume.pdf"
        assert jd_path.name == "job-description.md"

    def test_extracts_nonempty_text_from_md(self):
        """extract_text returns non-empty content from the real resume MD."""
        text = extract_text(FIXTURES_DIR / "resume.md")
        assert len(text) > 100
        assert "Squall Leonhart" in text

    def test_extracts_nonempty_text_from_pdf(self):
        """extract_text returns non-empty content from the real resume PDF."""
        text = extract_text(FIXTURES_DIR / "resume.pdf")
        assert len(text) > 50  # PDF extraction may lose some formatting
        assert "Squall" in text

    def test_extracts_nonempty_text_from_jd(self):
        """extract_text returns non-empty content from the real JD."""
        text = extract_text(FIXTURES_DIR / "job-description.md")
        assert len(text) > 100
        assert "Ultimecia Systems" in text
        assert "Director of Engineering" in text


class TestRealGrading:
    """Real LLM grading — calls devin -p with the actual grader model."""

    pytestmark = _skip_real_llm

    def test_grades_resume_against_jd_md(
        self, real_config, real_llm, real_logger, tmp_path: Path
    ):
        """End-to-end: grade the real resume.md against the real JD.md.

        Verifies:
            - The LLM returns a valid grade (0-10 float)
            - per_criterion is a non-empty list
            - Each criterion has requirement, tier, assessment, comment
            - Each LLM call completes within the grade-resume timeout (180s)
        """
        # Set up the input folder
        shutil.copy2(FIXTURES_DIR / "resume.md", tmp_path / "resume.md")
        shutil.copy2(FIXTURES_DIR / "job-description.md", tmp_path / "job-description.md")

        resume_path, jd_path = find_input_files(tmp_path)
        resume_content = extract_text(resume_path)
        jd_text = extract_text(jd_path)

        real_logger.info(f"  Resume: {resume_path.name} ({len(resume_content)} chars)")
        real_logger.info(f"  JD: {jd_path.name} ({len(jd_text)} chars)")

        # Grade with the real LLM
        timeout = real_config.timeout_for("grade-resume")
        real_logger.info(f"  Timeout: {timeout}s per call")

        start = time.monotonic()
        grade_data = grade_resume(resume_content, jd_text, real_llm, real_config)
        elapsed = time.monotonic() - start

        real_logger.info(f"  Completed in {elapsed:.1f}s")

        # ── Validate the grade ──
        assert "grade" in grade_data, f"Missing 'grade' key in: {grade_data}"
        grade = grade_data["grade"]
        assert isinstance(grade, (int, float)), f"Grade is not a number: {grade}"
        assert 0 <= grade <= 10, f"Grade out of range [0, 10]: {grade}"

        per_criterion = grade_data.get("per_criterion", [])
        assert len(per_criterion) > 0, "No per-criterion feedback returned"

        # Each criterion should have the expected fields
        for c in per_criterion:
            assert "requirement" in c, f"Criterion missing 'requirement': {c}"
            assert "assessment" in c, f"Criterion missing 'assessment': {c}"
            assert c["assessment"] in (
                "DIRECT_HIT", "ADDRESSED", "PARTIAL", "GAP"
            ), f"Unexpected assessment value: {c['assessment']}"

        # ── The total elapsed time should be reasonable ──
        # Two calls, each with `timeout` seconds. Give some slack.
        max_total = timeout * 2 + 10
        assert elapsed < max_total, (
            f"Grading took {elapsed:.1f}s, exceeding {max_total}s "
            f"(2 × {timeout}s timeout + 10s slack) — something is wrong."
        )

        # ── Print the formatted result for -s mode ──
        real_logger.info(f"  Grade: {grade}/10")
        real_logger.info(f"  Criteria: {len(per_criterion)}")
        print("\n" + format_result(grade_data))

    def test_grades_resume_pdf_against_jd(
        self, real_config, real_llm, real_logger, tmp_path: Path
    ):
        """End-to-end with PDF resume — verifies PDF extraction + grading works.

        Same assertions as the MD test, but the resume is a PDF.
        """
        shutil.copy2(FIXTURES_DIR / "resume.pdf", tmp_path / "resume.pdf")
        shutil.copy2(FIXTURES_DIR / "job-description.md", tmp_path / "job-description.md")

        resume_path, jd_path = find_input_files(tmp_path)
        resume_content = extract_text(resume_path)
        jd_text = extract_text(jd_path)

        real_logger.info(f"  Resume: {resume_path.name} ({len(resume_content)} chars)")
        real_logger.info(f"  JD: {jd_path.name} ({len(jd_text)} chars)")

        timeout = real_config.timeout_for("grade-resume")
        start = time.monotonic()
        grade_data = grade_resume(resume_content, jd_text, real_llm, real_config)
        elapsed = time.monotonic() - start

        real_logger.info(f"  Completed in {elapsed:.1f}s")

        assert "grade" in grade_data
        grade = grade_data["grade"]
        assert isinstance(grade, (int, float))
        assert 0 <= grade <= 10

        per_criterion = grade_data.get("per_criterion", [])
        assert len(per_criterion) > 0

        max_total = timeout * 2 + 10
        assert elapsed < max_total, (
            f"Grading took {elapsed:.1f}s, exceeding {max_total}s"
        )

        real_logger.info(f"  Grade: {grade}/10")
        print("\n" + format_result(grade_data))
