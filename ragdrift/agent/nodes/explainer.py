"""Explainer node: optional LLM diagnosis (gated behind --explain flag).

Providers:
- anthropic: Claude Haiku via anthropic SDK (needs ANTHROPIC_API_KEY)
- ollama:    Local Ollama REST API, no SDK, no API key needed
"""
import json

from ragdrift.agent.state import ScanState


def explainer_node(state: ScanState) -> dict:
    """Generate plain-English LLM diagnosis of detected drift."""
    if not state.get("explain", False):
        return {}

    scan_result = state.get("scan_result")
    if not scan_result:
        return {}

    drifted = [e for e in scan_result.get("drift_events", []) if e.get("severity", "none") != "none"]
    if not drifted:
        return {}

    prompt = _build_prompt(scan_result, drifted)
    provider = state.get("provider", "anthropic")

    diagnosis = _call_ollama(prompt) if provider == "ollama" else _call_anthropic(prompt)
    if diagnosis:
        scan_result["diagnosis"] = diagnosis

    return {"scan_result": scan_result}


def _build_prompt(scan_result: dict, events: list) -> str:
    events_json = json.dumps(events, indent=2, default=str)
    return f"""You are a RAG pipeline diagnostic assistant. Analyze the following drift events.

Scan summary:
- Documents sampled: {scan_result.get('docs_sampled', 0)}
- Documents drifted: {scan_result.get('docs_drifted', 0)}
- Overall severity: {scan_result.get('overall_severity', 'none')}
- Retrieval score before: {scan_result.get('retrieval_accuracy_before', 'N/A')}
- Retrieval score after: {scan_result.get('retrieval_accuracy_after', 'N/A')}

Drift events:
{events_json}

Respond with ONLY a JSON object with exactly these three fields:
- "what_changed": One sentence describing what changed in the documents.
- "retrieval_impact": One sentence explaining how this affects retrieval quality.
- "recommended_action": One sentence describing what the operator should do."""


def _call_anthropic(prompt: str) -> str | None:
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except ImportError:
        return None
    except Exception:
        return None


def _call_ollama(prompt: str) -> str | None:
    import requests
    try:
        r = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3.2", "prompt": prompt, "stream": False},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("response", "")
    except Exception:
        return None
