# AGENTS.md — ragdrift

Guidelines for AI coding assistants working in this repository.

## Project overview

ragdrift is a silent regression detector for RAG pipelines. It catches when document ingestion breaks before users notice. The core diff engine is pure Python with no LLM calls; the `--explain` flag is the only optional LLM path.

## Architecture

```
ragdrift/cli.py          CLI entry point — start here for understanding the flow
ragdrift/core/           Pure Python diff engine (no dependencies on agent/)
ragdrift/agent/          LangGraph orchestration (wraps core/, adds sampling logic)
ragdrift/storage/        SQLite persistence
demo/                    Sample corpus and inject_drift script
tests/                   Pytest suite
```

## Key invariants

1. **`ragdrift demo --inject-drift` must work with zero API keys and zero manual setup.** If you modify demo flow, verify this command still runs end-to-end in under 60 seconds.
2. **Semantic diff is disabled in demo mode.** `use_semantic=False` in CLI demo. Do not add sentence-transformers to base dependencies.
3. **BM25 index is rebuilt from current corpus files on every scan.** The reference index is always built from stored snapshot chunks in SQLite. Do not cache the fresh index.
4. **Score-accuracy is the retrieval metric, not recall@k.** See `ragdrift/core/probing/evaluator.py`. Recall@k fails for single-topic corpora.
5. **The `--explain` LLM call uses `claude-haiku-4-5-20251001` for Anthropic provider.** Do not change the model without updating this file.

## Running tests

```bash
pytest                     # full suite
pytest -k structural       # structural diff tests only
pytest -k golden           # probing tests only
pytest tests/test_full_scan.py  # end-to-end
```

## Running the demo

```bash
python3 -m ragdrift.cli demo --inject-drift                    # moderate (default)
python3 -m ragdrift.cli demo --inject-drift --level mild       # 2 docs
python3 -m ragdrift.cli demo --inject-drift --level catastrophic  # 10 docs
```

## Making changes

- **Adding a new drift signal**: add detection logic in `core/diff/`, wire it into `cli.py`'s `cmd_scan()` severity list, and add a test in `tests/`.
- **Changing the golden queries**: update `demo/golden_queries.json` AND verify `ragdrift demo --inject-drift` still shows a retrieval drop.
- **Changing corpus_v2 drift docs**: ensure each of the 5 docs still demonstrates a distinct failure mode (see spec in `demo/inject_drift.py`).

## Optional extras

```
ragdrift[vector]   → Qdrant + sentence-transformers for semantic diff
ragdrift[api]      → FastAPI server
ragdrift[ui]       → Streamlit dashboard
ragdrift[explain]  → Anthropic SDK for --explain
ragdrift[agent]    → LangGraph pipeline
ragdrift[all]      → everything
```
