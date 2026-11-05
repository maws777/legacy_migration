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

- These Runs were made when Anthropic was the provider and no `ANTHROPIC_API_KEY` was set, so both ran in Degraded_Mode and made zero provider calls. The default provider is now a local Ollama model (`qwen2.5:7b`): its tokens are recorded, and its cost is 0 €.
- Even with a key, the 10,000-row dataset would cost zero tokens: it contains no Unknown_Variant (all 12 Pattern_Keys are rule cases sent to humans: R3, R5, R6, R7, R8). The Resolver_Agent is only called for the distinct Unknown_Variant keys, as in the synthetic file (6 keys for 8 rows). With the fake LLM used in `tests/unit/test_agents.py`, that file costs 6 calls (120 tokens each) on a cold Run and 2 calls on the warm Run that follows.
- Time drivers: the rules stage dominates; field-local results are computed once per distinct value (FieldMemo), batches are 1,000 rows, and loads use one transaction per batch.

## How the AI usage of a Run is counted

Every LLM call is recorded in `llm_call` (run_id, purpose, input and output tokens, time). The report, the metrics (`GET /ops/runs/{run_id}/metrics`), `GET /ops/runs/{run_id}/cost` and the stats page use the same breakdown, recomputed when they are read:

- calls made with the run_id of the Run belong to it: the Resolver_Agent, and the Question_Advisor on the Run's questions (even when the advice is asked after the Run);
- the other calls (schema discovery, chat, questions of the Legacy B agent) belong to the latest full Run started at or before the call; calls made before the first full Run belong to it.

The end-of-run report keeps the `product.md` line `Estimated AI consumption: N tokens (≈ X €)`, with N the total of all purposes and X the real cost of the configured provider (0 € with Ollama), and adds two lines: the tokens by purpose and the cloud-equivalent cost, which prices the same tokens at the Anthropic prices of Rule_Config (`llm.price_eur_per_input_token`, `llm.price_eur_per_output_token`, model `llm.cloud_reference_model`). The cloud-equivalent cost is an estimate: Claude and qwen2.5 do not count tokens the same way. The Runs above were measured before this rule, with the Resolver_Agent tokens only.

## Real Runs with Ollama (qwen2.5:7b)

Machine: Intel Core i7-11800H (8 cores, 16 threads), 16 GB of RAM (15.7 GB usable), NVIDIA GeForce GTX 1650 Max-Q with 4 GB of VRAM, Windows. Ollama 0.40.14, model `qwen2.5:7b` (7.6 B parameters, Q4_K_M, 4.7 GB). The model does not fit in 4 GB of VRAM: Ollama put 12 of its 29 layers on the GPU and the rest on the CPU. Default Rule_Config with `legacy_b_lookup: false`, empty Human_Decision_Store and Resolution_Cache (cold Runs). Cost is 0 € for a local model (`price_per_token` returns 0 for ollama); tokens come from Ollama's `prompt_eval_count` and `eval_count`.

| | Synthetic edge cases | Legacy A dataset |
|---|---|---|
| Source file | `data/synthetic_edge_cases.csv` | `data/legacy/hackathon-2026-digital-continuity-dataset.csv` |
| run_id | `05d712b0-37d8-4b44-a947-7c398111d6d1` | `9041d010-8b18-4f63-ba9b-edf02eebcfc9` |
| Source rows | 8 | 10,000 |
| Processing time (s) | 115.585 | 9.311 |
| of which AI resolution (s) | 113.3 | 0.0 |
| Input / output tokens | 7,877 / 776 | 0 / 0 |
| LLM calls | 18 | 0 |
| Cost (EUR) | 0.0000 | 0.0000 |
| Tokens per 100,000 records | 108,162,500 | 0 |
| Difference from the 1,200-token reference | +108,161,300 | -1,200 |
| Distinct Pattern_Keys | 6 | 12 |
| Batch size | 1,000 | 1,000 |
| Resolution_Cache hit rate | 0% | 0% |
| Degraded_Mode | no | no |
| Row outcomes (MIGRATE / CORRECT / REJECT / ASK_HUMAN) | 1 / 5 / 0 / 2 | 6,942 / 629 / 1,916 / 513 |

The per-100,000 figure of the edge-case file is not a forecast: that file is 8 rows made of 6 distinct Unknown_Variants. Token use grows with the number of distinct Unknown_Variant keys, not with the row count. Here each key cost about 1,440 tokens (3 turns), and the 10,000-row dataset, which has no Unknown_Variant, cost 0 tokens even with the model running.

Per LLM call (the 18 calls of the edge-case Run): 2.8 s minimum, 6.3 s on average, 10.8 s maximum; a tool request (about 20-50 output tokens) took 4.5 s on average and a final answer (about 60-80 output tokens) 9.9 s. Input grows with each turn because the prompt carries the earlier tool results: about 340 tokens at turn 1, 420 at turn 2 and 550 at turn 3. All 18 answers were valid JSON in the agent protocol.

| Variant (raw value) | Calls | Tokens (in + out) | Time (s) |
|---|---|---|---|
| `Mégane E-Tech` | 3 | 1,466 + 146 = 1,612 | 23.5 |
| `CLIP` (2 rows, one key) | 3 | 1,180 + 121 = 1,301 | 15.6 |
| `Diag Tool` | 3 | 1,346 + 126 = 1,472 | 17.7 |
| `AIR-BAG UNIT` | 3 | 1,372 + 123 = 1,495 | 18.1 |
| `2024-03-15 10:30` | 3 | 1,207 + 146 = 1,353 | 19.2 |
| `ID 0004` | 3 | 1,306 + 114 = 1,420 | 19.1 |

The same scenario through the Orchestrator during the demo (`POST /ops/campaigns` on `var/demo_agent`) gave 18 calls, 7,864 + 786 = 8,650 tokens and 121.9 s, with the same outcomes.

Schema discovery on the 10,000-row file (header and the first 20 rows): one call of 2,332 input and 230 output tokens, 55 s through the command line and 31 s through the Orchestrator once the model was loaded.

Model load times: about 80 s for the first call after installation (44 s loading the model, 26 s evaluating the prompt), 16.9 s for a warm-up after the model had been unloaded, 2.9 s when it was still in memory. `llm.keep_alive: 2h` keeps the model loaded between Runs, and `llm.timeout_s` is 180 s so a slow first load does not fail. The first load attempt after installation failed with HTTP 500 (out of memory, see `docs/lessons-learnt.md`).

## 100,000-row Run

Not performed yet (optional task 14.1, Requirement 34.1).
