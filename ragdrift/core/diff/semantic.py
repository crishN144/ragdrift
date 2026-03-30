"""Semantic diff: embedding cosine distance from reference centroid.

Only available when ragdrift[vector] is installed.
"""
from typing import Any

import numpy as np


def compute_centroid(embeddings: list[list[float]]) -> list[float]:
    """Compute the centroid of a list of embeddings."""
    if not embeddings:
        return []
    arr = np.array(embeddings)
    centroid = arr.mean(axis=0)
    return centroid.tolist()


def cosine_distance(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine distance between two vectors."""
    a = np.array(vec_a)
    b = np.array(vec_b)
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 1.0
    return 1.0 - (dot / norm)


def diff_semantic(
    reference_centroid: list[float],
    new_chunks: list[str],
    embed_fn: Any | None = None,
) -> dict[str, Any]:
    """Compare semantic drift using embedding centroids.

    Args:
        reference_centroid: stored centroid from snapshot
        new_chunks: new chunk texts to embed and compare
        embed_fn: function that takes List[str] and returns List[List[float]]
    """
    if not reference_centroid or embed_fn is None:
        return {"drift_score": 0.0, "severity": "none", "available": False}

    new_embeddings = embed_fn(new_chunks)
    new_centroid = compute_centroid(new_embeddings)

    drift_score = cosine_distance(reference_centroid, new_centroid)

    severity = _semantic_severity(drift_score)
    return {
        "drift_score": round(drift_score, 4),
        "severity": severity,
        "available": True,
    }


def _semantic_severity(score: float) -> str:
    if score > 0.3:
        return "critical"
    if score > 0.15:
        return "high"
    if score > 0.08:
        return "medium"
    if score > 0.03:
        return "low"
    return "none"
