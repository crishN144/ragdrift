"""Tracing tests — key absent AND present-but-fake; all LLM calls mocked."""
import os
import subprocess
import sys
import types

import pytest

from ragdrift.observability import (
    build_usage_metadata,
    configure_tracing,
    estimate_cost_usd,
    llm_span,
    tracing_enabled,
)


@pytest.fixture
def no_langsmith_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)


@pytest.fixture
def fake_langsmith_key(monkeypatch):
    """Present-but-fake key; endpoint pointed at a closed local port so no
    background upload ever leaves the machine."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_fake_key_for_tests")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")


class TestCostTable:
    def test_haiku_dated_id_resolves_by_prefix(self):
        # 1000 in @ $1/MTok + 1000 out @ $5/MTok = $0.006
        assert estimate_cost_usd("claude-haiku-4-5-20251001", 1000, 1000) == 0.006

    def test_local_ollama_model_is_free(self):
        assert estimate_cost_usd("llama3.2", 5000, 5000) == 0.0

    def test_unknown_hosted_model_returns_none_not_a_guess(self):
        assert estimate_cost_usd("claude-future-9", 1000, 1000) is None

    def test_usage_metadata_shape(self):
        meta = build_usage_metadata("anthropic", "claude-haiku-4-5-20251001", 200, 100)
        assert meta["total_tokens"] == 300
        assert meta["ls_provider"] == "anthropic"
        assert meta["estimated_cost_usd"] == pytest.approx(0.0007)


class TestKeyAbsent:
    def test_tracing_disabled(self, no_langsmith_key):
        assert tracing_enabled() is False
        assert configure_tracing() is False

    def test_llm_span_is_noop_but_still_records(self, no_langsmith_key):
        with llm_span("t", "anthropic", "claude-haiku-4-5-20251001", {"prompt": "x"}) as span:
            span.record("out", 10, 5)
        assert span.usage["total_tokens"] == 15

    def test_llm_span_propagates_wrapped_exceptions(self, no_langsmith_key):
        with pytest.raises(ValueError, match="boom"):
            with llm_span("t", "anthropic", "m"):
                raise ValueError("boom")

    def test_langsmith_not_imported_at_module_load(self, no_langsmith_key):
        """README claim: without LANGSMITH_API_KEY, nothing extra is imported at
        module load — even with the langsmith package installed. Subprocess so
        this test's own imports can't mask it."""
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("LANGSMITH", "LANGCHAIN"))}
        code = "import sys, ragdrift.cli; sys.exit(1 if 'langsmith' in sys.modules else 0)"
        result = subprocess.run([sys.executable, "-c", code], env=env)
        assert result.returncode == 0, "importing ragdrift.cli pulled in langsmith"

    def test_explain_anthropic_unchanged(self, no_langsmith_key, monkeypatch):
        """Zero behavior change: the mocked provider call returns its text."""
        from ragdrift import cli

        fake_anthropic = _fake_anthropic_module("diagnosis text", in_tok=42, out_tok=7)
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
        assert cli._explain_anthropic("prompt") == "diagnosis text"


class TestKeyPresentButFake:
    def test_tracing_enabled(self, fake_langsmith_key):
        pytest.importorskip("langsmith")
        assert tracing_enabled() is True
        assert configure_tracing() is True

    def test_llm_span_records_without_raising(self, fake_langsmith_key):
        pytest.importorskip("langsmith")
        with llm_span("t", "anthropic", "claude-haiku-4-5-20251001", {"prompt": "x"}) as span:
            span.record("out", 100, 50)
        assert span.usage["estimated_cost_usd"] == pytest.approx(0.00035)

    def test_explain_anthropic_traced_and_unchanged(self, fake_langsmith_key, monkeypatch):
        pytest.importorskip("langsmith")
        from ragdrift import cli

        fake_anthropic = _fake_anthropic_module("traced diagnosis", in_tok=10, out_tok=3)
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
        assert cli._explain_anthropic("prompt") == "traced diagnosis"


def _fake_anthropic_module(text: str, in_tok: int, out_tok: int) -> types.ModuleType:
    mod = types.ModuleType("anthropic")

    class _Usage:
        input_tokens = in_tok
        output_tokens = out_tok

    class _Block:
        pass

    block = _Block()
    block.text = text

    class _Response:
        content = [block]
        usage = _Usage()

    class Anthropic:
        def __init__(self, *a, **k):
            self.messages = self

        def create(self, **kwargs):
            return _Response()

    mod.Anthropic = Anthropic
    return mod
