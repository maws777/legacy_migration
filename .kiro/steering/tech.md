# Tech

## Stack (confirmed)

- Python 3.11.
- FastAPI + uvicorn for the Legacy A, Legacy B, target and orchestrator APIs.
- SQLAlchemy 2.x on SQLite with CHECK constraints and `PRAGMA foreign_keys=ON`. The schema uses only types and SQL portable to PostgreSQL; it is not tested on PostgreSQL.
- pandas for CSV ingestion and batch processing.
- LangGraph, only for the workflow of a Run (one node processes a batch or a Pattern_Key, never a single row; the workflow pauses for human answers; checkpoints stored in SQLite) and for the query agent.
- Streamlit for the UI.
- pytest, plus hypothesis for property-based tests.
- LLM provider: Anthropic API (Claude). The key is read from the `ANTHROPIC_API_KEY` environment variable and is never committed or logged.

## AI usage principles

1. **Rules first.** When a deterministic rule can decide, the rule decides. Rejected alternative: one LLM call per row, because the brief gives no points for AI where a rule is better and targets ~1,200 tokens per 100k records.
2. **Patterns, not rows.** The LLM only sees distinct patterns. Results are cached by pattern key, so a pattern never costs tokens twice.
3. **Confidence gate.** Every LLM proposal carries a confidence and a justification. At or above the threshold (configurable, default 0.9) it is applied, cached and logged with `decided_by=llm`. Below the threshold the case goes to a human.
4. **Degraded mode.** Without an API key, or when the provider fails, the pipeline still completes. Anything not decided by a rule or a persisted human decision becomes ASK_HUMAN.
5. **Single LLM entry point.** All calls go through `agents/llm_client`, which counts input and output tokens per call and per run.
6. **Template explanations.** Rule explanations come from templates and cost zero tokens.
7. **Read-only query agent (bonus).** Generated SQL is validated (single SELECT, allow-listed tables) before execution.

## AI roles

1. `api_agent_legacy`: schema discovery from headers plus a sample. Detects the 2 formats and proposes the column mapping. A human validates it once.
2. `resolver_agent`: proposes a mapping for unknown variants with a confidence and a justification. Applied and cached at or above the threshold; sent to a human below it.
3. Rephrasing of human questions (optional).
4. `query_agent` (bonus, optional): natural language -> SQL, read-only, validated before execution.
5. `api_agent_legacy_b`: translates the Legacy B format (`CHASSIS_NO`, mixed-case `MODELE`, `DATE_MAJ` as yyyymmdd) into the common vocabulary (normalized VIN, canonical VehicleModelsDico value, ISO date).

## Engineering constraints

- **Idempotence**: upsert on `diagnosticOpID`. Generated IDs are deterministic (UUID v5). Re-running gives the same target state and no duplicates.
- **Traceability**: every field-level decision produces a decision-log record, and every source row has exactly one final outcome.
- **Legacy is read-only**: raw source values are never overwritten. They are kept in the decision log (`before`) and in `source_originTool`.
- **Performance**: batch processing; compute once per distinct value where possible. Measure the time and token drivers: number of distinct patterns, batch size, cache hit rate.
- **CSV ingestion**: `encoding="utf-8-sig"` (BOM), `sep=";"`, CRLF tolerant, every column read as a string (`dtype=str`, `keep_default_na=False`) so empty cells, leading zeros and `#NOM?` reach the rules untouched.
- **Configuration**: dictionaries, mappings, the confidence threshold and the batch size live in YAML under `rules/`, not in code.

## Conventions

- English for all code, comments, specs and docs.
- Type hints on public functions. Rule functions are pure: no I/O, no DB, no LLM.
- Every rule has unit tests built from the official examples. Hypothesis property tests are limited to R1, R4, Normalized_Key (R5/R6), Row_Outcome classification and load idempotence.
- Keep `docs/architecture.md` (each choice + justification + rejected alternative), `docs/lessons-learnt.md` and `docs/cost-performance.md` up to date as work progresses.
- Credit frugality: make targeted edits; never regenerate a whole document for a small change.

## Commands

- Tests: `pytest -q`.
- Long-running processes (uvicorn, streamlit) are started manually by the team, not by the agent.