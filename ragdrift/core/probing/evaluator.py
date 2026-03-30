"""Evaluates retrieval accuracy using golden query sets.

Metric: score-weighted accuracy.

Plain recall@k fails for topic-distinct corpora where the relevant doc is the
ONLY doc about a topic — it always appears in the top-k regardless of content
loss.  Instead we measure how much the expected document's BM25 score drops
relative to its own baseline:

    score_accuracy(query) = clamp(fresh_score / ref_score, 0, 1)

When "past consideration" or "duress" keywords are removed from a doc, its
BM25 score for those queries drops 40–70%.  Averaging across all queries gives
a corpus-level number that falls visibly (e.g. 100% → 62%).
"""
from typing import Any, Protocol


class Retriever(Protocol):
    def query(self, query_text: str, top_k: int = 5) -> list[tuple[str, float]]: ...


def recall_at_k(
    retrieved_doc_ids: list[str],
    expected_doc_ids: list[str],
    k: int = 5,
) -> float:
    """Calculate recall@k: fraction of expected docs found in top-k results."""
    if not expected_doc_ids:
        return 1.0
    retrieved_set = set(retrieved_doc_ids[:k])
    expected_set = set(expected_doc_ids)
    return len(retrieved_set & expected_set) / len(expected_set)


def _expected_score(
    score_map: dict[str, float],
    expected_doc_ids: list[str],
) -> float:
    """Return the highest BM25 score among the expected docs."""
    return max((score_map.get(doc_id, 0.0) for doc_id in expected_doc_ids), default=0.0)


def evaluate_retrieval(
    retriever: Retriever,
    golden_queries: list[dict[str, Any]],
    k: int = 5,
    reference_scores: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Run golden queries against a retriever and measure accuracy.

    Args:
        retriever: A BM25 or vector index that implements .query().
        golden_queries: List of golden query dicts with keys:
            - query: str
            - expected_doc_ids: List[str]
        k: Top-k results to consider.
        reference_scores: If provided (dict of query→ref_score), computes
            score_accuracy = fresh_score / ref_score.  Used when comparing
            a fresh index against a reference snapshot.

    Returns:
        Dict with avg_recall_at_k, avg_score_accuracy, per-query results,
        and raw expected scores (to be passed as reference_scores later).
    """
    per_query = []
    total_recall = 0.0
    total_score_accuracy = 0.0
    raw_scores: dict[str, float] = {}

    for gq in golden_queries:
        results = retriever.query(gq["query"], top_k=k)
        retrieved_ids = [doc_id for doc_id, _ in results]
        score_map = dict(results)

        r_at_k = recall_at_k(retrieved_ids, gq["expected_doc_ids"], k)
        total_recall += r_at_k

        exp_score = _expected_score(score_map, gq["expected_doc_ids"])
        raw_scores[gq["query"]] = exp_score

        # Score accuracy: fresh / reference (clamped to [0, 1])
        if reference_scores is not None:
            ref_s = reference_scores.get(gq["query"], 0.0)
            score_acc = min(exp_score / ref_s, 1.0) if ref_s > 0 else 1.0
        else:
            score_acc = 1.0  # no comparison possible yet

        total_score_accuracy += score_acc

        per_query.append({
            "query": gq["query"],
            "expected": gq["expected_doc_ids"],
            "retrieved": retrieved_ids,
            "recall_at_k": r_at_k,
            "expected_doc_score": round(exp_score, 4),
            "score_accuracy": round(score_acc, 4),
        })

    n = len(golden_queries) or 1
    avg_recall = total_recall / n
    avg_score_accuracy = total_score_accuracy / n

    return {
        "avg_recall_at_k": round(avg_recall, 4),
        "avg_score_accuracy": round(avg_score_accuracy, 4),
        "k": k,
        "num_queries": n,
        "per_query": per_query,
        "_raw_scores": raw_scores,  # passed as reference_scores for fresh eval
    }
