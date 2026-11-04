# Cost and performance

Values are copied from `GET /ops/runs/{run_id}/metrics` and `GET /ops/runs/{run_id}/report` of each Run, called on the Orchestrator app after `POST /ops/campaigns`. Both Runs used the default Rule_Config with `legacy_b_lookup: false`, an empty Human_Decision_Store and an empty Resolution_Cache (cold Runs).

## Runs

| | Synthetic edge cases | Legacy A dataset |
|---|---|---|
| Source file | `data/synthetic_edge_cases.csv` | `data/legacy/hackathon-2026-digital-continuity-dataset.csv` |
| run_id | `27830437-23a4-4199-9e92-9a07f080e050` | `56ef734b-4425-421a-896f-896ae31a975d` |
| Source rows | 8 | 10,000 |
| Processing time (s) | 0.117 | 5.464 |
| Input / output tokens | 0 / 0 | 0 / 0 |
| LLM calls | 0 | 0 |
| Cost (EUR, Rule_Config prices) | 0.0000 | 0.0000 |
| Tokens per 100,000 records | 0 | 0 |
| Difference from the 1,200-token reference | -1,200 | -1,200 |
| Distinct Pattern_Keys | 6 | 12 |
| Batch size | 1,000 | 1,000 |
| Resolution_Cache hit rate | 0% | 0% |
| Degraded_Mode | yes | yes |

Stage times of the dataset Run (seconds): ingestion 0.078, rules 1.817, AI resolution 0.000, human routing 0.104, load 0.377, reporting 0.096. The other 3.0 s are not timed per stage: decision-log writes, LangGraph checkpoints and graph overhead.

Cost formula: `input tokens x price_eur_per_input_token + output tokens x price_eur_per_output_token`, with the prices of `rules/config/pipeline.yaml` (0.00000092 and 0.0000046 EUR, approximate Claude Haiku 4.5 list prices).

## Reading the figures

- No `ANTHROPIC_API_KEY` was set on the machine that produced these Runs, so both ran in Degraded_Mode and made zero provider calls.
- Even with a key, the 10,000-row dataset would cost zero tokens: it contains no Unknown_Variant (all 12 Pattern_Keys are rule cases sent to humans: R3, R5, R6, R7, R8). The Resolver_Agent is only called for the distinct Unknown_Variant keys, as in the synthetic file (6 keys for 8 rows). With the fake LLM used in `tests/unit/test_agents.py`, that file costs 6 calls (120 tokens each) on a cold Run and 2 calls on the warm Run that follows.
- Time drivers: the rules stage dominates; field-local results are computed once per distinct value (FieldMemo), batches are 1,000 rows, and loads use one transaction per batch.

## 100,000-row Run

Not performed yet (optional task 14.1, Requirement 34.1).
