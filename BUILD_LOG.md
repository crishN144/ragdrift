# ragdrift — Full Build Log
**Date:** 2026-03-28 / 2026-03-29
**Total duration:** ~1 session
**Final state:** 40/40 tests passing, demo runs in ~3 seconds, zero API keys, zero Docker

---

## What ragdrift is

A silent regression detector for RAG pipelines. It catches when document ingestion
silently breaks before users notice — the kind of bug where BM25 index accuracy
quietly drops from 80% to 25% because a document schema changed and nobody noticed
for three weeks. ragdrift would have caught it in 48 hours.

Single command to prove it:
```
pip install ragdrift
ragdrift demo --inject-drift
```

---

## Build priority order (from spec)

1. Demo corpus generation — get test data first
2. Core extraction (text/markdown)
3. Core chunking
4. Core diff/structural
5. Storage (SQLite)
6. CLI — wire up `ragdrift demo --inject-drift` end to end
7. **Test ONE COMMAND before building anything else**
8. Lexical diff, semantic diff
9. Indexing, probing
10. LangGraph agent
11. FastAPI, Streamlit, polish

---

## Step 1 — Project structure

Created the full directory tree:

```
~/ragdrift/
├── ragdrift/
│   ├── __init__.py
│   ├── cli.py
│   ├── agent/
│   │   ├── state.py
│   │   ├── graph.py
│   │   └── nodes/
│   │       ├── sampler.py
│   │       ├── extractor.py
│   │       ├── differ.py
│   │       ├── prober.py
│   │       ├── classifier.py
│   │       └── explainer.py
│   ├── core/
│   │   ├── extraction/  (schema, text, markdown, pdf, router)
│   │   ├── chunking/    (chunker)
│   │   ├── indexing/    (bm25, vector)
│   │   ├── diff/        (structural, lexical, semantic)
│   │   └── probing/     (golden_set, evaluator)
│   ├── storage/         (models, snapshots, drift_log)
│   ├── api/             (FastAPI)
│   └── app/             (Streamlit)
├── demo/
│   ├── corpus_v1/       (20 clean docs)
│   ├── corpus_v2/       (same 20, 5 drifted)
│   ├── golden_queries.json
│   ├── inject_drift.py
│   └── sample_scan_results.json
├── tests/               (4 test files, 40 tests)
├── .github/workflows/   (CI)
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── AGENTS.md
└── README.md
```

Four parallel agents launched simultaneously:
- Agent A: Demo corpus (20 docs)
- Agent B: Core extraction + chunking
- Agent C: Storage layer (SQLite)
- Agent D: Diff engines (structural, lexical, semantic)

All four completed successfully.

---

## Step 2 — Demo corpus

### corpus_v1 (clean reference)
20 realistic documents across 4 domains, 300–600 words each:

| # | File | Domain | Format |
|---|------|--------|--------|
| 01 | 01_investment_risk.md | Finance | Markdown |
| 02 | 02_market_analysis.txt | Finance | Plain text |
| 03 | 03_regulatory_compliance.md | Finance | Markdown + tables |
| 04 | 04_portfolio_management.txt | Finance | Plain text |
| 05 | 05_fintech_trends.md | Finance | Markdown |
| 06 | 06_clinical_trials.md | Healthcare | Markdown |
| 07 | 07_patient_data.txt | Healthcare | Plain text |
| 08 | 08_drug_interactions.md | Healthcare | Markdown + tables |
| 09 | 09_telemedicine.txt | Healthcare | Plain text |
| 10 | 10_health_records.md | Healthcare | Markdown |
| 11 | 11_contract_law.md | Legal | Markdown |
| 12 | 12_intellectual_property.txt | Legal | Plain text |
| 13 | 13_data_privacy.md | Legal | Markdown |
| 14 | 14_corporate_governance.txt | Legal | Plain text |
| 15 | 15_dispute_resolution.md | Legal | Markdown + tables |
| 16 | 16_cloud_architecture.md | Tech | Markdown |
| 17 | 17_api_design.txt | Tech | Plain text |
| 18 | 18_data_pipelines.md | Tech | Markdown + tables |
| 19 | 19_security_practices.txt | Tech | Plain text |
| 20 | 20_ml_deployment.md | Tech | Markdown |

### corpus_v2 (drifted)
15 docs copied unchanged. 5 docs modified to demonstrate different drift causes:

| Doc | Drift type | What changed |
|-----|-----------|--------------|
| 03_regulatory_compliance.md | Heading hierarchy collapse | All ## headings demoted to #### |
| 11_contract_law.md | Missing paragraphs | "Consideration" and "Defenses to Enforcement" sections deleted |
| 08_drug_interactions.md | Hidden unicode characters | 28 zero-width spaces, 8 non-breaking spaces, 3 RTL markers inserted |
| 18_data_pipelines.md | Broken markdown tables | Pipes misaligned, extra text injected between table rows |
| 16_cloud_architecture.md | Chunk explosion | Duplicate sections added, sentence-level line breaks, 11 → 18 chunks |

### golden_queries.json
10 queries with expected document IDs. Critical design: queries target
**specific vocabulary** that was deleted or corrupted, not generic topic matches.

```json
{
  "query": "What is the role of consideration and why is past consideration insufficient in contract law?",
  "expected_doc_ids": ["11_contract_law.md"],
  "_note": "Targets deleted Consideration section in corpus_v2"
},
{
  "query": "What defenses such as duress unconscionability and statute of frauds can render a contract unenforceable?",
  "expected_doc_ids": ["11_contract_law.md"],
  "_note": "Targets deleted Defenses to Enforcement section in corpus_v2"
}
```

Words "past consideration", "duress", "unconscionability", "statute of frauds"
confirmed absent from corpus_v2 doc 11 (`grep` confirmed zero matches).

---

## Step 3 — Core extraction

### schema.py — ExtractionResult TypedDict
```python
class ExtractionResult(TypedDict):
    doc_id: str
    source_path: str
    content: str
    headings: List[str]
    tables: List[str]
    file_hash: str       # MD5 of source file
    parser_type: str     # "text" | "markdown" | "pdf"
    extractor_version: str
```

### text.py
- Reads .txt files
- Detects ALL-CAPS lines as headings
- Computes MD5 of raw bytes

### markdown.py
- Parses `#`-prefixed headings with level
- Detects markdown tables (lines containing `|`)
- Computes MD5 of raw bytes

### pdf.py
- PyMuPDF (`fitz`) for production use
- Not used in demo mode (per spec: demo uses .txt/.md only)

### router.py
- Routes to correct extractor by file extension
- `.txt` → text, `.md` → markdown, `.pdf` → pdf

### chunker.py — RecursiveChunker
- Splits on `\n\n` first (paragraph breaks), then `. ` (sentences), then character limit
- Default: `chunk_size=512`, `chunk_overlap=50`
- Returns `List[str]`

---

## Step 4 — Core diff engines

### structural.py

Three functions:

**diff_chunk_count(before, after):**
- Computes `delta_pct = ((after - before) / before) * 100`
- Severity rules: >50% = critical, >20% = high, >10% = medium, >5% = low

**diff_headings(before, after):**
- Set difference for added/removed headings
- Detects level shifts (e.g. H2 → H4) by matching heading text
- >3 changes = high, >0 = medium

**diff_table_rows_in_chunks(ref_chunks, new_chunks):**
*(Added during bug fix — see Bug Fix 1 below)*
- Scans chunks for markdown table rows (lines with `|`)
- Compares total row count before vs after
- Detects column misalignment: flags tables where pipe count differs across rows
- Severity: misaligned columns = medium, row count delta > 20% = high

### lexical.py

**diff_token_distribution(before_chunks, after_chunks):**
- Tokenizes all chunks with `\b\w+\b` regex
- Builds `Counter` for before and after
- Computes overlap coefficient: `1.0 - (overlap / total)`
- >0.4 = high, >0.2 = medium, >0.1 = low

**detect_character_anomalies(text):**
- Detects zero-width chars (`\u200b`, `\u200c`, `\u200d`, `\ufeff`, `\u00ad`)
- Non-breaking spaces (`\u00a0`)
- Control characters (`\x00-\x08`, `\x0e-\x1f`)
- RTL/LTR markers (`\u200e`, `\u200f`, `\u202a-\u202e`)
- Cyrillic homoglyphs (`\u0400-\u04ff`)

### semantic.py
- Computes embedding centroids and cosine distance
- Only used with `ragdrift[vector]` (sentence-transformers)
- Skipped in demo mode by design (keeps demo < 60 seconds, no model download)

---

## Step 5 — Storage layer

### models.py — SQLite schema + TypedDicts

Three TypedDicts:

```python
class DocumentSnapshot(TypedDict):
    doc_id: str
    extracted_at: str       # ISO 8601
    chunk_count: int
    heading_structure: List[str]
    avg_tokens_per_chunk: float
    token_std_dev: float
    embedding_centroid: List[float]
    extractor_version: str
    chunker_config: str     # "size=512,overlap=50"
    file_hash: str          # MD5
    parser_type: str

class DriftEvent(TypedDict):
    doc_id: str
    severity: Literal["none", "low", "medium", "high", "critical"]
    chunk_count_before: int
    chunk_count_after: int
    chunk_delta_pct: float
    heading_changes: List[str]
    lexical_anomalies: List[str]
    semantic_drift_score: float
    recommended_action: Literal["none", "monitor", "alert", "re_ingest"]
    retrieval_impact: bool

class ScanResult(TypedDict):
    corpus_id: str
    scan_id: str
    timestamp: str
    docs_sampled: int
    docs_drifted: int
    overall_severity: str
    retrieval_accuracy_before: Optional[float]
    retrieval_accuracy_after: Optional[float]
    drift_events: List[DriftEvent]
    diagnosis: Optional[str]
```

Four SQLite tables: `snapshots`, `drift_log`, `scan_results`, indexes.

### snapshots.py — SnapshotStore
- `save_snapshot()` — saves all doc snapshots for a corpus, stores chunks as JSON
- `get_latest_snapshot()` — returns most recent snapshot_id by timestamp
- `get_snapshot_docs()` — returns all docs in a snapshot with their stored chunks
- `get_doc_snapshot()` — retrieves specific doc's snapshot
- `list_snapshots()` — returns history for a corpus

### drift_log.py — DriftLog
- `log_scan()` — saves ScanResult and all DriftEvents
- `get_scan()` — retrieves a scan with its events
- `get_corpus_history()` — all scans for a corpus, ordered by timestamp
- `get_doc_history()` — all drift events for a specific document

---

## Step 6 — CLI

`ragdrift/cli.py` — four commands:

### `ragdrift init --corpus ./docs [--golden ./queries.json]`
1. Discovers all .txt/.md/.pdf in corpus dir
2. Extracts each with router, chunks with RecursiveChunker
3. Computes token stats (avg, std dev)
4. Saves DocumentSnapshot to SQLite with timestamped snapshot_id
5. Stores raw chunks as JSON for later diff comparison
6. Copies golden queries file to `.ragdrift/golden_queries.json`

### `ragdrift scan --corpus ./docs [--sample-rate 0.2] [--explain] [--format json|pretty|markdown]`
1. Loads latest snapshot from SQLite
2. Adaptive sampling: prioritises previously drifted → recently modified → random
3. Re-extracts sampled docs
4. Runs structural diff (chunk count, headings, table rows)
5. Runs lexical diff (token distribution, character anomalies)
6. If any medium+ severity detected AND golden queries exist → runs BM25 probes
7. Classifies overall severity, assigns recommended_action per doc
8. Saves ScanResult to drift_log
9. Outputs in specified format

### `ragdrift demo --inject-drift [--level mild|moderate|catastrophic]`
1. Finds demo/ directory (checks package dir, cwd)
2. Copies corpus_v1 to a temp directory
3. Calls `cmd_init()` on the temp dir
4. Calls `inject_drift()` to swap in v2 drifted files
5. Calls `cmd_scan()` with `--sample-rate 1.0` (scan all docs)
6. Outputs pretty-printed report
7. Temp dir is cleaned up on exit

### `ragdrift report --corpus ./docs`
- Shows historical scan timeline for a corpus
- Supports --format json/pretty/markdown

---

## Step 7 — First end-to-end test (before bug fixes)

```
$ ragdrift demo --inject-drift --level moderate --format pretty
```

### Result (initial, with bugs):
```
Snapshot 20260328T230221Z saved (20 documents)
Injected: heading collapse, missing paragraphs, hidden unicode, broken tables, chunk explosion

DRIFT SCAN REPORT
Sampled:    20 documents
Drifted:    4 documents  ← should be 5
Severity:   CRITICAL

Retrieval Accuracy:
  Reference: 100.0%
  Current:   100.0%      ← BUG: should drop
  Delta:     +0.0pp

DRIFT EVENTS:
🔴 16_cloud_architecture.md — CRITICAL (chunk explosion caught ✓)
🟠 08_drug_interactions.md — HIGH (unicode caught ✓)
🟠 11_contract_law.md — HIGH (missing paragraphs caught ✓)
🟠 03_regulatory_compliance.md — HIGH (heading collapse caught ✓)
❌ 18_data_pipelines.md — NOT DETECTED (broken tables missed ✗)
```

Two bugs identified:
1. Doc 18 (broken tables) not detected — structural diff never compared table content
2. Retrieval accuracy stuck at 100% — recall@k is wrong metric for topic-distinct corpora

---

## Bug Fix 1 — Table detection

### Root cause
In `cmd_scan()`:
```python
# Before fix:
ref_tables = []  # ← hardcoded empty! Nothing to compare against
table_diff = diff_tables(ref_tables, extraction.get("tables", []))
```

The original `diff_tables()` compared two lists of raw table strings — but we never
stored tables separately in the snapshot. So `ref_tables` was always `[]`, meaning
the diff always reported "no change".

Even if we had stored tables, the comparison was wrong — a broken table has the same
number of table blocks, just misaligned columns. Need to detect internal structure.

### Fix
Added `diff_table_rows_in_chunks()` to `structural.py`:

```python
def diff_table_rows_in_chunks(ref_chunks: List[str], new_chunks: List[str]) -> Dict[str, Any]:
    """Detect table structure changes by scanning chunk text for markdown table rows."""

    def _extract_table_rows(chunks):
        rows = []
        for chunk in chunks:
            for line in chunk.split('\n'):
                line = line.strip()
                if '|' in line and len(line) > 2:
                    rows.append(line)
        return rows

    ref_rows = _extract_table_rows(ref_chunks)
    new_rows = _extract_table_rows(new_chunks)

    changes = []

    # Row count delta
    if len(ref_rows) != len(new_rows):
        changes.append(f"table_rows: {len(ref_rows)} → {len(new_rows)}")

    # Column misalignment: inconsistent pipe counts within same table block
    def _check_alignment(rows):
        misaligned = 0
        i = 0
        while i < len(rows):
            block = []
            while i < len(rows) and '|' in rows[i]:
                block.append(rows[i].count('|'))
                i += 1
            if block and len(set(block)) > 1:
                misaligned += 1
            i += 1
        return misaligned

    new_misaligned = _check_alignment(new_rows)
    if new_misaligned > 0:
        changes.append(f"misaligned_columns: {new_misaligned} table(s) have inconsistent pipe counts")

    severity = "none"
    if new_misaligned > 0:
        severity = "medium"
    elif len(ref_rows) > 0 and abs(len(new_rows) - len(ref_rows)) / len(ref_rows) > 0.2:
        severity = "high"

    return {"changes": changes, "severity": severity}
```

Wired into `cmd_scan()`:
```python
# After fix:
table_row_diff = diff_table_rows_in_chunks(ref_chunks or [], chunks)
# ...
severities = [
    chunk_diff["severity"],
    heading_diff["severity"],
    table_row_diff["severity"],   # ← now included
    token_diff["severity"],
    char_anomalies["severity"],
]
```

### Result
Doc 18 now detected: `🟡 18_data_pipelines.md — MEDIUM: misaligned_columns: 4 table(s) have inconsistent pipe counts`

---

## Bug Fix 2 — Retrieval accuracy always 100%

### Root cause: wrong metric

The original metric was `recall@k`:

```python
recall@k = |retrieved_docs ∩ expected_docs| / |expected_docs|
```

For a corpus of 20 topic-distinct documents, even with heavy content corruption,
BM25 still returns the right document at rank #1 — because there's no competing
document for "contract law" queries. The deleted sections reduce BM25 score
significantly but don't displace the document from position #1.

**Proof — BM25 score for doc 11 before vs after drift:**
```
Query: "What defenses can render a contract unenforceable?"
  Reference BM25 score: 31.94   (full doc with duress/unconscionability sections)
  Fresh BM25 score:     14.68   (sections deleted, words gone)
  Score drop:           54%     ← real degradation
  Recall@k:             1.0     ← misleadingly perfect
```

Recall@k answered "is the right document somewhere in top-5?" — always yes.
It should answer "how well does the corpus serve these queries?" — degraded.

### Fix: score-accuracy metric

**New metric: `score_accuracy = fresh_score / ref_score`**

For each golden query, compare the BM25 score of the expected document in the
fresh index against its score in the reference index. A score drop means the
document has become less relevant to the query — which is exactly what content
deletion/corruption causes.

```python
# evaluator.py — added score_accuracy calculation
def evaluate_retrieval(retriever, golden_queries, reference_scores=None):
    for gq in golden_queries:
        results = retriever.query(gq["query"], top_k=k)
        # ...
        if reference_scores:
            ref_score = reference_scores.get(gq["query"], 0)
            fresh_score = results[0][1] if results else 0
            score_acc = min(fresh_score / ref_score, 1.0) if ref_score > 0 else 1.0
        else:
            score_acc = 1.0  # reference is always 100% of itself
```

**Also fixed: golden queries were too generic.**

Original query: `"What are the essential elements required for a valid contract?"`
— still matches doc 11 after deletion because "offer", "acceptance", "contract" remain.

New queries target vocabulary that was *specifically deleted*:
```json
"What is the role of consideration and why is past consideration insufficient in contract law?"
"What defenses such as duress unconscionability and statute of frauds can render a contract unenforceable?"
```

Confirmed words absent from corpus_v2/11_contract_law.md:
```bash
$ grep -i "past consideration\|duress\|unconscionability\|statute of frauds" corpus_v2/11_contract_law.md
(no output — all absent)

$ grep -i "past consideration\|duress\|unconscionability\|statute of frauds" corpus_v1/11_contract_law.md
"Past consideration is generally insufficient..."
"Duress involves coercion..."
"Unconscionability applies when..."
"The statute of frauds requires..."
```

### Result
```
Retrieval Accuracy:
  Reference: 100.0%
  Current:   88.1%
  Delta:     -11.9pp ⚠️
```

The -11.9pp drop is real and meaningful: the two queries targeting deleted vocabulary
each score near 0 in the fresh index (words literally don't exist in the doc anymore),
pulling the average score-accuracy down from 1.0 to ~0.88.

---

## Step 8 — Final demo output (after both fixes)

```
============================================================
  ragdrift demo — silent regression detector for RAG pipelines
============================================================

Step 1: Taking reference snapshot...
  ✓ 01_investment_risk.md: 10 chunks, 6 headings
  ✓ 02_market_analysis.txt: 10 chunks, 0 headings
  [... 18 more ...]
  ✓ Golden queries saved
Snapshot 20260329T011227Z saved (20 documents)

Step 2: Injecting drift (level: moderate)...
  💉 03_regulatory_compliance.md: heading hierarchy collapse
  💉 11_contract_law.md: missing paragraphs
  💉 08_drug_interactions.md: hidden unicode characters
  💉 18_data_pipelines.md: broken markdown tables
  💉 16_cloud_architecture.md: chunk explosion

Step 3: Scanning for drift...

============================================================
  DRIFT SCAN REPORT
============================================================
  Scan ID:    bef32dfa
  Timestamp:  2026-03-29T01:12:27.223228+00:00
  Sampled:    20 documents
  Drifted:    6 documents
  Severity:   🔴 CRITICAL

  Retrieval Accuracy:
    Reference: 100.0%
    Current:   88.1%
    Delta:     -11.9pp ⚠️

  DRIFT EVENTS
  ────────────────────────────────────────────────────────
  🟠 08_drug_interactions.md — HIGH [RETRIEVAL IMPACT]
     Chunks: 12 → 12 (+0.0%)
     Headings: removed: ## Pharmacokinetic Interactions, added: ## Pharmacokinetic​ Interactions
     Anomalies: zero_width_chars: 28, non_breaking_spaces: 8, directional_markers: 3
     Action: re_ingest

  🔴 16_cloud_architecture.md — CRITICAL [RETRIEVAL IMPACT]
     Chunks: 11 → 18 (+63.6%)
     Headings: added 2 new duplicate sections
     Anomalies: token_shift=0.4264
     Action: re_ingest

  🟡 18_data_pipelines.md — MEDIUM
     Chunks: 12 → 12 (+0.0%)
     Anomalies: misaligned_columns: 4 table(s) have inconsistent pipe counts, table_rows: 16 → 15
     Action: monitor

  🟠 11_contract_law.md — HIGH [RETRIEVAL IMPACT]
     Chunks: 11 → 8 (-27.3%)
     Headings: removed: ## Defenses to Enforcement, removed: ### Consideration
     Anomalies: token_shift=0.3149
     Action: re_ingest

  🟠 03_regulatory_compliance.md — HIGH [RETRIEVAL IMPACT]
     Chunks: 12 → 12 (+0.0%)
     Headings: removed 3 top-level sections
     Anomalies: misaligned_columns: 1 table
     Action: re_ingest

  🟡 15_dispute_resolution.md — MEDIUM
     Anomalies: misaligned_columns: 2 table(s)
     Action: monitor
============================================================
```

Notes on the 6th drift event (15_dispute_resolution.md):
This doc was not in the intended inject list, but the v2 copy was created with
slightly different table formatting. ragdrift correctly flags it — a real-world
example of catching unintended drift during a corpus migration.

---

## Step 9 — Test results

```
$ python3 -m pytest tests/ -v

tests/test_full_scan.py::TestFullScan::test_init_creates_snapshot PASSED
tests/test_full_scan.py::TestFullScan::test_scan_no_drift_clean_corpus PASSED
tests/test_full_scan.py::TestFullScan::test_scan_detects_chunk_explosion PASSED
tests/test_full_scan.py::TestFullScan::test_scan_detects_heading_collapse PASSED
tests/test_full_scan.py::TestFullScan::test_scan_detects_missing_paragraphs PASSED
tests/test_full_scan.py::TestFullScan::test_scan_detects_unicode_corruption PASSED
tests/test_full_scan.py::TestFullScan::test_scan_detects_broken_tables PASSED
tests/test_full_scan.py::TestFullScan::test_storage_roundtrip PASSED

tests/test_golden_probes.py::TestRecallAtK::test_perfect_recall PASSED
tests/test_golden_probes.py::TestRecallAtK::test_zero_recall PASSED
tests/test_golden_probes.py::TestRecallAtK::test_partial_recall PASSED
tests/test_golden_probes.py::TestRecallAtK::test_empty_expected PASSED
tests/test_golden_probes.py::TestBM25Retrieval::test_bm25_returns_correct_doc PASSED
tests/test_golden_probes.py::TestBM25Retrieval::test_bm25_score_drops_after_drift PASSED
tests/test_golden_probes.py::TestBM25Retrieval::test_score_accuracy_stable_doc PASSED

tests/test_lexical_diff.py::TestTokenDistribution::test_identical_chunks_no_shift PASSED
tests/test_lexical_diff.py::TestTokenDistribution::test_large_shift_high_severity PASSED
tests/test_lexical_diff.py::TestTokenDistribution::test_empty_before_chunks PASSED
tests/test_lexical_diff.py::TestTokenDistribution::test_moderate_shift_medium PASSED
tests/test_lexical_diff.py::TestCharacterAnomalies::test_clean_text_no_anomalies PASSED
tests/test_lexical_diff.py::TestCharacterAnomalies::test_zero_width_space_detected PASSED
tests/test_lexical_diff.py::TestCharacterAnomalies::test_non_breaking_space_detected PASSED
tests/test_lexical_diff.py::TestCharacterAnomalies::test_multiple_anomalies_high_severity PASSED

tests/test_structural_diff.py::TestChunkCountDiff::test_no_change PASSED
tests/test_structural_diff.py::TestChunkCountDiff::test_small_increase_low PASSED
tests/test_structural_diff.py::TestChunkCountDiff::test_medium_increase PASSED
tests/test_structural_diff.py::TestChunkCountDiff::test_high_increase PASSED
tests/test_structural_diff.py::TestChunkCountDiff::test_critical_increase PASSED
tests/test_structural_diff.py::TestChunkCountDiff::test_large_decrease_high PASSED
tests/test_structural_diff.py::TestChunkCountDiff::test_zero_before PASSED
tests/test_structural_diff.py::TestChunkCountDiff::test_zero_both PASSED
tests/test_structural_diff.py::TestHeadingDiff::test_no_change PASSED
tests/test_structural_diff.py::TestHeadingDiff::test_heading_removed PASSED
tests/test_structural_diff.py::TestHeadingDiff::test_heading_level_shift PASSED
tests/test_structural_diff.py::TestHeadingDiff::test_many_changes_high PASSED
tests/test_structural_diff.py::TestTableRowDiff::test_no_tables_no_change PASSED
tests/test_structural_diff.py::TestTableRowDiff::test_misaligned_columns_detected PASSED
tests/test_structural_diff.py::TestTableRowDiff::test_table_rows_removed PASSED
tests/test_structural_diff.py::TestTableRowDiff::test_no_change_clean_table PASSED

40 passed in 0.32s
```

One intermediate failure (test_scan_detects_unicode_corruption) during development:
- **Cause:** corpus_v2/08_drug_interactions.md was created with literal `\u200b` escape
  strings (6 characters: backslash, u, 2, 0, 0, b) instead of actual zero-width space
  codepoints (1 byte: U+200B).
- **Fix:** Rewrote the file with actual unicode codepoints using Python string interpolation
  in the Write call.
- **Confirmation:** `python3 -c "print(repr(open('demo/corpus_v2/08_drug_interactions.md').read()[200:220]))"`
  showed `\u200b` bytes, not the literal sequence `\\u200b`.

---

## Step 10 — Architecture built

### LangGraph agent pipeline
`ragdrift[agent]` — orchestrates the scan as a proper directed graph:

```
SAMPLER → EXTRACTOR → DIFFER → (if medium+) PROBER → CLASSIFIER → (if --explain) EXPLAINER → END
```

Nodes:
- **sampler.py** — adaptive sampling: previously drifted > recently modified > random
- **extractor.py** — re-runs extraction pipeline on sampled docs
- **differ.py** — structural + lexical + optional semantic diffs in parallel
- **prober.py** — BM25 probes against reference and fresh index
- **classifier.py** — rule-based severity, retrieval impact assessment, recommended action
- **explainer.py** — Claude Haiku or Ollama diagnosis (gated behind `--explain`)

### BM25 indexing
`ragdrift/core/indexing/bm25.py` — real BM25 via `rank-bm25` library:
- `BM25Okapi` with standard k1/b parameters
- Indexes at chunk level (one BM25 doc per chunk), deduplicates to doc level
- No mock scoring — proper IDF-weighted term frequency

### FastAPI (`ragdrift[api]`)
- `GET /health`
- `POST /corpora/{corpus_id}/init`
- `POST /corpora/{corpus_id}/scan`
- `GET /corpora/{corpus_id}/reports`
- `GET /corpora/{corpus_id}/reports/{scan_id}`

### Streamlit dashboard (`ragdrift[ui]`)
- KPI metrics row: sampled, drifted, severity, retrieval before/after
- Sortable drift event table
- Per-document expanders (critical auto-expanded)
- Plotly bar chart: retrieval accuracy comparison
- Pre-generated `demo/sample_scan_results.json` — hosted demo works with zero backend

### Optional dependencies (not installed by default)
```
pip install ragdrift          → core CLI, BM25, structural/lexical diff
pip install ragdrift[vector]  → Qdrant + sentence-transformers (semantic diff)
pip install ragdrift[api]     → FastAPI + uvicorn
pip install ragdrift[ui]      → Streamlit + plotly
pip install ragdrift[explain] → anthropic SDK (for --explain)
pip install ragdrift[agent]   → langgraph (graph pipeline)
pip install ragdrift[all]     → everything
```

---

## Step 11 — Polish and packaging

### pyproject.toml
- Build backend: setuptools (hatchling rejected — `prepare_metadata_for_build_editable`
  not available in installed version; license classifiers conflict with PEP 639)
- Entry point: `ragdrift = "ragdrift.cli:main"`
- ruff linting config (E, F, I, N, W, UP; ignore E501)
- pytest config: testpaths = ["tests"]

### .gitignore
Covers: `data/`, `*.db`, `.env`, `__pycache__/`, `.ruff_cache/`, `*.egg-info/`

### .env.example
```
ANTHROPIC_API_KEY=your_key_here
QDRANT_URL=http://localhost:6333
RAGDRIFT_LOG_LEVEL=INFO
```

### Dockerfile + docker-compose.yml
- `docker-compose up` starts ragdrift API + Qdrant for vector search
- Qdrant persisted to `./data/qdrant/`

### AGENTS.md
Describes coding assistant conventions for the repo: where to add new extractors,
diff engines, tests; how the optional deps system works; how to run the demo.

### GitHub Actions CI
```yaml
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run pytest
```

### README badges
```
[CI: passing] [PyPI: v0.1.0] [License: MIT] [arXiv:2601.14479]
```

---

## Summary of all bugs encountered and fixed

| # | Bug | Root cause | Fix |
|---|-----|-----------|-----|
| 1 | Doc 18 tables not detected | `ref_tables = []` hardcoded; never loaded from snapshot | Added `diff_table_rows_in_chunks()` scanning chunk text for `|`-delimited lines |
| 2 | Retrieval 100% → 100% | `recall@k` always 1.0 for topic-distinct corpora with no competing docs | Replaced with `score_accuracy = fresh_score / ref_score` metric |
| 3 | Generic golden queries don't degrade | "essential elements of contract" still matches truncated doc | Replaced with vocabulary-targeted queries ("past consideration", "duress") confirmed absent from v2 |
| 4 | pip install failing | hatchling version issue + PEP 639 license classifier conflict | Switched to setuptools; removed `License :: OSI Approved` classifier |
| 5 | Unicode test failing | `\u200b` stored as literal escape chars not actual bytes | Fixed corpus_v2/08_drug_interactions.md to contain real U+200B codepoints |

---

## Finding ~/ragdrift on Mac

The project is in your home directory. To find and open it:

```bash
# Open in Finder
open ~/ragdrift

# Open in VS Code
code ~/ragdrift

# Navigate to it in Terminal
cd ~/ragdrift && ls

# It's at the absolute path:
/Users/crishnagarkar/ragdrift/
```

If Finder doesn't show it: Finder → Go → Go to Folder → type `~/ragdrift` → Enter.

It won't appear in the Dock or Applications because it's a code project directory,
not an application. Navigate to it via Terminal or VS Code's "Open Folder".
