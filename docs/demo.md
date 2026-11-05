# Demo scenario

Each step gives the exact PowerShell command and the expected result. The results below were observed on 4 November 2026 with the services started by `scripts/start_all.ps1`, with Ollama running `qwen2.5:7b` locally and with the Legacy_B_API running, on a laptop with an Intel Core i7-11800H (8 cores), 16 GB of RAM and an NVIDIA GeForce GTX 1650 Max-Q (4 GB). The 10,000-row file has no Unknown_Variant, so the model is only used in step 1 (schema discovery); migrations make zero LLM calls. Without Ollama the counts are the same, step 1 proposes the reference mapping directly, and the reports end with a Degraded_Mode line. run_ids and timestamps differ on every run; counts do not, except the LLM answers of step 1 and of the agent variant, which can vary between runs.

Every API step also exists in the UI (http://127.0.0.1:8501): a guided five-step journey in French on the Accueil page (understand the file with schema discovery, run, answer questions, verify, switch), plus Résultats, Questions, Journal (decision log) and Bascule (phases).

## 0. Start from a clean database folder

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
Remove-Item -Recurse -Force var\demo -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -VarDir var\demo
$api = "http://127.0.0.1:8000"
function Post($path, $body) {
    Invoke-RestMethod -Method Post "$api$path" -ContentType "application/json" -Body ($body | ConvertTo-Json)
}
```

Expected: first `Loading ollama model qwen2.5:7b (up to 180 s)...` then `Model ready in 2.9 s: ollama qwen2.5:7b at http://localhost:11434` (2.9 s with the model already in memory; 16.9 s after it was unloaded, about 80 s for the very first load on this laptop). If the model cannot load, the script prints the error and the Orchestrator runs in Degraded_Mode. Then six windows (Legacy_A_API, Legacy_B_API, Target_API, Orchestrator, UI, Client_App) and `ready on http://127.0.0.1:800x` for each API; the whole start took 23 s. The Client_App window prints one line per query, with the backend that answered in brackets (`[LEGACY_A]` until phase 3, `[TARGET]` from phase 4; it comes from the `X-Served-By` header of the Orchestrator), for example `K4X1XUHD7DYXA6SMMVNB -> 200 [LEGACY_A] in 32.5 ms | queries 487 | availability ok 487 failed 0 | parity ok 487 failed 0`; the same lines go to `var\demo\client_app.log`. It keeps running during the whole demo; its availability and parity counters must never show a failure.

## 1. Schema mapping

The model proposes a Column_Mapping from the header and the first 20 rows. The proposal shows its conflicts and its differences from the reference mapping of Rule_Config, column by column.

```powershell
$p = Invoke-RestMethod -Method Post "$api/ops/schema/discover"
"fallback=$($p.fallback) conflicts=$($p.conflicts.Count) differences=$($p.differences_from_reference.Count)"
$p.conflicts | ForEach-Object { "conflict: $($_.message)" }
$p.differences_from_reference | Format-Table column, proposed, reference -AutoSize
try { Post "/ops/schema/validate" @{ proposal_id = $p.proposal_id; operator = "demo" } }
catch { "validation refused: HTTP $([int]$_.Exception.Response.StatusCode)" }
```

Observed (31 s, about 2,560 tokens; another run gave a slightly different wrong mapping):

```text
fallback=False conflicts=4 differences=5
conflict: column toolVersion appears 2 times in the LLM answer (DiagToolsVersions.DiagToolVersion / DiagToolsVersions.DiagToolName)
conflict: DiagnosticOperation.diagnosticOpID is mapped by 0 column(s)
conflict: DiagToolsVersions.DiagToolVersion is mapped by 0 column(s)
conflict: Label is mapped by 0 column(s)

column             proposed                         reference
originTool                                          DiagToolsVersions.DiagToolName
toolVersion        DiagToolsVersions.DiagToolName   DiagToolsVersions.DiagToolVersion
diagnosticFieId                                     DiagnosticOperation.diagnosticOpID
ecuCode                                             DiagValueResult.ValueDescription
DescriptionDiagECU DiagValueResult.ValueDescription Label

validation refused: HTTP 409
```

`qwen2.5:7b` gives a wrong mapping, and its JSON repeats the `toolVersion` key. The duplicate key and the targets mapped by zero columns are conflicts, so the validation is refused (409) and the wrong mapping can never be used. The operator then asks for the reference mapping and validates it:

```powershell
$ref = Invoke-RestMethod -Method Post "$api/ops/schema/discover?source=reference"
"fallback=$($ref.fallback) conflicts=$($ref.conflicts.Count) differences=$($ref.differences_from_reference.Count)"
$m = Post "/ops/schema/validate" @{ proposal_id = $ref.proposal_id; operator = "demo" }
"validated_by=$($m.validated_by) columns=$(@($m.mapping.PSObject.Properties).Count) differences=$($m.differences_from_reference.Count)"
```

Expected: `fallback=True conflicts=0 differences=0`, then `validated_by=demo columns=10 differences=0`. The validation answer repeats the differences the operator accepts (none here). Without Ollama, the first call already returns the reference mapping (`fallback=True conflicts=0`). From the command line, the same step is `python -m migration schema --operator demo`, which prints the proposal, the differences and the conflicts, then exits with code 1; `--no-llm` validates the reference mapping.

## 2. Migration 1

```powershell
$r1 = Post "/ops/campaigns" @{ operator = "demo" }
"status=$($r1.status) campaign=$($r1.campaign_status) open=$($r1.open_questions)"
(Invoke-RestMethod "$api/ops/runs/$($r1.run_id)/report").text
(Invoke-RestMethod "$api/ops/runs/$($r1.run_id)/metrics") | Select-Object llm_calls, input_tokens, output_tokens, degraded_mode, legacy_b_lookup
(Invoke-RestMethod "http://127.0.0.1:8003/target/stats").tables
```

Expected (7.5 seconds; `llm_calls 0`, `input_tokens 0`, `output_tokens 0`, `degraded_mode False`, `legacy_b_lookup enabled`: the dataset has no Unknown_Variant, so the Resolver_Agent is never called):

```text
status=completed campaign=paused open=12
10,000 records analysed
─────────────────────────
6,927 migrated automatically
637 automatically corrected
1,916 rejected
520 requiring human decision

100% decisions traced
Estimated AI consumption: 0 tokens (≈ 0.0000 €)
Processing time: 0 min 6 sec

Coverage: 7,564 of 10,000 source rows loaded (75.6%)
Rejected: 990 out of scope, 926 in error; pending human decision: 520
Run type: cold (empty Resolution_Cache)
```

Without Ollama, the report ends with `Degraded_Mode: Ollama unreachable at http://localhost:11434 (ConnectError); Unknown_Variants were sent to humans.` and the counts are the same.

Target_DB: 6,010 vehicles, 12 ECUs, 7,564 vehicle-ECU links, 18 tool versions, 7,564 diagnostic operations, 7,564 value results.

These counts differ from the Validation_Target (6,942 / 629 / 1,916 / 513) because the Legacy B lookup is on: the 15 planted conflicts give 20 rows with an R7 `Vehmodel|LEGACY_B_CONFLICT` question, and the unreadable Vehmodel rows take their model from Legacy B instead of asking a human. With the Legacy_B_API stopped, the same Run gives exactly the Validation_Target.

## 3. Human answers

```powershell
$q = Invoke-RestMethod "$api/ops/questions?status=open"
$q | Format-Table pattern_key, row_count, @{ n = "options"; e = { $_.options -join "," } } -AutoSize
```

Expected: 12 questions.

```text
pattern_key                                          row_count options
originTool|UNREADABLE                                      124 reject_group,case_by_case
Creation_date|UNREADABLE                                   151 reject_group,case_by_case
ecuCode|FORMAT2_NO_DID                                      46 reject_group,case_by_case
toolVersion|UNREADABLE                                     138 reject_group,case_by_case
Vehmodel|LEGACY_B_CONFLICT                                  20 reject_group,case_by_case
ecuCode|UNREADABLE|Identification calculateur <ECU>         14 reject_group,case_by_case
diagnosticFieId|SHARED_OP_ID                                60 reject_group,case_by_case,generate_op_id
ecuCode|UNREADABLE|Lecture référence logiciel <ECU>         13 reject_group,case_by_case
ecuName|UNREADABLE                                          30 reject_group,case_by_case
ecuCode|UNREADABLE|Configuration système <ECU>              14 reject_group,case_by_case
ecuCode|UNREADABLE|Numéro de série calculateur <ECU>        17 reject_group,case_by_case
ecuCode|UNREADABLE|                                          2 apply_value,reject_group,case_by_case
```

Only the homogeneous group (same raw value and same label) offers `apply_value`. Answer all of them: generate deterministic ids for the shared diagnosticOpIDs, apply ID0001 to the homogeneous group, reject the others.

```powershell
function Answer($question, $option, $value = $null) {
    Post "/ops/questions/$($question.question_id)/answer" @{ option = $option; value = $value; operator = "demo" }
}
foreach ($x in $q) {
    if ($x.pattern_key -eq "diagnosticFieId|SHARED_OP_ID") { $a = Answer $x "generate_op_id" }
    elseif ($x.options -contains "apply_value") { $a = Answer $x "apply_value" "ID0001" }
    else { $a = Answer $x "reject_group" }
    "{0} -> {1} {2}" -f $x.pattern_key, $a.question.status, $a.decision.kind
}
```

Expected: 12 lines ending in `closed`: 10 with `reject`, `diagnosticFieId|SHARED_OP_ID -> closed generate` and `ecuCode|UNREADABLE| -> closed value`. In the UI (page Questions), each question shows the row count, examples, what is missing, why no rule decided, the LLM status and the options; a submitted question leaves the list once the API confirms it.

## 4. Migration 2 without duplicates

```powershell
$r2 = Post "/ops/campaigns/$($r1.campaign_id)/resume" @{ operator = "demo" }
"new_run=$($r2.run_id -ne $r1.run_id) status=$($r2.status) campaign=$($r2.campaign_status) open=$($r2.open_questions)"
(Invoke-RestMethod "$api/ops/runs/$($r2.run_id)/report").text
(Invoke-RestMethod "http://127.0.0.1:8003/target/stats").tables
```

Expected: `new_run=True status=completed campaign=done open=0`, then 6,927 migrated, 683 corrected, 2,390 rejected, 0 requiring human decision; coverage 7,610 of 10,000 (76.1%); rejected 990 out of scope and 1,400 in error.

Target_DB: 6,043 vehicles, 7,610 vehicle-ECU links, 7,610 diagnostic operations and 7,610 value results. The 46 extra operations are rows decided by the answers; every operation still has exactly one result, so nothing was duplicated.

Idempotence check, a new campaign on the same file:

```powershell
$r3 = Post "/ops/campaigns" @{ operator = "demo" }
"status=$($r3.status) campaign=$($r3.campaign_status) open=$($r3.open_questions)"
(Invoke-RestMethod "http://127.0.0.1:8003/target/stats").tables
```

Expected: `status=completed campaign=done open=0` and exactly the same table counts as after migration 2.

## 5. Phases 2 -> 3 -> 4 with the Client_App running

```powershell
$t = Post "/ops/phase/transition" @{ target_phase = 2; operator = "demo" }; "to 2: $($t.status) phase=$($t.current_phase)"
$t = Post "/ops/phase/transition" @{ target_phase = 3; operator = "demo" }; "to 3: $($t.status) phase=$($t.current_phase)"
$rc = Invoke-RestMethod -Method Post "$api/ops/reconciliation/control-sample"
"compared=$($rc.compared) gaps=$($rc.gaps) aligned=$($rc.aligned_pct) not_performed=$($rc.not_performed.Count)"
$t = Post "/ops/phase/transition" @{ target_phase = 4; operator = "demo" }
"to 4: $($t.status) phase=$($t.current_phase) reason=$($t.reason)"
$t.summary
$t = Post "/ops/phase/approve" @{ operator = "lead" }; "approve: $($t.status) phase=$($t.current_phase)"
```

Expected:

```text
to 2: switched phase=2
to 3: switched phase=3
compared=45 gaps=0 aligned=100.0 not_performed=0
to 4: pending_approval phase=3 reason=cutover gate passed: waiting for explicit approval by a human operator
run_id                : <run_id of migration 3>
analysed              : 10000
loaded                : 7610
coverage_pct          : 76.1
rejected_out_of_scope : 990
rejected_error        : 1400
pending_human         : 0
approve: switched phase=4
```

(With a French Windows locale, PowerShell prints `76,1`.) `compared` counts expected records, not VINs: the 50 Control_Sample VINs give 45 records, because a VIN whose rows are all rejected has none. The summary comes from the latest full Run (the idempotence check of step 4).

The approval validates the coverage and exclusion summary shown at the pending step. Requesting phase 4 before the Control_Sample reconciliation, or while a question is open, is refused with `no reconciliation available` or the number of open questions, and the phase stays 3.

The Client_App window keeps answering 200 with parity during the three switches; from phase 4 the answers come from the Target_API. A short independent check:

```powershell
.venv\Scripts\python.exe -m client_app --count 10 --interval 0.1 | Select-Object -Last 1
```

Expected: `queries 10 | availability ok 10 failed 0 | parity ok 10 failed 0`.

## 6. Rollback

```powershell
$t = Post "/ops/phase/rollback" @{ target_phase = 3; operator = "ops" }; "rollback: $($t.status) phase=$($t.current_phase)"
.venv\Scripts\python.exe -m client_app --count 10 --interval 0.1 | Select-Object -Last 1
Invoke-RestMethod "$api/ops/phase/transitions" | Format-Table from_phase, to_phase, rollback, operator -AutoSize
```

Expected: `rollback: switched phase=3`, the Client_App line again `availability ok 10 failed 0 | parity ok 10 failed 0` (reads are back on Legacy A), and four transitions: 1 -> 2, 2 -> 3, 3 -> 4 (operator `lead`), 4 -> 3 with `rollback = True` (operator `ops`).

## 7. Decision log

Three rows of migration 1, one per outcome type:

```powershell
foreach ($row in 4, 13, 2) {
    $d = Invoke-RestMethod "$api/ops/runs/$($r1.run_id)/decisions?source_row=$row"
    $d.items | Where-Object field -eq "_row" | ForEach-Object { "row $row : $($_.action) $($_.rule_id) $($_.reason) | $($_.explanation)" }
}
(Invoke-RestMethod "$api/ops/runs/$($r1.run_id)/decisions?source_row=4").items | Format-Table field, action, rule_id, reason, after -AutoSize
Invoke-WebRequest -UseBasicParsing "$api/ops/runs/$($r1.run_id)/decisions/export?format=csv" -OutFile var\demo\decision_log_run1.csv
```

Expected:

```text
row 4 : CORRECT R5 DICT_NORMALIZED | Row outcome CORRECT (R5 DICT_NORMALIZED); 9 field decisions (MIGRATE 6, TRANSFORM 2, CORRECT 1). Final checks FC1-FC4 passed. Loaded into the Target_DB.
row 13 : REJECT R4 FORBIDDEN_CHAR | Row outcome REJECT (R4 FORBIDDEN_CHAR); 9 field decisions (MIGRATE 5, TRANSFORM 3, REJECT 1).
row 2 : ASK_HUMAN R8 UNREADABLE | Row outcome ASK_HUMAN (R8 UNREADABLE); 9 field decisions (MIGRATE 7, TRANSFORM 1, ASK_HUMAN 1).
```

Row 4 field by field: VIN MIGRATE R1 OK, originTool TRANSFORM R2 MAPPED (`DiagTool` -> `DDT2000`), toolVersion MIGRATE R8 COPIED, Creation_date TRANSFORM R8 DATE_PARSED (`2024-10-08T18:06`), ecuCode MIGRATE R3 FORMAT1 (ID0005), ValueReference MIGRATE R4 OK, ecuName CORRECT R5 DICT_NORMALIZED (`E-PS` -> `EPS`), Vehmodel MIGRATE R6 OK, diagnosticFieId MIGRATE R8 COPIED, then the `_row` record. The CSV export holds every Decision_Record of migration 1. In the UI (page Journal), filter by Source_Row_Id 4, 13 or 2 to show the same explanations.

## 8. Stop

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
```

Expected: one `Stopped ...` line per window, then `All services stopped.` Over the whole demo the Client_App sent 487 queries with 0 availability failure and 0 parity failure (latency 6.9 ms min, 46 ms on average, 1.7 s max during a migration); its last line is in `var\demo\client_app.log`.

## Variant: "apply a value to the group" on the edge cases

The 10,000-row file has a single homogeneous group (2 rows). `data/synthetic_edge_cases.csv` holds Unknown_Variants never seen in it, including two rows with the same raw tool `CLIP`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
powershell -ExecutionPolicy Bypass -File scripts\prepare_edge_demo.ps1 -Reset
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Source data\synthetic_edge_cases.csv -VarDir var\demo_edge -NoClient -NoLLM
$api = "http://127.0.0.1:8000"
$q = Invoke-RestMethod "$api/ops/questions?status=open"
$q | Format-Table pattern_key, row_count, @{ n = "options"; e = { $_.options -join "," } } -AutoSize
```

Expected: the preparation prints 1 migrated, 0 corrected, 0 rejected, 7 requiring human decision, then `Campaign ready in var/demo_edge` (it runs with `--no-llm` so the questions are created). Six open questions; `originTool|UNKNOWN_VARIANT|CLIP` (2 rows) offers `apply_value,reject_group,case_by_case`, while `Vehmodel|UNKNOWN_VARIANT|MÉGANEETECH` offers only `reject_group,case_by_case` (R6).

```powershell
$clip = $q | Where-Object pattern_key -eq "originTool|UNKNOWN_VARIANT|CLIP"
Invoke-RestMethod -Method Post "$api/ops/questions/$($clip.question_id)/answer" -ContentType "application/json" `
    -Body (@{ option = "apply_value"; value = "Clip"; operator = "demo" } | ConvertTo-Json)
$camp = (Invoke-RestMethod "$api/ops/runs?limit=1")[0].campaign_id
$r = Invoke-RestMethod -Method Post "$api/ops/campaigns/$camp/resume" -ContentType "application/json" -Body '{"operator":"demo"}'
(Invoke-RestMethod "$api/ops/runs/$($r.run_id)/report").text
(Invoke-RestMethod "$api/ops/runs/$($r.run_id)/decisions?field=originTool&action=CORRECT").items | Format-Table source_row, reason, decided_by, after, explanation -AutoSize
```

Expected: the answer returns a `value` decision `Clip`; the next Run reports 1 migrated, 2 corrected, 0 rejected, 5 requiring human decision with 5 open questions; rows 2 and 6 show `HUMAN_VALUE human Clip` with the explanation `Human decision on originTool|UNKNOWN_VARIANT|CLIP: apply_value 'Clip'.`; the Target_DB holds 3 diagnostic operations.

## Variant: the Resolver_Agent with Ollama on the edge cases

Needs Ollama running with `qwen2.5:7b` (see README, "LLM provider"). The answers come from a real model, so the tools chosen and the wording of the justifications can change between runs; the rules that frame them (allowed values, validator, threshold, auto-apply only for closed dictionaries) do not.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
Remove-Item -Recurse -Force var\demo_agent -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Source data\synthetic_edge_cases.csv -VarDir var\demo_agent -NoClient
$api = "http://127.0.0.1:8000"
$ref = Invoke-RestMethod -Method Post "$api/ops/schema/discover?source=reference"
Invoke-RestMethod -Method Post "$api/ops/schema/validate" -ContentType "application/json" `
    -Body (@{ proposal_id = $ref.proposal_id; operator = "demo" } | ConvertTo-Json) | Out-Null
$run = Invoke-RestMethod -Method Post "$api/ops/campaigns" -TimeoutSec 900 -ContentType "application/json" -Body '{"operator":"demo"}'
(Invoke-RestMethod "$api/ops/runs/$($run.run_id)/report").text
(Invoke-RestMethod "$api/ops/runs/$($run.run_id)/metrics") | Select-Object llm_calls, input_tokens, output_tokens, degraded_mode
Invoke-RestMethod "$api/ops/runs/$($run.run_id)/tool-calls" | Format-Table pattern_key, iteration, tool, params, result_summary -AutoSize
(Invoke-RestMethod "$api/ops/runs/$($run.run_id)/decisions?decided_by=llm").items | Where-Object field -ne "_row" | Format-Table source_row, field, before, after, confidence -AutoSize
Invoke-RestMethod "$api/ops/questions?status=open" | Format-Table pattern_key, llm_status, @{ n = "suggestion"; e = { "$($_.llm_suggestion.value) ($($_.llm_suggestion.confidence))" } } -AutoSize
```

Observed (the Run takes about 2 minutes: 18 LLM calls of about 6 s each):

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

Metrics: `llm_calls 18`, `input_tokens 7864`, `output_tokens 786`, `degraded_mode False`. Each of the 6 Unknown_Variant keys used the 3 turns allowed: two tool calls, then the final answer. All 18 answers were valid JSON in the expected protocol.

| Raw value | Field | Proposal (value, confidence) | Tools called (turns 1 and 2) | Result |
|---|---|---|---|---|
| `Mégane E-Tech` | Vehmodel | MEGANE E-TECH, 0.95 | find_similar_values, then find_similar_values or test_proposed_rule | applied automatically (`LLM_VALUE`) |
| `CLIP` (2 rows) | originTool | Clip, 0.95 | lookup_dictionary, find_similar_values | applied automatically to both rows |
| `Diag Tool` | originTool | DDT2000, 1.0 | find_similar_values (twice) | applied automatically |
| `AIR-BAG UNIT` | ecuName | AIRBAG, 0.75 | find_similar_values, lookup_dictionary or find_similar_values | sent to a human (`proposed_below_threshold`) |
| `2024-03-15 10:30` | Creation_date | 15/03/2024 10:30, 1.0 | lookup_dictionary, find_similar_values | sent to a human (`proposed_human_only`): dates are never applied automatically |
| `ID 0004` | ecuCode | ID0004, 1.0 | lookup_dictionary, find_similar_values | applied automatically |

Expected checks:

- The report has no Degraded_Mode line; the cost is `0.0000 €` while the token count is not 0.
- The tool-call table has 12 rows (2 per key), each with its iteration, tool name, JSON parameters and a one-line summary, for example `find_similar_values {"field": "ecuName", "limit": 3, "value": "AIRBAGUNIT"}  AIRBAG (0.75), ABS (0.31), IVI (0.31)` or `test_proposed_rule {"decision": "map", "value": "MEGANE E-TECH"}  valid: CORRECT on 1 row(s) with 'MEGANE E-TECH'; auto-apply allowed`.
- `decided_by = llm` decisions exist only for originTool, ecuName, Vehmodel or ecuCode with a confidence of at least 0.9 (5 rows here); none for a date, a ValueReference or an identifier.
- Two questions stay open: `ecuName|UNKNOWN_VARIANT|AIRBAGUNIT` with the suggestion `AIRBAG (0.75)` and `Creation_date|UNKNOWN_VARIANT|2024-03-15 10:30` with `15/03/2024 10:30 (1.0)`.
- No proposal was blocked by the validator in this run. A proposal outside the allowed values, or a key without a valid final answer after 3 turns, becomes a question with `llm_status = failed`; this path is covered by `tests/integration/test_ollama_pipeline.py::test_agent_with_simulated_ollama`.
