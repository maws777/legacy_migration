# Structure

## Repository tree

```text
data/legacy/      raw input CSV (never modified)
docs/brief/       hackathon brief
docs/             architecture.md, cost-performance.md, lessons-learnt.md
legacy_a/         simulated Legacy A: loads the raw CSV, read-only API
legacy_b/         Legacy B: own format (CHASSIS_NO, MODELE, DATE_MAJ), simulated data + control file of planted conflicts, read-only API
target/           target schema with constraints, repository, API
rules/            YAML dictionaries + R1-R8 + final checks (pure functions)
agents/           llm_client, api_agent_legacy, api_agent_legacy_b, resolver_agent, orchestrator, query_agent
migration/        Run workflow (LangGraph), idempotent load, phase controller, reconciliation
hitl/             grouped question queue, persisted human decisions
reporting/        decision log, end-of-run report, token and time tracking
ui/               Streamlit: stats, human queue, filterable log, phase selector, bonus chat
client_app/       simulated client sending continuous queries to the orchestrator
tests/            unit, property-based and integration tests
.kiro/steering/   product, tech, structure, migration-rules
.kiro/specs/      migration-engine spec
```

## Dependency rules

- `rules/` is pure. Input: one raw row plus read-only context (dictionaries, persisted human decisions, cached resolutions). Output: a row decision made of field decisions. No I/O, DB or LLM calls. Legacy B references reach the rules as read-only context prepared by the pipeline.
- `agents/llm_client` is the only module that talks to the LLM provider.
- The `target/` repository is the only writer to the target DB.
- `reporting/` is the only writer of the decision log.
- `legacy_a/` and `legacy_b/` expose read-only access only.
- `migration/` coordinates `rules/`, `agents/resolver_agent`, `hitl/`, `target/` and `reporting/`.
- `client_app/` talks only to the orchestrator API and never knows which backend serves it.
- `ui/` talks to the APIs, not to the database directly.

## Inter-module contracts

The exact contracts are frozen in the `migration-engine` design so the 7 team members can work in parallel with mocks. Change them only through the design document.

1. **API routes**: Legacy A, Legacy B, target, orchestrator.
2. **Decision log record**: `run_id, source_row, field, action (MIGRATE|TRANSFORM|CORRECT|REJECT|ASK_HUMAN), rule_id, before, after, decided_by (rule|llm|human), confidence, explanation, tokens_used, timestamp`. Stored as a table, exportable to JSON and CSV.
3. **Human question**: pattern key, row count, example rows, what is missing, why the agent could not decide alone, options (apply a value to the whole group / reject the group / case by case).
4. **LLM client interface**: one call entry point returning the response and its token counts; per-run token totals.