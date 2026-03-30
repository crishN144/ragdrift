import sqlite3
from pathlib import Path
from typing import Literal, TypedDict


# State schemas
class DocumentSnapshot(TypedDict):
    doc_id: str
    extracted_at: str  # ISO 8601
    chunk_count: int
    heading_structure: list[str]
    avg_tokens_per_chunk: float
    token_std_dev: float
    embedding_centroid: list[float]
    extractor_version: str
    chunker_config: str
    file_hash: str
    parser_type: str

class DriftEvent(TypedDict):
    doc_id: str
    severity: Literal["none", "low", "medium", "high", "critical"]
    chunk_count_before: int
    chunk_count_after: int
    chunk_delta_pct: float
    heading_changes: list[str]
    lexical_anomalies: list[str]
    semantic_drift_score: float
    recommended_action: Literal["none", "monitor", "alert", "re_ingest"]
    retrieval_impact: bool

class ScanResult(TypedDict):
    corpus_id: str
    scan_id: str
    timestamp: str  # ISO 8601
    docs_sampled: int
    docs_drifted: int
    overall_severity: str
    retrieval_accuracy_before: float | None
    retrieval_accuracy_after: float | None
    drift_events: list[DriftEvent]
    diagnosis: str | None

# SQL table creation
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    corpus_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    heading_structure TEXT NOT NULL,  -- JSON array
    avg_tokens_per_chunk REAL NOT NULL,
    token_std_dev REAL NOT NULL,
    embedding_centroid TEXT,  -- JSON array, nullable
    extractor_version TEXT NOT NULL,
    chunker_config TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    parser_type TEXT NOT NULL,
    raw_content TEXT,  -- store extraction content for diff
    chunks TEXT  -- JSON array of chunk texts
);

CREATE TABLE IF NOT EXISTS drift_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL,
    corpus_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    chunk_count_before INTEGER,
    chunk_count_after INTEGER,
    chunk_delta_pct REAL,
    heading_changes TEXT,  -- JSON
    lexical_anomalies TEXT,  -- JSON
    semantic_drift_score REAL,
    recommended_action TEXT,
    retrieval_impact INTEGER  -- boolean as int
);

CREATE TABLE IF NOT EXISTS scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL UNIQUE,
    corpus_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    docs_sampled INTEGER NOT NULL,
    docs_drifted INTEGER NOT NULL,
    overall_severity TEXT NOT NULL,
    retrieval_accuracy_before REAL,
    retrieval_accuracy_after REAL,
    diagnosis TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_corpus ON snapshots(corpus_id, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_doc ON snapshots(doc_id);
CREATE INDEX IF NOT EXISTS idx_drift_scan ON drift_log(scan_id);
CREATE INDEX IF NOT EXISTS idx_drift_doc ON drift_log(doc_id);
CREATE INDEX IF NOT EXISTS idx_scans_corpus ON scan_results(corpus_id);
"""

def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize the SQLite database with schema."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn
