"""Cost estimation for LLM calls made by ragdrift.

This is the ONE place model prices live. Prices are USD per million tokens
and DRIFT over time — update them from https://docs.claude.com/en/docs/about-claude/pricing
when Anthropic changes them. Local Ollama models cost $0.
Last verified: 2026-08-08.
"""
from __future__ import annotations

# {model_prefix: (input_usd_per_mtok, output_usd_per_mtok)}
# Longest-prefix match, so dated IDs like "claude-haiku-4-5-20251001" resolve.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),   # default --explain model
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated USD cost of one LLM call.

    Returns 0.0 for local (non-Claude, i.e. Ollama) models, None for hosted
    models missing from the price table — callers must never report a
    made-up number.
    """
    if not model.startswith("claude"):
        return 0.0  # local Ollama model
    matches = [p for p in PRICES_PER_MTOK if model.startswith(p)]
    if not matches:
        return None
    in_price, out_price = PRICES_PER_MTOK[max(matches, key=len)]
    return round((input_tokens * in_price + output_tokens * out_price) / 1_000_000, 6)
