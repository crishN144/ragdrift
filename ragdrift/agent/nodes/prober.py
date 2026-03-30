"""Prober node: runs golden queries against fresh vs reference BM25 index."""
import json
from pathlib import Path

from ragdrift.agent.state import ScanState
from ragdrift.core.chunking.chunker import RecursiveChunker
from ragdrift.core.extraction.router import extract
from ragdrift.core.indexing.bm25 import BM25Index
from ragdrift.core.probing.evaluator import evaluate_retrieval
from ragdrift.core.probing.golden_set import load_golden_queries


def prober_node(state: ScanState) -> dict:
    """Run golden queries against reference and fresh indexes, return accuracy scores."""
    corpus_dir = Path(state["corpus_dir"])
    reference_data = state.get("reference_data", {})

    golden_path = corpus_dir / ".ragdrift" / "golden_queries.json"
    if not golden_path.exists():
        return {"retrieval_accuracy_before": None, "retrieval_accuracy_after": None}

    golden_queries = load_golden_queries(golden_path)

    # Reference index: built from stored snapshot chunks
    ref_index = BM25Index()
    for doc_id, ref in reference_data.items():
        chunks = ref.get("chunks", [])
        if isinstance(chunks, str):
            chunks = json.loads(chunks)
        if chunks:
            ref_index.add_document(doc_id, chunks)
    ref_index.build()

    # Fresh index: re-extracted from current corpus files
    fresh_index = BM25Index()
    chunker = RecursiveChunker(
        chunk_size=state.get("chunk_size", 512),
        chunk_overlap=state.get("chunk_overlap", 50),
    )
    for p in sorted(corpus_dir.iterdir()):
        if p.suffix in {".txt", ".md", ".pdf"}:
            try:
                e = extract(p)
                fresh_index.add_document(p.name, chunker.chunk(e["content"]))
            except Exception:
                continue
    fresh_index.build()

    ref_eval = evaluate_retrieval(ref_index, golden_queries)
    fresh_eval = evaluate_retrieval(
        fresh_index, golden_queries,
        reference_scores=ref_eval["_raw_scores"],
    )

    return {
        "retrieval_accuracy_before": ref_eval["avg_score_accuracy"],
        "retrieval_accuracy_after": fresh_eval["avg_score_accuracy"],
    }
