"""Optional LangSmith tracing for ragdrift.

Everything here is a no-op unless BOTH hold:
- the `langsmith` package is installed (pip install ragdrift[langsmith])
- LANGSMITH_API_KEY is set in the environment

There is zero behavior change when either is absent — core scans, the CLI,
and the MCP server run exactly as before.
"""
from __future__ import annotations

import functools
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ragdrift.observability.costs import estimate_cost_usd


def langsmith_installed() -> bool:
    try:
        import langsmith  # noqa: F401
        return True
    except ImportError:
        return False


def tracing_enabled() -> bool:
    """True when LANGSMITH_API_KEY is set and langsmith is importable."""
    return bool(os.environ.get("LANGSMITH_API_KEY")) and langsmith_installed()


def configure_tracing() -> bool:
    """Enable LangSmith tracing for this process when an API key is present.

    Sets the env vars LangGraph/LangChain read, so graphs built with
    build_scan_graph() are traced natively without further wiring. Returns
    whether tracing is active. Safe to call unconditionally.
    """
    if not tracing_enabled():
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")  # older env name, same switch
    os.environ.setdefault("LANGSMITH_PROJECT", "ragdrift")
    return True


def build_usage_metadata(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> dict[str, Any]:
    """Token + cost metadata attached to a traced LLM run. Pure — unit-testable."""
    return {
        "ls_provider": provider,
        "ls_model_name": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": estimate_cost_usd(model, input_tokens, output_tokens),
    }


class _Span:
    """Recorder handed out by llm_span(); collects output + usage for the run."""

    def __init__(self, run_tree: Any, provider: str, model: str) -> None:
        self._run = run_tree
        self.provider = provider
        self.model = model
        self.usage: dict[str, Any] | None = None

    def record(self, output_text: str | None, input_tokens: int, output_tokens: int) -> None:
        self.usage = build_usage_metadata(self.provider, self.model, input_tokens, output_tokens)
        if self._run is None:
            return
        try:
            self._run.add_metadata(self.usage)
            self._run.end(outputs={
                "output": output_text,
                "usage_metadata": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
            })
        except Exception:
            pass  # tracing must never break a scan


@contextmanager
def llm_span(
    name: str, provider: str, model: str, inputs: dict[str, Any] | None = None
) -> Iterator[_Span]:
    """Trace one provider LLM call as a LangSmith 'llm' run.

    Usage:
        with llm_span("ragdrift.diagnosis", "anthropic", model, {"prompt": p}) as span:
            response = client.messages.create(...)
            span.record(text, response.usage.input_tokens, response.usage.output_tokens)

    No-op (still yields a recorder) when tracing is disabled. Exceptions from
    the wrapped call propagate unchanged; tracing failures are swallowed.
    """
    if not configure_tracing():
        yield _Span(None, provider, model)
        return
    try:
        import langsmith
        cm = langsmith.trace(
            name=name,
            run_type="llm",
            inputs=inputs or {},
            metadata={"ls_provider": provider, "ls_model_name": model},
        )
        run = cm.__enter__()
    except Exception:
        yield _Span(None, provider, model)
        return
    try:
        yield _Span(run, provider, model)
    except BaseException:
        cm.__exit__(*sys.exc_info())
        raise
    else:
        cm.__exit__(None, None, None)


def maybe_traceable(**traceable_kwargs: Any):
    """@traceable when tracing is enabled, plain call otherwise.

    langsmith is imported lazily at first traced call — never at decoration
    time — so `import ragdrift.cli` pulls in nothing extra even when the
    langsmith package is installed but no API key is set.
    """
    def deco(fn):
        traced = None

        @functools.wraps(fn)
        def lazy(*args: Any, **kwargs: Any):
            nonlocal traced
            if traced is not None:
                return traced(*args, **kwargs)
            if tracing_enabled():
                from langsmith import traceable

                traced = traceable(**traceable_kwargs)(fn)
                return traced(*args, **kwargs)
            return fn(*args, **kwargs)

        return lazy
    return deco
