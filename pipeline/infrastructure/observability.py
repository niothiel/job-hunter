"""LLM observability via Arize Phoenix (ADR-0014).

Optional dependency — the pipeline runs fine without Phoenix installed.
If ``arize-phoenix`` is not importable or ``PHOENIX_HOST`` is not set,
tracing is silently skipped.

When configured:
- Auto-instrumentation traces LangGraph nodes and LLM calls via
  ``openinference-instrumentation-langchain``.
- Manual spans in ``RealLLM.__call__`` attach ``job_slug`` and ``step``
  as span attributes for filtering by job in the Phoenix UI.
- Span status set to ERROR when the LLM call returns an error.
- Trace identity: ``session_id`` = job slug, ``user_id`` = candidate name,
  ``metadata`` = ``{dry_run, run_timestamp}``.

Configuration is env-var only (no config file changes):
- ``PHOENIX_HOST`` — Phoenix server URL (default: ``http://localhost:6006``).
- ``OTEL_EXPORTER_OTLP_ENDPOINT`` — OTLP endpoint (set automatically from
  ``PHOENIX_HOST`` if not already set).
- ``PHOENIX_PROJECT_NAME`` — Override the auto-generated project name.
  Auto-generated as ``job-hunter <YYYY-MM-DD HH:MM>`` for production runs,
  or ``job-hunter EVAL <YYYY-MM-DD HH:MM>`` when ``RUN_REAL_LLM=1`` is set.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

# Module-level flag: set by setup_tracing() if instrumentation succeeded.
_tracing_enabled: bool = False


class FallbackOTLPSpanExporter:
    """Wraps OTLPSpanExporter — on export failure, writes spans to a local file.

    When Phoenix is unreachable, the OTLP export fails. This wrapper catches
    the failure and serializes the span batch to an OTLP protobuf file on disk
    so a replay script can send them to Phoenix later.

    Files are written to ``<fallback_dir>/spans-<timestamp>.otlp``.
    Only writes on failure — no disk I/O when Phoenix is up.
    """

    def __init__(self, otlp_exporter: Any, fallback_dir: str | Path):
        self._exporter = otlp_exporter
        self._fallback_dir = Path(fallback_dir)

    def export(self, spans: Any) -> Any:
        from opentelemetry.sdk.trace.export import SpanExportResult

        result = self._exporter.export(spans)
        if result == SpanExportResult.FAILURE:
            self._write_spans_to_file(spans)
        return result

    def _write_spans_to_file(self, spans: Any) -> None:
        try:
            from opentelemetry.exporter.otlp.proto.common.trace_encoder import (
                encode_spans,
            )

            self._fallback_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%dT%H%M%S")
            path = self._fallback_dir / f"spans-{ts}.otlp"
            data = encode_spans(spans).SerializePartialToString()
            path.write_bytes(data)
        except Exception:
            # Best-effort — don't crash the pipeline over fallback logging.
            pass

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._exporter.force_flush(timeout_millis)


def _default_project_name() -> str:
    """Build a project name from the current timestamp.

    Production runs: ``job-hunter 2026-09-11 14:30``
    Integration tests: ``job-hunter EVAL 2026-09-11 14:30``
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    prefix = "job-hunter EVAL" if os.environ.get("RUN_REAL_LLM") == "1" else "job-hunter"
    return f"{prefix} {ts}"


def setup_tracing(fallback_dir: str | None = None) -> bool:
    """Configure Phoenix tracing if available and requested.

    Args:
        fallback_dir: Directory for OTLP span files when Phoenix is unreachable.
            If None, defaults to ``logs/exports`` relative to CWD.

    Returns True if tracing was enabled, False otherwise.
    No-op (returns False) when:
    - ``arize-phoenix`` is not installed (ImportError)
    - ``PHOENIX_HOST`` env var is not set
    - Auto-instrumentation registration fails

    This function is safe to call unconditionally at pipeline startup.
    """
    global _tracing_enabled

    if not os.environ.get("PHOENIX_HOST"):
        return False

    try:
        from opentelemetry import trace
        from openinference.instrumentation.langchain import LangChainInstrumentor

        phoenix_host = os.environ["PHOENIX_HOST"]

        # Derive the OTLP/traces endpoint from PHOENIX_HOST if not already set.
        # Phoenix v4+ serves OTLP over HTTP at <host>/v1/traces.
        if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            otlp_endpoint = phoenix_host.rstrip("/")
            if not otlp_endpoint.endswith("/v1/traces"):
                otlp_endpoint = f"{otlp_endpoint}/v1/traces"
            os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = otlp_endpoint

        # Determine the Phoenix project name.
        project_name = os.environ.get("PHOENIX_PROJECT_NAME") or _default_project_name()

        # Set up the TracerProvider with an OTLP exporter.
        # Phoenix v20+ exposes TracerProvider via phoenix.otel; older versions
        # used px.Client() which implicitly configured the global provider.
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from phoenix.otel import TracerProvider, PROJECT_NAME

            provider = TracerProvider(
                endpoint=os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"],
                protocol="http/protobuf",
                verbose=False,
                resource=Resource.create({PROJECT_NAME: project_name}),
            )
            # Replace the default SimpleSpanProcessor with a BatchSpanProcessor
            # for non-blocking span export in production.
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            otlp_exporter = OTLPSpanExporter(
                endpoint=os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]
            )
            # Wrap with fallback exporter so spans are saved to disk when
            # Phoenix is unreachable (ADR-0014).
            fb_dir = fallback_dir or str(Path.cwd() / "logs" / "exports")
            fallback_exporter = FallbackOTLPSpanExporter(otlp_exporter, fb_dir)
            batch_processor = BatchSpanProcessor(fallback_exporter)
            provider.add_span_processor(batch_processor)
            trace.set_tracer_provider(provider)
        except ImportError:
            # Fall back to older Phoenix API (px.Client sets up the provider).
            import phoenix as px
            px.Client()

        # Auto-instrument LangChain/LangGraph.
        LangChainInstrumentor().instrument(auto_instrument=True)

        _tracing_enabled = True
        return True
    except ImportError:
        # arize-phoenix or openinference-instrumentation-langchain not installed.
        return False
    except Exception:
        # Any other failure (server not running, config error, etc.) —
        # tracing is non-critical, degrade gracefully.
        return False


def is_tracing_enabled() -> bool:
    """Return True if Phoenix tracing was successfully configured."""
    return _tracing_enabled


def _set_token_attributes_from_atif(span: Any, export_path: str | None) -> None:
    """Read token counts from an ATIF export file and set them as span attributes.

    The ``devin -p --export <path>`` flag writes an ATIF v1.7 JSON file with
    ``final_metrics`` containing ``total_prompt_tokens``, ``total_completion_tokens``,
    and ``total_cached_tokens``. This function reads that file and sets
    OpenInference semantic convention attributes on the span.

    No-op only when ``export_path`` is None (no export was requested).
    If ``export_path`` is set but the file is missing, malformed, or lacks
    expected keys, raises an exception — the caller logs it so failures
    are visible rather than silently swallowed.

    Uses raw attribute strings (``llm.token_count.*``) rather than importing
    ``openinference.semconv`` to avoid a hard dependency.
    """
    if not export_path:
        return

    import json
    from pathlib import Path

    atif_path = Path(export_path)
    if not atif_path.exists():
        # The export path might be a directory (devin writes conversation.json inside).
        conv_file = atif_path / "conversation.json" if atif_path.is_dir() else atif_path
        if not conv_file.exists():
            raise FileNotFoundError(f"ATIF export file not found: {export_path}")
        atif_path = conv_file

    data = json.loads(atif_path.read_text(encoding="utf-8"))
    metrics = data.get("final_metrics")
    if metrics is None:
        raise ValueError(f"ATIF export missing 'final_metrics' key: {export_path}")

    prompt_tokens = metrics.get("total_prompt_tokens")
    completion_tokens = metrics.get("total_completion_tokens")
    cached_tokens = metrics.get("total_cached_tokens")

    if prompt_tokens is not None:
        span.set_attribute("llm.token_count.prompt", prompt_tokens)
    if completion_tokens is not None:
        span.set_attribute("llm.token_count.completion", completion_tokens)
    if prompt_tokens is not None and completion_tokens is not None:
        span.set_attribute("llm.token_count.total", prompt_tokens + completion_tokens)
    if cached_tokens is not None:
        span.set_attribute("llm.token_count.prompt_details.cache_read", cached_tokens)


def trace_llm_call(
    job_slug: str | None,
    step: str | None,
    model_name: str,
    fn: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Wrap an LLM call in an OpenTelemetry span with job_slug + step attributes.

    If tracing is not enabled, this is a pass-through — calls ``fn`` directly
    with no span overhead.

    Args:
        job_slug: The job being processed (correlates to Phoenix session_id).
        step: The pipeline step name (e.g. "grade-jd", "customize").
        model_name: The LLM model name (for span attribute, not passed to fn).
        fn: The callable to wrap (typically ``call_llm_safe``).
        *args, **kwargs: Passed through to ``fn``.

    Returns:
        Whatever ``fn`` returns.
    """
    if not _tracing_enabled:
        return fn(*args, **kwargs)

    try:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode
    except ImportError:
        return fn(*args, **kwargs)

    try:
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("llm_call") as span:
            span.set_attribute("job_slug", job_slug or "unknown")
            span.set_attribute("step", step or "unknown")
            span.set_attribute("model", model_name)
            # Set OpenInference span kind so Phoenix categorizes this as an LLM span.
            span.set_attribute("openinference.span.kind", "LLM")
            try:
                result = fn(*args, **kwargs)
            except Exception:
                span.set_status(Status(StatusCode.ERROR))
                raise
            # call_llm_safe returns (output, error) — mark span on error.
            if isinstance(result, tuple) and len(result) == 2 and result[1] is not None:
                span.set_status(Status(StatusCode.ERROR, str(result[1])))
            # Extract token counts from the ATIF export file if one was written.
            # Errors here must not trigger the outer fallback (which would
            # re-call fn). Log loudly so parsing failures are visible.
            try:
                _set_token_attributes_from_atif(span, kwargs.get("export_path"))
            except Exception as exc:
                import structlog
                structlog.get_logger(__name__).warning(
                    "ATIF token parsing failed", export_path=kwargs.get("export_path"), error=str(exc)
                )
            return result
    except Exception:
        # Span creation failed — don't break the LLM call.
        return fn(*args, **kwargs)
