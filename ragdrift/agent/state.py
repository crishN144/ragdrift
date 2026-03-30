"""State schemas for the ragdrift LangGraph agent."""
from typing import TypedDict

from ragdrift.storage.models import DriftEvent, ScanResult


class ScanState(TypedDict):
    """State passed through the LangGraph scan pipeline."""
    # Input
    corpus_dir: str
    sample_rate: float
    explain: bool
    provider: str  # "anthropic" | "ollama"

    # Config
    chunk_size: int
    chunk_overlap: int
    use_semantic: bool  # whether to run semantic diff

    # Pipeline state
    all_doc_paths: list[str]
    sampled_doc_paths: list[str]

    # Extraction results: {doc_id: {"content": str, "headings": list, "chunks": list, ...}}
    extractions: dict

    # Reference data: {doc_id: {snapshot fields}}
    reference_data: dict

    # Diff results: {doc_id: {"structural": {...}, "lexical": {...}, "semantic": {...}}}
    diff_results: dict

    # Probe results
    retrieval_accuracy_before: float | None
    retrieval_accuracy_after: float | None

    # Classification
    drift_events: list[DriftEvent]

    # Final output
    scan_result: ScanResult | None
