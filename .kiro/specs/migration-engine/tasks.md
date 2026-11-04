# Implementation Plan: migration-engine

## Overview

Python 3.11 implementation of `design.md`, ordered by the priority groups of the requirements Introduction (groups 1 to 9). Each task ends wired into the Run_Workflow or into a service, so no code is left orphaned. Names, signatures, routes and schemas come from the design sections "Public interfaces" and "Frozen contracts"; the tasks do not repeat them.

Test conventions for every task:

- Run with `pytest -q`. Example-based tests live inside the task that implements the code. Hypothesis is used only for Properties 1-6 of the design, in the optional `*` sub-tasks.
- Tests marked `dataset` need `data/legacy/hackathon-2026-digital-continuity-dataset.csv` and are skipped while that file is absent. All other tests use synthetic CSVs from `tests/fixtures/make_csv.py` and a fake `LLMClient`; no test calls the real LLM provider.
- uvicorn, Streamlit and the Client_App loop are started manually by the team. Tests use FastAPI `TestClient` and mocked `httpx` transports.

## Tasks

- [x] 1. [Group 1] Set up the repository, the shared contracts, Rule_Config and the target schema
  - Create the package layout of design "Module layout", the dependency file for the confirmed stack (FastAPI, uvicorn, SQLAlchemy 2.x, pandas, LangGraph, `langgraph-checkpoint-sqlite`, anthropic, httpx, pydantic v2, PyYAML, Streamlit, pytest, hypothesis) and a `.gitignore` entry for `var/`
  - `tests/conftest.py`: register the markers `property`, `integration`, `dataset` and `slow`; skip `dataset` tests automatically while the dataset CSV is absent
  - `tests/fixtures/make_csv.py`: synthetic CSV builder with the anomalies of `migration-rules.md` (`T CM`, `E-PS`, `HVAC-`, ` OBC`, `AR KANA`, `TRAFIC-`, `#NOM?`, `2,04E+12`, empty cells, Format_2 labels with F188, F738 and no DID, shared ids)
  - `rules/types.py`: every frozen dataclass and literal of the design, including `RuleContext`, so other team members can build mocks
  - `rules/pattern_key.py`: `make_key`, `extend_key`, `parse_key` with percent-encoding of `%`, `|` and `#`
  - `rules/fields.py` (first part): the shared primitives `normalize_vin`, `normalized_key`, `dict_lookup`, `is_unreadable` of design "Normalization definitions", used later by Legacy A, Legacy B and HITL validation
  - `rules/config.py` and `rules/config/{dictionaries,mappings,column_mapping,pipeline}.yaml`: pydantic models with the design defaults (Confidence_Threshold 0.9, batch size 1000, sample size 20, seeds), distinct Normalized_Keys per dictionary, `config_hash`, `ConfigError(file, error)` for a missing file, invalid YAML or a schema error
  - `target/schema.py`: `build_metadata(cfg)` with the six tables, portable types and named CHECK constraints built with `regexp_match()`; `make_engine(url)` enabling `PRAGMA foreign_keys=ON` and WAL on every connection, also used for `var/ops.db`
  - Tests: `tests/unit/test_config.py` (defaults, each invalid case raises `ConfigError` naming the file), `tests/unit/test_pattern_key.py` (round trip with special characters), `tests/integration/test_target_schema.py` (each named constraint refuses a bad write, foreign keys enforced, second Vehicle with the same VIN and second DiagValueResult refused)
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.3, 4.4, 4.5, 5.1_

- [x] 2. [Group 1] Implement ingestion, the Legacy_A_API and the Run_Workflow skeleton
  - `legacy_a/csv_reader.py`: `read_source()` with the stdlib `csv` module (design "Key Decisions"; this deviates from the pandas ingestion of `tech.md` and awaits team confirmation): UTF-8 with BOM removal, `;` separator, CRLF and LF, no row from a final line terminator, cells as exact strings, 1-based Source_Row_Id, `malformed=True` when the cell count differs from the header, `SourceReadError` on open failure or invalid UTF-8, sha256 of the file bytes
  - `migration/ingestion.py`: `ingest(path, mapping)` renaming source columns to canonical names; `IngestionError` listing every missing required column (exact, case-sensitive)
  - `legacy_a/store.py` and `legacy_a/api.py`: the GET-only routes of the frozen contract (paged rows, rows by R1-normalized VIN, header, health), FastAPI validation errors naming the parameter, 405 on other methods, empty `rows` past the last page or for an unknown VIN
  - `migration/run_store.py`, `migration/workflow.py`, `migration/pipeline.py`, `migration/cli.py`: `RunState` and `build_run_graph` with the nodes `init_run` (UUID4 run_id, UTC ingestion timestamp, `run` row in `var/ops.db`), `load_config` (status `failed`, exit code 1 and nothing written on `ConfigError`), `check_mapping` (uses the reference Column_Mapping of Rule_Config until task 7), `ingest` (rows kept in the RunStore, batches of `batch_size`) and a minimal `report`; `SqliteSaver` on `var/checkpoints.db`; `max_concurrency=1`; `MigrationPipeline.start_campaign()`; `python -m migration run`
  - Tests: `tests/unit/test_ingestion.py` (BOM, CRLF and LF, trailing newline, `007`, `#NOM?`, `2,04E+12`, accents, short and long rows, missing column, invalid UTF-8, unchanged file bytes, reference-table round trip of criterion 1.6), Legacy A part of `tests/integration/test_apis.py` (paging, validation errors, 405, empty results), first version of `tests/integration/test_workflow.py` (distinct run_ids, invalid config stops before any row)
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.1, 2.2, 2.3, 2.4, 2.5, 4.1, 4.3, 4.5, 4.6_

- [x] 3. [Group 2] Implement the Rule_Engine and the decision log
  - [x] 3.1 Implement the pure rule package
    - `rules/fields.py` (rest): `r1_vin`, `r2_tool`, `r4_classify`, `parse_creation_date`, `format_creation_date`
    - `rules/labels.py` (`extract_did`, `label_ecu`, `label_template`), `rules/opid.py` (UUID v5 over the canonical JSON of the Operation_Identity), `rules/templates.py` (one template per (rule_id, reason) pair), `rules/memo.py` (`FieldMemo`, `build_field_memo`)
    - `rules/engine.py`: `scan_row` and `decide_row` following evaluation steps 0-11; the R3, R4, R6/R7 and diagnosticOpID tables; the resolution order (extended-key Human_Decision, group-key Human_Decision, cached resolution, ASK_HUMAN); `final_checks` FC1-FC4; `row_outcome`; the `_row` record content; TRANSFORM and CORRECT labelling of design "Row outcome"
    - The engine reads the `FieldMemo` and computes only on a miss; it performs no I/O and leaves the row and the context unchanged
    - Tests: `tests/unit/test_official_examples.py` (every official example of `migration-rules.md` for R1-R8, each of FC1-FC4 failing once), `tests/unit/test_rules_*.py` (one example per line of the R3, R6/R7 and diagnosticOpID tables, plus R2, R5, R8 and the malformed row), `tests/unit/test_engine_determinism.py`, `tests/unit/test_precedence.py`, `tests/unit/test_opid.py`, `tests/unit/test_dates.py`, `tests/unit/test_memo.py` (one normalization call per distinct value), template completeness
    - _Requirements: 1.8, 5.1, 5.2, 5.3, 5.4, 5.6, 5.7, 5.9, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.2, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 12.1, 12.2, 12.4, 12.5, 13.1, 13.2, 13.3, 13.4, 13.5, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 15.1, 15.2, 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 21.8, 21.11, 21.12, 34.2_

  - [ ]* 3.2 Write property test for R1 VIN normalization
    - **Property 1: R1 VIN normalization and classification**
    - **Validates: Requirements 5.8, 6.1, 6.3, 6.4, 6.5, 6.6**
    - `tests/property/test_r1_vin.py`, generators from design "Property-based tests"

  - [ ]* 3.3 Write property test for R4 classification
    - **Property 2: R4 ValueReference classification**
    - **Validates: Requirements 5.8, 9.4, 9.5, 9.6, 9.7, 9.8**
    - `tests/property/test_r4_value_reference.py`

  - [ ]* 3.4 Write property test for Normalized_Key and dictionary lookup
    - **Property 3: Normalized_Key idempotence and dictionary lookup**
    - **Validates: Requirements 5.8, 10.1, 10.2, 10.3, 10.7, 11.1, 11.2**
    - `tests/property/test_normalized_key.py`

  - [ ]* 3.5 Write property test for dictionary-only stored values
    - **Property 4: Stored ecuName and VehicleModel are dictionary values**
    - **Validates: Requirements 5.8, 10.8, 11.6**
    - `tests/property/test_dictionary_invariant.py`

  - [ ]* 3.6 Write property test for Row_Outcome
    - **Property 5: Row_Outcome priority and confluence**
    - **Validates: Requirements 5.8, 16.1, 16.4, 16.6**
    - `tests/property/test_row_outcome.py`

  - [x] 3.7 Build the Run context and the decision log, and wire the rule nodes into the Run_Workflow
    - `migration/context.py`: `build_vin_model_index` (R6 dictionary matches only) and `build_shared_op_ids` (over all rows of the Run)
    - `reporting/schemas.py` (`DecisionRecord`, `LogFilters`, `DecisionPage`) and `reporting/decision_log.py`: `decision_log` table with its CHECKs and indexes, `write`, `query`, `export` (JSON and CSV formats of the frozen contract, `UnknownRunError`), `import_file`; a `DecisionLogError(run_id, source_row)` stops the Run with status `failed`
    - `migration/workflow.py`: nodes `prepare` (distinct values to `FieldMemo`; Human_Decisions, Resolution_Cache and Legacy_B_Model_Index come from empty providers in `PipelineDeps` until tasks 6, 7 and 8), `scan`, `index`, `freeze_context` (persists `run_shared_op_id`), `decide`, and a `load` node that writes the Decision_Records of each batch (the Target_DB upsert is added in task 5.1); `Send` fan-out per batch
    - Tests: `tests/unit/test_decision_log.py` (one `_row` record per row, JSON and CSV round trip with an empty confidence and non-ASCII text, export of an unknown run_id, earlier Runs unchanged, write failure stops the Run); extend `tests/integration/test_workflow.py` (every row of a synthetic file gets exactly one `_row` record with a non-empty explanation)
    - _Requirements: 4.2, 12.1, 12.2, 16.5, 17.1, 17.2, 17.3, 17.4, 17.6, 17.7, 17.8, 17.9, 17.10, 17.11, 34.2_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. [Group 3] Implement the idempotent load, the end-of-run report and the Run metrics
  - [x] 5.1 Implement the Target_Repository load, the report, the metrics and the run ops routes
    - `target/repository.py`: `TargetRepository.load_batch` (upsert in dependency order with `INSERT ... ON CONFLICT DO UPDATE`, one transaction per batch, rollback then one transaction per row, constraint name or type extracted from the error), `diagnostics_by_vin`, `table_counts`; no delete
    - `load` node: upsert only rows with outcome MIGRATE or CORRECT whose final checks passed, turn each failed row retry into REJECT `LOAD` and update its `_row` record, then write the batch Decision_Records
    - `reporting/metrics.py` (`StageTimer` around each stage, `run_metrics` table) and `reporting/report.py` (`ReportGenerator.build`, `format_text` in the exact `product.md` format with the extra notes, counts from the `_row` records, cost from the Rule_Config prices, cache hit rate 0 without lookups); the `report` node writes the report and the metrics from the same in-memory values
    - `agents/orchestrator.py`: create the FastAPI app with the common `ErrorResponse` and the ops routes `POST /ops/campaigns`, `GET /ops/runs`, `GET /ops/runs/{run_id}`, `/report`, `/metrics`, `/decisions`, `/decisions/export`
    - Tests: per-row rollback and constraint names in `tests/integration/test_target_schema.py`; `tests/unit/test_report.py` (exact text, cost formula, counts summing to analysed rows); `tests/integration/test_workflow.py` (second Run on the same file gives the same table counts and no duplicate DiagnosticOperation, forced batch failure retried row by row); ops route tests with `TestClient`
    - _Requirements: 3.8, 3.9, 4.2, 7.5, 12.3, 15.3, 16.9, 17.8, 17.11, 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7, 27.4_

  - [ ]* 5.2 Write property test for load idempotence
    - **Property 6: Load idempotence**
    - **Validates: Requirements 5.8, 12.3, 18.1, 18.6**
    - `tests/property/test_load_idempotence.py` on SQLite in `tmp_path` with the production `make_engine()`

- [x] 6. [Group 4] Implement the grouped HITL queue, persisted Human_Decisions and the resume of a campaign
  - `hitl/schemas.py`; `hitl/validation.py` (`validate_value` for each line of the Pattern_Key table, naming the failed rule); `hitl/store.py` (`HumanDecisionStore`, `human_decision` table, at most one decision per Pattern_Key); `hitl/queue.py` (`HITLQueue`: one question per Pattern_Key per Run, row count, first `hitl_example_rows` examples, `missing` and `why` templates, options, `case_by_case` creating one `#row=` question per row, refusals, `supersede_before`; tables `human_question` and `human_question_row`)
  - Workflow: `prepare` loads the Human_Decisions into the `RuleContext`; `ask` node per ASK_HUMAN Pattern_Key; `report` supersedes the open questions of earlier full Runs; `await_humans` calls `interrupt()` with no side effect before it; `MigrationPipeline.resume` sends `Command(resume=...)` and the next Run gets a new run_id
  - Orchestrator ops routes: `POST /ops/campaigns/{campaign_id}/resume`, `GET /ops/questions`, `GET /ops/questions/{question_id}`, `POST /ops/questions/{question_id}/answer` with the 409 and 422 cases of the contract
  - Tests: `tests/unit/test_hitl.py` (one question per Pattern_Key, row counts summing to the distinct (Source_Row_Id, Pattern_Key) pairs, examples, options, case by case N to N, every refusal, Human_Decisions read back after a restart and after deleting `var/checkpoints.db`); `tests/integration/test_workflow.py` (pause after the report with open questions, checkpoint stored, resume gives a new run_id and fewer questions, rows changed from ASK_HUMAN are loaded, no duplicates)
  - _Requirements: 4.7, 4.8, 5.4, 5.5, 5.6, 18.7, 20.1, 20.2, 20.3, 20.4, 20.5, 21.1, 21.2, 21.3, 21.4, 21.5, 21.6, 21.7, 21.8, 21.9, 21.10, 21.11, 21.12_

- [x] 7. [Group 5] Implement the LLM_Client, the API_Agent_Legacy, the Resolver_Agent with its cache, and Degraded_Mode
  - `agents/llm_client.py`: `LLMClient` per the frozen contract (key read from `ANTHROPIC_API_KEY` at each call; `missing_key` without provider contact and without call count; `timeout` distinct from `provider_error`; `llm_call` table; `totals(run_id)`); the key value never reaches logs, messages, records or files
  - `agents/api_agent_legacy.py`: `LegacyAClient` (httpx over the Legacy_A_API) and `SchemaDiscoveryAgent` (prompt with the header and the first N rows, proposal parsing, conflicts, reference fallback, zero calls in Degraded_Mode, `column_mapping` table, operator validation); `check_mapping` switches to the validated Column_Mapping (pending or header mismatch: zero rows and report notes); ops routes `POST /ops/schema/discover`, `POST /ops/schema/validate`, `GET /ops/schema/mapping`
  - `agents/resolver_agent.py` (one call per Pattern_Key carrying only field, reason, normalized value and allowed values; unparseable or invalid answer gives confidence 0) and `agents/resolution_cache.py` (persistent, keyed by Pattern_Key)
  - `resolve` node: cache lookup, Resolver_Agent call or Degraded_Mode marker, cache only proposals at or above the Confidence_Threshold, fill `llm_suggestions`, `llm_status` and `llm_tokens`; Degraded_Mode decided once at Run start; tokens, call count, cache hit rate and Degraded_Mode flag reach the metrics and the report; the Human_Question shows the LLM suggestion
  - Update the workflow test fixtures so they seed a validated Column_Mapping
  - Tests with a fake `LLMClient` and a patched Anthropic client: `tests/unit/test_llm_client.py`; `tests/unit/test_agents.py` (one call per Pattern_Key, at most one call per distinct key per Run, unparseable answer gives confidence 0, threshold gate, cache reuse with tokens 0, Human_Decision applied before the cache, mapping conflicts, reference fallback); `tests/integration/test_degraded_mode.py` (same rule decisions with the key unset, Unknown_Variants ASK_HUMAN with a Degraded_Mode explanation, zero provider calls); a check that captured logs and exports never contain the test key value
  - _Requirements: 5.5, 7.4, 8.10, 10.6, 11.5, 14.3, 17.5, 19.4, 19.5, 19.7, 20.1, 20.4, 22.1, 22.2, 22.3, 22.4, 22.5, 22.6, 22.7, 23.1, 23.2, 23.3, 23.4, 23.5, 23.6, 23.7, 23.8, 23.9, 24.1, 24.2, 24.3, 24.4, 24.5, 24.6, 24.7, 24.8, 24.9, 24.10, 25.1, 25.2, 25.3, 25.4, 25.5_

- [x] 8. [Group 6] Implement the Legacy B simulator, the Legacy_B_API and the API_Agent_Legacy_B
  - `legacy_b/simulator.py`: `generate(source, cfg, out_dir)` and `to_mixed_case` per the design, seeded from Rule_Config, writing `legacy_b/data/legacy_b_records.csv` and the Legacy_B_Control_File `legacy_b/data/legacy_b_conflicts.csv`; a `python -m legacy_b.simulator` entry point
  - `legacy_b/store.py` and `legacy_b/api.py`: GET-only routes (one VIN, bulk of 1 to 200 VINs, health), 404 without a record in the body, 405 on other methods, responses independent of the phase
  - `agents/api_agent_legacy_b.py`: `translate` (Common_Vocabulary, one `FieldError` per untranslatable field), `ApiAgentLegacyB.model_reference`, `model_index` (chunks of 200 VINs; 404 means no entry; transport error or 5xx means failed)
  - Pipeline: plug `ApiAgentLegacyB` into `PipelineDeps`; `prepare` fetches the Legacy_B_Model_Index when Legacy_B_Lookup is enabled or unset, sends zero requests when it is disabled, and uses an empty index with `legacy_b_lookup = failed` in the metrics when a lookup fails
  - Tests: `tests/integration/test_legacy_b_simulator.py` (two generations with the same seed are byte-identical, 15 conflicts on eligible VINs, mixed-case `MODELE`, translation examples and errors); Legacy B part of `tests/integration/test_apis.py`; `tests/integration/test_legacy_b_control_file.py` (`dataset`, skipped while the CSV is absent: R7 `LEGACY_B_CONFLICT` ASK_HUMAN on every row of every control-file VIN); workflow tests for enabled, disabled and failed lookup
  - _Requirements: 11.4, 11.9, 11.10, 11.11, 12.5, 19.5, 30.1, 30.2, 30.3, 30.4, 30.5, 30.6, 30.7, 30.8, 30.9, 30.10, 30.11_

- [x] 9. [Group 6] Implement the Phase_Controller, the cutover gate, the Target_API and the Orchestrator routing
  - `migration/phase_controller.py`: `phase_state` and `transition_log` tables; start on the last persisted phase or phase 1; forward moves one phase at a time; refusals with a reason and no log entry; `pending_target = 4` after a passing gate; `approve` re-evaluates the gate; `reject` cancels; operator rollbacks 4 to 3 (refused when the Legacy_A_API is unreachable) and 3 to 2; one ops.db transaction, then an in-memory swap under a lock; a persistence failure keeps the old phase
  - Gate: check (a) counts `open` Human_Questions; check (b) reads the latest Control_Sample result through an injected provider and fails with "no reconciliation available" until task 10 plugs in the Reconciler
  - `target/api.py`: read-only GET routes (diagnostics by VIN, stats, health)
  - `agents/orchestrator.py`: client routes `GET /api/vehicles/{vin}/diagnostics` and `GET /api/vehicles/{vin}/model`; the phase is read once per request; phases 1-3 build `DiagnosticsResponse` from Legacy A rows through `LegacyAClient` and the validated Column_Mapping; phase 4 reads the Target_API and sends zero Legacy_A_API requests; model reads go through `ApiAgentLegacyB`; 503 `BACKEND_UNAVAILABLE` without fallback; phase ops routes `/ops/phase`, `/ops/phase/transition`, `/approve`, `/reject`, `/rollback`, `/transitions`
  - Tests: `tests/unit/test_phase_controller.py` (every allowed and refused transition, pending approval, approve with a failing gate, persistence failure, Legacy A down on 4 to 3); `tests/integration/test_orchestrator.py` (routing per phase, same schema and same 404 in every phase, routing after a rollback, an in-flight request finishes on its backend, 503 when the backend is down, only 200 or 404 during forward moves and rollbacks); Target part of `tests/integration/test_apis.py`
  - _Requirements: 26.1, 26.2, 26.3, 26.4, 26.5, 26.6, 26.7, 26.8, 27.1, 27.2, 27.3, 27.4, 27.5, 27.6, 27.7, 27.8, 29.1, 29.2, 29.3, 29.4, 29.5, 29.6, 31.3_

- [x] 10. [Group 6] Implement the Reconciler and the Client_App
  - `migration/reconciliation.py`: `Reconciler.compare_vin`, `compare_control_sample`, `control_sample` (seeded, default size 50); expected records from `decide_row` with the per-VIN context and the `run_shared_op_id` of the latest full Run; match on Operation_Identity; Gap rules of the design; `reconciliation` table; VINs that cannot be read are `not_performed` and excluded; Aligned_Percentage rounded to one decimal, `None` when nothing was compared
  - `MigrationPipeline.reload_rows` (Run of kind `reload`: full context, load and log only the listed rows, no Human_Question); one reload and one re-compare per Gap, then one `_row|RECON_GAP|<source_row_id>` question unless one exists; extend `hitl/validation.py` with the `ACCEPT_TARGET` value
  - Orchestrator: phase 3 per-read comparison as a FastAPI background task; ops routes `POST /ops/reconciliation/control-sample` and `GET /ops/reconciliation/latest`; plug the Reconciler into gate check (b)
  - `client_app/main.py`, `client_app/__main__.py`, `client_app/vins.txt`: query loop against the Orchestrator only, interval 0.1 to 60 s (default 1 s), timeout default 5 s, HTTP status and latency in ms per query, failure reasons, displayed success and failure counts, loop continues after failures
  - Tests: `tests/integration/test_reconciliation.py` (Gap, one reload, question created once, aligned percentage, "not applicable", not performed); `tests/integration/test_client_app.py` with a mock Orchestrator (counts, latency, failure reasons, interval kept)
  - _Requirements: 28.1, 28.2, 28.3, 28.4, 28.5, 28.6, 28.7, 29.1, 29.3, 31.1, 31.2, 31.4, 31.5, 31.6_

- [x] 11. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. [Group 7] Implement the Streamlit UI
  - `ui/api_client.py`: httpx calls to the Orchestrator ops routes only, typed with the frozen response schemas; each error carries the name of the failed operation
  - `ui/app.py` and the pages `ui/pages/1_stats.py` (Run selector, four Row_Outcome counts, traced percentage, tokens, cost, seconds, Degraded_Mode, phase, Aligned_Percentage), `ui/pages/2_questions.py` (every open question with all its contents, one option per submission, removed only after the API confirms the Human_Decision), `ui/pages/3_decision_log.py` (filters run_id, Source_Row_Id, field, Action, rule_id, decided_by; no-match message; all records of a selected row with explanations), `ui/pages/4_phases.py` (phase, gate status, transition request, refusal reason)
  - Error banner naming the failed operation; last loaded data stays displayed; a failed answer stays in the open list
  - Tests: `tests/unit/test_ui_api_client.py` with a mocked Orchestrator (each call, error mapping)
  - _Requirements: 32.1, 32.2, 32.3, 32.4, 32.5, 32.6, 32.7, 32.8, 32.9_

- [x] 13. [Group 8] Add the validation-target test and write the Project_Docs
  - `tests/integration/test_validation_target.py` (`dataset`, skipped while the CSV is absent): Run on the 10,000-row file with an empty Human_Decision_Store and Legacy_B_Lookup disabled; counts sum to 10,000; OUT_OF_SCOPE count reported; each count compared with the Validation_Target within `VALIDATION_TOLERANCE` (default 0: exact match); deviations written to `var/validation_deviation.json`
  - `docs/architecture.md`: each choice with its justification and at least one rejected alternative, seeded from design "Key Decisions" and covering the topics listed in Requirement 35.1
  - `docs/cost-performance.md`: at least two Runs with different row counts (for example a synthetic fixture and the dataset), values copied from `GET /ops/runs/{run_id}/metrics`: run_id, rows, seconds, tokens, cost, tokens per 100,000 records and the difference from 1,200, distinct Pattern_Keys, batch size, cache hit rate as a percentage
  - `docs/lessons-learnt.md`: each anomaly found with its field and the decision taken; each Validation_Target deviation with the measured value, the signed difference and the cause, or an explicit statement that no deviation occurred
  - _Requirements: 34.3, 34.4, 35.1, 35.2, 35.3_

- [ ] 14. [Group 9] Optional items, do last
  - [ ]* 14.1 Measure the 100,000-row Run
    - `tests/integration/test_100k.py` (`slow` and `dataset`, skipped while the CSV is absent): build a 100,000-row file by tiling the dataset with deterministic VIN and id rewrites, run once to fill the Resolution_Cache, then assert that the second Run takes at most 300 s from ingestion start to the report, excluding human wait time
    - Record the elapsed time, row count, CPU model and RAM size in `docs/cost-performance.md`
    - _Requirements: 34.1, 34.5_

  - [ ]* 14.2 Implement the Query_Agent and the bonus chat
    - `agents/query_agent.py`: LangGraph graph; question length 1 to 1,000 characters after trimming, checked before any LLM call; SQL generated through `LLMClient`; `sqlglot` validation (one statement, SELECT only, allow-listed tables including joins and subqueries); execution on `file:var/target.db?mode=ro` with a `sqlite3` authorizer; row limit and truncated flag; token counts; error naming the failed step; "chat unavailable" in Degraded_Mode
    - `POST /ops/chat` in `agents/orchestrator.py`, active when `query_agent.enabled` is true; `ui/pages/5_chat.py` and the chat call in `ui/api_client.py`
    - Tests: `tests/unit/test_query_agent.py` (SELECT accepted; DML, DDL, multi-statement and foreign-table SQL refused; length errors without an LLM call)
    - _Requirements: 32.10, 33.1, 33.2, 33.3, 33.4, 33.5, 33.6, 33.7, 33.8, 33.9_

## Notes

- The plan has 25 items: 14 top-level tasks (2 of them checkpoints) and 11 sub-tasks. Groups with one unit of work are a single top-level task; only groups 2, 3 and 9 have sub-tasks, because property tests and optional items must be sub-tasks.
- Tasks marked `*` are optional: the six property tests (3.2-3.6, 5.2) and the group 9 items (14.1 for Requirement 34.1, 14.2 for Requirement 33). Requirement 5.8 asks for a hypothesis test for each listed criterion, so skipping the property sub-tasks leaves Requirement 5.8 unmet.
- Each property test is a single hypothesis test with `@settings(max_examples=100)` or more (`deadline=None` allowed for Property 6) and starts with the comment `# Feature: migration-engine, Property {n}: {property title}`.
- Tests marked `dataset` (tasks 8, 13 and 14.1) are skipped while `data/legacy/hackathon-2026-digital-continuity-dataset.csv` is absent; every other test runs on synthetic fixtures.
- Requirement 34.2 (one normalization per distinct value) is built and tested with the rules in 3.1 and 3.7, where the memo lives. Requirement 34.5 sits with the optional 100,000-row Run in 14.1, since it applies only once that Run is done.
- Requirements 11 and 12 use an empty Legacy_B_Model_Index provider until task 8, as allowed by the requirements Introduction.
- Ingestion with the stdlib `csv` module (task 2) follows the design and deviates from `tech.md`; the team still has to confirm it.
- Most tasks extend `migration/workflow.py`, `migration/pipeline.py` or `agents/orchestrator.py`, so groups 4 to 6 run mostly one task per wave in the graph below.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2", "3.1"] },
    { "id": 2, "tasks": ["3.2", "3.3", "3.4", "3.5", "3.6", "3.7"] },
    { "id": 3, "tasks": ["5.1"] },
    { "id": 4, "tasks": ["5.2", "6"] },
    { "id": 5, "tasks": ["7"] },
    { "id": 6, "tasks": ["8"] },
    { "id": 7, "tasks": ["9"] },
    { "id": 8, "tasks": ["10"] },
    { "id": 9, "tasks": ["12", "13"] },
    { "id": 10, "tasks": ["14.1", "14.2"] }
  ]
}
```
