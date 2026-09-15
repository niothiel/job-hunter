# Plan: Observability, structured logging, and log rotation

**Status:** approved — see ADR-0014 for decision rationale.

Three sequential workstreams to address gjalla findings (structured logging,
log rotation) and add LLM observability. OSS first, then port to closed.

## Problem

1. **Structured logging** (gjalla PR #4): plain string formatter makes
   correlating logs to job slugs and LangGraph nodes difficult.
2. **Log rotation** (gjalla PR #4): per-run log files accumulate with no
   rotation or retention — unbounded disk usage.
3. **LLM observability**: no tracing of prompts, responses, token costs,
   or latencies across the pipeline.

## Solution

### 1. structlog migration (fixes #1)

- Replace stdlib `logging` with `structlog` across 23 files (145 touchpoints)
- Mechanical migration first: swap imports, configure JSON file output +
  pretty console output
- `bind(slug=slug, node=step_name)` in per-job loop for automatic correlation
- Convert f-string messages to structured events incrementally

### 2. Log rotation (fixes #2)

- `TimedRotatingFileHandler` (daily, 30-day retention) replaces per-run
  `FileHandler`
- Cleanup step in `cleanup_transient_dirs` purges old log files
- No new dependency (stdlib `logging.handlers`)

### 3. Arize Phoenix observability (new capability)

- Optional: `requirements-observability.txt`
- Auto-instrument LangGraph via `openinference-instrumentation-langchain`
- Manual span in `RealLLM.__call__` with `job_slug` + `step` attributes
- Env-var configured, guarded imports — no-op without Phoenix
- `session_id` = job slug for trace correlation

## Implementation Order

1. **structlog migration** — largest diff, fixes gjalla #1. One commit.
2. **Log rotation** — small follow-up, fixes gjalla #2. One commit.
3. **Phoenix observability** — new feature. One commit.
4. Port all three to closed repo.

## Acceptance Criteria

### structlog
- [ ] `structlog` in `requirements.txt`
- [ ] `setup_logging` configures JSON file handler + pretty console handler
- [ ] All 23 files migrated (imports, creation, type annotations)
- [ ] `bind(slug=slug, node=...)` in per-job loop
- [ ] JSON log output includes `slug` and `node` fields
- [ ] All tests pass

### Log rotation
- [ ] `TimedRotatingFileHandler` (daily, 30-day retention) in `setup_logging`
- [ ] Cleanup step purges `logs/` files older than 30 days
- [ ] Tests for rotation config + cleanup
- [ ] All tests pass

### Phoenix
- [ ] `requirements-observability.txt` with `arize-phoenix` + `openinference-instrumentation-langchain`
- [ ] `setup_tracing()` — guarded import, no-op if not installed or env vars absent
- [ ] Auto-instrumentation when configured
- [ ] Manual span in `RealLLM.__call__` with `job_slug` + `step`
- [ ] `.devin.example/pipeline.env.example` documents Phoenix env vars
- [ ] README "Observability" section
- [ ] Unit tests for `setup_tracing()` (mock tracer)
- [ ] All tests pass

### Documentation
- [ ] ADR-0014 in both repos
- [ ] This plan doc in OSS; matching gjalla spec in closed
- [ ] `.devin.example/pipeline.env.example` updated

### Mirror maintenance
- [ ] All three workstreams ported to closed repo
- [ ] Closed repo tests pass (525+ passed, 7 skipped)
- [ ] No closed-repo-specific content in OSS

## Files Affected

### structlog (23 files)
- `pipeline/__main__.py` (30 log calls)
- `pipeline/infrastructure/lifecycle.py` (26 calls, 9 annotations)
- `pipeline/infrastructure/llm_interface.py` (1 call, 1 annotation)
- `pipeline/infrastructure/audit_store.py` (4 calls)
- `pipeline/infrastructure/state_store.py` (4 calls)
- `pipeline/infrastructure/delta_sync.py` (8 calls, 1 annotation)
- `pipeline/infrastructure/enrichment_store.py` (1 call, 3 annotations)
- `pipeline/infrastructure/job_schema.py` (1 call)
- `pipeline/steps/step1_feasibility.py` through `step10_finalize.py`
- `pipeline/conftest.py`
- `pipeline/helpers/smoke_test_pipeline.py`
- Test files: `test_delta_sync.py`, `test_enrichment_store.py`

### Log rotation
- `pipeline/infrastructure/lifecycle.py` (`setup_logging` + `cleanup_transient_dirs`)

### Phoenix
- `pipeline/__main__.py` (`setup_tracing()`)
- `pipeline/infrastructure/llm_interface.py` (`RealLLM.__call__`)
- `requirements-observability.txt` (new)
- `.devin.example/pipeline.env.example`
- `README.md`

## Risks

- **structlog bridge edge cases**: `logger.exception()` with `exc_info` may need attention
- **Phoenix auto-instrumentation overhead**: verify graceful degradation when server not running
- **Test breakage**: tests asserting on log format/content will need updating
