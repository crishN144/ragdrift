import json
import sqlite3
from datetime import UTC, datetime

from .models import DocumentSnapshot


class SnapshotStore:
    """Manages document snapshots in SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save_snapshot(
        self,
        corpus_id: str,
        snapshot_id: str,
        doc_snapshots: list[DocumentSnapshot],
        extractions: dict[str, dict],
    ) -> None:
        """Save all document snapshots for a corpus.

        Args:
            corpus_id: Identifier for the document corpus.
            snapshot_id: Unique identifier for this snapshot batch.
            doc_snapshots: List of DocumentSnapshot dicts to persist.
            extractions: Mapping of doc_id -> {"raw_content": str, "chunks": list[str]}.
        """
        created_at = datetime.now(UTC).isoformat()
        rows = []
        for doc in doc_snapshots:
            ext = extractions.get(doc["doc_id"], {})
            rows.append((
                snapshot_id,
                created_at,
                corpus_id,
                doc["doc_id"],
                doc["extracted_at"],
                doc["chunk_count"],
                json.dumps(doc["heading_structure"]),
                doc["avg_tokens_per_chunk"],
                doc["token_std_dev"],
                json.dumps(doc["embedding_centroid"]) if doc.get("embedding_centroid") else None,
                doc["extractor_version"],
                doc["chunker_config"],
                doc["file_hash"],
                doc["parser_type"],
                ext.get("raw_content"),
                json.dumps(ext.get("chunks", [])),
            ))

        self.conn.executemany(
            """
            INSERT INTO snapshots (
                snapshot_id, created_at, corpus_id, doc_id, extracted_at,
                chunk_count, heading_structure, avg_tokens_per_chunk,
                token_std_dev, embedding_centroid, extractor_version,
                chunker_config, file_hash, parser_type, raw_content, chunks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

    def get_latest_snapshot(self, corpus_id: str) -> str | None:
        """Return the latest snapshot_id for a corpus, or None."""
        row = self.conn.execute(
            """
            SELECT snapshot_id FROM snapshots
            WHERE corpus_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (corpus_id,),
        ).fetchone()
        return row["snapshot_id"] if row else None

    def get_snapshot_docs(self, corpus_id: str, snapshot_id: str) -> list[dict]:
        """Return all document records for a given corpus snapshot."""
        rows = self.conn.execute(
            """
            SELECT * FROM snapshots
            WHERE corpus_id = ? AND snapshot_id = ?
            ORDER BY doc_id
            """,
            (corpus_id, snapshot_id),
        ).fetchall()
        return [self._deserialize_row(r) for r in rows]

    def get_doc_snapshot(
        self, corpus_id: str, doc_id: str, snapshot_id: str | None = None
    ) -> dict | None:
        """Get a specific document's snapshot.

        If snapshot_id is None, returns the latest snapshot for that doc.
        """
        if snapshot_id:
            row = self.conn.execute(
                """
                SELECT * FROM snapshots
                WHERE corpus_id = ? AND doc_id = ? AND snapshot_id = ?
                """,
                (corpus_id, doc_id, snapshot_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                """
                SELECT * FROM snapshots
                WHERE corpus_id = ? AND doc_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (corpus_id, doc_id),
            ).fetchone()
        return self._deserialize_row(row) if row else None

    def list_snapshots(self, corpus_id: str) -> list[dict]:
        """Return all distinct snapshots for a corpus with their timestamps."""
        rows = self.conn.execute(
            """
            SELECT snapshot_id, MIN(created_at) AS created_at, COUNT(*) AS doc_count
            FROM snapshots
            WHERE corpus_id = ?
            GROUP BY snapshot_id
            ORDER BY created_at DESC
            """,
            (corpus_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deserialize_row(row: sqlite3.Row) -> dict:
        """Convert a sqlite3.Row to a plain dict with JSON fields decoded."""
        d = dict(row)
        for field in ("heading_structure", "embedding_centroid", "chunks"):
            if d.get(field) is not None:
                d[field] = json.loads(d[field])
        return d
