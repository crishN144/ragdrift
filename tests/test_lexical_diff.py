"""Tests for lexical diff: token distribution and character anomalies."""
import pytest

from ragdrift.core.diff.lexical import detect_character_anomalies, diff_token_distribution


class TestTokenDistribution:
    def test_identical_chunks(self):
        chunks = ["The quick brown fox jumps over the lazy dog"]
        result = diff_token_distribution(chunks, chunks)
        assert result["severity"] == "none"
        assert result["shift_score"] == pytest.approx(0.0)

    def test_completely_different_content(self):
        before = ["alpha beta gamma delta epsilon zeta eta theta iota kappa"]
        after = ["one two three four five six seven eight nine ten eleven"]
        result = diff_token_distribution(before, after)
        assert result["severity"] in ("high", "medium")
        assert result["shift_score"] > 0.3

    def test_partial_overlap(self):
        before = ["the contract requires consideration and offer acceptance"]
        after = ["the contract requires offer acceptance capacity legality"]
        result = diff_token_distribution(before, after)
        # Some overlap, so shift should be low-medium
        assert result["shift_score"] < 0.8

    def test_empty_chunks(self):
        result = diff_token_distribution([], [])
        assert result["severity"] == "none"


class TestCharacterAnomalies:
    def test_clean_text(self):
        result = detect_character_anomalies("Normal clean text without anomalies.")
        assert result["severity"] == "none"
        assert result["anomalies"] == []

    def test_zero_width_space_detected(self):
        text = "Drug\u200bInteractions"  # zero-width space inserted
        result = detect_character_anomalies(text)
        assert result["severity"] in ("medium", "high")
        assert any("zero_width" in a for a in result["anomalies"])

    def test_non_breaking_space_detected(self):
        text = "Hello\u00a0World"  # non-breaking space
        result = detect_character_anomalies(text)
        assert any("non_breaking" in a for a in result["anomalies"])

    def test_multiple_anomalies_high_severity(self):
        # Insert several types of hidden chars
        text = "Text\u200bwith\u200czero\u200dwidth\uFEFFchars"
        result = detect_character_anomalies(text)
        assert result["severity"] in ("medium", "high")
