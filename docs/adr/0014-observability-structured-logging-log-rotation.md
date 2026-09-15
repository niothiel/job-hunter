# Observability, structured logging, and log rotation

**Status:** accepted

Three related decisions made together: LLM observability tooling, structured
logging library, and log file rotation. Each addresses a different problem
but they share a correlation key (`slug`) and are implemented as sequential
workstreams.

## Context

Two gjalla findings flagged the current logging setup:

1. **Structured logging is critical for LangGraph correlation** — the pipeline
   uses a plain string formatter (`%(asctime)s [%(levelname)s] %(message)s`),
   making it difficult to correlate logs to specific job slugs or LangGraph
   nodes across runs.
2. **Unbounded disk usage from pipeline logs** — every run creates a new
   timestamped log file with no rotation or retention logic.

Separately, we want LLM observability — visual tracing of prompts, responses,
token costs, and latencies across the LangGraph pipeline — to understand and
debug LLM behavior.

## Considered Options

### Observability

- **Langfuse** — MIT-licensed, self-hostable, LangChain CallbackHandler
  integration. Rejected: self-hosting requires Docker Compose (Postgres +
  ClickHouse + Redis + S3). Too heavy for a single-user local pipeline.
- **LangSmith** — cloud-only on the free tier (5k traces/month, 14-day
  retention). Native LangGraph. Rejected: cloud-only sends prompt/response
  data to a third party; self-hosting is Enterprise-only.
- **OpenLLMetry + lightweight collector** — Apache 2.0, OpenTelemetry-based.
  Rejected: more DIY setup; no out-of-box UI without pairing with a separate
  backend.
- **Helicone** — Apache 2.0 but requires Docker for self-hosting. Rejected:
  same infrastructure concern as Langfuse.
- **Arize Phoenix (chosen)** — `pip install arize-phoenix && phoenix serve`
  runs a local observability UI at `localhost:6006` with no Docker. First-class
  LangGraph integration via OpenTelemetry auto-instrumentation. Traces
  prompts, responses, tokens, costs, latencies. 11k GitHub stars. Server is
  Elastic License 2.0 (fine for personal use; restriction is only on offering
  it as a managed service). Client SDK is Apache 2.0.

### Structured logging

- **JSON formatter on stdlib `logging`** — no new dependency, but no
  `bind()` API for context attachment. Every `log.info()` call would need
  `extra={...}` manually. Doesn't solve the correlation problem ergonomically.
- **structlog (chosen)** — structured logging library with `bind()` API.
  Every function handling a job can bind `slug` and `node` once; all
  subsequent calls automatically include them. JSON output for file handler,
  pretty console output for interactive runs. Drop-in replacement for stdlib
  logging via `structlog.stdlib.LoggerFactory`.

### Log rotation

- **`RotatingFileHandler` (size-based)** — rotates at N bytes, keeps N files.
  Loses time-based history. Not ideal for hourly pipeline runs.
- **`TimedRotatingFileHandler` (time-based, chosen)** — rotates daily, keeps
  30 days. Preserves time-based history for correlating runs by date.
- **Cleanup step in `cleanup_transient_dirs`** — delete logs older than 30
  days at end of each run. Simple, preserves per-run file naming. Chosen as
  a complement to `TimedRotatingFileHandler` for the per-run files that
  don't use the handler.

## Decision

### 1. Arize Phoenix for LLM observability

- **Optional dependency**: `requirements-observability.txt` with
  `arize-phoenix` and `openinference-instrumentation-langchain`.
- **Guarded imports**: pipeline runs fine without Phoenix installed. If
  `arize-phoenix` is not importable or env vars are not set, tracing is
  skipped.
- **Auto-instrumentation**: `register(auto_instrument=True)` in
  `pipeline/__main__.py` traces LangGraph nodes and LLM calls automatically.
- **Manual span in `RealLLM.__call__`**: attaches `job_slug` and `step` as
  span attributes for filtering by job in the Phoenix UI.
- **Trace identity**: `session_id` = job slug, `user_id` = candidate name,
  `metadata` = `{dry_run, run_timestamp}`.
- **Configuration**: env vars only (`PHOENIX_HOST`, etc.), documented in
  `.devin.example/pipeline.env.example` and README "Observability" section.
- **Tests**: unit test the `setup_tracing()` function with a mock tracer;
  assert configured when env vars set, no-op when absent, graceful
  degradation when import fails.

### 2. structlog for structured logging

- **Full migration**: 145 touchpoints across 23 files. Mechanical first —
  swap `logging` → `structlog`, configure JSON output. Convert f-string
  messages to structured event dictionaries incrementally in follow-up
  commits.
- **Output**: JSON for file handler (machine-parseable), pretty colored
  for console handler (human-readable during interactive runs).
- **Context binding**: `log.bind(slug=slug, node=step_name)` in the per-job
  loop in `__main__.py` and in step nodes. Every subsequent log call
  automatically includes `slug` and `node`.
- **stdlib bridge**: `structlog.stdlib.LoggerFactory` wraps stdlib logging
  so existing `logging.getLogger` calls keep working during migration.

### 3. TimedRotatingFileHandler + cleanup step for log rotation

- **`TimedRotatingFileHandler`**: daily rotation, 30-day retention, replaces
  the per-run `FileHandler` in `setup_logging`.
- **Cleanup step**: `cleanup_transient_dirs` also purges `logs/` files older
  than 30 days, catching any per-run files that predate the handler switch.
- **No new dependency**: `TimedRotatingFileHandler` is stdlib
  (`logging.handlers`).

## Consequences

- **Positive:** Structured logs with slug/node correlation solve gjalla
  issue 1. Log rotation solves gjalla issue 2. Phoenix gives LLM
  observability without infrastructure overhead.
- **Positive:** Phoenix is optional — OSS users who don't want observability
  don't install it. The pipeline is fully functional without it.
- **Positive:** structlog's `bind()` API makes adding context to logs
  ergonomic, encouraging developers to include correlation keys.
- **Negative:** structlog migration is a 145-touchpoint diff across 23 files.
  Mechanical migration is safe but large. Converting f-string messages to
  structured events is ongoing work.
- **Negative:** Phoenix server is ELv2 (not OSI-approved). The OSS project
  ships only the integration code (Apache 2.0 client); users install Phoenix
  themselves. This is the same pattern as optional Datadog integration.
- **Negative:** Two correlation systems (structlog logs + Phoenix traces)
  must stay consistent on the `slug` key. If they diverge, debugging across
  both is harder.
