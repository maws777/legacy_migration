# legacy_migration: migration-engine

Hackathon 2026 "Information Systems that evolve. Continuity that never breaks." Strangler Fig migration of the Legacy A diagnostics CSV to a constrained target model. Rules decide first, an LLM only sees distinct unknown patterns, and humans answer grouped questions. Every decision is logged.

- Specs and steering: `.kiro/specs/migration-engine/`, `.kiro/steering/`
- Docs: `docs/architecture.md`, `docs/cost-performance.md`, `docs/lessons-learnt.md`, `docs/demo.md`
- Code: `rules/` (pure rules), `migration/` (LangGraph Run_Workflow, phases, reconciliation), `agents/` (Orchestrator, LLM client, agents), `hitl/`, `reporting/`, `target/`, `legacy_a/`, `legacy_b/`, `ui/`, `client_app/`

## Installation

Prerequisites: Python 3.11 or later, Git, Windows PowerShell (the start and stop scripts are PowerShell; on Linux or macOS run the `uvicorn`, `streamlit` and `python -m` commands from `scripts/start_all.ps1` by hand).

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

## LLM provider (optional)

The LLM is optional. The provider is set in `rules/config/pipeline.yaml` (`llm.provider`, `llm.model`, `llm.base_url`). The default is a local **Ollama** server with the model `qwen2.5:7b`: no key, no network call outside the laptop, cost 0 € (tokens are still counted).

### Ollama (default)

1. Install Ollama: `winget install Ollama.Ollama`, or the installer from https://ollama.com/download (Windows, macOS, Linux). On Windows it runs in the background after installation; otherwise start it with `ollama serve`.
2. Download the model (about 4.7 GB):

   ```powershell
   ollama pull qwen2.5:7b
   ```

3. Check that the server answers and lists the model:

   ```powershell
   Invoke-RestMethod http://localhost:11434/api/tags | Select-Object -ExpandProperty models | Select-Object name
   ```

The LLM_Client calls `POST http://localhost:11434/api/chat` with a forced JSON answer (`"format": "json"`) and counts tokens from `prompt_eval_count` and `eval_count`. The first call can take several seconds while the model loads (`llm.timeout_s` is 60 s).

### Switch back to Anthropic

In `rules/config/pipeline.yaml`:

```yaml
llm:
  provider: anthropic
  model: claude-haiku-4-5
```

Then set the key (read at each call, never written anywhere). The cost uses `price_eur_per_input_token` and `price_eur_per_output_token` of the same file.

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."          # this terminal only
setx ANTHROPIC_API_KEY "sk-ant-..."            # persistent, for new terminals
```

Never commit the key or write it in a file of the repository.

### Degraded_Mode

If the chosen provider cannot be used when a Run starts (Ollama stopped or model not pulled, or no `ANTHROPIC_API_KEY` for anthropic), the Run continues in Degraded_Mode: same rule decisions, Unknown_Variants go to humans, schema discovery proposes the reference Column_Mapping. The end-of-run report gives the reason, for example `Degraded_Mode: Ollama unreachable at http://localhost:11434 (ConnectError); Unknown_Variants were sent to humans.` To run without any LLM on purpose, use `--no-llm` on the command line or `-NoLLM` with `scripts\start_all.ps1`.

The tests never call a provider: the Anthropic key is removed and the Ollama transport is replaced by a simulated one.

## Validate the Column_Mapping (once per database folder)

A Run processes zero rows until a Column_Mapping is validated (Requirement 23.6). Discover it (proposed by the LLM, or the reference fallback when no provider is available) and validate it as an operator:

```powershell
.venv\Scripts\python.exe -m migration schema --operator <your-name>
.venv\Scripts\python.exe -m migration schema --operator <your-name> --no-llm    # reference mapping, no LLM
```

Expected: the 10 source columns with their target attributes, then `Column_Mapping validated by <your-name>`. If the LLM proposal maps a target attribute to zero or two columns, the command lists the conflicts and validates nothing; run it again with `--no-llm`. With the services running, the same step is `POST /ops/schema/discover` then `POST /ops/schema/validate` (see `docs/demo.md`).

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
