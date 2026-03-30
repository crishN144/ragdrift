"""FastAPI REST API for ragdrift.

Endpoints:
    GET  /corpora                       List all known corpora
    POST /corpora/{corpus_id}/init      Initialize a corpus (take snapshot)
    POST /corpora/{corpus_id}/scan      Run a drift scan
    GET  /corpora/{corpus_id}/reports   Get scan history for a corpus
    GET  /corpora/{corpus_id}/reports/{scan_id}  Get a specific scan

Install: pip install ragdrift[api]
Run:     uvicorn ragdrift.api.main:app --reload
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="ragdrift",
    description="Silent regression detector for RAG pipelines",
    version="0.1.0",
)

# ── Request/Response models ────────────────────────────────


class InitRequest(BaseModel):
    corpus_path: str
    golden_queries_path: str | None = None
    chunk_size: int = 512
    chunk_overlap: int = 50


class ScanRequest(BaseModel):
    corpus_path: str
    sample_rate: float = 0.2
    explain: bool = False
    provider: str = "anthropic"


class CorpusInfo(BaseModel):
    corpus_id: str
    corpus_path: str
    snapshot_count: int
    latest_snapshot: str | None
    last_scan: str | None


# ── DB helpers (reuse same pattern as CLI) ─────────────────


def _get_db(corpus_path: str) -> sqlite3.Connection:
    from ragdrift.storage.models import init_db
    db_path = Path(corpus_path) / ".ragdrift" / "ragdrift.db"
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Corpus not initialised. Run init first.")
    return init_db(db_path)


# ── Routes ─────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/corpora/{corpus_id}/init")
def init_corpus(corpus_id: str, req: InitRequest):
    """Take a reference snapshot of a corpus."""
    import argparse

    from ragdrift.cli import cmd_init

    # Patch argparse namespace for CLI reuse
    args = argparse.Namespace(
        corpus=req.corpus_path,
        golden=req.golden_queries_path,
        chunk_size=req.chunk_size,
        chunk_overlap=req.chunk_overlap,
    )
    try:
        cmd_init(args)
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=f"Init failed: {e}")

    return {"status": "ok", "corpus_path": req.corpus_path}


@app.post("/corpora/{corpus_id}/scan")
def scan_corpus(corpus_id: str, req: ScanRequest):
    """Run a drift scan and return the full scan result."""
    import argparse
    import io
    import sys

    from ragdrift.cli import cmd_scan

    # Capture JSON output from cmd_scan
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured

    args = argparse.Namespace(
        corpus=req.corpus_path,
        sample_rate=req.sample_rate,
        explain=req.explain,
        provider=req.provider,
        format="json",
        chunk_size=512,
        chunk_overlap=50,
    )
    try:
        cmd_scan(args)
    except SystemExit as e:
        sys.stdout = old_stdout
        raise HTTPException(status_code=400, detail=f"Scan failed: {e}")
    finally:
        sys.stdout = old_stdout

    output = captured.getvalue().strip()
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Scan produced invalid output")


@app.get("/corpora/{corpus_id}/reports")
def get_reports(corpus_id: str, corpus_path: str):
    """Get scan history for a corpus."""
    from ragdrift.storage.drift_log import DriftLog
    conn = _get_db(corpus_path)
    log = DriftLog(conn)
    history = log.get_corpus_history(str(Path(corpus_path).resolve()))
    conn.close()
    return {"corpus_id": corpus_id, "scans": history}


@app.get("/corpora/{corpus_id}/reports/{scan_id}")
def get_report(corpus_id: str, scan_id: str, corpus_path: str):
    """Get a specific scan result."""
    from ragdrift.storage.drift_log import DriftLog
    conn = _get_db(corpus_path)
    log = DriftLog(conn)
    scan = log.get_scan(scan_id)
    conn.close()
    if not scan:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")
    return scan
