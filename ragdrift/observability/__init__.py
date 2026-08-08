"""Optional LangSmith observability. Core imports stay clean without langsmith."""
from ragdrift.observability.costs import PRICES_PER_MTOK, estimate_cost_usd
from ragdrift.observability.tracing import (
    build_usage_metadata,
    configure_tracing,
    llm_span,
    maybe_traceable,
    tracing_enabled,
)

__all__ = [
    "PRICES_PER_MTOK",
    "estimate_cost_usd",
    "build_usage_metadata",
    "configure_tracing",
    "llm_span",
    "maybe_traceable",
    "tracing_enabled",
]
