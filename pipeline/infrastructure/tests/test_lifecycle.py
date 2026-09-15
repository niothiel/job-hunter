"""Tests for pipeline.lifecycle."""
import logging
import logging.handlers
import os
import time

from pipeline.infrastructure.lifecycle import (
    _LOG_RETENTION_DAYS,
    acquire_lock,
    check_scraper_updates,
    cleanup_transient_dirs,
    git_commit_and_push,
    load_state,
    release_lock,
    save_state,
    setup_logging,
)


def test_setup_logging_creates_logs_dir(tmp_paths):
    setup_logging(tmp_paths)
    assert tmp_paths.logs.exists()


def test_setup_logging_uses_timed_rotating_handler(tmp_paths):
    """File handler should be a TimedRotatingFileHandler with daily rotation + 30-day retention (ADR-0014)."""
    setup_logging(tmp_paths)
    root = logging.getLogger("pipeline")
    file_handlers = [
        h for h in root.handlers
        if isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    assert len(file_handlers) == 1, "expected one TimedRotatingFileHandler"
    fh = file_handlers[0]
    assert fh.when.upper() == "MIDNIGHT"
    assert fh.backupCount == 30
    # Clean up handlers so they don't leak between tests
    root.handlers.clear()


def test_setup_logging_writes_json_to_file(tmp_paths):
    """Log output should be JSON lines in the file (machine-parseable)."""
    import json
    log = setup_logging(tmp_paths)
    log.info("test_event", key="value")
    # Flush handlers
    for h in logging.getLogger("pipeline").handlers:
        h.flush()
    log_file = tmp_paths.logs / "pipeline.log"
    assert log_file.exists()
    lines = log_file.read_text().strip().splitlines()
    # The last line should be our test event (or close to it)
    parsed = json.loads(lines[-1])
    assert parsed["event"] == "test_event"
    assert parsed["key"] == "value"
    # Clean up
    logging.getLogger("pipeline").handlers.clear()


def test_lock_acquire_and_release(tmp_paths, logger):
    assert acquire_lock(tmp_paths, logger) is True
    assert tmp_paths.lock_file.exists()
    # Second acquire fails (our PID is alive)
    assert acquire_lock(tmp_paths, logger) is False
    release_lock(tmp_paths, logger)
    assert not tmp_paths.lock_file.exists()


def test_stale_lock_removed(tmp_paths, logger):
    tmp_paths.devin_dir.mkdir(parents=True)
    tmp_paths.lock_file.write_text("999999")  # dead PID
    assert acquire_lock(tmp_paths, logger) is True
    assert tmp_paths.lock_file.read_text() == str(os.getpid())
    release_lock(tmp_paths, logger)


def test_corrupt_lock_removed(tmp_paths, logger):
    tmp_paths.devin_dir.mkdir(parents=True)
    tmp_paths.lock_file.write_text("not-a-pid")
    assert acquire_lock(tmp_paths, logger) is True
    release_lock(tmp_paths, logger)


def test_state_load_default(tmp_paths):
    state = load_state(tmp_paths)
    assert state["last_scraper_sha"] is None


def test_state_save_and_reload(tmp_paths, logger):
    state = {"last_scraper_sha": "abc123", "last_run": "2026-01-01T00:00:00Z"}
    save_state(state, tmp_paths, logger)
    state2 = load_state(tmp_paths)
    assert state2["last_scraper_sha"] == "abc123"
    assert state2["last_run"] == "2026-01-01T00:00:00Z"


def test_cleanup_transient_dirs(tmp_paths, logger):
    tmp_paths.grading.mkdir(parents=True)
    (tmp_paths.grading / "test.json").write_text("{}")
    tmp_paths.veracity.mkdir(parents=True)
    (tmp_paths.veracity / "test.json").write_text("{}")
    cleanup_transient_dirs(tmp_paths, logger)
    assert not tmp_paths.grading.exists()
    assert not tmp_paths.veracity.exists()


def test_cleanup_purges_old_logs(tmp_paths, logger):
    """Log files older than 30 days are purged by cleanup_transient_dirs (ADR-0014)."""
    tmp_paths.logs.mkdir(parents=True, exist_ok=True)
    # Create an old log file (31 days ago)
    old_file = tmp_paths.logs / "pipeline-2020-01-01-0000.log"
    old_file.write_text('{"event": "old"}')
    old_time = time.time() - (_LOG_RETENTION_DAYS + 1) * 86400
    os.utime(old_file, (old_time, old_time))
    # Create a recent log file (1 day ago)
    recent_file = tmp_paths.logs / "pipeline-recent.log"
    recent_file.write_text('{"event": "recent"}')
    recent_time = time.time() - 86400
    os.utime(recent_file, (recent_time, recent_time))

    cleanup_transient_dirs(tmp_paths, logger)

    assert not old_file.exists(), "old log file should have been purged"
    assert recent_file.exists(), "recent log file should have been kept"


def test_cleanup_keeps_logs_when_none_old(tmp_paths, logger):
    """No log files are purged when all are within the retention window."""
    tmp_paths.logs.mkdir(parents=True, exist_ok=True)
    fresh = tmp_paths.logs / "pipeline.log"
    fresh.write_text('{"event": "fresh"}')
    cleanup_transient_dirs(tmp_paths, logger)
    assert fresh.exists()


def test_git_commit_push_disabled(tmp_paths, logger):
    """git_push=False should be a no-op."""
    git_commit_and_push({"processed": 0, "ready": 0}, tmp_paths, logger, git_push=False)


def test_check_scraper_updates_no_scraper(tmp_paths, logger):
    """No scraper dir → get_scraper_sha returns None → proceed anyway."""
    state = {"last_scraper_sha": None}
    assert check_scraper_updates(state, tmp_paths, logger) is True
