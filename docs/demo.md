# Demo scenario

Each step gives the exact PowerShell command and the expected result. The results below were observed on 4 November 2026 with the services started by `scripts/start_all.ps1`, without `ANTHROPIC_API_KEY` (Degraded_Mode) and with the Legacy_B_API running. run_ids and timestamps differ on every run; counts do not.

Every API step also exists in the UI (http://127.0.0.1:8501): Home (campaigns), Stats, Questions, Decision log, Phases.

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

Expected: six windows (Legacy_A_API, Legacy_B_API, Target_API, Orchestrator, UI, Client_App) and `ready on http://127.0.0.1:800x` for each API. The Client_App window prints one line per query, for example `NN0X2DWVXA0T4C6PXKWY -> 200 in 25.3 ms | queries 12 | availability ok 12 failed 0 | parity ok 12 failed 0`. It keeps running during the whole demo; its availability and parity counters must never show a failure.

## 1. Schema mapping

```powershell
$p = Invoke-RestMethod -Method Post "$api/ops/schema/discover"
"fallback=$($p.fallback) conflicts=$($p.conflicts.Count)"
$m = Post "/ops/schema/validate" @{ proposal_id = $p.proposal_id; operator = "demo" }
"validated_by=$($m.validated_by) columns=$(@($m.mapping.PSObject.Properties).Count)"
```

Expected: `fallback=True conflicts=0` (reference Column_Mapping, no LLM key), then `validated_by=demo columns=10`. With a key, `fallback=False` means the LLM proposed the mapping; validate only when `conflicts=0` (otherwise the validation answers 409 with the conflicting attributes).

## 2. Migration 1

```powershell
$r1 = Post "/ops/campaigns" @{ operator = "demo" }
"status=$($r1.status) campaign=$($r1.campaign_status) open=$($r1.open_questions)"
(Invoke-RestMethod "$api/ops/runs/$($r1.run_id)/report").text
(Invoke-RestMethod "http://127.0.0.1:8003/target/stats").tables
```

Expected (about 7 seconds):

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
Degraded_Mode: no LLM provider available; Unknown_Variants were sent to humans.
```

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

Row 4 field by field: VIN MIGRATE R1 OK, originTool TRANSFORM R2 MAPPED (`DiagTool` -> `DDT2000`), toolVersion MIGRATE R8 COPIED, Creation_date TRANSFORM R8 DATE_PARSED (`2024-10-08T18:06`), ecuCode MIGRATE R3 FORMAT1 (ID0005), ValueReference MIGRATE R4 OK, ecuName CORRECT R5 DICT_NORMALIZED (`E-PS` -> `EPS`), Vehmodel MIGRATE R6 OK, diagnosticFieId MIGRATE R8 COPIED, then the `_row` record. The CSV export holds every Decision_Record of migration 1. In the UI (page Decision log), filter by Source_Row_Id 4, 13 or 2 to show the same explanations.

## 8. Stop

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
```

Expected: one `Stopped ...` line per window, then `All services stopped.`

## Variant: "apply a value to the group" on the edge cases

The 10,000-row file has a single homogeneous group (2 rows). `data/synthetic_edge_cases.csv` holds Unknown_Variants never seen in it, including two rows with the same raw tool `CLIP`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
powershell -ExecutionPolicy Bypass -File scripts\prepare_edge_demo.ps1 -Reset
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Source data\synthetic_edge_cases.csv -VarDir var\demo_edge -NoClient
$api = "http://127.0.0.1:8000"
$q = Invoke-RestMethod "$api/ops/questions?status=open"
$q | Format-Table pattern_key, row_count, @{ n = "options"; e = { $_.options -join "," } } -AutoSize
```

Expected: the preparation prints 1 migrated, 0 corrected, 0 rejected, 7 requiring human decision, then `Campaign ready in var/demo_edge` (it runs without the LLM key so the questions are created). Six open questions; `originTool|UNKNOWN_VARIANT|CLIP` (2 rows) offers `apply_value,reject_group,case_by_case`, while `Vehmodel|UNKNOWN_VARIANT|MÉGANEETECH` offers only `reject_group,case_by_case` (R6).

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
