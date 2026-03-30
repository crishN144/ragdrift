"""Extractor node: re-runs extraction on sampled documents."""
from pathlib import Path

from ragdrift.agent.state import ScanState
from ragdrift.core.chunking.chunker import RecursiveChunker
from ragdrift.core.extraction.router import extract


def extractor_node(state: ScanState) -> dict:
    """Re-extract sampled documents using the same pipeline as original ingest."""
    sampled_paths = state["sampled_doc_paths"]
    chunk_size = state.get("chunk_size", 512)
    chunk_overlap = state.get("chunk_overlap", 50)

    chunker = RecursiveChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    extractions = {}

    for path_str in sampled_paths:
        path = Path(path_str)
        try:
            result = extract(path)
            chunks = chunker.chunk(result["content"])
            extractions[path.name] = {
                "content": result["content"],
                "headings": result["headings"],
                "tables": result.get("tables", []),
                "chunks": chunks,
                "file_hash": result["file_hash"],
                "parser_type": result["parser_type"],
                "extractor_version": result["extractor_version"],
            }
        except Exception as e:
            extractions[path.name] = {"error": str(e)}

    return {"extractions": extractions}
