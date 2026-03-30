import json
import sqlite3

from .models import DriftEvent, ScanResult


class DriftLog:
    """Manages drift event logging and scan result persistence."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def log_scan(self, scan_result: ScanResult) -> None:
        """Save a scan result and all its drift events.

        Args:
            scan_result: A ScanResult dict containing the scan metadata
                         and a list of DriftEvent dicts.
        """
        # Insert the scan summary
        self.conn.execute(
            """
            INSERT INTO scan_results (
                scan_id, corpus_id, timestamp, docs_sampled, docs_drifted,
                overall_severity, retrieval_accuracy_before,
                retrieval_accuracy_after, diagnosis
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_result["scan_id"],
                scan_result["corpus_id"],
                scan_result["timestamp"],
                scan_result["docs_sampled"],
                scan_result["docs_drifted"],
                scan_result["overall_severity"],
                scan_result.get("retrieval_accuracy_before"),
                scan_result.get("retrieval_accuracy_after"),
                scan_result.get("diagnosis"),
            ),
        )

        # Insert each drift event
        event_rows = []
        for event in scan_result.get("drift_events", []):
            event_rows.append((
                scan_result["scan_id"],
                scan_result["corpus_id"],
                scan_result["timestamp"],
                event["doc_id"],
                event["severity"],
                event.get("chunk_count_before"),
                event.get("chunk_count_after"),
                event.get("chunk_delta_pct"),
                json.dumps(event.get("heading_changes", [])),
                json.dumps(event.get("lexical_anomalies", [])),
                event.get("semantic_drift_score"),
                event.get("recommended_action"),
                int(event.get("retrieval_impact", False)),
            ))

        if event_rows:
            self.conn.executemany(
                """
                INSERT INTO drift_log (
                    scan_id, corpus_id, timestamp, doc_id, severity,
                    chunk_count_before, chunk_count_after, chunk_delta_pct,
                    heading_changes, lexical_anomalies, semantic_drift_score,
                    recommended_action, retrieval_impact
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                event_rows,
            )

        self.conn.commit()

    def get_scan(self, scan_id: str) -> ScanResult | None:
        """Retrieve a scan result with all its drift events."""
        row = self.conn.execute(
            "SELECT * FROM scan_results WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        if not row:
            return None

        scan: ScanResult = {
            "corpus_id": row["corpus_id"],
            "scan_id": row["scan_id"],
            "timestamp": row["timestamp"],
            "docs_sampled": row["docs_sampled"],
            "docs_drifted": row["docs_drifted"],
            "overall_severity": row["overall_severity"],
            "retrieval_accuracy_before": row["retrieval_accuracy_before"],
            "retrieval_accuracy_after": row["retrieval_accuracy_after"],
            "drift_events": self._get_events_for_scan(scan_id),
            "diagnosis": row["diagnosis"],
        }
        return scan

    def get_corpus_history(self, corpus_id: str) -> list[ScanResult]:
        """Return all scan results for a corpus, ordered by timestamp descending."""
        rows = self.conn.execute(
            """
            SELECT * FROM scan_results
            WHERE corpus_id = ?
            ORDER BY timestamp DESC
            """,
            (corpus_id,),
        ).fetchall()

        results: list[ScanResult] = []
        for row in rows:
            results.append({
                "corpus_id": row["corpus_id"],
                "scan_id": row["scan_id"],
                "timestamp": row["timestamp"],
                "docs_sampled": row["docs_sampled"],
                "docs_drifted": row["docs_drifted"],
                "overall_severity": row["overall_severity"],
                "retrieval_accuracy_before": row["retrieval_accuracy_before"],
                "retrieval_accuracy_after": row["retrieval_accuracy_after"],
                "drift_events": self._get_events_for_scan(row["scan_id"]),
                "diagnosis": row["diagnosis"],
            })
        return results

    def get_doc_history(self, doc_id: str) -> list[DriftEvent]:
        """Return all drift events for a specific document, ordered by timestamp."""
        rows = self.conn.execute(
            """
            SELECT * FROM drift_log
            WHERE doc_id = ?
            ORDER BY timestamp DESC
            """,
            (doc_id,),
        ).fetchall()
        return [self._deserialize_event(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_events_for_scan(self, scan_id: str) -> list[DriftEvent]:
        """Fetch and deserialize all drift events for a scan."""
        rows = self.conn.execute(
            "SELECT * FROM drift_log WHERE scan_id = ?",
            (scan_id,),
        ).fetchall()
        return [self._deserialize_event(r) for r in rows]

    @staticmethod
    def _deserialize_event(row: sqlite3.Row) -> DriftEvent:
        """Convert a drift_log row into a DriftEvent dict."""
        return {
            "doc_id": row["doc_id"],
            "severity": row["severity"],
            "chunk_count_before": row["chunk_count_before"],
            "chunk_count_after": row["chunk_count_after"],
            "chunk_delta_pct": row["chunk_delta_pct"],
            "heading_changes": json.loads(row["heading_changes"]) if row["heading_changes"] else [],
            "lexical_anomalies": json.loads(row["lexical_anomalies"]) if row["lexical_anomalies"] else [],
            "semantic_drift_score": row["semantic_drift_score"],
            "recommended_action": row["recommended_action"],
            "retrieval_impact": bool(row["retrieval_impact"]),
        }
