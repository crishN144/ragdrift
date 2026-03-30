"""Classifier node: rule-based severity assignment (no LLM needed)."""
from ragdrift.agent.state import ScanState
from ragdrift.storage.models import DriftEvent

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "none": 0}


def _max_severity(severities: list) -> str:
    return max(severities, key=lambda s: _SEVERITY_ORDER.get(s, 0), default="none")


def _assess_retrieval_impact(
    chunk_diff: dict,
    heading_diff: dict,
    token_diff: dict,
    retrieval_before: float | None,
    retrieval_after: float | None,
) -> bool:
    if abs(chunk_diff.get("delta_pct", 0)) > 15:
        return True
    if token_diff.get("shift_score", 0) > 0.2:
        return True
    if len(heading_diff.get("changes", [])) > 2:
        return True
    if retrieval_before is not None and retrieval_after is not None and retrieval_before > 0:
        if (retrieval_before - retrieval_after) / retrieval_before > 0.1:
            return True
    return False


def _recommend_action(severity: str, retrieval_impact: bool) -> str:
    if severity == "critical" or (severity == "high" and retrieval_impact):
        return "re_ingest"
    if severity == "high" or (severity == "medium" and retrieval_impact):
        return "alert"
    if severity in ("medium", "low"):
        return "monitor"
    return "none"


def classifier_node(state: ScanState) -> dict:
    """Classify drift severity for each document based on diff results."""
    diff_results = state.get("diff_results", {})
    extractions = state.get("extractions", {})
    reference_data = state.get("reference_data", {})
    retrieval_before = state.get("retrieval_accuracy_before")
    retrieval_after = state.get("retrieval_accuracy_after")

    drift_events: list[DriftEvent] = []

    for doc_id, diffs in diff_results.items():
        structural = diffs.get("structural", {})
        lexical = diffs.get("lexical", {})
        semantic = diffs.get("semantic", {})
        ref = reference_data.get(doc_id, {})

        chunk_diff = structural.get("chunks", {})
        heading_diff = structural.get("headings", {})
        table_diff = structural.get("tables", {})
        token_diff = lexical.get("token_distribution", {})
        char_anomalies = lexical.get("character_anomalies", {})

        severity = _max_severity([
            chunk_diff.get("severity", "none"),
            heading_diff.get("severity", "none"),
            table_diff.get("severity", "none"),
            token_diff.get("severity", "none"),
            char_anomalies.get("severity", "none"),
            semantic.get("severity", "none"),
        ])

        # Collect anomalies list for the event
        anomalies: list[str] = []
        if token_diff.get("severity", "none") != "none":
            anomalies.append(f"token_shift={token_diff.get('shift_score', 0)}")
        anomalies.extend(char_anomalies.get("anomalies", []))
        anomalies.extend(table_diff.get("changes", []))

        retrieval_impact = _assess_retrieval_impact(
            chunk_diff, heading_diff, token_diff, retrieval_before, retrieval_after
        )
        action = _recommend_action(severity, retrieval_impact)

        new_chunks = extractions.get(doc_id, {}).get("chunks", [])
        drift_events.append(DriftEvent(
            doc_id=doc_id,
            severity=severity,
            chunk_count_before=ref.get("chunk_count", 0),
            chunk_count_after=len(new_chunks),
            chunk_delta_pct=chunk_diff.get("delta_pct", 0.0),
            heading_changes=heading_diff.get("changes", []),
            lexical_anomalies=anomalies,
            semantic_drift_score=semantic.get("drift_score", 0.0),
            recommended_action=action,
            retrieval_impact=retrieval_impact,
        ))

    return {"drift_events": drift_events}
