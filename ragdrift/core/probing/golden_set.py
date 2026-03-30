"""Manages golden query sets for retrieval probing."""
import json
from pathlib import Path
from typing import TypedDict


class GoldenQuery(TypedDict):
    query: str
    expected_doc_ids: list[str]
    domain: str

def load_golden_queries(path: Path) -> list[GoldenQuery]:
    """Load golden queries from a JSON file."""
    with open(path) as f:
        data = json.load(f)
    return data

def save_golden_queries(queries: list[GoldenQuery], path: Path) -> None:
    """Save golden queries to a JSON file."""
    with open(path, "w") as f:
        json.dump(queries, f, indent=2)
