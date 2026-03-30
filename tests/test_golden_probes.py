"""Tests for golden query probing and score-accuracy metric."""
import pytest

from ragdrift.core.indexing.bm25 import BM25Index
from ragdrift.core.probing.evaluator import evaluate_retrieval, recall_at_k

SAMPLE_DOCS = {
    "doc_contract.txt": [
        "A valid contract requires offer acceptance and consideration.",
        "Past consideration is insufficient. The statute of frauds requires a writing.",
        "Defenses include duress unconscionability and undue influence.",
    ],
    "doc_medical.txt": [
        "The CYP450 enzyme system metabolizes approximately 75 percent of all drugs.",
        "Drug interactions can be pharmacokinetic or pharmacodynamic in nature.",
    ],
    "doc_tech.txt": [
        "Microservices communicate via REST APIs and message queues like Kafka.",
        "Container orchestration with Kubernetes manages deployment and scaling.",
    ],
}

GOLDEN_QUERIES = [
    {
        "query": "What is consideration and why is past consideration insufficient?",
        "expected_doc_ids": ["doc_contract.txt"],
    },
    {
        "query": "How does the CYP450 enzyme system affect drug metabolism?",
        "expected_doc_ids": ["doc_medical.txt"],
    },
    {
        "query": "How do microservices communicate in cloud architecture?",
        "expected_doc_ids": ["doc_tech.txt"],
    },
]


def build_index(docs: dict) -> BM25Index:
    idx = BM25Index()
    for doc_id, chunks in docs.items():
        idx.add_document(doc_id, chunks)
    idx.build()
    return idx


class TestRecallAtK:
    def test_perfect_recall(self):
        assert recall_at_k(["doc_a", "doc_b"], ["doc_a"], k=5) == 1.0

    def test_zero_recall(self):
        assert recall_at_k(["doc_b", "doc_c"], ["doc_a"], k=5) == 0.0

    def test_partial_recall(self):
        assert recall_at_k(["doc_a", "doc_b"], ["doc_a", "doc_c"], k=5) == 0.5

    def test_empty_expected(self):
        assert recall_at_k(["doc_a"], [], k=5) == 1.0


class TestBM25Retrieval:
    def test_reference_retrieves_correctly(self):
        idx = build_index(SAMPLE_DOCS)
        results = idx.query("What is past consideration in contract law?", top_k=3)
        retrieved_ids = [doc_id for doc_id, _ in results]
        assert "doc_contract.txt" in retrieved_ids

    def test_score_drops_after_content_removal(self):
        """When specific keywords are removed, the expected doc's score drops."""
        # Reference index with full content
        ref_idx = build_index(SAMPLE_DOCS)

        # Drifted: remove contract defenses chunk
        drifted_docs = dict(SAMPLE_DOCS)
        drifted_docs["doc_contract.txt"] = [
            "A valid contract requires offer acceptance.",
            # Removed: "Past consideration is insufficient..." and "Defenses include duress..."
        ]
        fresh_idx = build_index(drifted_docs)

        ref_eval = evaluate_retrieval(ref_idx, GOLDEN_QUERIES)
        fresh_eval = evaluate_retrieval(
            fresh_idx, GOLDEN_QUERIES,
            reference_scores=ref_eval["_raw_scores"],
        )

        # Score accuracy must drop when targeted content is removed
        assert fresh_eval["avg_score_accuracy"] < ref_eval["avg_score_accuracy"]
        assert fresh_eval["avg_score_accuracy"] < 1.0

    def test_score_accuracy_stable_for_unchanged_docs(self):
        """Non-drifted corpus should have score_accuracy close to 1.0."""
        idx = build_index(SAMPLE_DOCS)
        ref_eval = evaluate_retrieval(idx, GOLDEN_QUERIES)
        fresh_eval = evaluate_retrieval(
            idx, GOLDEN_QUERIES,
            reference_scores=ref_eval["_raw_scores"],
        )
        assert fresh_eval["avg_score_accuracy"] == pytest.approx(1.0)
