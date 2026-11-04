# Migration Rules (reference)

This document is the reference that all code must respect. Change a rule here first, then in the spec and the tests. All assumptions were confirmed by the team.

## Source file

- Separator `;`, UTF-8 with BOM, CRLF line endings. The file went through French Excel (`#NOM?`, `2,04E+12`).
- 10,000 rows. The pipeline must handle 100,000 rows in a few minutes.
- Two merged formats:
  - **Format 1** (~5,470 rows): `ecuCode` in ID0001-ID0005 with a free French label (e.g. "Lecture référence logiciel ADAS"). The label is unreliable.
  - **Format 2** (~4,410 rows): `ecuCode` = `Id###` (989 codes, not unique across ECUs). The label carries a UDS DID, e.g. "Donnée diagnostic AIRBAG (F188)" or "Paramètre diagnostic HVAC (F738)".

## Column mapping

Source column names are exact, including the typo `diagnosticFieId`.

| Source column | Target |
|---|---|
| `originTool` | `DiagToolsVersions.DiagToolName` (after mapping, R2) |
| `toolVersion` | `DiagToolsVersions.DiagToolVersion` |
| `VIN` | `Vehicle.VIN` (R1) |
| `diagnosticFieId` | `DiagnosticOperation.diagnosticOpID` (R7, R8) |
| `ecuName` | `DiagnozedECU.ecuName` (R5) |
| `ecuCode` | Determines `ValueDescription` (R3) and the `ValueReference` validation rule (R4) |
| `DescriptionDiagECU` | Used only for format 2 (DID extraction) or as a last resort (R3, R5) |
| `ValueReference` | `DiagValueResult.ValueReference` (R4) |
| `Creation_date` | `DiagnosticOperation.creationDate` (source format `dd/mm/yyyy hh:mm`) |
| `Vehmodel` | `Vehicle.VehicleModel` (R6) |

## Target model

- `Vehicle` (@VIN, @VehicleModel): one vehicle has **exactly one** model.
- `DiagnozedECU` (@ecuName): `Vehicle` 1,* <-> 1,* `DiagnozedECU`.
- `DiagnosticOperation` (@diagnosticOpID, @creationDate): `Vehicle` 1 -> * operations; `DiagnozedECU` 1 -> * operations.
- `DiagValueResult` (@ValueReference, @ValueDescription): **exactly 1** result per operation (1-1).
- `DiagToolsVersions` (@DiagToolName, @DiagToolVersion): 1 tool-version -> * operations.

## Dictionaries

- **VehicleModelsDico**: KANGOO, MASTER, CLIO, SCENIC E-TECH, ARKANA, CAPTUR, TRAFIC, AUSTRAL, MEGANE E-TECH, RAFALE.
- **ECUDico**: BCM, AIRBAG, TCM, BMS, OBC, ECM, ABS, ADAS, HVAC, IVI, VCU, EPS.
- **ToolDico**: DDT2000, Clip, CANalyzer.
- **ValueDescription**:

| Code | Description |
|---|---|
| ID0001 | Vehicle VIN |
| ID0002 | Part Number (F187) |
| ID0003 | Hardware (F191) |
| ID0004 | Software (F188) |
| ID0005 | Calibration (F012) |

## Metadata to add

- `source_originTool`: raw source value, mandatory for audit.
- Ingestion timestamp.
- `run_id`.
- Source row id.

## Normalization and control rules

### R1 - VIN

Trim, uppercase, remove disallowed characters. The result must match `^[0-9A-Z]{20}$`, otherwise **reject**.
Official example: `vf1-ab12 3c4567890def0` -> `VF1AB123C4567890DEF0`.

### R2 - originTool

| Source value | Target value |
|---|---|
| `DiagTool` | `DDT2000` |
| `AfterSalesDiag` | `Clip` |
| `CANalyzer`, `Clip`, `DDT2000` | unchanged |

Any other value must be **rejected or explicitly mapped** before loading. Always keep the raw value in `source_originTool`.

### R3 - ValueDescription

Only ID0001-ID0005 are allowed.

- **Format 1**: `ecuCode` in {ID0001...ID0005} is used as is. The label is ignored; a code/label inconsistency is logged as informational.
- **Format 2**: the DID is extracted from the label: F187 -> ID0002, F191 -> ID0003, F188 -> ID0004, F012 -> ID0005.
- Any other DID (e.g. "Paramètre diagnostic ... (F738)", 990 rows) -> **reject, out of scope** ("only useful data").
- **Empty `ecuCode`**: deduce from the DID in the label if present, or ID0001 if the label is one of "Lecture identifiant châssis", "Identification véhicule", "Numéro identification véhicule", "Identifiant véhicule usine". Otherwise -> **human**.

### R4 - ValueReference

- **ID0001**: the target value **is the normalized VIN**. The source value is replaced and kept in the log.
- **ID0002-ID0005**: trim + uppercase, then the value must match `^[0-9A-Z]{10}$`, otherwise **reject** with an explanation: Excel scientific notation (digits lost), `#NOM?`, empty, wrong length, forbidden character.

### R5 - ecuName

Remove spaces and dashes, then uppercase. The result must be in ECUDico (traced auto-correction). If empty or unreadable: deduce from the ECU named in the label (100% reliability measured on complete rows). Otherwise -> **human**.

### R6 - VehicleModel

Same normalization as R5, compared to VehicleModelsDico. If unreadable: take the model from another row with the same VIN if exactly one exists. Otherwise use the model Legacy B holds for the VIN (CORRECT, Legacy B named as source in the explanation). Otherwise -> **human**.

### R7 - Cardinalities

- A VIN with two different models -> **human**.
- A `diagnosticOpID` carried by two different operations -> **human**.
- Legacy B and the row give two different valid models for the same VIN -> **human** (rule R7).
- One operation = one result.

### R8 - Empty fields (confirmed)

`#NOM?` is treated like an empty value for these fields.

- Empty `originTool`, `toolVersion`, `Creation_date` -> **human** (grouped decision).
- Empty `diagnosticFieId` -> **deterministically generated id** (UUID v5 over VIN + ecuName + ValueDescription + date + value), marked "generated", so two runs produce the same id.

## Legacy B

- Own format: `CHASSIS_NO` (VIN), `MODELE` in mixed case (e.g. "Megane E-Tech"), `DATE_MAJ` as yyyymmdd. The Legacy B API agent translates it to the common vocabulary (normalized VIN, canonical VehicleModelsDico value, ISO date).
- Data simulated from the VINs of the source file, with about fifteen planted model conflicts listed in a control file.
- The Legacy B lookup can be disabled in configuration. The validation target is computed with it disabled.

## Final check before load

Reject the row if any check fails:

1. VIN is valid (`^[0-9A-Z]{20}$`).
2. DiagToolName in {DDT2000, CANalyzer, Clip}.
3. ValueDescription in {ID0001...ID0005}.
4. For ID0002-ID0005, ValueReference matches `^[0-9A-Z]{10}$`.

## Per-row decision priority

**REJECT > ASK_HUMAN > CORRECT > MIGRATE**

## Transformation vs correction

- **Transformation** is the normal mapping: `DiagTool` -> `DDT2000`, `Id###` -> `ID000x`, VIN as ID0001 value.
- **Correction** is the repair of an anomaly: `T CM` -> `TCM`.

## Implementation notes (interpretation, not new rules)

- R5/R6 dictionary lookup compares normalized keys on both sides and stores the canonical dictionary value. Example: `SCENIC E-TECH` normalizes to `SCENICETECH`; a source value normalizing to `SCENICETECH` is stored as `SCENIC E-TECH`.
- A normalized value equal to the raw value is MIGRATE; a value changed to reach the dictionary is CORRECT.

## Observed anomalies (test reference)

- `ecuName`: variants `T CM`, `E-PS`, `HVAC-`, ` OBC` (~430), empty (103), `#NOM?` (73).
- `Vehmodel`: `AR KANA`, `TRAFIC-` (85), `#NOM?` (15).
- `ValueReference`: length != 10, characters `/ # @ _ -`, Excel scientific notation, `#NOM?`, empty.
- Empty values: `originTool` 124, `toolVersion` 138, `diagnosticFieId` 125, `ecuCode` 140, `DescriptionDiagECU` 117, `Creation_date` 151.
- 30 `diagnosticFieId` values shared by two different operations.
- All VINs currently have 20 valid characters; R1 is still applied.

## Expected result (validation target)

6,942 migrated, 629 corrected, 1,916 rejected (incl. 990 out of scope), 513 ASK_HUMAN. The four outcomes are disjoint and sum to 10,000. Computed with the Legacy B lookup disabled. Exact counts, confirmed by an independent calculation on the same file (the earlier values ~6,940 / ~630 / ~1,920 / ~510 were rounded).