"""LLM protocol + implementations + dependency injection helper.

The LLM is the only effect boundary we abstract (ADR-0005). Filesystem
operations stay direct, gated by config.dry_run. This lets tests swap in
FakeLLM without subprocess fakes or network calls.

Production: RealLLM wraps devin_cli.call_llm_safe.
Tests:      FakeLLM returns canned responses keyed by prompt substring.

Injection: LangGraph's native DI via config["configurable"]. Nodes access
deps via get_deps(runnable_config) -> NodeDeps.

Every RealLLM call is logged to the llm_calls audit table (W7) with full
prompt, response, model params, and timing. job_slug and step are passed
through by callers; step nodes pass job_slug=state.slug and step="<step-name>".
"""
from __future__ import annotations

import json
import re
import structlog
import time
from dataclasses import dataclass
from typing import Protocol

from pipeline.infrastructure.config import PipelineConfig
from pipeline.infrastructure.paths import Paths


# ─── Protocol ──────────────────────────────────────────────────────────────


# ─── LLM output parsing ────────────────────────────────────────────────────


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def parse_llm_json(text: str | None) -> dict | list | None:
    """Parse LLM stdout as JSON, tolerating markdown code fences and surrounding text.

    LLMs frequently wrap JSON output in ```json ... ``` fences despite
    instructions not to. This strips fences, then falls back to extracting
    the outermost { ... } block if the full text isn't valid JSON.
    Returns None on failure so callers can fall back to file-based parsing.
    Handles both JSON objects (dict) and arrays (list).
    """
    if not text:
        return None
    stripped = text.strip()
    # Strip markdown fences if present
    m = _FENCE_RE.match(stripped)
    if m:
        stripped = m.group(1).strip()
    # Try direct parse
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        pass
    # Fallback: extract outermost { ... } or [ ... ] block
    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        start = stripped.find(open_ch)
        end = stripped.rfind(close_ch) + 1
        if start >= 0 and end > start:
            try:
                return json.loads(stripped[start:end])
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
    return None


# ─── Protocol ──────────────────────────────────────────────────────────────


class LLM(Protocol):
    """Callable that sends a prompt to an LLM and returns (output, error)."""

    def __call__(
        self, prompt: str, *, model: str, timeout: int, workspace: str,
        retries: int = 2, retry_delay: int = 5,
        export_path: str | None = None,
        permission_mode: str = "dangerous",
        config_path: str | None = None,
        job_slug: str | None = None,
        step: str | None = None,
    ) -> tuple[str | None, str | None]: ...


# ─── RealLLM (production) ──────────────────────────────────────────────────


class RealLLM:
    """Wraps devin_cli.call_llm_safe — the only production impl.

    Delegates to the existing devin -p subprocess wrapper. The error type
    is normalized to str (call_llm_safe returns LLMError, we stringify it)
    so the protocol signature stays simple.

    Every call is logged to the llm_calls audit table (W7) with full prompt,
    response, params, and duration. job_slug and step identify the call
    context for later querying.

    Timing data is also recorded in self.calls (mirrors FakeLLM's audit
    trail) so test scripts and eval runners can use RealLLM directly
    without a separate timing wrapper. Each entry is a dict with:
        step, call_index, duration_s, timeout_s, status, job_slug, model
    self.last_output holds the most recent call's output (or None).
    """

    def __init__(self, db_path: str | None = None):
        """Args:
            db_path: Path to the audit DB. If None, RealLLM logs a warning
                     and skips audit logging (useful for tests that don't
                     need the audit trail). Defaults to None.
        """
        self._db_path = db_path
        self.calls: list[dict] = []
        self.last_output: str | None = None
        self._step_counters: dict[str, int] = {}

    def __call__(
        self, prompt: str, *, model: str, timeout: int, workspace: str,
        retries: int = 2, retry_delay: int = 5,
        export_path: str | None = None,
        permission_mode: str = "dangerous",
        config_path: str | None = None,
        job_slug: str | None = None,
        step: str | None = None,
    ) -> tuple[str | None, str | None]:
        # Import here so the pipeline package doesn't hard-depend on devin_cli
        # at import time — tests using FakeLLM don't need devin installed.
        from pipeline.infrastructure.devin_cli import call_llm_safe
        from pipeline.infrastructure.observability import trace_llm_call

        step_label = step or "unknown"
        call_index = self._step_counters.get(step_label, 0) + 1
        self._step_counters[step_label] = call_index

        start = time.monotonic()
        output, error = trace_llm_call(
            job_slug, step, model, call_llm_safe,
            prompt, model=model, timeout=timeout, workspace=workspace,
            retries=retries, retry_delay=retry_delay,
            export_path=export_path,
            permission_mode=permission_mode,
            config_path=config_path,
        )
        # Note: trace_llm_call receives model as its 3rd arg (model_name)
        # for the span attribute, and passes model=model through to fn via kwargs.
        duration_s = time.monotonic() - start
        duration_ms = int(duration_s * 1000)

        # Record timing for test/eval use (mirrors FakeLLM's calls list).
        if output:
            status = "ok"
        elif error:
            status = f"error: {str(error)[:80]}"
        else:
            status = "empty"
        self.calls.append({
            "step": step_label,
            "call_index": call_index,
            "call_id": f"{step_label}#{call_index}",
            "duration_s": round(duration_s, 1),
            "timeout_s": timeout,
            "status": status,
            "job_slug": job_slug,
            "model": model,
        })
        self.last_output = output

        # Audit log (W7). Failure-isolated — never affects the call result.
        self._log_call(
            job_slug=job_slug, step=step, model=model, prompt=prompt,
            response=output, error=error,
            params={"timeout": timeout, "retries": retries,
                    "retry_delay": retry_delay, "permission_mode": permission_mode},
            duration_ms=duration_ms,
        )

        if error:
            return None, str(error)
        return output, None

    def _log_call(self, *, job_slug, step, model, prompt, response, error,
                  params, duration_ms):
        """Log the call to the llm_calls audit table (W7). Never raises."""
        if not self._db_path:
            return  # audit logging disabled (e.g. in tests without a DB)
        try:
            from pipeline.infrastructure.audit_store import AuditStore
            audit = AuditStore(self._db_path)
            # Log the response or the error string as the "response" field.
            resp = response if response else (str(error) if error else None)
            audit.log_llm_call(
                job_slug=job_slug, step=step, model=model,
                prompt=prompt, response=resp, params=params,
                duration_ms=duration_ms,
            )
            audit.close()
        except Exception as e:
            structlog.get_logger(__name__).warning(
                "llm_calls audit log failed",
                step=step,
                error=str(e),
            )


# ─── FakeLLM (tests) ───────────────────────────────────────────────────────


class FakeLLM:
    """Test double — returns canned responses keyed by prompt substring match.

    Responses are checked in insertion order (dict preserves order in 3.7+).
    The first matching key wins. If no key matches, returns (None, error).

    Records all calls in self.calls for test assertions.
    """

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses: dict[str, str] = responses or {}
        self.calls: list[str] = []  # audit trail of prompts received

    def __call__(
        self, prompt: str, *, model: str, timeout: int, workspace: str,
        retries: int = 2, retry_delay: int = 5,
        export_path: str | None = None,
        permission_mode: str = "dangerous",
        config_path: str | None = None,
        job_slug: str | None = None,
        step: str | None = None,
    ) -> tuple[str | None, str | None]:
        self.calls.append(prompt)
        for key, response in self.responses.items():
            if key in prompt:
                return response, None
        return None, f"FakeLLM: no matching response for prompt (keys: {list(self.responses.keys())})"

    def add_response(self, key: str, response: str) -> None:
        """Add or replace a canned response after construction."""
        self.responses[key] = response


# ─── Dependency injection ──────────────────────────────────────────────────


@dataclass(frozen=True)
class NodeDeps:
    """Dependencies injected into every graph node via config["configurable"].

    Frozen so it can't be accidentally mutated mid-graph.
    """

    llm: LLM
    logger: structlog.stdlib.BoundLogger
    config: PipelineConfig
    paths: Paths


def get_deps(runnable_config) -> NodeDeps:
    """Extract NodeDeps from a LangGraph RunnableConfig.

    Usage in nodes:
        def my_node(state: JobState, config: dict) -> dict:
            deps = get_deps(config)
            output, error = deps.llm("prompt...", model=..., timeout=..., workspace=...)
    """
    c = runnable_config["configurable"]
    return NodeDeps(
        llm=c["llm"],
        logger=c["logger"],
        config=c["config"],
        paths=c["paths"],
    )
