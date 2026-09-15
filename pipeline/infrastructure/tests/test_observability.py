"""Tests for pipeline.observability — Phoenix tracing setup (ADR-0014).

Tests verify:
- setup_tracing() returns False (no-op) when PHOENIX_HOST is unset.
- setup_tracing() returns False when arize-phoenix is not installed.
- setup_tracing() returns True and sets _tracing_enabled when Phoenix
  is importable and PHOENIX_HOST is set (mocked imports).
- setup_tracing() sets project name (auto-generated or from env).
- trace_llm_call() is a pass-through when tracing is disabled.
- trace_llm_call() wraps fn in a span with attributes when tracing is enabled.
- trace_llm_call() sets span status to ERROR on LLM errors.
- Graceful degradation when span creation fails.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

from pipeline.infrastructure import observability
from pipeline.infrastructure.observability import (
    is_tracing_enabled,
    setup_tracing,
    trace_llm_call,
)


@pytest.fixture(autouse=True)
def _reset_tracing():
    """Reset tracing state between tests."""
    observability._tracing_enabled = False
    yield
    observability._tracing_enabled = False


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove PHOENIX_HOST and related env vars for all tests by default."""
    monkeypatch.delenv("PHOENIX_HOST", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("PHOENIX_PROJECT_NAME", raising=False)
    monkeypatch.delenv("RUN_REAL_LLM", raising=False)


class TestSetupTracing:
    def test_noop_without_phoenix_host(self):
        """No PHOENIX_HOST env var → returns False, no tracing."""
        assert setup_tracing() is False
        assert is_tracing_enabled() is False

    def test_noop_when_phoenix_not_installed(self, monkeypatch):
        """PHOENIX_HOST set but arize-phoenix not installed → returns False."""
        monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")
        # Simulate phoenix not being importable by making the import fail.
        with patch.dict(sys.modules, {"phoenix": None, "openinference.instrumentation.langchain": None}):
            assert setup_tracing() is False
        assert is_tracing_enabled() is False

    def _mock_phoenix_modules(self):
        """Build mock phoenix + opentelemetry modules matching setup_tracing's imports."""
        mock_tracer_provider_cls = MagicMock()
        mock_provider_instance = MagicMock()
        mock_tracer_provider_cls.return_value = mock_provider_instance
        mock_otel_trace = MagicMock()
        mock_otel_pkg = MagicMock()
        mock_otel_pkg.trace = mock_otel_trace
        mock_instrumentor = MagicMock()
        mock_instrumentor_instance = MagicMock()
        mock_instrumentor.return_value = mock_instrumentor_instance
        mock_phoenix_otel = MagicMock(TracerProvider=mock_tracer_provider_cls, PROJECT_NAME="phoenix.project")
        mock_otel_sdk = MagicMock()
        mock_otel_sdk_trace_export = MagicMock()
        mock_otel_exporter = MagicMock()
        return {
            "opentelemetry": mock_otel_pkg,
            "opentelemetry.trace": mock_otel_trace,
            "opentelemetry.sdk": mock_otel_sdk,
            "opentelemetry.sdk.resources": mock_otel_sdk,
            "opentelemetry.sdk.trace.export": mock_otel_sdk_trace_export,
            "opentelemetry.exporter": mock_otel_exporter,
            "opentelemetry.exporter.otlp": mock_otel_exporter,
            "opentelemetry.exporter.otlp.proto": mock_otel_exporter,
            "opentelemetry.exporter.otlp.proto.http": mock_otel_exporter,
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": mock_otel_exporter,
            "phoenix.otel": mock_phoenix_otel,
            "openinference.instrumentation.langchain": MagicMock(LangChainInstrumentor=mock_instrumentor),
        }, mock_tracer_provider_cls, mock_instrumentor_instance, mock_provider_instance

    def test_enabled_when_phoenix_available(self, monkeypatch):
        """PHOENIX_HOST set + Phoenix importable → returns True, tracing enabled."""
        monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")

        mocks, mock_tp_cls, mock_inst, mock_provider = self._mock_phoenix_modules()
        with patch.dict(sys.modules, mocks):
            result = setup_tracing()

        assert result is True
        assert is_tracing_enabled() is True
        # TracerProvider was constructed and set as the global provider.
        mock_tp_cls.assert_called_once()
        mock_otel_trace = mocks["opentelemetry.trace"]
        mock_otel_trace.set_tracer_provider.assert_called_once()
        mock_inst.instrument.assert_called_once_with(auto_instrument=True)
        # BatchSpanProcessor was added (replacing the default SimpleSpanProcessor).
        mock_provider.add_span_processor.assert_called_once()

    def test_sets_otlp_endpoint_from_phoenix_host(self, monkeypatch):
        """OTEL_EXPORTER_OTLP_ENDPOINT is derived from PHOENIX_HOST with /v1/traces."""
        monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        mocks, _, _, _ = self._mock_phoenix_modules()
        with patch.dict(sys.modules, mocks):
            setup_tracing()

        import os
        assert os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") == "http://localhost:6006/v1/traces"

    def test_does_not_override_existing_otlp_endpoint(self, monkeypatch):
        """Existing OTEL_EXPORTER_OTLP_ENDPOINT is not overridden."""
        monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://custom:4317")

        mocks, _, _, _ = self._mock_phoenix_modules()
        with patch.dict(sys.modules, mocks):
            setup_tracing()

        import os
        assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://custom:4317"

    def test_project_name_auto_generated_for_production(self, monkeypatch):
        """Project name is auto-generated as 'job-hunter <timestamp>' for production."""
        monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")
        monkeypatch.delenv("RUN_REAL_LLM", raising=False)
        monkeypatch.delenv("PHOENIX_PROJECT_NAME", raising=False)

        mocks, mock_tp_cls, _, _ = self._mock_phoenix_modules()
        with patch.dict(sys.modules, mocks):
            setup_tracing()

        # Check the Resource.create call included a project name starting with "job-hunter "
        call_kwargs = mock_tp_cls.call_args
        resource_arg = call_kwargs.kwargs.get("resource")
        assert resource_arg is not None
        # Resource.create receives a dict with PROJECT_NAME key
        resource_create_call = mocks["opentelemetry.sdk.resources"].Resource.create.call_args
        resource_dict = resource_create_call.args[0]
        project_val = resource_dict["phoenix.project"]
        assert project_val.startswith("job-hunter ")
        assert "EVAL" not in project_val

    def test_project_name_auto_generated_for_eval(self, monkeypatch):
        """Project name includes 'EVAL' when RUN_REAL_LLM=1 is set."""
        monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")
        monkeypatch.setenv("RUN_REAL_LLM", "1")
        monkeypatch.delenv("PHOENIX_PROJECT_NAME", raising=False)

        mocks, mock_tp_cls, _, _ = self._mock_phoenix_modules()
        with patch.dict(sys.modules, mocks):
            setup_tracing()

        resource_create_call = mocks["opentelemetry.sdk.resources"].Resource.create.call_args
        resource_dict = resource_create_call.args[0]
        project_val = resource_dict["phoenix.project"]
        assert project_val.startswith("job-hunter EVAL ")

    def test_project_name_from_env_override(self, monkeypatch):
        """PHOENIX_PROJECT_NAME env var overrides the auto-generated name."""
        monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")
        monkeypatch.setenv("PHOENIX_PROJECT_NAME", "my-custom-project")

        mocks, mock_tp_cls, _, _ = self._mock_phoenix_modules()
        with patch.dict(sys.modules, mocks):
            setup_tracing()

        resource_create_call = mocks["opentelemetry.sdk.resources"].Resource.create.call_args
        resource_dict = resource_create_call.args[0]
        assert resource_dict["phoenix.project"] == "my-custom-project"

    def test_graceful_degradation_on_instrumentation_failure(self, monkeypatch):
        """If instrumentation raises, setup_tracing returns False (graceful)."""
        monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")

        mocks, _, mock_inst, _ = self._mock_phoenix_modules()
        mock_inst.instrument.side_effect = RuntimeError("server down")
        with patch.dict(sys.modules, mocks):
            result = setup_tracing()

        assert result is False
        assert is_tracing_enabled() is False


class TestTraceLlmCall:
    def _mock_otel(self):
        """Build mock opentelemetry modules for trace_llm_call tests."""
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        mock_otel_trace = MagicMock()
        mock_otel_trace.get_tracer.return_value = mock_tracer
        mock_otel_trace.Status = MagicMock()
        mock_otel_trace.StatusCode = MagicMock()
        mock_otel_pkg = MagicMock()
        mock_otel_pkg.trace = mock_otel_trace
        return mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span

    def test_passthrough_when_tracing_disabled(self):
        """When tracing is disabled, trace_llm_call calls fn directly."""
        fn = MagicMock(return_value=("output", None))
        result = trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt", timeout=30)
        assert result == ("output", None)
        fn.assert_called_once_with("prompt", timeout=30)

    def test_wraps_in_span_when_enabled(self):
        """When tracing is enabled, fn is called inside a span with attributes."""
        observability._tracing_enabled = True

        mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span = self._mock_otel()
        fn = MagicMock(return_value=("output", None))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            result = trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt", timeout=30)

        assert result == ("output", None)
        fn.assert_called_once_with("prompt", timeout=30)
        mock_tracer.start_as_current_span.assert_called_once_with("llm_call")
        mock_span.set_attribute.assert_any_call("job_slug", "slug-1")
        mock_span.set_attribute.assert_any_call("step", "grade-jd")
        mock_span.set_attribute.assert_any_call("model", "gpt-4")

    def test_uses_unknown_for_none_attributes(self):
        """None job_slug/step are set to 'unknown' in the span."""
        observability._tracing_enabled = True

        mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span = self._mock_otel()
        fn = MagicMock(return_value=("output", None))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            trace_llm_call(None, None, "gpt-4", fn, "prompt")

        mock_span.set_attribute.assert_any_call("job_slug", "unknown")
        mock_span.set_attribute.assert_any_call("step", "unknown")

    def test_sets_error_status_on_llm_error(self):
        """Span status is set to ERROR when the LLM call returns an error."""
        observability._tracing_enabled = True

        mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span = self._mock_otel()
        fn = MagicMock(return_value=(None, "LLM timed out"))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            result = trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt")

        assert result == (None, "LLM timed out")
        mock_span.set_status.assert_called_once()
        status_arg = mock_span.set_status.call_args.args[0]
        # The Status object should have been constructed — just verify set_status was called.
        assert mock_span.set_status.call_count == 1

    def test_no_error_status_on_success(self):
        """Span status is NOT set to ERROR when the LLM call succeeds."""
        observability._tracing_enabled = True

        mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span = self._mock_otel()
        fn = MagicMock(return_value=("output", None))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt")

        mock_span.set_status.assert_not_called()

    def test_sets_error_status_on_exception(self):
        """Span status is set to ERROR when fn raises, and the exception propagates."""
        observability._tracing_enabled = True

        mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span = self._mock_otel()
        fn = MagicMock(side_effect=RuntimeError("boom"))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            with pytest.raises(RuntimeError, match="boom"):
                trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt")

        mock_span.set_status.assert_called_once()

    def test_graceful_degradation_on_span_failure(self):
        """If span creation fails, fn is still called (result preserved)."""
        observability._tracing_enabled = True

        mock_otel_trace = MagicMock()
        mock_otel_trace.get_tracer.side_effect = RuntimeError("tracer broken")
        mock_otel_trace.Status = MagicMock()
        mock_otel_trace.StatusCode = MagicMock()
        mock_otel_pkg = MagicMock()
        mock_otel_pkg.trace = mock_otel_trace

        fn = MagicMock(return_value=("output", None))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            result = trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt")

        assert result == ("output", None)
        fn.assert_called_once_with("prompt")

    def test_sets_token_attributes_from_atif(self, tmp_path):
        """Token counts are extracted from the ATIF export file and set on the span."""
        import json
        observability._tracing_enabled = True

        # Write a minimal ATIF file
        atif_file = tmp_path / "export.json"
        atif_file.write_text(json.dumps({
            "schema_version": "ATIF-v1.7",
            "final_metrics": {
                "total_prompt_tokens": 5000,
                "total_completion_tokens": 200,
                "total_cached_tokens": 3000,
                "total_steps": 5,
            },
        }))

        mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span = self._mock_otel()
        fn = MagicMock(return_value=("output", None))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt",
                           export_path=str(atif_file))

        mock_span.set_attribute.assert_any_call("llm.token_count.prompt", 5000)
        mock_span.set_attribute.assert_any_call("llm.token_count.completion", 200)
        mock_span.set_attribute.assert_any_call("llm.token_count.total", 5200)
        mock_span.set_attribute.assert_any_call("llm.token_count.prompt_details.cache_read", 3000)

    def test_no_token_attributes_without_export_path(self):
        """No token attributes are set when export_path is not provided."""
        observability._tracing_enabled = True

        mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span = self._mock_otel()
        fn = MagicMock(return_value=("output", None))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt")

        # Verify no llm.token_count.* attributes were set
        for call in mock_span.set_attribute.call_args_list:
            arg = call.args[0]
            assert not arg.startswith("llm.token_count"), f"Unexpected token attribute: {arg}"

    def test_atif_parsing_logs_warning_on_missing_file(self, tmp_path):
        """ATIF parsing logs a warning when the export file doesn't exist, but the LLM call succeeds."""
        observability._tracing_enabled = True

        mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span = self._mock_otel()
        fn = MagicMock(return_value=("output", None))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            with patch("structlog.get_logger") as mock_get_logger:
                result = trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt",
                                        export_path=str(tmp_path / "nonexistent.json"))

        assert result == ("output", None)
        # fn must be called exactly once — no double-call from ATIF parsing failure.
        fn.assert_called_once()
        # Basic attributes still set, but no token counts
        mock_span.set_attribute.assert_any_call("job_slug", "slug-1")
        for call in mock_span.set_attribute.call_args_list:
            arg = call.args[0]
            assert not arg.startswith("llm.token_count"), f"Unexpected token attribute: {arg}"
        # A warning should have been logged
        mock_get_logger.return_value.warning.assert_called_once()

    def test_atif_parsing_logs_warning_on_malformed_json(self, tmp_path):
        """ATIF parsing logs a warning when the export file is invalid JSON."""
        observability._tracing_enabled = True

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json {{{")

        mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span = self._mock_otel()
        fn = MagicMock(return_value=("output", None))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            with patch("structlog.get_logger") as mock_get_logger:
                result = trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt",
                                        export_path=str(bad_file))

        assert result == ("output", None)
        fn.assert_called_once()
        mock_get_logger.return_value.warning.assert_called_once()

    def test_sets_openinference_span_kind(self):
        """The span kind is set to LLM for Phoenix categorization."""
        observability._tracing_enabled = True

        mock_otel_pkg, mock_otel_trace, mock_tracer, mock_span = self._mock_otel()
        fn = MagicMock(return_value=("output", None))

        with patch.dict(sys.modules, {"opentelemetry": mock_otel_pkg, "opentelemetry.trace": mock_otel_trace}):
            trace_llm_call("slug-1", "grade-jd", "gpt-4", fn, "prompt")

        mock_span.set_attribute.assert_any_call("openinference.span.kind", "LLM")


class TestFallbackExporter:
    """Tests for FallbackOTLPSpanExporter."""

    def _mock_otel_modules(self, encode_spans=None):
        """Inject mock opentelemetry modules into sys.modules for tests.

        Returns the SpanExportResult mock and the encode_spans mock.
        """
        from enum import Enum

        class SpanExportResult(Enum):
            SUCCESS = 0
            FAILURE = 1

        mock_trace_export = MagicMock(SpanExportResult=SpanExportResult)
        mock_trace_encoder = MagicMock()
        if encode_spans is not None:
            mock_trace_encoder.encode_spans = encode_spans
        else:
            mock_encoded = MagicMock()
            mock_encoded.SerializePartialToString.return_value = b"fake-otlp-data"
            mock_trace_encoder.encode_spans = MagicMock(return_value=mock_encoded)

        modules = {
            "opentelemetry": MagicMock(),
            "opentelemetry.sdk": MagicMock(),
            "opentelemetry.sdk.trace": MagicMock(),
            "opentelemetry.sdk.trace.export": mock_trace_export,
            "opentelemetry.exporter": MagicMock(),
            "opentelemetry.exporter.otlp": MagicMock(),
            "opentelemetry.exporter.otlp.proto": MagicMock(),
            "opentelemetry.exporter.otlp.proto.common": MagicMock(),
            "opentelemetry.exporter.otlp.proto.common.trace_encoder": mock_trace_encoder,
        }
        return modules, SpanExportResult, mock_trace_encoder.encode_spans

    def test_writes_to_file_on_failure(self, tmp_path):
        """When the OTLP export fails, spans are written to a .otlp file."""
        from pipeline.infrastructure.observability import FallbackOTLPSpanExporter

        modules, SpanExportResult, _ = self._mock_otel_modules()

        mock_inner = MagicMock()
        mock_inner.export.return_value = SpanExportResult.FAILURE

        exporter = FallbackOTLPSpanExporter(mock_inner, str(tmp_path / "fallback"))

        with patch.dict(sys.modules, modules):
            result = exporter.export(["fake-span"])

        assert result == SpanExportResult.FAILURE
        mock_inner.export.assert_called_once_with(["fake-span"])

        files = list((tmp_path / "fallback").glob("spans-*.otlp"))
        assert len(files) == 1
        assert files[0].read_bytes() == b"fake-otlp-data"

    def test_no_file_on_success(self, tmp_path):
        """When the OTLP export succeeds, no .otlp file is written."""
        from pipeline.infrastructure.observability import FallbackOTLPSpanExporter

        modules, SpanExportResult, _ = self._mock_otel_modules()

        mock_inner = MagicMock()
        mock_inner.export.return_value = SpanExportResult.SUCCESS

        exporter = FallbackOTLPSpanExporter(mock_inner, str(tmp_path / "fallback"))

        with patch.dict(sys.modules, modules):
            result = exporter.export(["fake-span"])

        assert result == SpanExportResult.SUCCESS
        assert not (tmp_path / "fallback").exists()

    def test_shutdown_delegates_to_inner(self):
        """shutdown() delegates to the wrapped exporter."""
        from pipeline.infrastructure.observability import FallbackOTLPSpanExporter

        mock_inner = MagicMock()
        exporter = FallbackOTLPSpanExporter(mock_inner, "/tmp/test")
        exporter.shutdown()
        mock_inner.shutdown.assert_called_once()

    def test_force_flush_delegates_to_inner(self):
        """force_flush() delegates to the wrapped exporter."""
        from pipeline.infrastructure.observability import FallbackOTLPSpanExporter

        mock_inner = MagicMock()
        mock_inner.force_flush.return_value = True
        exporter = FallbackOTLPSpanExporter(mock_inner, "/tmp/test")
        assert exporter.force_flush(5000) is True
        mock_inner.force_flush.assert_called_once_with(5000)

    def test_write_failure_doesnt_crash(self, tmp_path):
        """If file writing fails, the exporter doesn't crash."""
        from pipeline.infrastructure.observability import FallbackOTLPSpanExporter

        # encode_spans raises — _write_spans_to_file should catch it.
        mock_encoder = MagicMock()
        mock_encoder.encode_spans = MagicMock(side_effect=RuntimeError("encode failed"))
        modules, SpanExportResult, _ = self._mock_otel_modules(mock_encoder.encode_spans)

        mock_inner = MagicMock()
        mock_inner.export.return_value = SpanExportResult.FAILURE

        exporter = FallbackOTLPSpanExporter(mock_inner, "/proc/cannot-create-here")

        with patch.dict(sys.modules, modules):
            # Should not raise.
            result = exporter.export(["fake-span"])

        assert result == SpanExportResult.FAILURE
