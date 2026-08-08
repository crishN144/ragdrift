# Changelog

## 0.2.0 — 2026-08-08

### Added

- **MCP server** (`pip install ragdrift[mcp]`): new `ragdrift-mcp` console entry
  point (stdio transport, official `mcp` SDK) exposing six tools —
  `snapshot_corpus`, `run_drift_scan`, `probe_golden_queries`, `diagnose_drift`,
  `list_recent_scans`, `get_latest_report` — backed by the same code paths as
  the CLI. Structured JSON outputs reuse the existing `ScanResult`/`DriftEvent`
  models; failures surface as MCP tool errors, never server crashes.
  Register in Claude Code with `claude mcp add ragdrift -- ragdrift-mcp`.
- **LangSmith tracing** (`pip install ragdrift[langsmith]`): env-gated on
  `LANGSMITH_API_KEY`, zero behavior change and zero import cost when absent.
  Scans/snapshots trace as `ragdrift.scan`/`ragdrift.init` chain runs; every
  `--explain` LLM call traces as an LLM run with token usage and estimated cost
  in run metadata (price table in `ragdrift/observability/costs.py`; Ollama =
  $0). LangGraph pipelines from `build_scan_graph()` trace natively.
- **`upload_golden_eval()`** (`ragdrift.observability.langsmith_eval`): opt-in
  helper that uploads golden queries as a LangSmith dataset and runs one scored
  experiment (score-accuracy + recall@5 per query).
- 28 new tests (MCP tools via the SDK's in-memory client; tracing with the key
  absent and present-but-fake; eval upload with a stubbed client). Suite: 68.

### Changed

- `ragdrift/cli.py`: the `init` and `scan` command cores were extracted into
  importable `run_init()` / `run_scan()` functions (quiet by default, `verbose`
  flag preserves the exact CLI output). CLI behavior is unchanged.

## 0.1.0 — initial release

- Snapshot/scan/report CLI, structural + lexical + semantic diff, BM25
  golden-query probing, LLM-as-judge `--explain`, LangGraph agent pipeline,
  SQLite drift log, self-contained demo.
