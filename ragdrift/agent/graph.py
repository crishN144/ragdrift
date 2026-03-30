"""LangGraph pipeline for the ragdrift scan workflow.

Graph: sampler → extractor → differ → (prober?) → classifier → (explainer?) → END

Conditional edges:
- differ → prober  if any medium+ severity detected
- differ → classifier  otherwise
- classifier → explainer  if state["explain"] is True
- classifier → END  otherwise

Install: pip install ragdrift[agent]
"""
from __future__ import annotations

from typing import Literal

from ragdrift.agent.state import ScanState


def _max_severity_from_diffs(diff_results: dict) -> int:
    """Return numeric max severity across all diff results."""
    order = {"critical": 4, "high": 3, "medium": 2, "low": 1, "none": 0}
    max_sev = 0
    for diffs in diff_results.values():
        for category in diffs.values():
            if isinstance(category, dict):
                # Nested dict (e.g. structural has chunks/headings/tables each with severity)
                for v in category.values():
                    if isinstance(v, dict) and "severity" in v:
                        max_sev = max(max_sev, order.get(v["severity"], 0))
                if "severity" in category:
                    max_sev = max(max_sev, order.get(category["severity"], 0))
    return max_sev


def _route_after_differ(state: ScanState) -> Literal["prober", "classifier"]:
    diff_results = state.get("diff_results", {})
    if _max_severity_from_diffs(diff_results) >= 2:  # medium or above
        return "prober"
    return "classifier"


def _route_after_classifier(state: ScanState) -> Literal["explainer", "__end__"]:
    return "explainer" if state.get("explain", False) else "__end__"


def build_scan_graph():
    """Build and compile the LangGraph scan pipeline.

    Returns a compiled LangGraph graph ready to invoke with a ScanState dict.
    Raises ImportError if langgraph is not installed.
    """
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        raise ImportError(
            "langgraph is required for the agent pipeline. "
            "Install with: pip install ragdrift[agent]"
        )

    from ragdrift.agent.nodes.classifier import classifier_node
    from ragdrift.agent.nodes.differ import differ_node
    from ragdrift.agent.nodes.explainer import explainer_node
    from ragdrift.agent.nodes.extractor import extractor_node
    from ragdrift.agent.nodes.prober import prober_node
    from ragdrift.agent.nodes.sampler import sampler_node

    graph = StateGraph(ScanState)

    graph.add_node("sampler", sampler_node)
    graph.add_node("extractor", extractor_node)
    graph.add_node("differ", differ_node)
    graph.add_node("prober", prober_node)
    graph.add_node("classifier", classifier_node)
    graph.add_node("explainer", explainer_node)

    graph.set_entry_point("sampler")
    graph.add_edge("sampler", "extractor")
    graph.add_edge("extractor", "differ")
    graph.add_conditional_edges(
        "differ",
        _route_after_differ,
        {"prober": "prober", "classifier": "classifier"},
    )
    graph.add_edge("prober", "classifier")
    graph.add_conditional_edges(
        "classifier",
        _route_after_classifier,
        {"explainer": "explainer", "__end__": END},
    )
    graph.add_edge("explainer", END)

    return graph.compile()
