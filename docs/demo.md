# Demo scenario

After `scripts\start_all.ps1`, the whole demo happens in the UI (http://127.0.0.1:8501), without a terminal. The only commands are the start and stop scripts.

The results below come from the check of 5 November 2026 (Run timestamps, UTC), with Ollama running `qwen2.5:7b` locally and the Legacy_B_API running. The laptop has an Intel Core i7-11800H, 16 GB of RAM and a GTX 1650 Max-Q (4 GB). run_ids and timestamps change on every run, and the counts do not. The model's answers (schema proposal, agent advice, chat SQL, Legacy B questions) can change between runs; the checks that frame them do not.

How the UI behaves:

- **Operator**: the name in the left sidebar is used for every action. It is kept in the URL (`?operator=...`), so it survives a reload (F5).
- **Long actions run in the background**: the AI mapping proposal, the campaigns and the agent advice. The button answers at once, the page shows `<action> : en cours (N s), rafraîchissement automatique toutes les 3 s` and disables the buttons until the end. A reload (F5) or another tab still shows the running action, because its state comes from the Orchestrator.
- **One model call at a time**: the local model answers one request at a time, so the AI mapping, the advice, the chat and the Legacy B agent run one after the other.

## 0. Start

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -VarDir var\demo
```

Use a new folder name (or delete `var\demo`) for a demo from scratch.

Expected output:

1. `Loading ollama model qwen2.5:7b (up to 180 s)...`
2. `Model ready in 2.9 s` (2.9 s with the model already in memory, about 17 s after it was unloaded, about 80 s for the very first load). If the model cannot load, the script prints the error and the Orchestrator runs in Degraded_Mode: everything below still works, with the reference mapping and without advice or chat.
3. Six windows open: Legacy_A_API, Legacy_B_API, Target_API, Orchestrator, UI and Client_App.
4. Each API prints `ready on http://127.0.0.1:800x`.

The Client_App window prints one line per query, for example `6X2VYKWMPYVXDH4WBFDY -> 200 in 30.6 ms | queries 615 | availability ok 615 failed 0 | parity ok 615 failed 0`. The same lines go to `var\demo\client_app.log`. Keep the window visible: its two counters must never show a failure.

Open http://127.0.0.1:8501 and type your name in **Operator** (left sidebar).

## 1. Home, step 1: column mapping

The home page shows `Aucun mapping validé : la migration ne traitera aucune ligne tant qu'il n'est pas validé.`, and **Start a campaign (Run 1)** is disabled.

1. Click **Proposer le mapping avec l'IA**. The page shows `schema discovery : en cours`. The proposal arrives after **37 s**: one LLM call, 2,332 + 217 = 2,549 tokens.
2. The table lists the 10 source columns with the proposed target and the reference target. Differences are shown in red, here 5 of them:

   | Column | Proposed | Reference |
   |---|---|---|
   | `originTool` | (aucun) | DiagToolsVersions.DiagToolName |
   | `toolVersion` | DiagToolsVersions.DiagToolName | DiagToolsVersions.DiagToolVersion |
   | `diagnosticFieId` | (aucun) | DiagnosticOperation.diagnosticOpID |
   | `ecuCode` | (aucun) | DiagValueResult.ValueDescription |
   | `DescriptionDiagECU` | DiagValueResult.ValueDescription | Label |

3. A red box explains why the validation is refused: `Validation refusée tant qu'il reste 4 conflit(s)`. The conflicts are:
   - `toolVersion` appears 2 times in the LLM answer;
   - `diagnosticOpID` is mapped by 0 columns;
   - `DiagToolVersion` is mapped by 0 columns;
   - `Label` is mapped by 0 columns.

   Clicking **Valider le mapping** now shows `Validation refusée : HTTP 409: 4 Column_Mapping conflict(s): ...`, and nothing is validated.
4. Fix the proposal with one of two ways:
   - **Correct it.** Under **Correction colonne par colonne**, choose the reference target for the 5 red columns, then click **Valider le mapping**. As long as one conflict is left, the validation is refused and the message lists it.
   - **Use the reference.** Click **Utiliser le mapping de référence**: no differences and no conflicts. Then click **Valider le mapping**.
5. Expected: `Mapping validé par <you> le 05/11/2026 à 11:12:19 UTC`, and **Start a campaign (Run 1)** is enabled.

Without Ollama, the AI button directly returns the reference mapping, marked "Mapping de référence (Rule_Config)".

## 2. Home, step 2: migration 1

Click **Start a campaign (Run 1)**. The page shows `start campaign : en cours`, then after **13 s**: `start campaign done in 13.1 s: Run 0425fbe8-... completed, campaign paused, 12 open question(s).` No LLM call is made: the dataset has no Unknown_Variant.

Open the **Stats** page and select the Run. Expected report:

```text
10,000 records analysed
─────────────────────────
6,927 migrated automatically
637 automatically corrected
1,916 rejected
520 requiring human decision
100% decisions traced
Estimated AI consumption: 2,549 tokens (≈ 0.0000 €)
Processing time: 0 min 13 sec
Coverage: 7,564 of 10,000 source rows loaded (75.6%)
Rejected: 990 out of scope, 926 in error; pending human decision: 520
Run type: cold (empty Resolution_Cache)
AI usage by purpose (tokens): Resolver_Agent 0, schema discovery 2,549, Question_Advisor 0, chat (Query_Agent) 0
Cloud-equivalent cost (anthropic claude-haiku-4-5 prices): 0.0031 € (real cost 0.0000 € with ollama qwen2.5:7b)
```

The **Target_DB** table shows:

- 6,010 vehicle
- 12 diagnozed_ecu
- 7,564 vehicle_ecu
- 18 diag_tools_versions
- 7,564 diagnostic_operation
- 7,564 diag_value_result

These counts differ from the Validation_Target (6,942 / 629 / 1,916 / 513) because the Legacy B lookup is on. The 15 planted conflicts give 20 rows with an R7 `Vehmodel|LEGACY_B_CONFLICT` question. The unreadable Vehmodel rows take their model from Legacy B instead of asking a human. With the Legacy_B_API stopped, the same Run gives exactly the Validation_Target.

## 3. Questions page: agent advice, then human answers

The page lists 12 open questions, one per Pattern_Key:

| Pattern_Key | Rows | Options |
|---|---|---|
| originTool\|UNREADABLE | 124 | reject, case by case |
| Creation_date\|UNREADABLE | 151 | reject, case by case |
| ecuCode\|FORMAT2_NO_DID | 46 | reject, case by case |
| toolVersion\|UNREADABLE | 138 | reject, case by case |
| Vehmodel\|LEGACY_B_CONFLICT | 20 | reject, case by case |
| ecuCode\|UNREADABLE\|Identification calculateur \<ECU\> | 14 | reject, case by case |
| diagnosticFieId\|SHARED_OP_ID | 60 | reject, case by case, generate deterministic diagnosticOpIDs |
| ecuCode\|UNREADABLE\|Lecture référence logiciel \<ECU\> | 13 | reject, case by case |
| ecuName\|UNREADABLE | 30 | reject, case by case |
| ecuCode\|UNREADABLE\|Configuration système \<ECU\> | 14 | reject, case by case |
| ecuCode\|UNREADABLE\|Numéro de série calculateur \<ECU\> | 17 | reject, case by case |
| ecuCode\|UNREADABLE\| | 2 | apply a value, reject, case by case |

Open a question to see:

- what is missing and why no rule decided;
- the LLM status;
- the example rows;
- the options.

Only the homogeneous group (same raw value and same label) offers "Apply a value to the group".

**Agent advice.** In `Vehmodel|LEGACY_B_CONFLICT`, click **Demander l'avis de l'agent**. The page shows `agent advice : en cours`, and the result arrives after about 80 s. The agent only advises: the question stays open and the answer form is unchanged. Observed answers:

| Question | Time, calls, tokens | Tools called | Advice |
|---|---|---|---|
| Vehmodel\|LEGACY_B_CONFLICT (20 rows) | 78.7 s, 4 calls, 5,160 + 242 = 5,402 | sample_group_rows, same_vin_rows, field_distribution | **Case by case**, confidence 0.70: "no clear majority model, the conflicting values need to be resolved individually". Unknown: "the correct model for each vehicle with conflicting values". |
| diagnosticFieId\|SHARED_OP_ID (60 rows) | 77.1 s, 4 calls, 5,334 + 251 = 5,585 | sample_group_rows, same_vin_rows, field_distribution | **Generate deterministic diagnosticOpIDs**, confidence 0.95: "the same diagnosticFieId is carried by different operations". Evidence: `2dbd5c42-...` appears 2 times. |

Under the advice, a table lists each tool call with its parameters and a one-line result. For example: `field_distribution {"field": "Vehmodel", "limit": 5} -> Vehmodel: 10000 rows, 15 unreadable; top 'TRAFIC' x1040, ...`. The advice is also traced in the decision journal (`question_advice`).

**Answers.** For each question, choose an option and click **Submit**; the question leaves the list:

- `diagnosticFieId|SHARED_OP_ID`: generate deterministic diagnosticOpIDs;
- `ecuCode|UNREADABLE|` (2 rows): apply a value to the group, value `ID0001`;
- the 10 others: reject the group.

Expected: `0 open question(s)`.

## 4. Home: migration 2 without duplicates, then idempotence

1. Select the paused campaign, then click **Resume after human answers (next Run)**. After **9 s**: `resume campaign done in 9.2 s: Run ... completed, campaign done, 0 open question(s).`
   - Stats page, new Run: 6,927 migrated, 683 corrected, 2,390 rejected, 0 requiring human decision.
   - Coverage: 7,610 of 10,000 (76.1%). Rejected: 990 out of scope, 1,400 in error.
   - Target_DB: 6,043 vehicle, 7,610 vehicle_ecu, 7,610 diagnostic_operation, 7,610 diag_value_result.
   - The 46 extra operations are rows decided by the answers. Every operation still has exactly one result.
2. Click **Start a campaign (Run 1)** again, on the same file. After 9 s: `completed, campaign done, 0 open question(s)`. The Target_DB counts on the Stats page are exactly the same: nothing was duplicated.

## 5. Phases page: 2 -> 3 -> 4 with the Client_App running

1. **Target phase** 2, then **Request transition**: `switched: phase 2`.
2. **Target phase** 3, then **Request transition**: `switched: phase 3`.
3. **Reconcile the Control_Sample**: the result shows `"compared": 45`, `"gaps": 0`, `"aligned_pct": 100.0`. The count is 45 expected records, not 50 VINs: a VIN whose rows are all rejected has none.
4. **Target phase** 4, then **Request transition**: a warning shows `cutover gate passed: waiting for explicit approval by a human operator. Approving also validates the exclusions shown above.` The info line shows the coverage and the exclusions of the latest full Run: `coverage 76.1% (7,610 of 10,000 rows loaded); excluded: 990 out of scope, 1,400 in error, 0 pending human decision`.
5. Change **Operator** to `lead`, then click **Approve cutover**: `switched: phase 4`. Reads now come from the Target_API.

The phase-4 request is refused, and the phase stays 3, in two cases:

- it comes before the reconciliation: `no reconciliation available`;
- a question is still open: the message gives the number of open questions.

The Client_App window keeps answering 200 with parity through every switch.

## 6. Phases page: rollback

Change **Operator** to `ops`, then click **Roll back to phase 3**: `switched: phase 3`. Reads are back on Legacy A, and the Client_App still shows no failure.

The **Transition log** table lists four transitions:

- 1 -> 2
- 2 -> 3
- 3 -> 4, by `lead`
- 4 -> 3, with `rollback = true`, by `ops`

Over the whole check, the Client_App sent 3,202 queries with 0 availability failures and 0 parity failures. That count covers six service sessions, restarted between code fixes.

## 7. Decision log page

Select the run_id of migration 1, then type a row number in **Source_Row_Id** under "One source row":

- **4**: `_row` CORRECT R5 DICT_NORMALIZED, `Row outcome CORRECT (R5 DICT_NORMALIZED); 9 field decisions (MIGRATE 6, TRANSFORM 2, CORRECT 1). Final checks FC1-FC4 passed. Loaded into the Target_DB.` Field by field:
  - originTool TRANSFORM R2 MAPPED: `DiagTool` -> `DDT2000`;
  - ecuName CORRECT R5 DICT_NORMALIZED: `E-PS` -> `EPS`;
  - Creation_date TRANSFORM R8 DATE_PARSED.
- **13**: `_row` REJECT R4 FORBIDDEN_CHAR, `Row outcome REJECT (R4 FORBIDDEN_CHAR); 9 field decisions (MIGRATE 5, TRANSFORM 3, REJECT 1).`
- **2**: `_row` ASK_HUMAN R8 UNREADABLE, `Row outcome ASK_HUMAN (R8 UNREADABLE); 9 field decisions (MIGRATE 7, TRANSFORM 1, ASK_HUMAN 1).`

The filters above the table (field, action, rule_id, decided_by) narrow the whole log.

## 8. Chat page (bonus)

Click the three example buttons. Each answer shows the SQL, the rows, the tokens and the time. The Query_Agent checks the SQL before running it: one statement, SELECT only, allowed tables only. It then runs the query on a read-only connection.

| Question | Time, tokens | SQL | Answer |
|---|---|---|---|
| How many vehicles are there for each vehicle model? | 6.0 s, 700 + 33 | `SELECT COUNT(*) AS vehicle_count, vehicle.vehicle_model FROM vehicle GROUP BY vehicle.vehicle_model ORDER BY vehicle_count DESC LIMIT 200` | 10 rows: TRAFIC 634, CLIO 627, ARKANA 622, MEGANE E-TECH 621, KANGOO 597, RAFALE 589, SCENIC E-TECH 583, MASTER 583, CAPTUR 583, AUSTRAL 571 (6,010 vehicles) |
| Which ECU has the most diagnostic operations? | 3.9 s, 699 + 32 | `SELECT ecu_name, COUNT(*) AS op_count FROM diagnostic_operation GROUP BY ecu_name ORDER BY op_count DESC LIMIT 1` | BMS, 655 |
| List the 5 most recent diagnostic operations with their VIN and ECU. | 10.8 s, 705 + 53 | `SELECT diagnostic_operation.diagnostic_op_id, diagnostic_operation.vin, diagnostic_operation.ecu_name, diagnostic_operation.creation_date FROM diagnostic_operation JOIN vehicle ON ... ORDER BY diagnostic_operation.creation_date DESC LIMIT 5` | `Y8LBTACKBFFNP8HT3H94` ABS 2026-11-02 02:44, `H83E1U06RSZZD1CYG1SV` AIRBAG 2026-11-01 20:54, ... |

These figures were measured after migration 1. After migration 2, the counts include the 46 extra operations.

Read the SQL before trusting a number: a small model can write a valid query that answers another question. An earlier prompt joined `diagnostic_operation` and counted 7,564 operations as "vehicles" (see `docs/lessons-learnt.md`). Type `delete all vehicles` to show a refusal: the error names the failed check (`validate_sql: non-SELECT statement ...`), or the model writes a harmless SELECT; nothing is ever changed.

**Legacy B asks its own questions.** Under "Legacy B interroge la cible", click **Lancer l'agent Legacy B (3 questions)**. The agent runs as its own process, the same as `scripts\legacy_b_asks.ps1`. It writes three questions from its own records and sends them to the chat as a `legacy_b` consumer. Then it compares the returned models with its own. The page shows `Agent Legacy B : en cours` and its output; it finishes after about **86 s**:

```text
Legacy B agent: 3 questions (llm, 635 tokens)
[1] List the VIN and the vehicle model of the vehicles 004RXZ1B5KMTR09NJ9H4, 007S6UNA77X2VG3DPLRG, 00F2VJA40P1U1DF6GVY4, 00MVM3M4UR490ZF8J780, 00VTTBUTF9VTZE2NCCH9 from the target database.
    SQL: SELECT vehicle.vin, vehicle.vehicle_model FROM vehicle WHERE vehicle.vin IN (...) ORDER BY vehicle.vin LIMIT 200
    1 row(s):
      {"vin": "00F2VJA40P1U1DF6GVY4", "vehicle_model": "CLIO"}
    compared with Legacy B: 1 same model, 0 different, 0 VIN(s) unknown to Legacy B
    913 tokens, 24.1 s
[2] What are the latest diagnostic operation dates for the vehicles with VINs 004RXZ1B5KMTR09NJ9H4 and 007S6UNA77X2VG3DPLRG?
    0 row(s):
    828 tokens, 11.2 s
[3] Are there any diagnostic tools versions recorded for the vehicles with VINs 00F2VJA40P1U1DF6GVY4 and 00MVM3M4UR490ZF8J780 that are different from the ones in Legacy B?
    1 row(s): {"vehicle_model": "CLIO", "diag_tool_name": "Clip", "diag_tool_version": "1.4.2"}
    858 tokens, 18.7 s
```

Question 2 returns no row because these two VINs have no loaded operation. Question 3 is a weak question: Legacy B holds no tool versions. The answer is still a read-only query on real rows.

## 9. Stats page: the cost of the AI

Select migration 1. The report and the **Coût IA** table add up every use of the model: the schema discovery, the advice on its questions, and the chat and Legacy B questions asked before the next Run.

The table also shows two costs side by side:

- the real cost: 0 € with Ollama;
- the cloud-equivalent cost: the same tokens at the Anthropic prices of Rule_Config.

Observed at the end of the check, including the repeated chat and Legacy B runs made while fixing the prompts:

```text
Estimated AI consumption: 41,072 tokens (≈ 0.0000 €)
AI usage by purpose (tokens): Resolver_Agent 0, schema discovery 2,549, Question_Advisor 10,987, chat (Query_Agent) 24,093, Legacy B questions 3,443
Cloud-equivalent cost (anthropic claude-haiku-4-5 prices): 0.0523 € (real cost 0.0000 € with ollama qwen2.5:7b)
```

A single pass of this scenario uses about 2,549 + 10,987 + 2,222 (three chat questions) + 3,234 (Legacy B: 635 for its questions, 2,599 for their SQL) = 19,000 tokens, or about 0.024 € at cloud prices.

## 10. Stop

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
```

Expected: one `Stopped ...` line per window, then `All services stopped.`

## Variant: "apply a value to the group" on the edge cases

The 10,000-row file has a single homogeneous group (2 rows). `data/synthetic_edge_cases.csv` holds Unknown_Variants never seen in it, including two rows with the same raw tool `CLIP`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
powershell -ExecutionPolicy Bypass -File scripts\prepare_edge_demo.ps1 -Reset
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Source data\synthetic_edge_cases.csv -VarDir var\demo_edge -NoClient -NoLLM
```

The preparation prints 1 migrated, 0 corrected, 0 rejected, 7 requiring human decision, then `Campaign ready in var/demo_edge`. It runs without the LLM so the questions are created.

In the UI:

1. **Questions** page: 6 open questions.
   - `originTool|UNKNOWN_VARIANT|CLIP` (2 rows) offers "Apply a value to the group".
   - `Vehmodel|UNKNOWN_VARIANT|MÉGANEETECH` offers only reject or case by case (R6).
2. In `originTool|UNKNOWN_VARIANT|CLIP`, choose **Apply a value to the group**, value `Clip`, then **Submit**.
3. **Home**: select the paused campaign, then **Resume after human answers (next Run)**. The Run reports 1 migrated, 2 corrected, 0 rejected, 5 requiring human decision, with 5 open questions.
4. **Decision log**: set field `originTool` and action `CORRECT`. Rows 2 and 6 show `HUMAN_VALUE` by `human`, value `Clip`, with the explanation `Human decision on originTool|UNKNOWN_VARIANT|CLIP: apply_value 'Clip'.`

The Stats page shows 3 diagnostic operations in the Target_DB.

## Variant: the Resolver_Agent with Ollama on the edge cases

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Source data\synthetic_edge_cases.csv -VarDir var\demo_agent2 -NoClient
```

In the UI:

1. Home: **Utiliser le mapping de référence**, then **Valider le mapping**.
2. **Start a campaign (Run 1)**. The Run takes about 2 minutes: 18 LLM calls of about 6 s each.

Observed report:

```text
8 records analysed
─────────────────────────
1 migrated automatically
5 automatically corrected
0 rejected
2 requiring human decision

100% decisions traced
Estimated AI consumption: 8,650 tokens (≈ 0.0000 €)
Processing time: 2 min 2 sec
```

Each of the 6 Unknown_Variant keys used the 3 turns allowed: two tool calls, then the final answer.

| Raw value | Field | Proposal (value, confidence) | Result |
|---|---|---|---|
| `Mégane E-Tech` | Vehmodel | MEGANE E-TECH, 0.95 | applied automatically (`LLM_VALUE`) |
| `CLIP` (2 rows) | originTool | Clip, 0.95 | applied automatically to both rows |
| `Diag Tool` | originTool | DDT2000, 1.0 | applied automatically |
| `AIR-BAG UNIT` | ecuName | AIRBAG, 0.75 | sent to a human (below the 0.9 threshold) |
| `2024-03-15 10:30` | Creation_date | 15/03/2024 10:30, 1.0 | sent to a human: dates are never applied automatically |
| `ID 0004` | ecuCode | ID0004, 1.0 | applied automatically |

Where to look in the UI:

- **Decision log**, section "Resolver_Agent tool calls": 12 rows (2 per key), each with its iteration, tool, JSON parameters and a one-line result. For example: `find_similar_values {"field": "ecuName", "limit": 3, "value": "AIRBAGUNIT"} -> AIRBAG (0.75), ABS (0.31), IVI (0.31)`.
- **Decision log**, filter `decided_by = llm`: decisions only for originTool, ecuName, Vehmodel or ecuCode, with a confidence of at least 0.9.
- **Questions** page: 2 open questions carry the model's suggestion: `AIRBAG (0.75)` and `15/03/2024 10:30 (1.0)`.
