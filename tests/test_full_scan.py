"""End-to-end test: init → inject drift → scan → verify report."""
import shutil
from pathlib import Path

import pytest

from ragdrift.core.chunking.chunker import RecursiveChunker
from ragdrift.core.diff.lexical import detect_character_anomalies
from ragdrift.core.diff.structural import diff_chunk_count, diff_headings, diff_table_rows_in_chunks
from ragdrift.core.extraction.router import extract
from ragdrift.storage.models import init_db
from ragdrift.storage.snapshots import SnapshotStore

DEMO_DIR = Path(__file__).parent.parent / "demo"
V1_DIR = DEMO_DIR / "corpus_v1"
V2_DIR = DEMO_DIR / "corpus_v2"


@pytest.fixture
def corpus(tmp_path):
    """Create a temporary corpus copy from v1."""
    work = tmp_path / "corpus"
    shutil.copytree(V1_DIR, work)
    return work


def _extract_all(corpus_dir: Path) -> dict:
    chunker = RecursiveChunker()
    result = {}
    for p in sorted(corpus_dir.glob("*.md")) + sorted(corpus_dir.glob("*.txt")):
        e = extract(p)
        result[p.name] = {
            "extraction": e,
            "chunks": chunker.chunk(e["content"]),
        }
    return result


class TestExtractionAndChunking:
    def test_extracts_all_20_docs(self, corpus):
        data = _extract_all(corpus)
        assert len(data) == 20

    def test_markdown_headings_extracted(self, corpus):
        data = _extract_all(corpus)
        md_docs = {k: v for k, v in data.items() if k.endswith(".md")}
        for name, doc in md_docs.items():
            assert len(doc["extraction"]["headings"]) > 0, f"{name} has no headings"

    def test_all_docs_produce_chunks(self, corpus):
        data = _extract_all(corpus)
        for name, doc in data.items():
            assert len(doc["chunks"]) > 0, f"{name} produced no chunks"


class TestDriftDetection:
    def test_chunk_explosion_detected(self, corpus):
        """16_cloud_architecture.md has +63% chunks in v2 → critical."""
        chunker = RecursiveChunker()

        v1_doc = V1_DIR / "16_cloud_architecture.md"
        v2_doc = V2_DIR / "16_cloud_architecture.md"

        v1_chunks = chunker.chunk(extract(v1_doc)["content"])
        v2_chunks = chunker.chunk(extract(v2_doc)["content"])

        result = diff_chunk_count(len(v1_chunks), len(v2_chunks))
        assert result["severity"] in ("high", "critical")
        assert result["delta_pct"] > 50

    def test_missing_paragraphs_detected(self, corpus):
        """11_contract_law.md loses Consideration + Defenses sections → chunk drop."""
        chunker = RecursiveChunker()

        v1_chunks = chunker.chunk(extract(V1_DIR / "11_contract_law.md")["content"])
        v2_chunks = chunker.chunk(extract(V2_DIR / "11_contract_law.md")["content"])

        result = diff_chunk_count(len(v1_chunks), len(v2_chunks))
        assert result["delta"] < 0  # fewer chunks
        assert result["severity"] in ("high", "critical")

    def test_heading_collapse_detected(self, corpus):
        """03_regulatory_compliance.md: H2 → H4 heading collapse."""
        v1_headings = extract(V1_DIR / "03_regulatory_compliance.md")["headings"]
        v2_headings = extract(V2_DIR / "03_regulatory_compliance.md")["headings"]

        result = diff_headings(v1_headings, v2_headings)
        assert result["severity"] in ("medium", "high")
        assert len(result["changes"]) > 0

    def test_unicode_anomalies_detected(self, corpus):
        """08_drug_interactions.md has hidden unicode inserted."""
        content = extract(V2_DIR / "08_drug_interactions.md")["content"]
        result = detect_character_anomalies(content)
        assert result["severity"] in ("medium", "high")
        assert len(result["anomalies"]) > 0

    def test_broken_table_detected(self, corpus):
        """18_data_pipelines.md has misaligned table columns in v2."""
        chunker = RecursiveChunker()
        v1_chunks = chunker.chunk(extract(V1_DIR / "18_data_pipelines.md")["content"])
        v2_chunks = chunker.chunk(extract(V2_DIR / "18_data_pipelines.md")["content"])

        result = diff_table_rows_in_chunks(v1_chunks, v2_chunks)
        assert result["severity"] in ("medium", "high")
        assert result["misaligned_tables"] > 0


class TestStorageRoundtrip:
    def test_snapshot_save_and_retrieve(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        store = SnapshotStore(conn)

        snapshot = {
            "doc_id": "test.md",
            "extracted_at": "2026-01-01T00:00:00+00:00",
            "chunk_count": 5,
            "heading_structure": ["# Title", "## Section"],
            "avg_tokens_per_chunk": 120.5,
            "token_std_dev": 20.1,
            "embedding_centroid": [],
            "extractor_version": "1.0.0",
            "chunker_config": "size=512,overlap=50",
            "file_hash": "abc123",
            "parser_type": "markdown",
        }

        store.save_snapshot(
            corpus_id="test_corpus",
            snapshot_id="snap_001",
            doc_snapshots=[snapshot],
            extractions={"test.md": {"content": "hello world", "chunks": ["chunk1", "chunk2"]}},
        )

        retrieved = store.get_doc_snapshot("test_corpus", "test.md")
        assert retrieved is not None
        assert retrieved["chunk_count"] == 5
        assert retrieved["heading_structure"] == ["# Title", "## Section"]
        assert retrieved["chunks"] == ["chunk1", "chunk2"]

        conn.close()
