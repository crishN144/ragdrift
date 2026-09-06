"""MCP server exposing ragdrift's drift checks as agent tools.

Runs over stdio only — stdout is the protocol channel, so this module calls
the quiet cores (run_init / run_scan with verbose=False) and never prints.
Scan/report tools return ragdrift's existing TypedDict models (ScanResult,
DriftEvent) serialized as JSON; the remaining tools return compact JSON
summaries.

Install: pip install ragdrift[mcp]
Run:     ragdrift-mcp
Register in Claude Code:  claude mcp add ragdrift -- ragdrift-mcp
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    from mcp.server import MCPServer  # mcp >= 2.0
except ImportError:  # pragma: no cover
    try:
        from mcp.server.fastmcp import FastMCP as MCPServer  # mcp 1.x
    except ImportError as e:
        raise ImportError(
            "The mcp package is required for the ragdrift MCP server. "
            "Install with: pip install ragdrift[mcp]"
        ) from e

# Tool failures must be raised as ToolError. Since mcp 2.1 any other exception
# reaches the caller as a bare "Error executing tool <name>" with the reason
# stripped, which hides the thing the agent needs to act on.
try:
    from mcp.server.mcpserver.exceptions import ToolError  # mcp >= 2.0
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp.exceptions import ToolError  # mcp 1.x

from ragdrift.cli import (
    _corpus_id,
    _explain_drift,
    _get_db_path,
    _run_probes,
    run_init,
    run_scan,
)
from ragdrift.core.chunking.chunker import RecursiveChunker
from ragdrift.observability import configure_tracing
from ragdrift.storage.drift_log import DriftLog
from ragdrift.storage.models import ScanResult, init_db
from ragdrift.storage.snapshots import SnapshotStore

mcp = MCPServer(
    "ragdrift",
    instructions=(
        "Silent regression detector for RAG document corpora. Typical flow: "
        "snapshot_corpus once, then run_drift_scan before each re-ingestion; "
        "probe_golden_queries measures retrieval accuracy, diagnose_drift adds an "
        "LLM root-cause analysis, list_recent_scans/get_latest_report read history."
    ),
)


@contextmanager
def _core_errors_as_tool_errors() -> Iterator[None]:
    """Re-raise core validation failures as ToolError.

    ragdrift's core raises ValueError for conditions the caller can act on (no
    snapshot yet, empty corpus). The core has no mcp dependency, so the
    translation happens here at the protocol boundary.
    """
    try:
        yield
    except ValueError as e:
        raise ToolError(str(e)) from e


def _resolve_corpus(corpus_dir: str) -> Path:
    path = Path(corpus_dir).expanduser().resolve()
    if not path.is_dir():
        raise ToolError(f"corpus_dir is not a directory: {path}")
    return path


def _open_drift_log(path: Path):
    db_path = _get_db_path(path)
    if not db_path.exists():
        raise ToolError(
            f"No ragdrift database for {path}. Run the snapshot_corpus tool "
            "(or `ragdrift init`) first."
        )
    return init_db(db_path)


@mcp.tool()
def snapshot_corpus(
    corpus_dir: str,
    golden_queries_path: str | None = None,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> dict[str, Any]:
    """Take a reference snapshot of a document corpus (chunk counts, headings, token stats).

    Use this once per corpus before any drift check, or again to reset the baseline
    after an intentional re-ingestion. Equivalent to `ragdrift init`.

    Args:
        corpus_dir: Absolute path to the directory of .txt/.md/.pdf documents.
        golden_queries_path: Optional path to a golden-queries JSON file
            ([{"query": ..., "expected_doc_ids": [...], "domain": ...}]); enables
            retrieval-accuracy probing on later scans.
        chunk_size: Chunker target size in characters (default 512).
        chunk_overlap: Chunker overlap in characters (default 50).
    """
    path = _resolve_corpus(corpus_dir)
    with _core_errors_as_tool_errors():
        return run_init(
            path,
            golden=golden_queries_path,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            verbose=False,
        )


@mcp.tool()
def run_drift_scan(
    corpus_dir: str,
    sample_rate: float = 1.0,
    explain: bool = False,
    provider: str = "anthropic",
) -> ScanResult:
    """Scan a corpus for silent drift/regressions against its latest snapshot.

    Use this to check whether documents changed in ways that hurt RAG retrieval
    (chunk explosions, heading loss, hidden unicode, table misalignment) — run it
    before re-ingesting a corpus or when retrieval quality seems degraded. The
    result is logged to the corpus's drift history. Requires a prior snapshot.

    Args:
        corpus_dir: Absolute path to the snapshotted corpus directory.
        sample_rate: Fraction of documents to check, 0.0-1.0 (default 1.0 = all;
            the CLI defaults to 0.2 for large corpora).
        explain: If true, also run an LLM diagnosis of the drift (needs
            ANTHROPIC_API_KEY, or a local Ollama when provider="ollama").
        provider: LLM provider for explain — "anthropic" or "ollama".
    """
    path = _resolve_corpus(corpus_dir)
    with _core_errors_as_tool_errors():
        return run_scan(
            path,
            sample_rate=sample_rate,
            explain=explain,
            provider=provider,
            verbose=False,
        )


@mcp.tool()
def probe_golden_queries(corpus_dir: str) -> dict[str, Any]:
    """Run the corpus's golden queries against reference vs current BM25 indexes.

    Use this to measure retrieval accuracy directly — per-query score_accuracy
    (current BM25 score / snapshot-time score) plus recall@5 — without running a
    full drift scan. Requires a snapshot taken with golden queries.

    Args:
        corpus_dir: Absolute path to the snapshotted corpus directory.
    """
    path = _resolve_corpus(corpus_dir)
    golden_path = path / ".ragdrift" / "golden_queries.json"
    if not golden_path.exists():
        raise ToolError(
            f"No golden queries at {golden_path}. Snapshot with golden_queries_path first."
        )

    conn = _open_drift_log(path)
    try:
        store = SnapshotStore(conn)
        corpus = _corpus_id(path)
        snapshot_id = store.get_latest_snapshot(corpus)
        if not snapshot_id:
            raise ToolError("No snapshot found. Run the snapshot_corpus tool first.")
        ref_docs = store.get_snapshot_docs(corpus, snapshot_id)
    finally:
        conn.close()

    with _core_errors_as_tool_errors():
        before, after, per_query = _run_probes(path, ref_docs, [], RecursiveChunker(), golden_path)
    return {
        "snapshot_id": snapshot_id,
        "retrieval_accuracy_before": before,
        "retrieval_accuracy_after": after,
        "accuracy_drop_pp": round((before - after) * 100, 2)
        if before is not None and after is not None
        else None,
        "per_query": per_query,
    }


@mcp.tool()
def diagnose_drift(
    corpus_dir: str,
    scan_id: str | None = None,
    provider: str = "anthropic",
) -> dict[str, Any]:
    """Run an LLM root-cause diagnosis on the drifted documents of a stored scan.

    Use this after run_drift_scan reports drift, to get per-document root causes,
    retrieval-impact explanations, and fixes. Needs ANTHROPIC_API_KEY (or a local
    Ollama when provider="ollama").

    Args:
        corpus_dir: Absolute path to the snapshotted corpus directory.
        scan_id: Scan to diagnose; defaults to the most recent scan.
        provider: "anthropic" (Claude Haiku) or "ollama" (local llama3.2).
    """
    path = _resolve_corpus(corpus_dir)
    conn = _open_drift_log(path)
    try:
        drift_log = DriftLog(conn)
        if scan_id:
            scan = drift_log.get_scan(scan_id)
            if not scan:
                raise ToolError(f"No scan with id {scan_id!r} for this corpus.")
        else:
            history = drift_log.get_corpus_history(_corpus_id(path))
            if not history:
                raise ToolError("No scans recorded yet. Run the run_drift_scan tool first.")
            scan = history[0]
    finally:
        conn.close()

    drifted = [e for e in scan["drift_events"] if e["severity"] != "none"]
    if not drifted:
        return {"scan_id": scan["scan_id"], "diagnosis": None,
                "note": "No drifted documents in this scan — nothing to diagnose."}

    with _core_errors_as_tool_errors():
        raw = _explain_drift(scan, provider, extra_context={})
    if raw is None:
        raise ToolError(
            f"LLM diagnosis failed via provider {provider!r} — check credentials "
            "(ANTHROPIC_API_KEY) or that Ollama is running."
        )
    diagnosis: Any = raw
    try:
        stripped = raw.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0]
        diagnosis = json.loads(stripped.strip())
    except (json.JSONDecodeError, AttributeError):
        pass  # return the raw text if the model didn't emit clean JSON
    return {"scan_id": scan["scan_id"], "provider": provider, "diagnosis": diagnosis}


@mcp.tool()
def list_recent_scans(corpus_dir: str, limit: int = 10) -> list[dict[str, Any]]:
    """List recent drift scans for a corpus, newest first (summaries only).

    Use this to check when a corpus was last scanned and how severity has trended;
    fetch a specific scan's full detail with get_latest_report or diagnose_drift.

    Args:
        corpus_dir: Absolute path to the snapshotted corpus directory.
        limit: Maximum number of scans to return (default 10).
    """
    path = _resolve_corpus(corpus_dir)
    conn = _open_drift_log(path)
    try:
        history = DriftLog(conn).get_corpus_history(_corpus_id(path))
    finally:
        conn.close()
    return [
        {
            "scan_id": s["scan_id"],
            "timestamp": s["timestamp"],
            "docs_sampled": s["docs_sampled"],
            "docs_drifted": s["docs_drifted"],
            "overall_severity": s["overall_severity"],
            "retrieval_accuracy_before": s["retrieval_accuracy_before"],
            "retrieval_accuracy_after": s["retrieval_accuracy_after"],
            "has_diagnosis": s["diagnosis"] is not None,
        }
        for s in history[:limit]
    ]


@mcp.tool()
def get_latest_report(corpus_dir: str) -> ScanResult:
    """Fetch the full report of the most recent drift scan, including every drift event.

    Use this to inspect the latest scan without re-scanning — per-document severity,
    chunk deltas, heading changes, lexical anomalies, recommended actions, and any
    stored LLM diagnosis.

    Args:
        corpus_dir: Absolute path to the snapshotted corpus directory.
    """
    path = _resolve_corpus(corpus_dir)
    conn = _open_drift_log(path)
    try:
        history = DriftLog(conn).get_corpus_history(_corpus_id(path))
    finally:
        conn.close()
    if not history:
        raise ToolError("No scans recorded yet. Run the run_drift_scan tool first.")
    return history[0]


def main() -> None:
    """Console entry point: `ragdrift-mcp` (stdio transport)."""
    configure_tracing()  # no-op unless ragdrift[langsmith] + LANGSMITH_API_KEY
    mcp.run()


if __name__ == "__main__":
    main()
