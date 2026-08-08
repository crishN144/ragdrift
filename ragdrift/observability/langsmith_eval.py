"""Opt-in upload of a golden-query eval run to LangSmith.

Creates (or reuses) a LangSmith dataset from the corpus's golden queries,
then runs one experiment: each query is answered by a fresh BM25 index built
from the current corpus files, scored against the reference snapshot's BM25
scores (the same score-accuracy metric `ragdrift scan` uses). Scored runs
appear in the LangSmith UI under the experiment.

Requires: pip install ragdrift[langsmith] and LANGSMITH_API_KEY. Never called
implicitly — nothing uploads unless you invoke upload_golden_eval().
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ragdrift.observability.tracing import tracing_enabled


def upload_golden_eval(corpus_dir: str | Path, dataset_name: str | None = None) -> dict[str, Any]:
    """Upload the corpus's golden queries + one scored eval run to LangSmith.

    Returns a summary dict: dataset_name, experiment_name, num_queries,
    avg_score_accuracy. Raises RuntimeError when LangSmith is not configured
    or the corpus has no snapshot/golden queries.
    """
    if not tracing_enabled():
        raise RuntimeError(
            "LangSmith is not configured. Install with `pip install ragdrift[langsmith]` "
            "and set LANGSMITH_API_KEY."
        )

    corpus_dir = Path(corpus_dir).resolve()
    golden_path = corpus_dir / ".ragdrift" / "golden_queries.json"
    if not golden_path.exists():
        raise RuntimeError(
            f"No golden queries at {golden_path}. Run `ragdrift init --corpus ... --golden ...` first."
        )

    from ragdrift.core.chunking.chunker import RecursiveChunker
    from ragdrift.core.extraction.router import extract
    from ragdrift.core.indexing.bm25 import BM25Index
    from ragdrift.core.probing.evaluator import evaluate_retrieval
    from ragdrift.core.probing.golden_set import load_golden_queries
    from ragdrift.storage.models import init_db
    from ragdrift.storage.snapshots import SnapshotStore

    db_path = corpus_dir / ".ragdrift" / "ragdrift.db"
    if not db_path.exists():
        raise RuntimeError("No snapshot found. Run 'ragdrift init' first.")
    conn = init_db(db_path)
    store = SnapshotStore(conn)
    corpus_id = str(corpus_dir)
    snapshot_id = store.get_latest_snapshot(corpus_id)
    if not snapshot_id:
        conn.close()
        raise RuntimeError("No snapshot found. Run 'ragdrift init' first.")
    ref_docs = store.get_snapshot_docs(corpus_id, snapshot_id)
    conn.close()

    golden_queries = load_golden_queries(golden_path)

    # Reference index from stored snapshot chunks (same as the prober node)
    ref_index = BM25Index()
    for doc in ref_docs:
        chunks = doc.get("chunks")
        if isinstance(chunks, str):
            chunks = json.loads(chunks)
        if chunks:
            ref_index.add_document(doc["doc_id"], chunks)
    ref_index.build()
    ref_scores = evaluate_retrieval(ref_index, golden_queries)["_raw_scores"]

    # Fresh index from the current corpus files
    fresh_index = BM25Index()
    chunker = RecursiveChunker()
    for p in sorted(corpus_dir.iterdir()):
        if p.suffix in {".txt", ".md", ".pdf"}:
            try:
                e = extract(p)
                fresh_index.add_document(p.name, chunker.chunk(e["content"]))
            except Exception:
                continue
    fresh_index.build()

    from langsmith import Client

    client = Client()
    dataset_name = dataset_name or f"ragdrift-golden-{corpus_dir.name}"
    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
    else:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description=f"ragdrift golden queries for corpus {corpus_dir}",
        )
        client.create_examples(
            inputs=[{"query": q["query"]} for q in golden_queries],
            outputs=[{"expected_doc_ids": q["expected_doc_ids"]} for q in golden_queries],
            dataset_id=dataset.id,
        )

    def target(inputs: dict) -> dict:
        query = inputs["query"]
        results = fresh_index.query(query, top_k=5)
        score_map = dict(results)
        expected = next(
            (q["expected_doc_ids"] for q in golden_queries if q["query"] == query), []
        )
        fresh_score = max((score_map.get(d, 0.0) for d in expected), default=0.0)
        ref_score = ref_scores.get(query, 0.0)
        score_acc = min(fresh_score / ref_score, 1.0) if ref_score > 0 else 1.0
        return {
            "retrieved": [doc_id for doc_id, _ in results],
            "score_accuracy": round(score_acc, 4),
        }

    def score_accuracy(run: Any, example: Any) -> dict:
        return {"key": "score_accuracy", "score": run.outputs["score_accuracy"]}

    def recall_at_5(run: Any, example: Any) -> dict:
        expected = set(example.outputs.get("expected_doc_ids", []))
        retrieved = set(run.outputs.get("retrieved", []))
        score = len(expected & retrieved) / len(expected) if expected else 1.0
        return {"key": "recall_at_5", "score": score}

    from langsmith import evaluate

    result = evaluate(
        target,
        data=dataset_name,
        evaluators=[score_accuracy, recall_at_5],
        experiment_prefix="ragdrift-golden",
        client=client,
    )

    accuracies = [
        r["run"].outputs.get("score_accuracy", 0.0) for r in result if r.get("run")
    ]
    avg = round(sum(accuracies) / len(accuracies), 4) if accuracies else None
    return {
        "dataset_name": dataset_name,
        "experiment_name": getattr(result, "experiment_name", None),
        "num_queries": len(golden_queries),
        "avg_score_accuracy": avg,
    }
