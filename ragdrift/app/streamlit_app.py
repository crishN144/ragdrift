"""Streamlit dashboard for ragdrift — silent regression detector for RAG pipelines.

Runs entirely from bundled demo data — no backend or API keys required.
Deploy to Streamlit Community Cloud for a live hosted demo.

Install: pip install ragdrift[ui]
Run:     streamlit run ragdrift/app/streamlit_app.py
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

# ── Page config ────────────────────────────────────────────

st.set_page_config(
    page_title="ragdrift — RAG Drift Monitor",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Load demo data ─────────────────────────────────────────

DEMO_DIR = Path(__file__).parent.parent.parent / "demo"
SAMPLE_SCAN_PATH = DEMO_DIR / "sample_scan_results.json"


@st.cache_data
def load_demo_data() -> dict:
    if SAMPLE_SCAN_PATH.exists():
        return json.loads(SAMPLE_SCAN_PATH.read_text())
    # Fallback inline data if file not found
    return {
        "scan_id": "demo-001",
        "timestamp": "2026-03-28T23:00:00+00:00",
        "docs_sampled": 20,
        "docs_drifted": 5,
        "overall_severity": "critical",
        "retrieval_accuracy_before": 1.0,
        "retrieval_accuracy_after": 0.87,
        "drift_events": [],
        "diagnosis": None,
    }


# ── Severity helpers ───────────────────────────────────────

SEVERITY_COLOR = {
    "none": "🟢",
    "low": "🔵",
    "medium": "🟡",
    "high": "🟠",
    "critical": "🔴",
}

SEVERITY_BG = {
    "none": "#d4edda",
    "low": "#cce5ff",
    "medium": "#fff3cd",
    "high": "#ffe0b2",
    "critical": "#f8d7da",
}


def severity_badge(severity: str) -> str:
    icon = SEVERITY_COLOR.get(severity, "⚪")
    return f"{icon} **{severity.upper()}**"


# ── Sidebar ────────────────────────────────────────────────

with st.sidebar:
    st.title("ragdrift")
    st.caption("Silent regression detector for RAG pipelines")
    st.markdown("---")
    st.markdown("**Quick start**")
    st.code("pip install ragdrift\nragdrift demo --inject-drift", language="bash")
    st.markdown("---")
    st.markdown("[GitHub](https://github.com/crishnagarkar/ragdrift) · [arXiv](https://arxiv.org/abs/2601.14479)")

# ── Main content ───────────────────────────────────────────

scan = load_demo_data()

st.title("🔍 ragdrift — RAG Pipeline Drift Monitor")
st.caption(f"Scan `{scan.get('scan_id', 'demo-001')}` · {scan.get('timestamp', '')[:19].replace('T', ' ')} UTC")

# ── Top-level KPIs ─────────────────────────────────────────

col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric("Documents Sampled", scan.get("docs_sampled", 0))
with col2:
    st.metric("Documents Drifted", scan.get("docs_drifted", 0))
with col3:
    severity = scan.get("overall_severity", "none")
    st.metric("Overall Severity", severity.upper(), delta=None)
with col4:
    before = scan.get("retrieval_accuracy_before")
    if before is not None:
        st.metric("Retrieval (Ref)", f"{before:.0%}")
with col5:
    after = scan.get("retrieval_accuracy_after")
    if after is not None and before is not None:
        delta = after - before
        st.metric("Retrieval (Now)", f"{after:.0%}", delta=f"{delta:+.0%}", delta_color="inverse")

st.markdown("---")

# ── Drift events table ─────────────────────────────────────

st.subheader("Drift Events")

events = scan.get("drift_events", [])
drifted_events = [e for e in events if e.get("severity", "none") != "none"]

if not drifted_events:
    st.success("No drift detected in this scan.")
else:
    # Summary table
    import pandas as pd

    rows = []
    for e in sorted(drifted_events, key=lambda x: ["critical","high","medium","low","none"].index(x.get("severity","none"))):
        rows.append({
            "Document": e["doc_id"],
            "Severity": SEVERITY_COLOR.get(e["severity"], "⚪") + " " + e["severity"].upper(),
            "Chunk Δ": f"{e['chunk_count_before']} → {e['chunk_count_after']} ({e['chunk_delta_pct']:+.1f}%)",
            "Retrieval Impact": "⚠️ Yes" if e.get("retrieval_impact") else "No",
            "Action": e.get("recommended_action", "none"),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── Expandable per-doc detail ──────────────────────────

    st.subheader("Per-Document Detail")

    for event in sorted(drifted_events, key=lambda x: ["critical","high","medium","low","none"].index(x.get("severity","none"))):
        sev = event["severity"]
        icon = SEVERITY_COLOR.get(sev, "⚪")
        impact_tag = " · **[RETRIEVAL IMPACT]**" if event.get("retrieval_impact") else ""

        with st.expander(f"{icon} {event['doc_id']} — {sev.upper()}{impact_tag}", expanded=(sev == "critical")):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Chunks Before", event["chunk_count_before"])
            with c2:
                st.metric("Chunks After", event["chunk_count_after"])
            with c3:
                delta = event["chunk_delta_pct"]
                st.metric("Chunk Δ%", f"{delta:+.1f}%", delta_color="inverse" if abs(delta) > 10 else "off")

            if event.get("heading_changes"):
                st.markdown("**Heading changes:**")
                for change in event["heading_changes"]:
                    st.markdown(f"- `{change}`")

            if event.get("lexical_anomalies"):
                st.markdown("**Lexical anomalies:**")
                for anomaly in event["lexical_anomalies"]:
                    st.markdown(f"- `{anomaly}`")

            action = event.get("recommended_action", "none")
            action_colors = {"re_ingest": "error", "alert": "warning", "monitor": "info", "none": "success"}
            action_type = action_colors.get(action, "info")
            getattr(st, action_type)(f"Recommended action: **{action}**")

# ── Retrieval accuracy timeline ────────────────────────────

st.markdown("---")
st.subheader("Retrieval Accuracy")

before = scan.get("retrieval_accuracy_before")
after = scan.get("retrieval_accuracy_after")

if before is not None and after is not None:
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=["Reference Snapshot", "Current Corpus"],
            y=[before * 100, after * 100],
            marker_color=["#28a745", "#dc3545" if after < before else "#28a745"],
            text=[f"{before:.0%}", f"{after:.0%}"],
            textposition="outside",
        ))
        fig.update_layout(
            title="Score Accuracy: Reference vs Current",
            yaxis_title="Score Accuracy (%)",
            yaxis_range=[0, 110],
            height=300,
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        if after < before:
            drop_pp = (before - after) * 100
            st.warning(f"⚠️ Retrieval score accuracy dropped **{drop_pp:.1f} percentage points** after drift injection. Re-ingest flagged documents to restore quality.")
    except ImportError:
        st.info(f"Reference: {before:.0%} → Current: {after:.0%} (install plotly for chart)")
else:
    st.info("No retrieval probe data in this scan. Golden queries required to measure retrieval accuracy.")

# ── Footer ─────────────────────────────────────────────────

st.markdown("---")
st.caption("ragdrift v0.1.0 · [GitHub](https://github.com/crishnagarkar/ragdrift) · Built to catch ingestion regressions before users notice them.")
