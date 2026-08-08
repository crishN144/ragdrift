"""MCP server tests — exercised via the SDK's in-memory client, no subprocess.

All LLM calls are mocked. Skipped entirely when the mcp extra isn't installed.
"""
import json
import shutil
from pathlib import Path

import pytest

_mcp_client = pytest.importorskip("mcp.client")
if not hasattr(_mcp_client, "Client"):
    pytest.skip("mcp >= 2.0 required for in-memory client tests", allow_module_level=True)
Client = _mcp_client.Client

from demo.inject_drift import inject_drift  # noqa: E402
from ragdrift.cli import run_init, run_scan  # noqa: E402
from ragdrift.mcp_server import mcp  # noqa: E402

DEMO_DIR = Path(__file__).parent.parent / "demo"

EXPECTED_TOOLS = {
    "snapshot_corpus",
    "run_drift_scan",
    "probe_golden_queries",
    "diagnose_drift",
    "list_recent_scans",
    "get_latest_report",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def corpus(tmp_path):
    """Fresh copy of the v1 demo corpus."""
    work = tmp_path / "corpus"
    shutil.copytree(DEMO_DIR / "corpus_v1", work)
    return work


@pytest.fixture
def drifted_corpus(corpus):
    """Snapshotted corpus with moderate drift injected and one scan recorded."""
    run_init(corpus, golden=str(DEMO_DIR / "golden_queries.json"))
    inject_drift(corpus, DEMO_DIR / "corpus_v2", level="moderate")
    run_scan(corpus, sample_rate=1.0)
    return corpus


def _payload(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


class TestToolSurface:
    @pytest.mark.anyio
    async def test_lists_all_six_tools(self):
        async with Client(mcp) as client:
            tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == EXPECTED_TOOLS

    @pytest.mark.anyio
    async def test_every_tool_has_agent_facing_description(self):
        async with Client(mcp) as client:
            tools = (await client.list_tools()).tools
        for tool in tools:
            assert tool.description and "Use this" in tool.description, tool.name


class TestSnapshotAndScan:
    @pytest.mark.anyio
    async def test_snapshot_corpus(self, corpus):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "snapshot_corpus",
                {"corpus_dir": str(corpus),
                 "golden_queries_path": str(DEMO_DIR / "golden_queries.json")},
            )
        data = _payload(result)
        assert data["docs_snapshotted"] == 20
        assert data["golden_queries_saved"] is True

    @pytest.mark.anyio
    async def test_scan_detects_injected_drift(self, corpus):
        async with Client(mcp) as client:
            await client.call_tool(
                "snapshot_corpus",
                {"corpus_dir": str(corpus),
                 "golden_queries_path": str(DEMO_DIR / "golden_queries.json")},
            )
            inject_drift(corpus, DEMO_DIR / "corpus_v2", level="moderate")
            result = await client.call_tool(
                "run_drift_scan", {"corpus_dir": str(corpus)}
            )
        data = _payload(result)
        assert data["docs_drifted"] > 0
        assert data["overall_severity"] in ("medium", "high", "critical")
        assert data["retrieval_accuracy_after"] < data["retrieval_accuracy_before"]

    @pytest.mark.anyio
    async def test_scan_clean_corpus_reports_none(self, corpus):
        run_init(corpus, golden=str(DEMO_DIR / "golden_queries.json"))
        async with Client(mcp) as client:
            result = await client.call_tool(
                "run_drift_scan", {"corpus_dir": str(corpus)}
            )
        data = _payload(result)
        assert data["overall_severity"] == "none"
        assert data["docs_drifted"] == 0


class TestProbeAndHistory:
    @pytest.mark.anyio
    async def test_probe_golden_queries(self, drifted_corpus):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "probe_golden_queries", {"corpus_dir": str(drifted_corpus)}
            )
        data = _payload(result)
        assert data["retrieval_accuracy_before"] == 1.0
        assert data["retrieval_accuracy_after"] < 1.0
        assert data["accuracy_drop_pp"] > 0
        assert len(data["per_query"]) > 0

    @pytest.mark.anyio
    async def test_list_recent_scans(self, drifted_corpus):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "list_recent_scans", {"corpus_dir": str(drifted_corpus)}
            )
        assert not result.is_error
        scans = [json.loads(c.text) for c in result.content]
        assert len(scans) == 1
        assert scans[0]["docs_drifted"] > 0
        assert scans[0]["has_diagnosis"] is False

    @pytest.mark.anyio
    async def test_get_latest_report(self, drifted_corpus):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_latest_report", {"corpus_dir": str(drifted_corpus)}
            )
        data = _payload(result)
        assert data["drift_events"], "full report must include drift events"
        assert {"doc_id", "severity", "recommended_action"} <= set(data["drift_events"][0])


class TestDiagnose:
    @pytest.mark.anyio
    async def test_diagnose_with_mocked_llm(self, drifted_corpus, monkeypatch):
        fake = {"overall_summary": "chunk explosion in two docs", "documents": []}
        monkeypatch.setattr(
            "ragdrift.cli._explain_anthropic", lambda prompt: json.dumps(fake)
        )
        async with Client(mcp) as client:
            result = await client.call_tool(
                "diagnose_drift", {"corpus_dir": str(drifted_corpus)}
            )
        data = _payload(result)
        assert data["diagnosis"] == fake
        assert data["provider"] == "anthropic"

    @pytest.mark.anyio
    async def test_diagnose_clean_scan_returns_note(self, corpus):
        run_init(corpus)
        run_scan(corpus, sample_rate=1.0)  # no drift injected
        async with Client(mcp) as client:
            result = await client.call_tool(
                "diagnose_drift", {"corpus_dir": str(corpus)}
            )
        data = _payload(result)
        assert data["diagnosis"] is None
        assert "nothing to diagnose" in data["note"]

    @pytest.mark.anyio
    async def test_diagnose_llm_failure_is_tool_error(self, drifted_corpus, monkeypatch):
        monkeypatch.setattr("ragdrift.cli._explain_anthropic", lambda prompt: None)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "diagnose_drift", {"corpus_dir": str(drifted_corpus)}
            )
        assert result.is_error
        assert "LLM diagnosis failed" in result.content[0].text


class TestErrors:
    @pytest.mark.anyio
    async def test_bad_corpus_path_is_tool_error_not_crash(self):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "run_drift_scan", {"corpus_dir": "/nonexistent/nowhere"}
            )
            assert result.is_error
            assert "not a directory" in result.content[0].text
            # server still answers after the error
            tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == EXPECTED_TOOLS

    @pytest.mark.anyio
    async def test_scan_without_snapshot_is_tool_error(self, corpus):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "run_drift_scan", {"corpus_dir": str(corpus)}
            )
        assert result.is_error
        assert "No snapshot found" in result.content[0].text

    @pytest.mark.anyio
    async def test_probe_without_golden_queries_is_tool_error(self, corpus):
        run_init(corpus)  # snapshot, but no golden queries
        async with Client(mcp) as client:
            result = await client.call_tool(
                "probe_golden_queries", {"corpus_dir": str(corpus)}
            )
        assert result.is_error
        assert "golden queries" in result.content[0].text.lower()
