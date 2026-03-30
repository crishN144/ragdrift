"""Tests for structural diff: chunk count and heading diffs."""
import pytest

from ragdrift.core.diff.structural import (
    diff_chunk_count,
    diff_headings,
    diff_table_rows_in_chunks,
)


class TestChunkCountDiff:
    def test_no_change(self):
        result = diff_chunk_count(10, 10)
        assert result["severity"] == "none"
        assert result["delta_pct"] == 0.0

    def test_small_increase_low(self):
        result = diff_chunk_count(10, 11)
        assert result["severity"] == "low"
        assert result["delta_pct"] == pytest.approx(10.0)

    def test_medium_increase(self):
        result = diff_chunk_count(10, 12)
        assert result["severity"] == "medium"

    def test_high_increase(self):
        result = diff_chunk_count(10, 13)
        assert result["severity"] == "high"

    def test_critical_increase(self):
        result = diff_chunk_count(10, 18)
        assert result["severity"] == "critical"

    def test_large_decrease_high(self):
        # 27% drop
        result = diff_chunk_count(11, 8)
        assert result["severity"] == "high"
        assert result["delta_pct"] < 0

    def test_zero_before(self):
        result = diff_chunk_count(0, 5)
        assert result["delta_pct"] == 100.0

    def test_zero_both(self):
        result = diff_chunk_count(0, 0)
        assert result["severity"] == "none"


class TestHeadingDiff:
    def test_no_change(self):
        headings = ["# Title", "## Section", "### Sub"]
        result = diff_headings(headings, headings)
        assert result["severity"] == "none"
        assert result["changes"] == []

    def test_heading_removed(self):
        before = ["# Title", "## Section A", "## Section B"]
        after = ["# Title", "## Section A"]
        result = diff_headings(before, after)
        assert result["severity"] == "medium"
        assert any("removed" in c for c in result["changes"])

    def test_heading_level_shift(self):
        # H2 → H4 (heading hierarchy collapse)
        before = ["# Title", "## Section"]
        after = ["# Title", "#### Section"]
        result = diff_headings(before, after)
        assert result["severity"] == "medium"
        assert any("level_shift" in c for c in result["changes"])

    def test_many_changes_high(self):
        before = ["# Title", "## A", "## B", "## C", "## D"]
        after = ["# Title", "## X", "## Y"]
        result = diff_headings(before, after)
        assert result["severity"] == "high"


class TestTableRowDiff:
    def test_no_tables_no_change(self):
        chunks = ["Some text without tables.", "More plain text."]
        result = diff_table_rows_in_chunks(chunks, chunks)
        assert result["severity"] == "none"
        assert result["misaligned_tables"] == 0

    def test_misaligned_columns_detected(self):
        # Before: well-formed table
        before = ["| Col1 | Col2 | Col3 |\n|------|------|------|\n| A | B | C |"]
        # After: broken column alignment (extra pipe in one row)
        after = ["| Col1 | Col2 | Col3 |\n|------|------|------|\n| A || B | C |"]
        result = diff_table_rows_in_chunks(before, after)
        assert result["severity"] in ("medium", "high")
        assert result["misaligned_tables"] >= 1

    def test_table_rows_removed(self):
        before = [
            "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |"
        ]
        after = [
            "| A | B |\n|---|---|\n| 1 | 2 |"
        ]
        result = diff_table_rows_in_chunks(before, after)
        assert result["ref_table_rows"] > result["new_table_rows"]

    def test_no_change_clean_table(self):
        chunk = "| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |"
        result = diff_table_rows_in_chunks([chunk], [chunk])
        assert result["severity"] == "none"
        assert result["misaligned_tables"] == 0
