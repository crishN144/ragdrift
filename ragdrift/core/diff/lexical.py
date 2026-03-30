"""Lexical diff: token distribution shift, character anomalies."""
import re
from collections import Counter
from typing import Any


def diff_token_distribution(before_chunks: list[str], after_chunks: list[str]) -> dict[str, Any]:
    """Compare token distributions between before and after chunks."""
    before_tokens = _tokenize_all(before_chunks)
    after_tokens = _tokenize_all(after_chunks)

    before_counts = Counter(before_tokens)
    after_counts = Counter(after_tokens)

    # Calculate distribution shift using simple overlap coefficient
    all_tokens = set(before_counts.keys()) | set(after_counts.keys())
    if not all_tokens:
        return {"shift_score": 0.0, "severity": "none", "details": []}

    overlap = sum(min(before_counts.get(t, 0), after_counts.get(t, 0)) for t in all_tokens)
    total = max(sum(before_counts.values()), sum(after_counts.values()), 1)
    shift_score = 1.0 - (overlap / total)

    # Find most changed tokens
    details = []
    for token in all_tokens:
        b = before_counts.get(token, 0)
        a = after_counts.get(token, 0)
        if abs(a - b) > 2 and (b == 0 or abs(a - b) / max(b, 1) > 0.5):
            details.append(f"'{token}': {b} \u2192 {a}")

    details = sorted(details, key=lambda x: x)[:10]  # top 10

    severity = _shift_severity(shift_score)
    return {"shift_score": round(shift_score, 4), "severity": severity, "details": details}


def detect_character_anomalies(text: str) -> dict[str, Any]:
    """Detect hidden/unusual unicode characters."""
    anomalies = []

    # Zero-width characters
    zero_width = re.findall(r'[\u200b\u200c\u200d\ufeff\u00ad]', text)
    if zero_width:
        anomalies.append(f"zero_width_chars: {len(zero_width)} found")

    # Non-breaking spaces
    nbsp = text.count('\u00a0')
    if nbsp > 0:
        anomalies.append(f"non_breaking_spaces: {nbsp} found")

    # Control characters (excluding normal whitespace)
    control = re.findall(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', text)
    if control:
        anomalies.append(f"control_chars: {len(control)} found")

    # Unusual unicode categories
    rtl = re.findall(r'[\u200e\u200f\u202a-\u202e]', text)
    if rtl:
        anomalies.append(f"directional_markers: {len(rtl)} found")

    # Homoglyph detection (common Latin lookalikes from Cyrillic etc)
    homoglyphs = re.findall(r'[\u0400-\u04ff\u0500-\u052f]', text)
    if homoglyphs:
        anomalies.append(f"potential_homoglyphs: {len(homoglyphs)} found")

    severity = "none"
    if len(anomalies) > 2:
        severity = "high"
    elif len(anomalies) > 0:
        severity = "medium"

    return {"anomalies": anomalies, "severity": severity}


def _tokenize_all(chunks: list[str]) -> list[str]:
    """Simple whitespace tokenizer."""
    tokens = []
    for chunk in chunks:
        tokens.extend(re.findall(r'\b\w+\b', chunk.lower()))
    return tokens


def _shift_severity(score: float) -> str:
    if score > 0.4:
        return "high"
    if score > 0.2:
        return "medium"
    if score > 0.1:
        return "low"
    return "none"
