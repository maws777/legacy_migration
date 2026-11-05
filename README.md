# legacy_migration: migration-engine

Hackathon 2026 "Information Systems that evolve. Continuity that never breaks." Strangler Fig migration of the Legacy A diagnostics CSV to a constrained target model. Rules decide first, an LLM only sees distinct unknown patterns, and humans answer grouped questions. Every decision is logged.

- Specs and steering: `.kiro/specs/migration-engine/`, `.kiro/steering/`
- Docs: `docs/architecture.md`, `docs/cost-performance.md`, `docs/lessons-learnt.md`, `docs/demo.md`
- Code: `rules/` (pure rules), `migration/` (LangGraph Run_Workflow, phases, reconciliation), `agents/` (Orchestrator, LLM client, agents), `hitl/`, `reporting/`, `target/`, `legacy_a/`, `legacy_b/`, `ui/`, `client_app/`

## Installation

Prerequisites: Python 3.11 or later, Git, Windows PowerShell (the start and stop scripts are PowerShell; on Linux or macOS run the `uvicorn`, `streamlit` and `python -m` commands from `scripts/start_all.ps1` by hand), and [Ollama](https://ollama.com/download) with the configured model.

```powershell
git clone https://github.com/maws777/legacy_migration.git
cd legacy_migration
git switch migration-engine
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Check the installation (about 40 seconds; the `dataset` tests use `data/legacy/hackathon-2026-digital-continuity-dataset.csv`, which is in the repository):

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Local LLM with Ollama

The LLM runs locally through Ollama; no API account or key is needed. Install Ollama, then download the default model and keep the Ollama service running:

```powershell
ollama pull qwen2.5:3b
ollama list
```

The default endpoint is `http://127.0.0.1:11434`; model, endpoint, timeout, and illustrative token accounting rates are configured in `rules/config/pipeline.yaml`. Ollama does not charge per token: the report's estimated euro amount is a hypothetical comparison (currently €1 per million input tokens and €5 per million output tokens), not a provider bill. Electricity and hardware costs are not included. If Ollama is stopped or the configured model is not downloaded, the migration continues in Degraded_Mode: deterministic and saved human decisions still apply, unknown variants go to humans, and schema discovery uses the reference mapping.

## Validate the Column_Mapping (once per database folder)

A Run processes zero rows until a Column_Mapping is validated (Requirement 23.6). With Ollama running, discover it using the local model; otherwise, discovery uses the reference fallback. Validate it as an operator:

In the UI, open Home, click **Discover schema mapping**, review the proposed source-to-target columns, then click **Validate this mapping**. Conflicts must be resolved before validation. The proposal indicates whether it came from the local LLM or the reference fallback.

```powershell
.venv\Scripts\python.exe -m migration schema --operator <your-name>
```

Expected: the 10 source columns with their target attributes, then `Column_Mapping validated by <your-name>`. With the services running, the same step is `POST /ops/schema/discover` then `POST /ops/schema/validate` (see `docs/demo.md`).

## Run

Command line, one campaign (first Run) on the dataset, databases in `var/`:

```powershell
.venv\Scripts\python.exe -m migration run --operator <your-name>
```

It prints the end-of-run report. With the Legacy_B_API stopped, the Legacy B lookup fails, the Run continues with an empty Legacy B index, and the counts equal the Validation_Target: 6,942 migrated, 629 corrected, 1,916 rejected (990 out of scope), 513 requiring human decision.

All services, each in its own window:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1                 # databases in var\
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -VarDir var\demo  # separate folder
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
```

| Window | Port | Started with |
|---|---|---|
| Legacy_A_API | 8001 | `uvicorn legacy_a.api:app` (reads the source CSV) |
| Legacy_B_API | 8002 | `uvicorn legacy_b.api:app` (reads `legacy_b/data/legacy_b_records.csv`) |
| Target_API | 8003 | `uvicorn target.api:app` (read-only on `<VarDir>/target.db`) |
| Orchestrator + ops routes | 8000 | `uvicorn agents.orchestrator:app` (API docs at http://127.0.0.1:8000/docs) |
| Streamlit UI | 8501 | `streamlit run ui/app.py` (http://127.0.0.1:8501) |
| Client_App | - | `python -m client_app` (control VINs of `client_app/vins.txt`, one query per second) |

`start_all.ps1` options: `-Source <csv>`, `-VarDir <folder>`, `-Vins <file>`, `-Interval <seconds>`, `-NoClient`. Process ids are kept in `var\processes.json` for `stop_all.ps1`.

Other commands:

- `.venv\Scripts\python.exe -m legacy_b.simulator`: regenerate the Legacy B data and the control file of the 15 planted conflicts (`legacy_b/data/`).
- `powershell -ExecutionPolicy Bypass -File scripts\prepare_edge_demo.ps1 -Reset`: prepare a campaign on `data/synthetic_edge_cases.csv` to show "apply a value to the group" (see `docs/demo.md`).

Configuration lives in `rules/config/*.yaml` (dictionaries, mappings, Column_Mapping reference, confidence threshold, batch size, Legacy B switch, prices, service URLs).

## Demo

The full scenario, with each command and its expected result, is in `docs/demo.md`.
