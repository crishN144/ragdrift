"""CLI entry point for ragdrift.

Commands:
    ragdrift init --corpus ./docs [--golden ./golden_queries.json]
    ragdrift scan --corpus ./docs [--sample-rate 0.2] [--explain] [--provider anthropic|ollama]
    ragdrift demo --inject-drift [--level mild|moderate|catastrophic] [--format json|pretty|markdown]
    ragdrift report --corpus ./docs [--format json|pretty|markdown]
"""

import argparse
import json
import shutil
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ragdrift.core.chunking.chunker import RecursiveChunker
from ragdrift.core.diff.lexical import detect_character_anomalies, diff_token_distribution
from ragdrift.core.diff.structural import (
    diff_chunk_count,
    diff_headings,
    diff_table_rows_in_chunks,
)
from ragdrift.core.extraction.router import extract
from ragdrift.storage.drift_log import DriftLog
from ragdrift.storage.models import (
    DocumentSnapshot,
    DriftEvent,
    ScanResult,
    init_db,
)
from ragdrift.storage.snapshots import SnapshotStore


def _get_db_path(corpus_dir: Path) -> Path:
    """Get the database path for a corpus."""
    return corpus_dir / ".ragdrift" / "ragdrift.db"


def _corpus_id(corpus_dir: Path) -> str:
    """Generate a stable corpus ID from the directory path."""
    return str(corpus_dir.resolve())


def _discover_docs(corpus_dir: Path) -> list[Path]:
    """Find all supported documents in the corpus directory."""
    extensions = {".txt", ".md", ".pdf"}
    docs = []
    for ext in extensions:
        docs.extend(corpus_dir.glob(f"*{ext}"))
    return sorted(docs)


def _compute_token_stats(chunks: list[str]) -> tuple[float, float]:
    """Compute average tokens per chunk and standard deviation."""
    import re

    if not chunks:
        return 0.0, 0.0
    token_counts = [len(re.findall(r"\b\w+\b", c)) for c in chunks]
    avg = sum(token_counts) / len(token_counts)
    if len(token_counts) < 2:
        return avg, 0.0
    variance = sum((t - avg) ** 2 for t in token_counts) / (len(token_counts) - 1)
    std_dev = variance**0.5
    return round(avg, 2), round(std_dev, 2)


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a corpus: take a reference snapshot."""
    corpus_dir = Path(args.corpus).resolve()
    if not corpus_dir.is_dir():
        print(f"Error: {corpus_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Setup database
    db_dir = corpus_dir / ".ragdrift"
    db_dir.mkdir(exist_ok=True)
    db_path = _get_db_path(corpus_dir)
    conn = init_db(db_path)
    snapshot_store = SnapshotStore(conn)

    corpus = _corpus_id(corpus_dir)
    snapshot_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    docs = _discover_docs(corpus_dir)

    if not docs:
        print("Error: No documents found in corpus directory", file=sys.stderr)
        sys.exit(1)

    chunker = RecursiveChunker(
        chunk_size=args.chunk_size if hasattr(args, "chunk_size") else 512,
        chunk_overlap=args.chunk_overlap if hasattr(args, "chunk_overlap") else 50,
    )
    chunker_config = f"size={chunker.chunk_size},overlap={chunker.chunk_overlap}"

    doc_snapshots = []
    extractions = {}

    # Show a clean short path — strip long temp prefixes for readability
    display_path = corpus_dir.name if "ragdrift_demo_" in str(corpus_dir) else str(corpus_dir)
    print(f"  corpus: {display_path}  ({len(docs)} documents)")

    for doc_path in docs:
        try:
            extraction = extract(doc_path)
        except Exception as e:
            print(f"  Warning: Failed to extract {doc_path.name}: {e}", file=sys.stderr)
            continue

        chunks = chunker.chunk(extraction["content"])
        avg_tokens, token_std = _compute_token_stats(chunks)

        snapshot: DocumentSnapshot = {
            "doc_id": doc_path.name,
            "extracted_at": datetime.now(UTC).isoformat(),
            "chunk_count": len(chunks),
            "heading_structure": extraction["headings"],
            "avg_tokens_per_chunk": avg_tokens,
            "token_std_dev": token_std,
            "embedding_centroid": [],  # empty in base mode
            "extractor_version": extraction["extractor_version"],
            "chunker_config": chunker_config,
            "file_hash": extraction["file_hash"],
            "parser_type": extraction["parser_type"],
        }
        doc_snapshots.append(snapshot)
        extractions[doc_path.name] = {
            "content": extraction["content"],
            "chunks": chunks,
        }
        print(f"  ✓ {doc_path.name}: {len(chunks)} chunks, {len(extraction['headings'])} headings", flush=True)
        time.sleep(0.08)

    snapshot_store.save_snapshot(corpus, snapshot_id, doc_snapshots, extractions)
    conn.close()

    # Copy golden queries if provided
    if args.golden:
        golden_path = Path(args.golden).resolve()
        if golden_path.exists():
            dest = db_dir / "golden_queries.json"
            shutil.copy2(golden_path, dest)
            print(f"  ✓ Golden queries saved ({dest})")

    print(f"\nSnapshot {snapshot_id} saved ({len(doc_snapshots)} documents)")


def cmd_scan(args: argparse.Namespace) -> None:
    """Scan corpus for drift against the latest snapshot."""
    corpus_dir = Path(args.corpus).resolve()
    db_path = _get_db_path(corpus_dir)

    if not db_path.exists():
        print("Error: No snapshot found. Run 'ragdrift init' first.", file=sys.stderr)
        sys.exit(1)

    conn = init_db(db_path)
    snapshot_store = SnapshotStore(conn)
    drift_log = DriftLog(conn)

    corpus = _corpus_id(corpus_dir)
    latest_snapshot_id = snapshot_store.get_latest_snapshot(corpus)

    if not latest_snapshot_id:
        print("Error: No snapshot found. Run 'ragdrift init' first.", file=sys.stderr)
        sys.exit(1)

    ref_docs = snapshot_store.get_snapshot_docs(corpus, latest_snapshot_id)
    ref_by_id = {d["doc_id"]: d for d in ref_docs}

    docs = _discover_docs(corpus_dir)
    sample_rate = args.sample_rate if hasattr(args, "sample_rate") else 0.2
    sample_size = max(1, int(len(docs) * sample_rate))

    # Adaptive sampling: prioritize previously drifted docs
    doc_history = {}
    for doc_path in docs:
        history = drift_log.get_doc_history(doc_path.name)
        if history:
            doc_history[doc_path.name] = max(
                h.get("severity", "none") for h in history
            )

    # Sort: previously drifted first, then recently modified, then random
    import random

    random.seed(42)  # reproducible sampling

    def _priority(doc_path: Path) -> tuple:
        severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
        prev_severity = doc_history.get(doc_path.name, "none")
        return (severity_rank.get(prev_severity, 5), -doc_path.stat().st_mtime, random.random())

    sorted_docs = sorted(docs, key=_priority)
    sampled_docs = sorted_docs[:sample_size]

    chunker = RecursiveChunker()
    scan_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now(UTC).isoformat()

    drift_events: list[DriftEvent] = []
    docs_drifted = 0

    for doc_path in sampled_docs:
        try:
            extraction = extract(doc_path)
        except Exception as e:
            print(f"  Warning: Failed to extract {doc_path.name}: {e}", file=sys.stderr)
            continue

        chunks = chunker.chunk(extraction["content"])
        avg_tokens, token_std = _compute_token_stats(chunks)

        ref = ref_by_id.get(doc_path.name)
        if not ref:
            continue

        # Parse reference data
        ref_headings = json.loads(ref["heading_structure"]) if isinstance(ref["heading_structure"], str) else ref["heading_structure"]
        ref_chunks = json.loads(ref["chunks"]) if ref.get("chunks") and isinstance(ref["chunks"], str) else ref.get("chunks", [])

        # Structural diff
        chunk_diff = diff_chunk_count(ref["chunk_count"], len(chunks))
        heading_diff = diff_headings(ref_headings, extraction["headings"])

        # Table diff — compare table row structure extracted from stored chunks
        table_row_diff = diff_table_rows_in_chunks(ref_chunks or [], chunks)

        # Lexical diff
        token_diff = diff_token_distribution(ref_chunks if ref_chunks else [], chunks)
        char_anomalies = detect_character_anomalies(extraction["content"])

        # Aggregate anomalies
        lexical_anomalies = []
        if token_diff["severity"] != "none":
            lexical_anomalies.append(f"token_shift={token_diff['shift_score']}")
        lexical_anomalies.extend(char_anomalies.get("anomalies", []))
        if table_row_diff["changes"]:
            lexical_anomalies.extend(table_row_diff["changes"])

        # Determine severity
        severities = [
            chunk_diff["severity"],
            heading_diff["severity"],
            table_row_diff["severity"],
            token_diff["severity"],
            char_anomalies["severity"],
        ]
        severity = _max_severity(severities)

        # Determine retrieval impact
        retrieval_impact = _assess_retrieval_impact(chunk_diff, heading_diff, token_diff)

        # Determine recommended action
        action = _recommend_action(severity, retrieval_impact)

        event: DriftEvent = {
            "doc_id": doc_path.name,
            "severity": severity,
            "chunk_count_before": ref["chunk_count"],
            "chunk_count_after": len(chunks),
            "chunk_delta_pct": chunk_diff["delta_pct"],
            "heading_changes": heading_diff["changes"],
            "lexical_anomalies": lexical_anomalies,
            "semantic_drift_score": 0.0,  # skip in base mode
            "recommended_action": action,
            "retrieval_impact": retrieval_impact,
        }
        drift_events.append(event)

        if severity != "none":
            docs_drifted += 1

    # Run golden probes if available and any medium+ drift detected
    retrieval_before: float | None = None
    retrieval_after: float | None = None
    per_query_results: list[dict] = []
    has_medium_plus = any(
        e["severity"] in ("medium", "high", "critical") for e in drift_events
    )

    golden_path = corpus_dir / ".ragdrift" / "golden_queries.json"
    if golden_path.exists() and has_medium_plus:
        retrieval_before, retrieval_after, per_query_results = _run_probes(
            corpus_dir, ref_docs, sampled_docs, chunker, golden_path
        )

    overall_severity = _max_severity([e["severity"] for e in drift_events]) if drift_events else "none"

    scan_result: ScanResult = {
        "corpus_id": corpus,
        "scan_id": scan_id,
        "timestamp": timestamp,
        "docs_sampled": len(sampled_docs),
        "docs_drifted": docs_drifted,
        "overall_severity": overall_severity,
        "retrieval_accuracy_before": retrieval_before,
        "retrieval_accuracy_after": retrieval_after,
        "drift_events": drift_events,
        "diagnosis": None,
    }

    # Optional LLM explanation
    if args.explain if hasattr(args, "explain") else False:
        provider = args.provider if hasattr(args, "provider") else "anthropic"

        # Build ingestion fingerprints from reference snapshot
        fingerprints = {
            doc["doc_id"]: {
                "file_hash": doc.get("file_hash", "unknown"),
                "extractor_version": doc.get("extractor_version", "unknown"),
                "chunker_config": doc.get("chunker_config", "unknown"),
                "parser_type": doc.get("parser_type", "unknown"),
            }
            for doc in ref_docs
        }

        # Build drift history for drifted docs (how many times each drifted before)
        drift_history: dict[str, int] = {}
        for event in drift_events:
            if event["severity"] != "none":
                history = drift_log.get_doc_history(event["doc_id"])
                drift_history[event["doc_id"]] = len(history)

        extra_context = {
            "per_query_results": per_query_results,
            "fingerprints": fingerprints,
            "drift_history": drift_history,
        }
        print("\033[2J\033[H", end="", flush=True)
        print(f"{C.MAUVE}  ┌─────────────────────────────────────────────────────────┐{C.RESET}")
        print(f"{C.MAUVE}  │  LLM DIAGNOSIS  ·  Claude Haiku                         │{C.RESET}")
        print(f"{C.MAUVE}  └─────────────────────────────────────────────────────────┘{C.RESET}")
        print(f"\n  {C.DIM}calling {provider} for root cause analysis…{C.RESET}", flush=True)
        diagnosis = _explain_drift(scan_result, provider, extra_context)
        scan_result["diagnosis"] = diagnosis

    # Save to drift log
    drift_log.log_scan(scan_result)
    conn.close()

    # Output
    fmt = args.format if hasattr(args, "format") else "json"
    _output_scan(scan_result, fmt)


def cmd_demo(args: argparse.Namespace) -> None:
    """Run a self-contained demo: setup, snapshot, inject drift, scan, report."""
    level = args.level if hasattr(args, "level") else "moderate"
    fmt = args.format if hasattr(args, "format") else "pretty"

    # Find demo data
    demo_dir = _find_demo_dir()
    if not demo_dir:
        print("Error: Could not find demo corpus data.", file=sys.stderr)
        sys.exit(1)

    v1_dir = demo_dir / "corpus_v1"
    v2_dir = demo_dir / "corpus_v2"
    golden_path = demo_dir / "golden_queries.json"

    if not v1_dir.exists():
        print(f"Error: Demo corpus not found at {v1_dir}", file=sys.stderr)
        sys.exit(1)

    # Create temp working directory
    with tempfile.TemporaryDirectory(prefix="ragdrift_demo_") as tmpdir:
        work_dir = Path(tmpdir) / "corpus"
        shutil.copytree(v1_dir, work_dir)

        import time

        print()
        print("  ╭──────────────────────────────────────────────────────────────╮")
        print("  │   ragdrift · silent regression detector for RAG pipelines    │")
        print("  ╰──────────────────────────────────────────────────────────────╯")
        print()
        time.sleep(1.5)

        # Step 1: Init
        print("▶ Step 1  Taking reference snapshot...")
        time.sleep(0.5)
        init_args = argparse.Namespace(
            corpus=str(work_dir),
            golden=str(golden_path) if golden_path.exists() else None,
        )
        cmd_init(init_args)
        print()
        time.sleep(2.0)

        # Step 2: Inject drift
        print(f"▶ Step 2  Injecting drift (level: {level})...")
        time.sleep(0.5)
        from demo.inject_drift import inject_drift as do_inject

        injected = do_inject(work_dir, v2_dir, level=level)
        for item in injected:
            print(f"  💉 {item['doc']}: {item['drift_type']}")
            time.sleep(0.3)
        print()
        time.sleep(2.5)

        # Step 3: Clear screen so Step 3 header appears at top of new frame
        print("\033[2J\033[H", end="", flush=True)
        print("▶ Step 3  Scanning for drift...", flush=True)
        time.sleep(0.3)
        _scan_steps = [
            "  sampling documents…",
            "  extracting chunks…",
            "  diffing structure…",
            "  checking lexical anomalies…",
            "  running golden probes…",
            "  classifying severity…",
        ]
        for _step in _scan_steps:
            print(f"{C.DIM}{_step}{C.RESET}", flush=True)
            time.sleep(0.25)
        scan_args = argparse.Namespace(
            corpus=str(work_dir),
            sample_rate=1.0,  # scan all docs in demo
            explain=args.explain if hasattr(args, "explain") else False,
            provider=args.provider if hasattr(args, "provider") else "anthropic",
            format=fmt,
        )
        cmd_scan(scan_args)


def cmd_report(args: argparse.Namespace) -> None:
    """Show historical drift reports for a corpus."""
    corpus_dir = Path(args.corpus).resolve()
    db_path = _get_db_path(corpus_dir)

    if not db_path.exists():
        print("Error: No data found. Run 'ragdrift init' and 'ragdrift scan' first.", file=sys.stderr)
        sys.exit(1)

    conn = init_db(db_path)
    drift_log = DriftLog(conn)
    corpus = _corpus_id(corpus_dir)

    history = drift_log.get_corpus_history(corpus)
    conn.close()

    if not history:
        print("No scan history found for this corpus.")
        return

    fmt = args.format if hasattr(args, "format") else "json"

    if fmt == "json":
        print(json.dumps(history, indent=2, default=str))
    elif fmt == "pretty":
        print(f"\n{'='*60}")
        print(f"  Drift History: {corpus_dir.name}")
        print(f"{'='*60}\n")
        for scan in history:
            _print_scan_summary(scan)
    elif fmt == "markdown":
        print(f"# Drift History: {corpus_dir.name}\n")
        for scan in history:
            _print_scan_markdown(scan)


# ── Internal helpers ────────────────────────────────────────


def _find_demo_dir() -> Path | None:
    """Find the demo directory, checking multiple locations."""
    candidates = [
        Path(__file__).parent.parent / "demo",
        Path.cwd() / "demo",
    ]
    # Also check if installed as package
    try:

        pkg_dir = Path(__file__).parent.parent
        candidates.insert(0, pkg_dir / "demo")
    except Exception:
        pass

    for candidate in candidates:
        if candidate.exists() and (candidate / "corpus_v1").exists():
            return candidate
    return None


def _max_severity(severities: list[str]) -> str:
    """Return the highest severity from a list."""
    order = {"critical": 4, "high": 3, "medium": 2, "low": 1, "none": 0}
    if not severities:
        return "none"
    return max(severities, key=lambda s: order.get(s, 0))


def _assess_retrieval_impact(
    chunk_diff: dict, heading_diff: dict, token_diff: dict
) -> bool:
    """Determine if drift is likely to impact retrieval quality."""
    # Large chunk count changes definitely impact retrieval
    if abs(chunk_diff.get("delta_pct", 0)) > 15:
        return True
    # Significant token distribution shifts impact retrieval
    if token_diff.get("shift_score", 0) > 0.2:
        return True
    # Many heading changes can affect chunking boundaries
    if len(heading_diff.get("changes", [])) > 2:
        return True
    return False


def _recommend_action(severity: str, retrieval_impact: bool) -> str:
    """Recommend an action based on severity and retrieval impact."""
    if severity == "critical" or (severity == "high" and retrieval_impact):
        return "re_ingest"
    if severity == "high" or (severity == "medium" and retrieval_impact):
        return "alert"
    if severity == "medium" or severity == "low":
        return "monitor"
    return "none"


def _run_probes(
    corpus_dir: Path,
    ref_docs: list[dict],
    sampled_docs: list[Path],
    chunker,
    golden_path: Path,
) -> tuple[float | None, float | None, list[dict]]:
    """Run golden probes against reference and fresh indexes."""
    from ragdrift.core.indexing.bm25 import BM25Index
    from ragdrift.core.probing.evaluator import evaluate_retrieval
    from ragdrift.core.probing.golden_set import load_golden_queries

    golden_queries = load_golden_queries(golden_path)

    # Build reference index from stored snapshot chunks
    ref_index = BM25Index()
    for doc in ref_docs:
        chunks = doc.get("chunks") if isinstance(doc.get("chunks"), list) else (
            json.loads(doc["chunks"]) if doc.get("chunks") else []
        )
        if chunks:
            ref_index.add_document(doc["doc_id"], chunks)
    ref_index.build()

    # Build fresh index by re-extracting current corpus files
    fresh_index = BM25Index()
    for doc_path in _discover_docs(corpus_dir):
        try:
            extraction = extract(doc_path)
            chunks = chunker.chunk(extraction["content"])
            fresh_index.add_document(doc_path.name, chunks)
        except Exception:
            continue
    fresh_index.build()

    # Evaluate reference first to get baseline scores
    ref_eval = evaluate_retrieval(ref_index, golden_queries)
    # Evaluate fresh using reference scores to compute score-accuracy drop
    fresh_eval = evaluate_retrieval(
        fresh_index, golden_queries,
        reference_scores=ref_eval["_raw_scores"],
    )

    # Use score_accuracy as the retrieval metric — it captures content-level
    # degradation even when the right document still appears in top-k
    return ref_eval["avg_score_accuracy"], fresh_eval["avg_score_accuracy"], fresh_eval["per_query"]


def _explain_drift(
    scan_result: ScanResult,
    provider: str,
    extra_context: dict | None = None,
) -> str | None:
    """Generate LLM diagnosis of drift."""
    drifted_events = [
        e for e in scan_result["drift_events"] if e["severity"] != "none"
    ]
    if not drifted_events:
        return None

    prompt = _build_explain_prompt(scan_result, drifted_events, extra_context or {})

    if provider == "ollama":
        return _explain_ollama(prompt)
    else:
        return _explain_anthropic(prompt)


def _build_explain_prompt(
    scan_result: ScanResult,
    events: list[DriftEvent],
    extra_context: dict,
) -> str:
    """Build the explanation prompt with full context."""
    events_json = json.dumps(events, indent=2, default=str)
    accuracy_before = scan_result.get("retrieval_accuracy_before")
    accuracy_after = scan_result.get("retrieval_accuracy_after")

    accuracy_line = ""
    if accuracy_before is not None and accuracy_after is not None:
        drop = round((accuracy_before - accuracy_after) * 100, 1)
        accuracy_line = f"Retrieval accuracy: {accuracy_before*100:.1f}% → {accuracy_after*100:.1f}% (dropped {drop}pp)"

    # Failed golden queries — queries where score dropped most
    per_query_section = ""
    per_query_results = extra_context.get("per_query_results", [])
    if per_query_results:
        failed = [q for q in per_query_results if q.get("score_accuracy", 1.0) < 0.95]
        if failed:
            failed_lines = "\n".join([
                f'  - "{q["query"]}" → score_accuracy {q["score_accuracy"]:.0%}'
                f' (expected doc: {q["expected"][0] if q["expected"] else "?"})'
                for q in sorted(failed, key=lambda x: x["score_accuracy"])[:5]
            ])
            per_query_section = f"\nFailed golden queries (score_accuracy < 95%):\n{failed_lines}"

    # Ingestion fingerprints for drifted docs only
    fingerprint_section = ""
    fingerprints = extra_context.get("fingerprints", {})
    if fingerprints:
        drifted_ids = [e["doc_id"] for e in events]
        fp_lines = []
        for doc_id in drifted_ids:
            fp = fingerprints.get(doc_id, {})
            if fp:
                fp_lines.append(
                    f"  - {doc_id}: hash={fp.get('file_hash','?')[:8]}..."
                    f", parser={fp.get('parser_type','?')}"
                    f", chunker={fp.get('chunker_config','?')}"
                )
        if fp_lines:
            fingerprint_section = "\nIngestion fingerprints (reference snapshot):\n" + "\n".join(fp_lines)

    # Drift history — docs that have drifted before are higher risk
    history_section = ""
    drift_history = extra_context.get("drift_history", {})
    repeat_drifters = {k: v for k, v in drift_history.items() if v > 0}
    if repeat_drifters:
        hist_lines = [
            f"  - {doc_id}: drifted {count} time(s) previously — likely unstable source"
            for doc_id, count in repeat_drifters.items()
        ]
        history_section = "\nRepeat drift history:\n" + "\n".join(hist_lines)

    return f"""You are a RAG pipeline engineer diagnosing a document corpus regression.

Scan summary:
- {scan_result['docs_drifted']} of {scan_result['docs_sampled']} documents drifted
- Overall severity: {scan_result['overall_severity']}
- {accuracy_line}
{per_query_section}
{fingerprint_section}
{history_section}

Drift events:
{events_json}

Your job: diagnose each drifted document individually, detect cross-document patterns, and give a prevention tip.

Rules:
- Name specific documents, never generic summaries
- Connect structural changes to the retrieval mechanism:
  * chunk explosion → fragments context, smaller chunks lose cross-section coherence
  * heading removal → loses BM25 signal for section-level queries
  * unicode injection → corrupts tokenization, creates vocabulary noise
  * missing content → removes query-relevant vocabulary entirely
- Use failed golden queries to identify which docs specifically hurt retrieval
- Use fingerprints to distinguish file content change vs extractor/chunker issue
- Confidence: high if structural evidence is unambiguous (chunk delta >20% or multiple heading changes), medium if lexical anomalies only, low if token_shift only
- time_estimate: how long the fix would take a developer (e.g. "2 minutes", "15 minutes")

CRITICAL: Respond with ONLY the raw JSON object. No markdown fences, no ```json, no explanation before or after. Start your response with {{ and end with }}.
{{
  "overall_summary": "One sentence: what happened to this corpus",
  "severity_assessment": "One sentence: urgency and consequence if left unfixed",
  "overall_risk_rating": "critical|high|medium|low",
  "documents": [
    {{
      "doc_id": "exact filename",
      "root_cause": "Specific change description with numbers (e.g. chunk explosion 11→18, 3 headings removed)",
      "retrieval_impact": "Mechanistic explanation of how this hurts search quality",
      "risk_level": "critical|high|medium|low",
      "action": "Specific fix recommendation",
      "time_estimate": "Developer time to fix",
      "confidence": "high|medium|low"
    }}
  ],
  "pattern_detected": "Are multiple drift events related (same root cause) or independent failures?",
  "prevention_tip": "Concrete, actionable tip to prevent this class of drift in future"
}}

Include only drifted documents (severity != none). Sort documents array by risk_level descending (critical first)."""


def _explain_anthropic(prompt: str) -> str | None:
    """Get explanation from Anthropic Claude."""
    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except ImportError:
        print("Warning: anthropic SDK not installed. Install with: pip install ragdrift[explain]", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Warning: LLM explanation failed: {e}", file=sys.stderr)
        return None


def _explain_ollama(prompt: str) -> str | None:
    """Get explanation from local Ollama instance."""
    import requests

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3.2",
                "prompt": prompt,
                "stream": False,
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json().get("response", "")
    except Exception as e:
        print(f"Warning: Ollama explanation failed: {e}", file=sys.stderr)
        return None


def _output_scan(scan_result: ScanResult, fmt: str) -> None:
    """Output scan results in the specified format."""
    if fmt == "json":
        print(json.dumps(scan_result, indent=2, default=str))
    elif fmt == "pretty":
        _print_scan_pretty(scan_result)
    elif fmt == "markdown":
        _print_scan_markdown(scan_result)


def _severity_icon(severity: str) -> str:
    icons = {
        "none": "✅",
        "low": "🔵",
        "medium": "🟡",
        "high": "🟠",
        "critical": "🔴",
    }
    return icons.get(severity, "❓")


# Catppuccin Mocha ANSI colors
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[38;2;108;112;134m"   # overlay0 — muted labels
    RED     = "\033[38;2;243;139;168m"   # critical
    ORANGE  = "\033[38;2;250;179;135m"   # high
    YELLOW  = "\033[38;2;249;226;175m"   # medium/warning
    GREEN   = "\033[38;2;166;227;161m"   # success
    BLUE    = "\033[38;2;137;180;250m"   # info
    MAUVE   = "\033[38;2;203;166;247m"   # headers
    TEXT    = "\033[38;2;205;214;244m"   # normal text


def _severity_color(severity: str) -> str:
    return {
        "critical": C.RED,
        "high": C.ORANGE,
        "medium": C.YELLOW,
        "low": C.BLUE,
        "none": C.GREEN,
    }.get(severity, C.TEXT)


def _clean_heading(h: str) -> str:
    """Strip markdown ## prefix and 'removed:'/'added:' labels for display."""
    import re
    h = re.sub(r"^(removed|added):\s*", r"\1: ", h)
    h = re.sub(r"#{1,6}\s*", "", h)
    return h.strip()


def _clean_anomaly(a: str) -> str:
    """Make raw anomaly keys readable."""
    replacements = {
        "zero_width_chars": "zero-width chars",
        "non_breaking_spaces": "non-breaking spaces",
        "directional_markers": "directional markers",
        "token_shift": "token shift",
        "misaligned_columns": "misaligned columns",
        "table_rows": "table rows",
    }
    for k, v in replacements.items():
        a = a.replace(k, v)
    return a


def _print_scan_pretty(scan: ScanResult) -> None:
    """Print a scan result in human-readable format."""
    import time as _time
    _time.sleep(1.0)
    print(f"\n{C.DIM}{'─'*60}{C.RESET}", flush=True)
    print(f"{C.MAUVE}{C.BOLD}  DRIFT SCAN REPORT{C.RESET}", flush=True)
    print(f"{C.DIM}{'─'*60}{C.RESET}", flush=True)
    _time.sleep(0.4)
    print(f"  {C.DIM}scan{C.RESET}      {scan['scan_id']}", flush=True)
    print(f"  {C.DIM}time{C.RESET}      {scan['timestamp']}", flush=True)
    print(f"  {C.DIM}sampled{C.RESET}   {scan['docs_sampled']} documents", flush=True)
    _time.sleep(0.3)
    print(f"  {C.DIM}drifted{C.RESET}   {C.BOLD}{scan['docs_drifted']} documents{C.RESET}", flush=True)
    _time.sleep(0.3)
    sev_col = _severity_color(scan['overall_severity'])
    print(f"  {C.DIM}severity{C.RESET}  {_severity_icon(scan['overall_severity'])} {sev_col}{C.BOLD}{scan['overall_severity'].upper()}{C.RESET}", flush=True)

    if scan.get("retrieval_accuracy_before") is not None:
        before = scan["retrieval_accuracy_before"]
        after = scan["retrieval_accuracy_after"]
        delta = (after - before) * 100 if before is not None and after is not None else 0
        _time.sleep(0.5)
        print(f"\n  {C.DIM}retrieval accuracy{C.RESET}", flush=True)
        _time.sleep(0.3)
        print(f"    {C.DIM}reference{C.RESET}  {C.GREEN}{before:.1%}{C.RESET}", flush=True)
        _time.sleep(0.3)
        print(f"    {C.DIM}current{C.RESET}    {C.YELLOW if delta < 0 else C.GREEN}{after:.1%}{C.RESET}", flush=True)
        if delta < 0:
            print(f"    {C.DIM}delta{C.RESET}      {C.RED}{delta:+.1f}pp ⚠️{C.RESET}", flush=True)
        else:
            print(f"    {C.DIM}delta{C.RESET}      {C.GREEN}{delta:+.1f}pp{C.RESET}", flush=True)

    if scan["drift_events"]:
        import time as _time
        print(f"\n{C.DIM}  {'─'*56}{C.RESET}")
        print(f"{C.MAUVE}  DRIFT EVENTS{C.RESET}")
        print(f"{C.DIM}  {'─'*56}{C.RESET}")
        for event in scan["drift_events"]:
            if event["severity"] == "none":
                continue
            sev = event["severity"]
            col = _severity_color(sev)
            icon = _severity_icon(sev)
            impact = f" {C.YELLOW}[RETRIEVAL IMPACT]{C.RESET}" if event.get("retrieval_impact") else ""
            _time.sleep(0.6)
            print(f"\n  {icon} {C.BOLD}{event['doc_id']}{C.RESET} {C.DIM}—{C.RESET} {col}{sev.upper()}{C.RESET}{impact}", flush=True)
            _time.sleep(0.2)
            chunks_col = C.RED if abs(event['chunk_delta_pct']) > 20 else C.TEXT
            print(f"     {C.DIM}chunks{C.RESET}     {chunks_col}{event['chunk_count_before']} → {event['chunk_count_after']}  ({event['chunk_delta_pct']:+.1f}%){C.RESET}", flush=True)
            if event["heading_changes"]:
                _time.sleep(0.15)
                cleaned = [_clean_heading(h) for h in event["heading_changes"][:3]]
                # Truncate to 72 chars total
                heading_str = ",  ".join(cleaned)
                if len(heading_str) > 72:
                    heading_str = heading_str[:70] + "…"
                print(f"     {C.DIM}headings{C.RESET}   {heading_str}", flush=True)
            if event["lexical_anomalies"]:
                _time.sleep(0.15)
                cleaned = [_clean_anomaly(a) for a in event["lexical_anomalies"][:3]]
                anomaly_str = ",  ".join(cleaned)
                if len(anomaly_str) > 72:
                    anomaly_str = anomaly_str[:70] + "…"
                print(f"     {C.DIM}anomalies{C.RESET}  {C.ORANGE}{anomaly_str}{C.RESET}", flush=True)
            _time.sleep(0.15)
            action = event['recommended_action'].replace('_', '-')
            action_col = C.RED if action == "re-ingest" else C.YELLOW if action == "alert" else C.DIM
            print(f"     {C.DIM}action{C.RESET}     {action_col}{action}{C.RESET}", flush=True)

    if scan.get("diagnosis"):
        import time as _time
        _time.sleep(2.0)
        print("\033[2J\033[H", end="", flush=True)
        try:
            raw = scan["diagnosis"].strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            diag = json.loads(raw.strip())
            risk_icon = _severity_icon(diag.get("overall_risk_rating", "none"))
            risk_col = _severity_color(diag.get("overall_risk_rating", "none"))
            _time.sleep(0.5)
            print(f"\n  {C.DIM}overall{C.RESET}   {diag.get('overall_summary', 'N/A')}", flush=True)
            _time.sleep(0.4)
            print(f"  {C.DIM}urgency{C.RESET}   {diag.get('severity_assessment', 'N/A')}", flush=True)
            _time.sleep(0.4)
            print(f"  {C.DIM}risk{C.RESET}      {risk_icon} {risk_col}{C.BOLD}{diag.get('overall_risk_rating', 'N/A').upper()}{C.RESET}", flush=True)

            def _wrap_lines(text: str, width: int = 72) -> str:
                """Word-wrap text, each continuation line indented 2 spaces."""
                import textwrap
                lines = textwrap.wrap(text, width=width)
                return ("\n  ").join(lines)

            docs = diag.get("documents", [])
            if docs:
                _time.sleep(0.6)
                # Each doc gets its own clear screen so viewer can read it fully
                for i, doc in enumerate(docs):
                    _time.sleep(0.3)
                    print(f"\033[2J\033[H{C.DIM}  document {i+1} of {len(docs)}{C.RESET}", flush=True)
                    print(f"{C.DIM}  {'─'*56}{C.RESET}", flush=True)
                    print(f"{C.MAUVE}  Per-Document Analysis{C.RESET}", flush=True)
                    print(f"{C.DIM}  {'─'*56}{C.RESET}", flush=True)

                    icon = _severity_icon(doc.get("risk_level", "none"))
                    col = _severity_color(doc.get("risk_level", "none"))
                    conf = doc.get("confidence", "")
                    conf_str = f"  {C.DIM}[{conf} confidence]{C.RESET}" if conf else ""
                    _time.sleep(0.4)
                    print(f"\n  {icon} {C.BOLD}{doc.get('doc_id', '?')}{C.RESET}  {col}{doc.get('risk_level','').upper()}{C.RESET}{conf_str}", flush=True)
                    _time.sleep(0.5)
                    print(f"\n  {C.DIM}cause{C.RESET}", flush=True)
                    _time.sleep(0.2)
                    print(f"  {_wrap_lines(doc.get('root_cause', 'N/A'))}", flush=True)
                    _time.sleep(0.6)
                    print(f"\n  {C.DIM}impact{C.RESET}", flush=True)
                    _time.sleep(0.2)
                    print(f"  {_wrap_lines(doc.get('retrieval_impact', 'N/A'))}", flush=True)
                    _time.sleep(0.6)
                    print(f"\n  {C.DIM}fix{C.RESET}", flush=True)
                    _time.sleep(0.2)
                    print(f"  {col}{_wrap_lines(doc.get('action', 'N/A'))}{C.RESET}", flush=True)
                    _time.sleep(0.4)
                    print(f"\n  {C.DIM}effort{C.RESET}   {doc.get('time_estimate', 'N/A')}", flush=True)
                    _time.sleep(2.5)  # hold each doc so viewer can read it

            pattern = diag.get("pattern_detected")
            prevention = diag.get("prevention_tip")
            if pattern or prevention:
                print("\033[2J\033[H", end="", flush=True)
            if pattern:
                print(f"{C.DIM}  {'─'*56}{C.RESET}", flush=True)
                print(f"{C.MAUVE}  Cross-Document Patterns{C.RESET}", flush=True)
                print(f"{C.DIM}  {'─'*56}{C.RESET}", flush=True)
                _time.sleep(0.4)
                print(f"\n  {_wrap_lines(pattern)}", flush=True)
                _time.sleep(2.0)

            if prevention:
                _time.sleep(0.4)
                print(f"\n{C.DIM}  {'─'*56}{C.RESET}", flush=True)
                print(f"{C.MAUVE}  Prevention{C.RESET}", flush=True)
                print(f"{C.DIM}  {'─'*56}{C.RESET}", flush=True)
                _time.sleep(0.4)
                print(f"\n  {C.GREEN}{_wrap_lines(prevention)}{C.RESET}", flush=True)
                _time.sleep(3.0)

        except (json.JSONDecodeError, TypeError) as e:
            # Try to recover truncated JSON by finding the last complete document
            raw2 = raw.strip()
            try:
                # Find last complete closing brace
                last_brace = raw2.rfind('}')
                if last_brace > 0:
                    recovered = raw2[:last_brace+1]
                    # If it's inside a list, close the list and object
                    if recovered.count('[') > recovered.count(']'):
                        recovered += ']}'
                    diag = json.loads(recovered)
                    # Re-run display with recovered data (just summary)
                    print(f"\n  {C.DIM}overall{C.RESET}   {diag.get('overall_summary', 'N/A')}", flush=True)
                    print(f"  {C.DIM}urgency{C.RESET}   {diag.get('severity_assessment', 'N/A')}", flush=True)
                else:
                    print(f"  {C.DIM}(diagnosis truncated){C.RESET}", flush=True)
            except Exception:
                print(f"  {C.DIM}(diagnosis unavailable: {e}){C.RESET}", flush=True)

    print(f"\n{C.DIM}{'─'*60}{C.RESET}\n")


def _print_scan_summary(scan: dict) -> None:
    """Print a brief scan summary for report history."""
    icon = _severity_icon(scan.get("overall_severity", "none"))
    print(f"  {icon} Scan {scan.get('scan_id', 'N/A')} | {scan.get('timestamp', 'N/A')}")
    print(f"     Sampled: {scan.get('docs_sampled', 0)} | Drifted: {scan.get('docs_drifted', 0)} | Severity: {scan.get('overall_severity', 'none').upper()}")
    if scan.get("retrieval_accuracy_before") is not None and scan.get("retrieval_accuracy_after") is not None:
        print(f"     Retrieval: {scan['retrieval_accuracy_before']:.1%} → {scan['retrieval_accuracy_after']:.1%}")
    print()


def _print_scan_markdown(scan: dict) -> None:
    """Print scan result in markdown format."""
    severity = scan.get("overall_severity", "none")
    print(f"## Scan {scan.get('scan_id', 'N/A')}")
    print(f"- **Timestamp:** {scan.get('timestamp', 'N/A')}")
    print(f"- **Severity:** {severity.upper()}")
    print(f"- **Documents sampled:** {scan.get('docs_sampled', 0)}")
    print(f"- **Documents drifted:** {scan.get('docs_drifted', 0)}")

    events = scan.get("drift_events", [])
    if events:
        print("\n| Document | Severity | Chunks | Action |")
        print("|----------|----------|--------|--------|")
        for event in events:
            if event.get("severity", "none") == "none":
                continue
            print(f"| {event['doc_id']} | {event['severity']} | {event.get('chunk_count_before', '?')} → {event.get('chunk_count_after', '?')} | {event.get('recommended_action', 'none')} |")
    print()


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="ragdrift",
        description="Silent regression detector for RAG pipelines",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_parser = subparsers.add_parser("init", help="Take a reference snapshot of a corpus")
    init_parser.add_argument("--corpus", required=True, help="Path to document corpus directory")
    init_parser.add_argument("--golden", help="Path to golden queries JSON file")
    init_parser.add_argument("--chunk-size", type=int, default=512)
    init_parser.add_argument("--chunk-overlap", type=int, default=50)

    # scan
    scan_parser = subparsers.add_parser("scan", help="Scan corpus for drift")
    scan_parser.add_argument("--corpus", required=True, help="Path to document corpus directory")
    scan_parser.add_argument("--sample-rate", type=float, default=0.2, help="Fraction of docs to sample (default: 0.2)")
    scan_parser.add_argument("--explain", action="store_true", help="Generate LLM diagnosis")
    scan_parser.add_argument("--provider", choices=["anthropic", "ollama"], default="anthropic", help="LLM provider for --explain")
    scan_parser.add_argument("--format", choices=["json", "pretty", "markdown"], default="json")
    scan_parser.add_argument("--chunk-size", type=int, default=512)
    scan_parser.add_argument("--chunk-overlap", type=int, default=50)

    # demo
    demo_parser = subparsers.add_parser("demo", help="Run self-contained drift demo")
    demo_parser.add_argument("--inject-drift", action="store_true", help="Inject drift into demo corpus")
    demo_parser.add_argument("--level", choices=["mild", "moderate", "heavy", "catastrophic"], default="moderate", help="Drift severity level")
    demo_parser.add_argument("--format", choices=["json", "pretty", "markdown"], default="pretty")
    demo_parser.add_argument("--explain", action="store_true", help="Generate LLM diagnosis of drift")
    demo_parser.add_argument("--provider", choices=["anthropic", "ollama"], default="anthropic", help="LLM provider for --explain")

    # report
    report_parser = subparsers.add_parser("report", help="Show historical drift reports")
    report_parser.add_argument("--corpus", required=True, help="Path to document corpus directory")
    report_parser.add_argument("--format", choices=["json", "pretty", "markdown"], default="json")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "demo":
        cmd_demo(args)
    elif args.command == "report":
        cmd_report(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
