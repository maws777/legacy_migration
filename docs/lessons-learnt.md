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
