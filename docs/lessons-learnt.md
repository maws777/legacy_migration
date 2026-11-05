# Lessons learnt

Figures come from the cold Run `56ef734b-4425-421a-896f-896ae31a975d` on the 10,000-row dataset (Legacy B lookup disabled, no Human_Decision) and from `var/validation_deviation.json` written by `tests/integration/test_validation_target.py`.

## Anomalies found and decisions taken

| Field | Anomaly | Decision | Rows |
|---|---|---|---|
| `ecuCode` | Format_2 label DID outside F187/F191/F188/F012 (e.g. F738) | REJECT R3 `OUT_OF_SCOPE` | 990 |
| `ecuCode` | Format_2 code with no DID in the label | ASK_HUMAN, one question `ecuCode\|FORMAT2_NO_DID` | 46 |
| `ecuCode` | Empty or `#NOM?`, DID in the label | CORRECT R3 `DEDUCED_DID` | 47 |
| `ecuCode` | Empty or `#NOM?`, VIN-reading label | CORRECT R3 `DEDUCED_VIN_LABEL` (ID0001) | 21 |
| `ecuCode` | Empty or `#NOM?`, nothing to deduce | ASK_HUMAN, one question per Label_Template (5 templates, one of them empty) | 60 |
| `ValueReference` | Length other than 10 | REJECT R4 `WRONG_LENGTH` | 517 |
| `ValueReference` | Characters outside 0-9 A-Z (`/ # @ _ -`) | REJECT R4 `FORBIDDEN_CHAR` | 248 |
| `ValueReference` | Empty | REJECT R4 `EMPTY` | 82 |
| `ValueReference` | Excel scientific notation (`2,04E+12`) | REJECT R4 `EXCEL_SCI` | 76 |
| `ValueReference` | `#NOM?` | REJECT R4 `NOM` | 3 |
| `ValueReference` | ID0001 rows | TRANSFORM R4 `VIN_AS_VALUE` (the normalized VIN) | 1,511 |
| `ecuName` | Variants `T CM`, `E-PS`, `HVAC-`, ` OBC` | CORRECT R5 `DICT_NORMALIZED` | 427 |
| `ecuName` | Empty or `#NOM?`, single ECU in the label | CORRECT R5 `LABEL_ECU` | 146 |
| `ecuName` | Empty or `#NOM?`, no ECU in the label | ASK_HUMAN `ecuName\|UNREADABLE` | 30 |
| `Vehmodel` | Variants `AR KANA`, `TRAFIC-` | CORRECT R6 `DICT_NORMALIZED` | 85 |
| `Vehmodel` | `#NOM?` with no unique model on other rows of the VIN | ASK_HUMAN `Vehmodel\|UNREADABLE` | 15 |
| `diagnosticFieId` | Empty | CORRECT R8 `GENERATED_ID` (UUID v5) | 112 |
| `diagnosticFieId` | 30 ids carried by two different operations | ASK_HUMAN `diagnosticFieId\|SHARED_OP_ID` (with "generate" option) | 60 |
| `originTool` | Empty | ASK_HUMAN `originTool\|UNREADABLE` | 124 |
| `toolVersion` | Empty | ASK_HUMAN `toolVersion\|UNREADABLE` | 138 |
| `Creation_date` | Empty | ASK_HUMAN `Creation_date\|UNREADABLE` | 151 |
| VIN | none: every VIN has 20 valid characters (R1 still applied) | - | 0 |

Rows can carry several anomalies, so the row counts above do not add up to the outcome counts. The run produced 12 Human_Questions for 513 ASK_HUMAN rows. No Unknown_Variant exists in the dataset, so `data/synthetic_edge_cases.csv` (accented "Mégane E-Tech", "CLIP", "Diag Tool", "AIR-BAG UNIT", an ISO date, "ID 0004") exercises the Resolver_Agent path.

## Deviations from the Validation_Target

No deviation occurred. Every measured count equals the Validation_Target, and `tests/integration/test_validation_target.py` checks them with a tolerance of 0.

| Outcome | Measured | Validation_Target | Difference |
|---|---|---|---|
| MIGRATE | 6,942 | 6,942 | 0 |
| CORRECT | 629 | 629 | 0 |
| REJECT | 1,916 | 1,916 | 0 |
| ASK_HUMAN | 513 | 513 | 0 |
| REJECT OUT_OF_SCOPE | 990 | 990 | 0 |

The first version of the target (~6,940 / ~630 / ~1,920 / ~510) gave rounded values. An independent calculation on the same file gives exactly 6,942 / 629 / 1,916 / 513, so the apparent gaps of +2 / -1 / -4 / +3 came only from that rounding. The steering files and the spec now hold the exact counts.

## Other lessons

- **R4 waits for the ValueDescription.** 9 ASK_HUMAN rows have an undecided ecuCode (R3) and a ValueReference that R4 would reject for any code ID0002-ID0005. R4 runs only once the code is known, so these rows stay ASK_HUMAN until a human chooses the code, then become REJECT unless the code is ID0001.

- **Homogeneous-group rule.** With "apply a value to the group" restricted to groups with the same raw value and label, most dataset questions only offer "reject the group" or "case by case" (for example the 46-row `ecuCode|FORMAT2_NO_DID` group). This protects data quality but makes "case by case" the main path for large groups.
- **Line endings.** The CSV is stored with LF in Git and checked out with CRLF on Windows (`core.autocrlf=true`); the reader accepts both, and the decision counts do not depend on it.
- **Validation target needs Legacy B off.** With the lookup enabled, the 15 planted conflicts add R7 questions on every row of their VINs (tested in `tests/integration/test_legacy_b_control_file.py`).

## Local AI: what we observed

Real Runs on 4 November 2026 with Ollama 0.40.14 and `qwen2.5:7b` (Q4_K_M, 4.7 GB) on an Intel Core i7-11800H laptop with 16 GB of RAM and a GTX 1650 Max-Q (4 GB of VRAM). Figures are in `docs/cost-performance.md`.

- **The first load failed for lack of memory.** The first schema discovery got HTTP 500 after 7 s; the Ollama log said `llama-server reported out-of-memory during startup` (it could not allocate a 2.5 GB host buffer, with about 1 GB of RAM free). The pipeline did what it should: the LLM call failed, schema discovery fell back to the reference mapping, and nothing was blocked. But the reason was only `HTTP 500` in our result. We now keep Ollama's error message in the failure result and in the Degraded_Mode reason of the report, and the availability check loads the model, so a load failure is reported at Run start instead of in the middle of a Run.
- **The first successful call took 80 s.** 44 s to load the model, 26 s to read a 24-token prompt, 8 s to answer: the model does not fit in 4 GB of VRAM (12 of 29 layers on the GPU, the rest on the CPU). The next call took 3 s. The default timeout of 60 s would have failed it. We raised `llm.timeout_s` to 180 s, send `keep_alive: 2h` with every request so the model stays loaded between Runs, and `scripts/start_all.ps1` warms the model up and prints `Model ready` or the error before starting the services.
- **qwen2.5:7b proposed a wrong Column_Mapping, and the conflict detection blocked it.** On the 10,000-row file it mapped `ecuCode` to DiagToolName and `DescriptionDiagECU` to ValueDescription, left `originTool` and `diagnosticFieId` unmapped, misread both ecuCode formats (`^[A-Z0-9]{12}$` and `^Id[0-9]{4}$` instead of `^ID000[1-5]$` and `^Id[0-9]{3}$`), and repeated the `toolVersion` key in its JSON (once with another target in a second run). The targets mapped by zero columns (diagnosticOpID, Label) were conflicts, so the human validation was refused with 409 and the wrong mapping was never used. Python's JSON parser silently keeps the last value of a repeated key, so we now treat a repeated column as a conflict too, and every proposal (and every validation answer) lists its differences from the reference mapping column by column. The operator validates the reference mapping (`?source=reference` or `--no-llm`). Lesson: on this task a 7 B local model is not reliable; the human validation and the deterministic checks are what make it safe.
- **The Resolver_Agent was correct, slow and cheap.** On the 6 Unknown_Variants of `data/synthetic_edge_cases.csv`, the 6 proposals were right (MEGANE E-TECH, Clip, DDT2000, AIRBAG, 15/03/2024 10:30, ID0004) and all 18 answers followed the JSON protocol. 4 keys (5 rows) were applied automatically at confidence 0.95 or 1.0, all on closed-dictionary fields; AIRBAG (0.75) went to a human below the threshold; the date went to a human although its confidence was 1.0, because dates are never auto-applied. No date, ValueReference or identifier was applied by the model, and the validator had nothing to block in this run. Cost: 18 calls, 7,877 input and 776 output tokens (about 1,440 tokens per key), 113 s of AI resolution, 6.3 s per call on average, 0 €. The model always used its 3 turns, often calling the same tool twice: a smaller turn limit would save time but leave the model less room to check its answer with `test_proposed_rule`.
- **The dataset needs no LLM.** With Ollama running and Legacy B disabled, the 10,000-row migration gave exactly 6,942 / 629 / 1,916 / 513 with 0 LLM call and 0 token in 9.3 s: it has no Unknown_Variant, so the model only matters for schema discovery and for new variants.

### Agents on demand and chat (real check of the five UI improvements)

- **The background jobs removed the ReadTimeout.** The AI mapping proposal (37 s) and each advice (about 78 s) are longer than the 30 s HTTP timeout of the UI. As jobs, the button answers at once and the page polls every 3 s. The Client_App answered 3,202 queries with no failure through the whole check, including the Runs and the phase switches.
- **The AI mapping was wrong again and stayed blocked.** It made the same mistakes as before: a repeated `toolVersion`, and three targets mapped by no column. The operator corrected 5 columns in the drop-down lists and validated. Validation stays refused while a conflict is left.
- **The Question_Advisor follows the protocol, but its evidence needs reading.** Both advices used 4 turns (3 tools, then the final answer) and offered an allowed option. Neither answered the question for the human. On `Vehmodel|LEGACY_B_CONFLICT`, the agent never called `legacy_b_model`, the one tool that shows the Legacy B side. One evidence line named a model (CAPTUR) that no tool returned for the group. On `SHARED_OP_ID`, the list of unknowns was empty. The deterministic checks cover the option and the value, not the wording of the evidence. Possible next steps: suggest the first tool per reason in the prompt, and send the advice back when it has no unknowns.
- **One sentence of the prompt changed the chat's answers.** With the first schema note ("join vehicle_ecu, vehicle, diagnozed_ecu on vin and ecu_name"), 2 of the 3 example questions failed with `ambiguous column name`. Removing the note fixed the failures. But the model still joined `vehicle_ecu` and counted 7,564 vehicle/ECU pairs as "vehicles" (6,010 is the right count). Two changes fixed it:
  - the prompt now describes only 4 tables; `vehicle_ecu` and `diagnozed_ecu` stay allowed but undescribed;
  - the notes say how to count vehicles.

  A later sentence asking to "select the vin column" brought the wrong count back, so it was removed. Lesson: a valid SELECT can answer another question. The UI shows the SQL next to every answer, and the read-only checks protect the data, not the meaning.
- **A token limit cut the Legacy B questions.** With `max_tokens` at 200, the JSON of three questions holding five VINs was cut in the middle, and the agent fell back to its fixed questions. 400 is enough. Legacy B's useful question is the first one ("List the VIN and the vehicle model of ..."): it is the only one whose rows it can compare with its own records. The model still wrote one question about data Legacy B does not hold (tool versions).
- **Rendering every page against the live services found a bug the tests missed.** The UI test built an advice with a `datetime`. The real client builds it from JSON, where the date is a string, and the Questions page crashed on its date format. The model field is now typed `datetime`, and the test uses the ISO string.
