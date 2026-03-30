"""Differ node: runs structural, lexical diffs in parallel per doc."""
import json

from ragdrift.agent.state import ScanState
from ragdrift.core.diff.lexical import detect_character_anomalies, diff_token_distribution
from ragdrift.core.diff.structural import diff_chunk_count, diff_headings, diff_table_rows_in_chunks


def differ_node(state: ScanState) -> dict:
    """Run diffs comparing new extractions against stored reference snapshots."""
    extractions = state.get("extractions", {})
    reference_data = state.get("reference_data", {})
    use_semantic = state.get("use_semantic", False)

    diff_results = {}

    for doc_id, extraction in extractions.items():
        if "error" in extraction:
            continue

        ref = reference_data.get(doc_id)
        if not ref:
            continue

        ref_headings = ref.get("heading_structure", [])
        if isinstance(ref_headings, str):
            ref_headings = json.loads(ref_headings)

        ref_chunks = ref.get("chunks", [])
        if isinstance(ref_chunks, str):
            ref_chunks = json.loads(ref_chunks)

        new_chunks = extraction.get("chunks", [])

        # Structural diffs
        chunk_diff = diff_chunk_count(ref.get("chunk_count", 0), len(new_chunks))
        heading_diff = diff_headings(ref_headings, extraction.get("headings", []))
        table_diff = diff_table_rows_in_chunks(ref_chunks, new_chunks)

        # Lexical diffs
        token_diff = diff_token_distribution(ref_chunks, new_chunks)
        char_anomalies = detect_character_anomalies(extraction.get("content", ""))

        # Semantic diff (optional, requires ragdrift[vector])
        semantic_result = {"drift_score": 0.0, "severity": "none", "available": False}
        if use_semantic:
            try:
                from sentence_transformers import SentenceTransformer

                from ragdrift.core.diff.semantic import diff_semantic
                ref_centroid = ref.get("embedding_centroid", [])
                if isinstance(ref_centroid, str):
                    ref_centroid = json.loads(ref_centroid)
                if ref_centroid and new_chunks:
                    model = SentenceTransformer("all-MiniLM-L6-v2")
                    semantic_result = diff_semantic(
                        ref_centroid, new_chunks,
                        embed_fn=lambda texts: model.encode(texts).tolist(),
                    )
            except ImportError:
                pass

        diff_results[doc_id] = {
            "structural": {
                "chunks": chunk_diff,
                "headings": heading_diff,
                "tables": table_diff,
            },
            "lexical": {
                "token_distribution": token_diff,
                "character_anomalies": char_anomalies,
            },
            "semantic": semantic_result,
        }

    return {"diff_results": diff_results}
