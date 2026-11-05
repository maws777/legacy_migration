# Cost and performance

The following are historical measurements copied from `GET /ops/runs/{run_id}/metrics` and `GET /ops/runs/{run_id}/report`, before the migration to local Ollama. Both Runs used the then-current Rule_Config with `legacy_b_lookup: false`, an empty Human_Decision_Store and an empty Resolution_Cache (cold Runs); they ran in Degraded_Mode and made no LLM calls.

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

The historical table's cost formula used the former Anthropic prices (0.00000092 and 0.0000046 EUR per input/output token, respectively). Current reports use illustrative accounting rates from `rules/config/pipeline.yaml`: €0.000001 per input token (€1 per million) and €0.000005 per output token (€5 per million). These rates only give a hypothetical euro equivalent; Ollama has no per-token provider charge. Local electricity and hardware costs are not measured. Previously saved reports are unchanged; new Runs use the configured rates.

## Reading the figures

- Ollama was not configured for those historical Runs, so both ran in Degraded_Mode and made zero provider calls.
- The 10,000-row dataset contains no Unknown_Variant (all 12 Pattern_Keys are rule cases sent to humans: R3, R5, R6, R7, R8), so it requires no Resolver_Agent calls even when Ollama is enabled. The Resolver_Agent is called only for distinct Unknown_Variant keys, as in the synthetic file (6 keys for 8 rows).
- Time drivers: the rules stage dominates; field-local results are computed once per distinct value (FieldMemo), batches are 1,000 rows, and loads use one transaction per batch.

## 100,000-row Run

Not performed yet (optional task 14.1, Requirement 34.1).
