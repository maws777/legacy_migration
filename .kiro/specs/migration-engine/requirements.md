# Requirements Document

## Introduction

The migration-engine moves the Legacy A vehicle-diagnostics CSV export to a constrained target model while the client application and Legacy B keep working, following the Strangler Fig pattern in 4 phases. Deterministic rules decide first, an LLM handles distinct unknown patterns only, and a human answers grouped questions for what remains. Every decision is logged with an explanation.

The steering files are the source of truth and are referenced, not copied:

- `.kiro/steering/migration-rules.md`: column mapping, target model, dictionaries, rules R1-R8, final checks, observed anomalies, validation target.
- `.kiro/steering/product.md`: goals, end-of-run report format, Strangler Fig phases, demo scenario.
- `.kiro/steering/tech.md`: stack, AI principles, engineering constraints.
- `.kiro/steering/structure.md`: modules, dependency rules, inter-module contracts.

If this document and a steering file disagree, the steering file wins and this document is corrected. The inter-module contracts (API routes, Decision_Record, Human_Question, LLM_Client interface) are frozen in `design.md`.

Implementation priority follows the requirement groups:

1. Ingestion, Legacy A, target schema: Requirements 1-4.
2. Rules, row outcome, decision log: Requirements 5-17.
3. Idempotent load, report, measurement: Requirements 18-19.
4. Grouped human-in-the-loop: Requirements 20-21.
5. LLM client, AI agents, degraded mode: Requirements 22-25.
6. Orchestrator, phases, Legacy B, client app: Requirements 26-31.
7. UI: Requirement 32.
8. Cross-cutting performance and documentation: Requirements 34-35, except Requirement 34.1.
9. Optional, do last: Requirement 34.1 (100,000-row timing, recorded by Requirement 34.5), then Requirement 33 (bonus query agent).

Requirements 11 and 12 read Legacy B models only through the read-only Legacy_B_Model_Index, so group 2 can be built and tested with a stub index before Requirement 30.

## Confirmed decisions

- **A1 [CONFIRMED]** Stack: Python 3.11, FastAPI + uvicorn, SQLAlchemy 2.x, pandas, LangGraph, Streamlit, pytest + hypothesis. LangGraph is used only for the Run_Workflow (one node processes a batch or a Pattern_Key, never a single row; the workflow pauses for human answers; checkpoints are stored in SQLite) and for the Query_Agent.
- **A2 [CONFIRMED]** Target_DB: SQLite through SQLAlchemy, CHECK constraints, `PRAGMA foreign_keys=ON`. The schema uses only column types and SQL constructs portable to PostgreSQL; no test is run on PostgreSQL.
- **A3 [CONFIRMED]** LLM provider: local Ollama HTTP API; model and base URL are configured in `rules/config/pipeline.yaml`, with no API key.
- **A4 [CONFIRMED]** Empty-field policy R8 (Requirement 13), applied to every Unreadable_Value (empty after trimming or `#NOM?`) in originTool, toolVersion, Creation_date and diagnosticFieId.
- **A5 [CONFIRMED]** A Format_2 row whose label carries no DID goes to a human. R3 does not cover this case. All such rows share one Pattern_Key and one Human_Question (Requirement 8.11).
- **A6 [CONFIRMED]** A missing value deduced by a rule (ecuCode, ecuName, VehicleModel, generated diagnosticOpID) is a CORRECT, as the repair of an anomaly.
- **A7 [CONFIRMED]** "100,000 rows in a few minutes" means at most 5 minutes on a team laptop, excluding human wait time.
- **A8 [CONFIRMED]** A Human_Decision takes precedence over a cached resolution for the same Pattern_Key (Requirements 5.6 and 24.7).
- **A9 [CONFIRMED]** A data row whose cell count differs from the header cell count is delivered with a malformed flag and rejected with rule_id INGEST and reason MALFORMED_ROW (Requirement 1.8), so that every row read keeps a Row_Outcome and a Decision_Record.

## Glossary

- **Migration_Pipeline**: the `migration/` batch process that runs ingestion, rules, AI resolution, human routing, load and reporting for one Run, executed as a Run_Workflow.
- **Run_Workflow**: the LangGraph workflow (A1) that executes the Migration_Pipeline. Each node execution processes one batch of rows or one Pattern_Key, never a single row. The workflow pauses for human answers and stores its checkpoints in SQLite.
- **Ingestion_Module**: the component that reads the Legacy A CSV file into raw rows.
- **Legacy_A_API**: the read-only HTTP API of the simulated Legacy A (`legacy_a/`).
- **Legacy_B_API**: the read-only HTTP API of the simulated Legacy B returning, per VIN, the Legacy B record in the Legacy B format (`CHASSIS_NO`, `MODELE`, `DATE_MAJ`) (`legacy_b/`).
- **Legacy_B_Simulator**: the `legacy_b/` component that generates the simulated Legacy B data and the Legacy_B_Control_File from the VINs of the Legacy A source file.
- **Legacy_B_Control_File**: the file listing the model conflicts planted in the simulated Legacy B data, one entry per conflict with the VIN, the Legacy A model and the Legacy B model.
- **API_Agent_Legacy_B**: the agent that reads the Legacy_B_API and translates each Legacy B record into the Common_Vocabulary (`agents/api_agent_legacy_b`).
- **Common_Vocabulary**: the representation shared by the Migration_Pipeline and the Orchestrator: R1-normalized VIN, canonical VehicleModelsDico value obtained through Normalized_Key, and ISO 8601 date (yyyy-mm-dd).
- **Legacy_B_Lookup**: the Rule_Config switch that enables or disables the use of Legacy B models by the Migration_Pipeline.
- **Legacy_B_Model_Index**: for each normalized VIN, the canonical VehicleModel returned by the API_Agent_Legacy_B, fetched by the Migration_Pipeline before the rules run and passed to the Rule_Engine as read-only context. The index is empty when the Legacy_B_Lookup is disabled.
- **Target_DB**: the target relational database implementing the target model of `migration-rules.md`.
- **Target_Repository**: the only component that writes to the Target_DB (`target/`).
- **Target_API**: the read HTTP API over the Target_DB (`target/`).
- **Rule_Engine**: the pure functions of `rules/` implementing R1-R8 and the final checks.
- **Rule_Config**: the YAML files under `rules/` holding dictionaries, value mappings, Column_Mapping reference, Confidence_Threshold, batch size, prices per token, the Legacy_B_Lookup switch and other tunables.
- **Column_Mapping**: the mapping from source column names (including the typo `diagnosticFieId`) to target attributes.
- **Decision_Logger**: the only writer of the decision log (`reporting/`).
- **Decision_Record**: one decision-log entry with the fields listed in `structure.md`.
- **Report_Generator**: the component producing the end-of-run report and Run metrics (`reporting/`).
- **HITL_Queue**: the grouped human question queue (`hitl/`).
- **Human_Question**: one question for one Pattern_Key, with the contents listed in `structure.md`.
- **Human_Decision**: a persisted human answer bound to a Pattern_Key.
- **Human_Decision_Store**: the persistent store of Human_Decisions (`hitl/`).
- **LLM_Client**: the only component that calls the LLM provider (`agents/llm_client`).
- **API_Agent_Legacy**: the agent that discovers the source schema and proposes the Column_Mapping.
- **Resolver_Agent**: the agent that proposes a mapping for an Unknown_Variant, with a confidence and a justification.
- **Resolution_Cache**: the persistent cache of Resolver_Agent proposals applied at or above the Confidence_Threshold, keyed by Pattern_Key.
- **Orchestrator**: the API that serves client requests and routes them to backends according to the current phase.
- **Phase_Controller**: the component holding the current Strangler Fig phase (1-4) and enforcing forward transitions and one-phase rollbacks.
- **Reconciler**: the phase 3 component comparing legacy-derived expected records with Target_DB records.
- **Client_App**: the simulated client sending continuous queries to the Orchestrator.
- **UI**: the Streamlit application (`ui/`).
- **Query_Agent**: the bonus agent translating natural-language questions into read-only SQL.
- **Test_Suite**: the pytest and hypothesis tests under `tests/`.
- **Project_Docs**: the files `docs/architecture.md`, `docs/cost-performance.md` and `docs/lessons-learnt.md`.
- **Run**: one execution of the Migration_Pipeline over the source file, identified by a run_id.
- **Source_Row_Id**: the identifier of a source data row, stable across Runs on the same file.
- **Field_Decision**: the decision taken on one field of one row: Action, rule_id, before, after, decided_by, confidence, explanation.
- **Action**: one of MIGRATE, TRANSFORM, CORRECT, REJECT, ASK_HUMAN.
- **Row_Outcome**: the single final result of a source row in a Run: MIGRATE, CORRECT, REJECT or ASK_HUMAN.
- **Label**: the value of the source column `DescriptionDiagECU`.
- **DID**: the UDS data identifier made of the 4 hexadecimal characters inside the final parentheses of the Label, e.g. `F188` in "Donnée diagnostic AIRBAG (F188)".
- **Format_1**: a row whose `ecuCode` is one of ID0001-ID0005.
- **Format_2**: a row whose `ecuCode` matches `^Id[0-9]{3}$`.
- **Normalized_Key**: a value with spaces and dashes removed, then uppercased (R5, R6).
- **Label_ECU**: the ECUDico entry named in the Label, when the uppercased Label contains exactly one ECUDico entry as a whole word.
- **Label_Template**: the trimmed Label in which every whole word that equals an ECUDico entry once uppercased is replaced by the token `<ECU>`, e.g. "Lecture référence logiciel ADAS" -> "Lecture référence logiciel <ECU>".
- **VIN_Model_Index**: for each normalized VIN, the set of canonical VehicleModel values matched on the rows of the Run.
- **Unreadable_Value**: a raw value that is empty after trimming or equal to `#NOM?`.
- **Unknown_Variant**: a raw value that is not an Unreadable_Value and that no rule, Human_Decision or cached resolution maps to an allowed target value.
- **Excel_Scientific_Notation**: a trimmed, uppercased value matching `^[0-9]+([.,][0-9]+)?E[+-]?[0-9]+$`, e.g. `2,04E+12`.
- **Pattern_Key**: a deterministic key grouping undecided cases, so that one Pattern_Key receives one decision. Its components are:
  - field + reason + Label_Template for an R3 case whose ecuCode is an Unreadable_Value (Requirement 8.9), without the raw ecuCode value;
  - field + reason for the R3 case of a Format_2 row without DID (Requirement 8.5) and for every R5, R6, R7 and R8 case that is not an Unknown_Variant;
  - field + reason + normalized raw value (Normalized_Key for ecuName and Vehmodel, trimmed value for the other fields) for an Unknown_Variant sent to the Resolver_Agent, so that each distinct value costs at most one LLM_Client call;
  - the group Pattern_Key extended with the Source_Row_Id for a case-by-case Human_Question (Requirement 21.5).
- **Confidence_Threshold**: the minimum LLM confidence for automatic application, configurable in Rule_Config, default 0.9.
- **Degraded_Mode**: operation without an available LLM provider.
- **Operation_Identity**: the tuple (normalized VIN, ecuName, ValueDescription, creationDate, ValueReference) of a row.
- **Gap**: an expected target record, derived from Legacy A rows with Row_Outcome MIGRATE or CORRECT, that is missing from the Target_DB or differs from the stored record.
- **Control_Sample**: a fixed set of VINs selected from Rule_Config size and seed, identical across Runs.
- **Aligned_Percentage**: aligned compared records divided by compared records, as a percentage.
- **Validation_Target**: the expected outcome counts on the 10,000-row dataset listed in `product.md` (MIGRATE 6,942, CORRECT 629, REJECT 1,916 incl. OUT_OF_SCOPE 990, ASK_HUMAN 513, exact counts), computed with the Legacy_B_Lookup disabled.

## Requirements

### Requirement 1: CSV ingestion

**User Story:** As a migration engineer, I want the Legacy A CSV read without alteration, so that the rules see the exact raw values.

#### Acceptance Criteria

1. WHEN a Run starts, THE Ingestion_Module SHALL read the source CSV with UTF-8 decoding, removal of a leading BOM, `;` as separator, the first line as the header, acceptance of both CRLF and LF line endings, and no data row created by a line terminator at the end of the file.
2. THE Ingestion_Module SHALL deliver every cell as a string without trimming or type conversion, keeping empty cells as empty strings and values such as `007`, `#NOM?` and `2,04E+12` unchanged character for character.
3. THE Ingestion_Module SHALL assign each data row a Source_Row_Id that is unique within the file and identical across Runs on a byte-identical file.
4. IF a source column required by the validated Column_Mapping is missing from the file header, compared by exact case-sensitive name, THEN THE Ingestion_Module SHALL stop the Run before any row is processed and report all missing column names.
5. THE Ingestion_Module SHALL leave the source file bytes identical before and after a Run.
6. WHEN a reference table of string cells with two or more columns and an equal column count on every row, containing empty cells, values with leading zeros, `#NOM?` values and accented characters but no `;`, double quote, CR or LF, is written as a `;`-separated UTF-8 BOM CRLF file and then ingested, THE Ingestion_Module SHALL return a header and cells identical to the written table.
7. IF the source file cannot be opened or its content is not valid UTF-8, THEN THE Ingestion_Module SHALL stop the Run before any row is processed and report the failure reason.
8. IF a data row has a cell count different from the header, THEN THE Ingestion_Module SHALL deliver the row with a malformed flag, and THE Rule_Engine SHALL record REJECT with rule_id INGEST and reason MALFORMED_ROW (A9).

### Requirement 2: Legacy A read-only API

**User Story:** As the Orchestrator, I want read-only access to Legacy A, so that legacy reads keep working during migration.

#### Acceptance Criteria

1. WHEN a client requests a page of raw rows with a page number of at least 1 and a page size from 1 to 1,000 rows (default 100 when omitted), THE Legacy_A_API SHALL return the rows of that page in ascending Source_Row_Id order, each with its Source_Row_Id and its raw values unchanged, so that the concatenation of all pages contains every raw row exactly once.
2. WHEN a client requests the diagnostics for a VIN, THE Legacy_A_API SHALL return every raw row whose R1-normalized VIN equals the R1-normalized requested VIN, in ascending Source_Row_Id order, each with its Source_Row_Id and its raw values unchanged.
3. IF a client sends a request with a method other than GET, THEN THE Legacy_A_API SHALL respond with HTTP 405 and leave the data unchanged.
4. IF the page number is not an integer of at least 1 or the page size is not an integer from 1 to 1,000, THEN THE Legacy_A_API SHALL reject the request with a validation error indicating the invalid parameter and return no rows.
5. IF a valid page request is beyond the last page or a VIN request matches no raw row, THEN THE Legacy_A_API SHALL return an empty list of rows and no error.

### Requirement 3: Target schema and constraints

**User Story:** As a data owner, I want the target model enforced by the database, so that invalid data cannot be stored.

#### Acceptance Criteria

1. THE Target_DB SHALL implement Vehicle, DiagnozedECU, the Vehicle-DiagnozedECU many-to-many link, DiagnosticOperation, DiagValueResult and DiagToolsVersions with the attributes and cardinalities of the target model in `migration-rules.md`.
2. THE Target_DB SHALL enforce foreign keys on every database connection (A2) and refuse any write whose foreign key references a record that does not exist.
3. THE Target_DB SHALL refuse any write that would create a second Vehicle record with the same VIN or a Vehicle record without a VehicleModel.
4. THE Target_DB SHALL refuse any write that would create a second DiagValueResult for the same DiagnosticOperation.
5. THE Target_DB SHALL refuse, through named CHECK constraints, any write where VIN does not match `^[0-9A-Z]{20}$`, VehicleModel is not in VehicleModelsDico, ecuName is not in ECUDico, DiagToolName is not in ToolDico, ValueDescription is not one of ID0001, ID0002, ID0003, ID0004 or ID0005, or a ValueReference whose ValueDescription is ID0002-ID0005 does not match `^[0-9A-Z]{10}$`.
6. THE Target_DB SHALL store on each DiagnosticOperation the non-null metadata source_originTool, ingestion timestamp, run_id, Source_Row_Id and a generated-diagnosticOpID flag that is true when the Migration_Pipeline generated the diagnosticOpID and false when the diagnosticOpID came from the source row.
7. THE Target_DB schema definition SHALL create every table and constraint of criteria 1-6 without error on SQLite, using only column types and SQL constructs portable to PostgreSQL (A2). No test is run on PostgreSQL.
8. IF the Target_DB refuses a write because of a constraint, THEN THE Target_Repository SHALL roll back every record written for that source row, leave the records committed for other rows unchanged, and return to the Migration_Pipeline the violated constraint name, or the constraint type (FOREIGN KEY, UNIQUE, NOT NULL or CHECK) when the database reports no name.
9. WHEN the Target_Repository returns a constraint violation for a row, THE Migration_Pipeline SHALL set the Row_Outcome of that row to REJECT with rule_id LOAD and the returned constraint name or type in the explanation, then continue loading the remaining rows of the Run.

### Requirement 4: Run identity and configuration

**User Story:** As a migration engineer, I want each Run identified, configured from files and executed as a resumable workflow, so that results are traceable, tunable without code changes and continued after human answers.

#### Acceptance Criteria

1. WHEN a Run starts, THE Migration_Pipeline SHALL assign a run_id that differs from the run_id of every previous Run and SHALL record the ingestion timestamp in UTC before the Ingestion_Module reads the first row.
2. THE Migration_Pipeline SHALL attach the run_id of the current Run to every Decision_Record and to the end-of-run report produced by the Report_Generator for that Run.
3. WHEN a Run starts, THE Migration_Pipeline SHALL read the dictionaries, value mappings, Column_Mapping reference, Confidence_Threshold, batch size and Legacy_B_Lookup switch from Rule_Config before processing the first row, and SHALL use those values unchanged until the Run ends.
4. IF Rule_Config defines no Confidence_Threshold, THEN THE Migration_Pipeline SHALL use a Confidence_Threshold of 0.9.
5. IF a Rule_Config file is missing, is not parseable YAML, or fails schema validation (including a Confidence_Threshold outside 0.0 to 1.0 inclusive or a batch size that is not an integer of at least 1), THEN THE Migration_Pipeline SHALL stop before processing any row, write no Target_DB record and no Decision_Record, exit with a failure status, and report the file name and the validation error.
6. THE Migration_Pipeline SHALL execute each Run as a Run_Workflow in which each node execution processes one batch of rows or one Pattern_Key (A1).
7. WHEN a Run ends with at least one open Human_Question, THE Run_Workflow SHALL pause after the Report_Generator has produced the end-of-run report of that Run, and SHALL store its checkpoint in SQLite.
8. WHEN a paused Run_Workflow is resumed after human answers, THE Migration_Pipeline SHALL start a new Run with a new run_id that applies the Human_Decisions read from the Human_Decision_Store (Requirement 21.8).

### Requirement 5: Rule engine contract

**User Story:** As a developer, I want rules as pure, tested functions, so that every decision is reproducible.

#### Acceptance Criteria

1. THE Rule_Engine SHALL take one raw row plus a read-only context (Rule_Config, Human_Decisions, cached resolutions, VIN_Model_Index, Legacy_B_Model_Index, diagnosticOpID usage) and return one row decision made of Field_Decisions. Each Field_Decision SHALL have an Action from the Action list and a rule_id naming one of R1-R8, a final check, `INGEST` (ingestion) or `LOAD` (Target_DB constraint or load failure, Requirements 3.9 and 18.5).
2. THE Rule_Engine SHALL compute decisions from its inputs only, without file, database, network or LLM access, and SHALL leave the raw row and the context unchanged after each call.
3. WHEN the Rule_Engine is called with a raw row and a context equal to those of an earlier call, including after calls on other rows in between, THE Rule_Engine SHALL return a row decision equal to the earlier one. Equal row decisions have the same Field_Decisions, in the same order, with equal attributes.
4. WHEN a case matches a Pattern_Key that has a Human_Decision or a cached resolution in the context, THE Rule_Engine SHALL return a Field_Decision with that decision's Action and after value, and with decided_by naming the Human_Decision or the Resolution_Cache as source.
5. WHEN the Rule_Engine returns a Field_Decision taken from a Human_Decision or a cached resolution, THE Migration_Pipeline SHALL NOT send that case to the Resolver_Agent or the HITL_Queue.
6. IF a case's Pattern_Key has both a Human_Decision and a cached resolution, THEN THE Rule_Engine SHALL apply the Human_Decision.
7. THE Test_Suite SHALL contain at least one unit test for each official example given in `migration-rules.md` for R1-R8 and for each final check, and each of these tests SHALL assert the expected result of its example.
8. THE Test_Suite SHALL use hypothesis property-based tests only for R1 VIN normalization (Requirement 6.6), R4 ValueReference classification (Requirement 9.8), Normalized_Key and dictionary lookup (Requirements 10.7, 10.8 and 11.6), Row_Outcome classification (Requirements 16.4 and 16.6) and load idempotence (Requirement 18.6), with at least one hypothesis test for each of these criteria.
9. THE Test_Suite SHALL verify every other criterion that states a behaviour over several rows, values or contexts, including criteria 1.6, 5.3, 13.5, 14.4, 17.9, 20.5, 24.9 and 33.7, with example-based unit tests.

### Requirement 6: R1 VIN

**User Story:** As a data owner, I want VINs normalized and validated, so that every stored vehicle has a valid identifier.

#### Acceptance Criteria

1. WHEN the Rule_Engine processes a VIN, THE Rule_Engine SHALL compute the normalized VIN by trimming the raw value, then uppercasing it, then removing every character outside `[0-9A-Z]`.
2. WHEN the Rule_Engine processes the raw VIN `vf1-ab12 3c4567890def0`, THE Rule_Engine SHALL produce the normalized VIN `VF1AB123C4567890DEF0` (official example).
3. IF the normalized VIN does not match `^[0-9A-Z]{20}$`, including when the normalized VIN is empty, THEN THE Rule_Engine SHALL return a Field_Decision with Action REJECT, rule_id R1, before set to the raw value, and an explanation containing the normalized VIN and its character count.
4. WHEN the normalized VIN matches `^[0-9A-Z]{20}$` and is character-for-character identical to the raw value, THE Rule_Engine SHALL return a Field_Decision with Action MIGRATE, rule_id R1, and before and after both set to the raw value.
5. WHEN the normalized VIN matches `^[0-9A-Z]{20}$` and is not character-for-character identical to the raw value, THE Rule_Engine SHALL return a Field_Decision with Action CORRECT, rule_id R1, before set to the raw value and after set to the normalized VIN.
6. THE Rule_Engine SHALL produce, for every input string, a normalized VIN that contains only characters from `[0-9A-Z]` and that is unchanged when normalized a second time.

### Requirement 7: R2 originTool

**User Story:** As a data owner, I want tool names mapped to the ToolDico, so that tool versions are consistent.

#### Acceptance Criteria

1. WHEN the trimmed originTool value equals `DiagTool` (case-sensitive), THE Rule_Engine SHALL set DiagToolName to `DDT2000` and record a Field_Decision with Action TRANSFORM, rule_id R2, before equal to the raw originTool value and after equal to `DDT2000`.
2. WHEN the trimmed originTool value equals `AfterSalesDiag` (case-sensitive), THE Rule_Engine SHALL set DiagToolName to `Clip` and record a Field_Decision with Action TRANSFORM, rule_id R2, before equal to the raw originTool value and after equal to `Clip`.
3. WHEN the trimmed originTool value equals `CANalyzer`, `Clip` or `DDT2000` (case-sensitive), THE Rule_Engine SHALL set DiagToolName to that trimmed value and record a Field_Decision with Action MIGRATE, rule_id R2, before equal to the raw originTool value and after equal to that trimmed value.
4. IF originTool is an Unknown_Variant, THEN THE Rule_Engine SHALL route the field to the Resolver_Agent path of Requirement 24.
5. THE Migration_Pipeline SHALL store the raw originTool value, unmodified from the value read by the Ingestion_Module, in source_originTool for every row written to the Target_DB.

### Requirement 8: R3 ValueDescription

**User Story:** As a data owner, I want only in-scope value descriptions migrated, so that the target holds only useful data.

#### Acceptance Criteria

1. WHEN the Rule_Engine applies R3 to a Format_1 row, THE Rule_Engine SHALL set ValueDescription to the ecuCode value and record Action MIGRATE with rule_id R3.
2. IF the Label of a Format_1 row carries a DID among F187, F191, F188 and F012 whose criterion 3 mapping differs from ecuCode, THEN THE Rule_Engine SHALL keep ecuCode as ValueDescription, keep Action MIGRATE with rule_id R3, and add to the explanation an inconsistency note naming the DID and its mapped code.
3. WHEN the Rule_Engine applies R3 to a Format_2 row whose Label DID is F187, F191, F188 or F012, THE Rule_Engine SHALL set ValueDescription to ID0002, ID0003, ID0004 or ID0005 respectively and record Action TRANSFORM with rule_id R3.
4. IF the Label of a Format_2 row carries a DID other than F187, F191, F188 and F012, THEN THE Rule_Engine SHALL record Action REJECT with rule_id R3 and reason OUT_OF_SCOPE.
5. IF the Label of a Format_2 row carries no DID, THEN THE Rule_Engine SHALL record Action ASK_HUMAN with rule_id R3 (A5).
6. WHEN the Rule_Engine applies R3 to a row whose ecuCode is an Unreadable_Value and whose Label DID is F187, F191, F188 or F012, THE Rule_Engine SHALL set ValueDescription from the DID mapping of criterion 3 and record Action CORRECT with rule_id R3.
7. IF ecuCode is an Unreadable_Value and the Label carries a DID other than F187, F191, F188 and F012, THEN THE Rule_Engine SHALL record Action REJECT with rule_id R3 and reason OUT_OF_SCOPE.
8. WHEN the Rule_Engine applies R3 to a row whose ecuCode is an Unreadable_Value, whose Label carries no DID, and whose trimmed Label exactly equals one of "Lecture identifiant châssis", "Identification véhicule", "Numéro identification véhicule" or "Identifiant véhicule usine", THE Rule_Engine SHALL set ValueDescription to ID0001 and record Action CORRECT with rule_id R3.
9. IF ecuCode is an Unreadable_Value and none of criteria 6-8 applies, THEN THE Rule_Engine SHALL record Action ASK_HUMAN with rule_id R3.
10. IF ecuCode is not an Unreadable_Value and the row is neither Format_1 nor Format_2, THEN THE Rule_Engine SHALL route the ecuCode value as an Unknown_Variant to the Resolver_Agent path of Requirement 24.
11. THE Rule_Engine SHALL build the Pattern_Key of an ASK_HUMAN case of criterion 5 from the field name and the reason only, so that all Format_2 rows of a Run whose Label carries no DID share one Pattern_Key and one Human_Question.
12. THE Rule_Engine SHALL build the Pattern_Key of an ASK_HUMAN case of criterion 9 from the field name, the reason and the Label_Template of the row, without the raw ecuCode value.

### Requirement 9: R4 ValueReference

**User Story:** As a data owner, I want value references validated per description, so that damaged values are rejected with a reason.

#### Acceptance Criteria

1. WHEN ValueDescription is ID0001, THE Rule_Engine SHALL record a Field_Decision for ValueReference with rule_id R4, `before` set to the raw ValueReference and `after` set to the normalized VIN of the same row.
2. WHEN ValueDescription is ID0001 and the raw ValueReference is not identical to the normalized VIN (case-sensitive comparison, without trimming), THE Rule_Engine SHALL set the Action of that Field_Decision to TRANSFORM.
3. WHEN ValueDescription is ID0001 and the raw ValueReference is identical to the normalized VIN, THE Rule_Engine SHALL set the Action of that Field_Decision to MIGRATE.
4. WHEN ValueDescription is one of ID0002, ID0003, ID0004 or ID0005, THE Rule_Engine SHALL remove leading and trailing whitespace from ValueReference, then uppercase it, before validating it against `^[0-9A-Z]{10}$`.
5. IF the trimmed, uppercased ValueReference for ID0002-ID0005 does not match `^[0-9A-Z]{10}$`, THEN THE Rule_Engine SHALL record a Field_Decision with Action REJECT, rule_id R4, `before` set to the raw value, and exactly one reason: the first one that applies, in this order: empty (length 0), `#NOM?` (equal to `#NOM?`), Excel_Scientific_Notation, forbidden character (at least one character outside 0-9 and A-Z), wrong length (only 0-9 and A-Z characters, length other than 10).
6. WHEN the trimmed, uppercased ValueReference for ID0002-ID0005 matches `^[0-9A-Z]{10}$` and is identical to the raw value, THE Rule_Engine SHALL record a Field_Decision with Action MIGRATE and rule_id R4.
7. WHEN the trimmed, uppercased ValueReference for ID0002-ID0005 matches `^[0-9A-Z]{10}$` and differs from the raw value, THE Rule_Engine SHALL record a Field_Decision with Action CORRECT, rule_id R4, `before` set to the raw value and `after` set to the trimmed, uppercased value.
8. THE Rule_Engine SHALL, for every input string with ValueDescription ID0002-ID0005, produce either Action MIGRATE or CORRECT with an `after` value matching `^[0-9A-Z]{10}$`, or Action REJECT with exactly one of the five reasons of criterion 5 (property-based invariant).

### Requirement 10: R5 ecuName

**User Story:** As a data owner, I want ECU names repaired against the ECUDico, so that naming variants do not create false ECUs.

#### Acceptance Criteria

1. WHEN the Rule_Engine processes ecuName, THE Rule_Engine SHALL compare the Normalized_Key of the raw value with the Normalized_Key of each ECUDico entry.
2. WHEN the Normalized_Key of ecuName matches an ECUDico entry and the raw value is character-for-character identical to that entry, THE Rule_Engine SHALL store that entry and record MIGRATE with rule_id R5.
3. WHEN the Normalized_Key of ecuName matches an ECUDico entry and the raw value differs from that entry in any character, including leading or trailing spaces, THE Rule_Engine SHALL store that entry and record CORRECT with rule_id R5 (e.g. `T CM` -> `TCM`, `E-PS` -> `EPS`, `HVAC-` -> `HVAC`, ` OBC` -> `OBC`).
4. IF the Normalized_Key of ecuName matches no ECUDico entry and a Label_ECU exists, THEN THE Rule_Engine SHALL store the Label_ECU and record CORRECT with rule_id R5.
5. IF ecuName is an Unreadable_Value and no Label_ECU exists, THEN THE Rule_Engine SHALL record ASK_HUMAN with rule_id R5.
6. IF ecuName is an Unknown_Variant and no Label_ECU exists, THEN THE Rule_Engine SHALL route the field to the Resolver_Agent path of Requirement 24.
7. THE Rule_Engine SHALL compute Normalized_Key so that, for any input string, applying Normalized_Key to its own result returns that result unchanged (idempotence).
8. THE Rule_Engine SHALL store as ecuName only values that are ECUDico entries (invariant).
9. THE Rule_Engine SHALL build the Pattern_Key of an ASK_HUMAN case of criterion 5 from the field name and the reason only, so that all rows of a Run with an Unreadable_Value ecuName and no Label_ECU share one Pattern_Key and one Human_Question.

### Requirement 11: R6 VehicleModel

**User Story:** As a data owner, I want vehicle models repaired against the VehicleModelsDico, so that each vehicle has one valid model.

#### Acceptance Criteria

1. WHEN the Rule_Engine processes the Vehmodel field of a row, THE Rule_Engine SHALL compare the Normalized_Key of the raw value with the Normalized_Key of each VehicleModelsDico entry.
2. WHEN the Normalized_Key of Vehmodel equals the Normalized_Key of a VehicleModelsDico entry, THE Rule_Engine SHALL store that entry's canonical value and record a Field_Decision with rule_id R6, before set to the raw value, after set to the canonical value, and Action MIGRATE if the raw value is identical character for character to the canonical value, CORRECT otherwise (e.g. `SCENICETECH` -> `SCENIC E-TECH`, `AR KANA` -> `ARKANA`, `TRAFIC-` -> `TRAFIC`).
3. IF the Normalized_Key of Vehmodel matches no VehicleModelsDico entry and the VIN_Model_Index holds exactly one model for the normalized VIN of the row, THEN THE Rule_Engine SHALL store that model and record CORRECT with rule_id R6, before set to the raw value and after set to that model.
4. IF Vehmodel is an Unreadable_Value, the VIN_Model_Index holds zero models or two or more models for the normalized VIN of the row, and the Legacy_B_Model_Index holds a model for that VIN, THEN THE Rule_Engine SHALL store the Legacy B model and record a Field_Decision with Action CORRECT, rule_id R6, before set to the raw value, after set to the Legacy B model, and an explanation naming Legacy B as the source.
5. IF Vehmodel is an Unknown_Variant and the VIN_Model_Index holds zero models or two or more models for the normalized VIN of the row, THEN THE Rule_Engine SHALL route the field to the Resolver_Agent path of Requirement 24.
6. THE Migration_Pipeline SHALL store only VehicleModelsDico values as VehicleModel, for every raw Vehmodel string (invariant).
7. IF Vehmodel is an Unreadable_Value, the VIN_Model_Index holds zero models or two or more models for the normalized VIN of the row, and the Legacy_B_Model_Index holds no model for that VIN, THEN THE Rule_Engine SHALL record ASK_HUMAN with rule_id R6.
8. THE Rule_Engine SHALL build the Pattern_Key of an ASK_HUMAN case of criterion 7 from the field name and the reason only, so that all such rows of a Run share one Pattern_Key and one Human_Question.
9. WHERE the Legacy_B_Lookup is enabled in Rule_Config or not set in Rule_Config, WHEN a Run starts, THE Migration_Pipeline SHALL fetch the Legacy B model of each distinct normalized VIN of the Run through the API_Agent_Legacy_B before the Rule_Engine processes the first row, and SHALL pass the results to the Rule_Engine as the read-only Legacy_B_Model_Index, with no entry for a VIN that has no Legacy B record.
10. WHERE the Legacy_B_Lookup is disabled in Rule_Config, THE Migration_Pipeline SHALL pass an empty Legacy_B_Model_Index to the Rule_Engine and send zero requests to the API_Agent_Legacy_B during the Run.
11. IF the API_Agent_Legacy_B fails to return a result for at least one normalized VIN of the Run at Run start, a VIN without a Legacy B record counting as a result and not as a failure, THEN THE Migration_Pipeline SHALL pass an empty Legacy_B_Model_Index to the Rule_Engine, record the Legacy_B_Lookup status as failed in the Run metrics, and continue the Run.

### Requirement 12: R7 Cardinalities

**User Story:** As a data owner, I want cardinality conflicts sent to a human, so that the target model constraints hold.

#### Acceptance Criteria

1. IF the VIN_Model_Index, built over all rows of the Run, holds two or more distinct canonical VehicleModel values for a normalized VIN, THEN THE Rule_Engine SHALL record ASK_HUMAN with rule_id R7 on every row of the Run whose normalized VIN equals that VIN.
2. IF two or more rows of the Run have the same trimmed diagnosticFieId value, that value is not an Unreadable_Value, and the rows have different decided Operation_Identity values, THEN THE Rule_Engine SHALL record ASK_HUMAN with rule_id R7 on every row of the Run carrying that diagnosticFieId value.
3. WHEN the Target_Repository loads rows with Row_Outcome MIGRATE or CORRECT that share a diagnosticOpID and have the same Operation_Identity, THE Target_Repository SHALL store exactly one DiagnosticOperation and exactly one DiagValueResult for those rows.
4. THE Rule_Engine SHALL give each of criteria 1, 2 and 5 its own reason and SHALL build the Pattern_Key of each resulting ASK_HUMAN case from the field name and that reason only, so that all rows of a Run that meet the same criterion share one Pattern_Key and one Human_Question.
5. IF the Legacy_B_Model_Index holds a canonical VehicleModel for the normalized VIN of a row and Requirement 11 criterion 2 or 3 decided a different canonical VehicleModel for that row, THEN THE Rule_Engine SHALL record ASK_HUMAN with rule_id R7 on that row.

### Requirement 13: R8 Empty fields [CONFIRMED]

**User Story:** As a data owner, I want a defined policy for empty fields, so that no row is loaded with missing mandatory data.

#### Acceptance Criteria

1. IF originTool, toolVersion or Creation_date of a row is an Unreadable_Value, THEN THE Rule_Engine SHALL record, for each such field, a Field_Decision with Action ASK_HUMAN, rule_id R8, before equal to the raw value and no after value.
2. THE Rule_Engine SHALL build the R8 Pattern_Key from the field name and the reason "unreadable" only, so that all rows of a Run with the same unreadable field share one Pattern_Key and one Human_Question.
3. IF diagnosticFieId is an Unreadable_Value and no component of the row's Operation_Identity has a Field_Decision with Action ASK_HUMAN, THEN THE Rule_Engine SHALL set diagnosticOpID to a UUID v5 computed over the decided Operation_Identity values, flag the id as generated, and record a Field_Decision with Action CORRECT, rule_id R8, before equal to the raw value and after equal to the generated id (A6).
4. IF diagnosticFieId is an Unreadable_Value and at least one component of the row's Operation_Identity has a Field_Decision with Action ASK_HUMAN, THEN THE Rule_Engine SHALL not generate diagnosticOpID for that row in that Run.
5. WHEN the Rule_Engine generates a diagnosticOpID, THE Rule_Engine SHALL return the same id for the same Operation_Identity values in the same Run or in another Run on the same input, and different ids for different Operation_Identity values.

### Requirement 14: Date, tool version and operation id mapping

**User Story:** As a data owner, I want the remaining columns mapped as specified, so that every target attribute has a defined source.

#### Acceptance Criteria

1. WHEN the trimmed Creation_date matches `dd/mm/yyyy hh:mm` (two-digit day, two-digit month, four-digit year, one space, two-digit hour, colon, two-digit minute) and denotes a valid calendar date-time, THE Rule_Engine SHALL store that date-time at minute precision as creationDate and record a Field_Decision with Action TRANSFORM.
2. THE Rule_Engine SHALL treat a Creation_date as a valid calendar date-time only if the month is 01-12, the day is between 01 and the last day of that month in that year (29 February only in leap years), the hour is 00-23 and the minute is 00-59.
3. IF Creation_date is not an Unreadable_Value and, after trimming, does not match `dd/mm/yyyy hh:mm` or does not denote a valid calendar date-time, THEN THE Rule_Engine SHALL route the field to the Resolver_Agent path of Requirement 24.
4. WHEN a valid date-time at minute precision, including 29 February of a leap year and 31 December at 23:59, is formatted as `dd/mm/yyyy hh:mm` and then parsed, THE Rule_Engine SHALL return the original date-time.
5. WHEN toolVersion is not an Unreadable_Value, THE Rule_Engine SHALL store the value unchanged as DiagToolVersion and record a Field_Decision with Action MIGRATE.
6. WHEN diagnosticFieId is not an Unreadable_Value, THE Rule_Engine SHALL store the value unchanged as diagnosticOpID and record a Field_Decision with Action MIGRATE.

### Requirement 15: Final checks

**User Story:** As a data owner, I want a last check before load, so that no row bypasses the four mandatory controls.

#### Acceptance Criteria

1. WHEN the Rule_Engine completes the Field_Decisions of a row that has no REJECT and no ASK_HUMAN Field_Decision, THE Rule_Engine SHALL run all four final checks of `migration-rules.md` on the final values of the row, whether each value was decided by a rule, the Resolver_Agent, the Resolution_Cache or a Human_Decision.
2. IF one or more final checks fail, THEN THE Rule_Engine SHALL record one REJECT Field_Decision per failed check, each with a rule_id naming that check and an explanation stating the failing value.
3. THE Migration_Pipeline SHALL pass a row to the Target_Repository only when all four final checks of that row passed in the current Run.

### Requirement 16: Action classification and row outcome

**User Story:** As an auditor, I want each row to have one outcome derived from its field decisions, so that the counts are exact and explainable.

#### Acceptance Criteria

1. THE Rule_Engine SHALL label each Field_Decision with exactly one Action out of MIGRATE, TRANSFORM, CORRECT, REJECT and ASK_HUMAN.
2. THE Rule_Engine SHALL label TRANSFORM a Field_Decision that applies one of these normal mappings: the R2 mapping, the mapping of a Format_2 DID to its ID000x code, the use of the VIN as the ID0001 value, or the date conversion.
3. THE Rule_Engine SHALL label CORRECT a Field_Decision that applies one of these anomaly repairs: a dictionary normalization whose result differs from the raw value, the deduction of a missing value, or a value chosen by the Resolver_Agent (directly or through the Resolution_Cache) or by a Human_Decision.
4. WHEN the Rule_Engine has produced all Field_Decisions of a row, THE Rule_Engine SHALL set the Row_Outcome to the highest-priority Action among them, using the order REJECT > ASK_HUMAN > CORRECT > MIGRATE, with TRANSFORM counted as MIGRATE.
5. THE Rule_Engine SHALL assign exactly one Row_Outcome to each source row that the Ingestion_Module reads in a Run.
6. THE Rule_Engine SHALL return the same Row_Outcome for every permutation of the Field_Decisions of a row (confluence).
7. THE Rule_Engine SHALL label MIGRATE each Field_Decision that meets none of the TRANSFORM, CORRECT, REJECT or ASK_HUMAN conditions.
8. IF a Field_Decision applies both a normal mapping from criterion 2 and an anomaly repair from criterion 3, THEN THE Rule_Engine SHALL label that Field_Decision CORRECT.
9. WHEN a Run completes, THE Report_Generator SHALL report one count for each Row_Outcome value (MIGRATE, CORRECT, REJECT, ASK_HUMAN), and these four counts SHALL sum to the number of source rows that the Ingestion_Module read in that Run.

### Requirement 17: Decision log

**User Story:** As an auditor, I want every decision logged with an explanation, so that 100% of the migration is traceable.

#### Acceptance Criteria

1. THE Decision_Logger SHALL write exactly one Decision_Record per Field_Decision with the fields run_id, source_row (the Source_Row_Id), field, action, rule_id, before, after, decided_by (one of rule, llm, human), confidence, explanation, tokens_used and timestamp (UTC, ISO 8601).
2. THE Decision_Logger SHALL write exactly one row-level Decision_Record per source row per Run, with field set to the reserved value `_row` and action set to the Row_Outcome of that row.
3. THE Decision_Logger SHALL store in `before` of every field-level Decision_Record the raw source value exactly as read by the Ingestion_Module, before any trimming or normalization.
4. WHEN the Decision_Logger writes a Decision_Record with decided_by rule, THE Decision_Logger SHALL set explanation to a non-empty text generated from the explanation template of its rule_id, leave confidence empty and set tokens_used to 0.
5. WHEN the Decision_Logger writes a Decision_Record with decided_by llm, THE Decision_Logger SHALL store the Resolver_Agent confidence (0.0 to 1.0) in confidence and its justification in explanation, and set tokens_used to the input plus output tokens reported by the LLM_Client for the call, or to 0 when the proposal comes from the Resolution_Cache.
6. WHEN the Decision_Logger writes a Decision_Record with decided_by human, THE Decision_Logger SHALL store the Pattern_Key and the chosen option of the Human_Decision in explanation, leave confidence empty and set tokens_used to 0.
7. THE Decision_Logger SHALL store Decision_Records in a database table queryable by run_id, and leave the Decision_Records of earlier Runs unchanged.
8. WHEN an export of a Run is requested for a run_id, THE Decision_Logger SHALL produce one JSON file and one UTF-8 CSV file with a header row, each containing all Decision_Records of that run_id and no other, with the fields of criterion 1.
9. WHEN reference Decision_Records, including one with an empty confidence and one with non-ASCII characters, are exported to JSON then imported, and exported to CSV then imported, THE Decision_Logger SHALL return records equal field by field to the original Decision_Records.
10. IF writing a Decision_Record fails, THEN THE Migration_Pipeline SHALL stop the Run with an error indicating the run_id and Source_Row_Id concerned.
11. IF an export is requested for a run_id that has no Decision_Records, THEN THE Decision_Logger SHALL return an error indicating the unknown run_id and produce no file.

### Requirement 18: Idempotent load

**User Story:** As a migration engineer, I want re-runs to produce the same target state, so that the migration can be repeated safely.

#### Acceptance Criteria

1. WHEN the Migration_Pipeline loads a row with Row_Outcome MIGRATE or CORRECT, THE Target_Repository SHALL upsert the DiagnosticOperation keyed by diagnosticOpID with its DiagValueResult, Vehicle, DiagnozedECU, Vehicle-DiagnozedECU link and DiagToolsVersions. An existing record is updated, and no second record is inserted.
2. IF a row has Row_Outcome REJECT or ASK_HUMAN, THEN THE Target_Repository SHALL insert or update no Target_DB record from that row.
3. THE Migration_Pipeline SHALL load rows in batches of N rows, where N is the batch size set in Rule_Config as a positive integer and the last batch holds the remaining rows. It SHALL commit each batch as one transaction, so that either all rows of the batch are stored or none are.
4. IF a batch commit fails, THEN THE Target_Repository SHALL roll back the whole batch, retry each row of that batch once in its own transaction, and then continue with the next batch.
5. IF the single-row retry of a row fails, THEN THE Migration_Pipeline SHALL set the Row_Outcome of that row to REJECT and record, through the Decision_Logger, a Decision_Record with Action REJECT, rule_id LOAD and an explanation indicating the load failure.
6. WHEN a Run is repeated on the same source file with the same Rule_Config, Human_Decisions and Resolution_Cache, THE Target_DB SHALL hold the same number of records in each target table, with the same business values and at most one DiagnosticOperation per diagnosticOpID, ignoring run metadata (run_id and load timestamps).
7. WHEN a Run follows new Human_Decisions, THE Target_Repository SHALL store the rows whose Row_Outcome changed from ASK_HUMAN to MIGRATE or CORRECT and keep every previously loaded DiagnosticOperation, with at most one DiagnosticOperation per diagnosticOpID.

### Requirement 19: End-of-run report and measurement

**User Story:** As a jury member, I want an exact end-of-run report with time and token figures, so that accuracy and cost are visible.

#### Acceptance Criteria

1. WHEN a Run ends, THE Report_Generator SHALL produce the report in the exact format of `product.md`, filled with the number of source rows read by the Ingestion_Module in the Run (analysed rows), the count of each Row_Outcome (MIGRATE, CORRECT, REJECT, ASK_HUMAN), the traced percentage, the total tokens (input plus output), the estimated cost in euros and the processing time in seconds.
2. THE Report_Generator SHALL report Row_Outcome counts whose sum equals the analysed row count of the same Run.
3. THE Report_Generator SHALL compute the traced percentage as 100 × the number of source rows of the Run with a row-level Decision_Record, divided by the number of source rows of the Run.
4. THE Report_Generator SHALL compute the estimated cost in euros as (input tokens of all LLM_Client calls of the Run × price per input token) + (output tokens of all LLM_Client calls of the Run × price per output token), using the prices in Rule_Config, giving 0 when the Run made no LLM_Client call.
5. WHEN a Run ends, THE Report_Generator SHALL persist the Run metrics under the run_id: total processing time in seconds from Run start to Run end, processing time in seconds for each Migration_Pipeline stage (ingestion, rules, AI resolution, human routing, load, reporting), input tokens, output tokens, LLM_Client call count, distinct Pattern_Key count, Resolution_Cache hit rate, batch size from Rule_Config, OUT_OF_SCOPE reject count, Degraded_Mode flag and Legacy_B_Lookup status (enabled, disabled or failed).
6. THE Report_Generator SHALL persist the same values in the Run metrics as in the report for processing time, input plus output tokens and Row_Outcome counts of the same run_id.
7. THE Report_Generator SHALL compute the Resolution_Cache hit rate as the number of Resolution_Cache lookups in the Run that returned a cached resolution divided by the total number of Resolution_Cache lookups in the Run, and SHALL record 0 when the Run made no Resolution_Cache lookup.

### Requirement 20: Grouped human questions

**User Story:** As a business expert, I want one question per broad pattern, so that I answer about fifteen questions per Run instead of hundreds of rows.

#### Acceptance Criteria

1. IF no rule, Human_Decision, cached resolution or LLM proposal with confidence at or above the Confidence_Threshold decides a field of a row, THEN THE Migration_Pipeline SHALL record for that field a Field_Decision with Action ASK_HUMAN.
2. WHEN the Migration_Pipeline records a Field_Decision with Action ASK_HUMAN, THE Migration_Pipeline SHALL add the row to the HITL_Queue under the Pattern_Key of that field case, once per distinct Pattern_Key, so that a row with undecided fields under 2 Pattern_Keys appears in 2 Human_Questions.
3. THE HITL_Queue SHALL hold exactly one Human_Question per distinct Pattern_Key per Run, whatever the number of rows in the group.
4. THE HITL_Queue SHALL include in each Human_Question: the Pattern_Key; the row count, defined as the number of distinct Source_Row_Ids in the group; the first min(row count, N) rows of the group in source file order as examples, where N is set in Rule_Config (default 5); the undecided field; the reason no rule or agent could decide, including the LLM proposal and its confidence, or an indication that no LLM proposal was obtained; and the options apply a value to the group, reject the group, and case by case, plus, for the R7 shared-diagnosticOpID Pattern_Key (Requirement 12.2), the option generate a deterministic diagnosticOpID (UUID v5 over the Operation_Identity) for each operation of the group.
5. WHEN a Run ends, THE HITL_Queue SHALL hold for that Run a number of Human_Questions equal to the number of distinct Pattern_Keys among that Run's Field_Decisions with Action ASK_HUMAN, with row counts summing to the number of distinct (Source_Row_Id, Pattern_Key) pairs among those Field_Decisions.

### Requirement 21: Human answers and persisted decisions

**User Story:** As a business expert, I want my answers reused as rules, so that the next Run asks fewer questions.

#### Acceptance Criteria

1. WHEN a human chooses "apply a value to the group" on an open Human_Question, THE HITL_Queue SHALL validate the value against the pattern or dictionary rule of the field before storing anything.
2. WHEN the human value passes validation, THE HITL_Queue SHALL store one Human_Decision holding the Pattern_Key, the value and the answer timestamp in the Human_Decision_Store, and close the Human_Question.
3. IF a human value fails validation, THEN THE HITL_Queue SHALL refuse the answer, state the failed rule, store no Human_Decision, and keep the Human_Question open.
4. WHEN a human chooses "reject the group" on an open Human_Question, THE HITL_Queue SHALL store one REJECT Human_Decision holding the Pattern_Key and the answer timestamp in the Human_Decision_Store, and close the Human_Question.
5. WHEN a human chooses "case by case" on an open Human_Question whose Pattern_Key is not extended with a Source_Row_Id, THE HITL_Queue SHALL close that group Human_Question without storing a Human_Decision for its Pattern_Key, and create exactly one Human_Question per row of the group (a group of N rows yields N Human_Questions), each with the group Pattern_Key extended with the Source_Row_Id of its row.
6. IF a human answers a closed Human_Question, chooses "case by case" on a Human_Question whose Pattern_Key is extended with a Source_Row_Id, or chooses "generate a deterministic diagnosticOpID" on a Human_Question other than the one of the R7 shared-diagnosticOpID Pattern_Key, THEN THE HITL_Queue SHALL refuse the answer, store no new Human_Decision, leave any stored Human_Decision for that Pattern_Key unchanged, and leave the Human_Question open or closed as it was.
7. THE Human_Decision_Store SHALL hold at most one Human_Decision per Pattern_Key and persist each Human_Decision (Pattern_Key, value, REJECT or generate option, answer timestamp) independently of the Run_Workflow checkpoints, so that it is read back unchanged after a process restart, after deletion of the Run_Workflow checkpoints, and in later Runs.
8. WHEN the Rule_Engine evaluates, in a later Run, a field whose Pattern_Key, or whose Pattern_Key extended with the Source_Row_Id of its row, has a value or REJECT Human_Decision in the Human_Decision_Store, THE Rule_Engine SHALL record a Field_Decision with decided_by human and the rule_id of the case, with Action CORRECT and the stored value as after for a value Human_Decision, or Action REJECT for a REJECT Human_Decision. When both keys have a Human_Decision, the Rule_Engine SHALL use the one of the extended Pattern_Key. It SHALL do this without calling the Resolver_Agent or the LLM_Client and without routing the case to the HITL_Queue.
9. WHEN a Run executes on the same source file as an earlier Run after Human_Decisions were stored, THE HITL_Queue SHALL create zero Human_Questions for a case whose Pattern_Key, or whose Pattern_Key extended with the Source_Row_Id of its row, has a Human_Decision.
10. WHEN a human chooses "generate a deterministic diagnosticOpID" on the open Human_Question of the R7 shared-diagnosticOpID Pattern_Key (Requirement 12.2), THE HITL_Queue SHALL store one Human_Decision holding the Pattern_Key, the generate option and the answer timestamp in the Human_Decision_Store, and close the Human_Question.
11. WHEN the Rule_Engine evaluates, in a later Run, a row whose R7 shared-diagnosticOpID Pattern_Key has a generate Human_Decision and whose Operation_Identity has no component with a Field_Decision with Action ASK_HUMAN, THE Rule_Engine SHALL set the diagnosticOpID of that row to the UUID v5 of Requirement 13.3 computed over the decided Operation_Identity values, flag the id as generated, and record a Field_Decision with Action CORRECT, rule_id R7, decided_by human, before equal to the raw diagnosticFieId value and after equal to the generated id.
12. IF, in a later Run, a row's R7 shared-diagnosticOpID Pattern_Key has a generate Human_Decision and a component of its Operation_Identity has a Field_Decision with Action ASK_HUMAN, THEN THE Rule_Engine SHALL generate no diagnosticOpID for that row and route no case of the R7 shared-diagnosticOpID Pattern_Key of that row to the HITL_Queue.

### Requirement 22: LLM client

**User Story:** As a tech lead, I want a single metered local LLM entry point, so that token use is counted without requiring a paid API key.

#### Acceptance Criteria

1. THE LLM_Client SHALL be the only component that calls the LLM provider.
2. WHEN a component sends a prompt with a run_id and the provider responds within the per-call timeout configured in Rule_Config, THE LLM_Client SHALL return a success result containing the response text, the input token count and the output token count reported by the provider.
3. WHEN a provider call for a run_id ends in success or failure, THE LLM_Client SHALL increment the call count of that run_id by 1 and add the input and output token counts reported by the provider to the totals of that run_id, adding 0 for any count the provider did not report.
4. THE LLM_Client SHALL call the configured Ollama base URL and model without requiring an API key.
5. IF the provider returns an error or does not respond within the per-call timeout configured in Rule_Config, THEN THE LLM_Client SHALL return a failure result whose error type distinguishes a provider error from a timeout, without raising an exception to the caller.
6. IF Ollama is unreachable or the configured model is not installed when a Run starts, THEN THE Migration_Pipeline SHALL enter Degraded_Mode for that Run and make zero calls to the LLM chat endpoint.
7. WHEN a component requests the totals of a run_id, THE LLM_Client SHALL return the accumulated input tokens, output tokens and call count of that run_id, with 0 for each value when the run_id has no recorded calls.

### Requirement 23: Schema discovery agent

**User Story:** As a migration engineer, I want the source schema discovered once and validated by a human, so that format changes are detected without manual mapping.

#### Acceptance Criteria

1. WHEN schema discovery is requested, THE API_Agent_Legacy SHALL send to the LLM_Client only the header line of the source file and the first N data rows. N is the sample size from Rule_Config (an integer of at least 1, default 20). IF the file holds fewer than N data rows, THEN THE API_Agent_Legacy SHALL send all of them.
2. WHEN the LLM_Client returns a schema discovery response, THE API_Agent_Legacy SHALL return a proposal containing a proposed Column_Mapping with one entry per header column name (including `diagnosticFieId`), the detected formats Format_1 and Format_2, and the `ecuCode` criterion that distinguishes them.
3. IF the proposed Column_Mapping maps a target attribute to zero or several source columns, THEN THE API_Agent_Legacy SHALL flag the attribute as a conflict in the proposal, list the source columns involved, and reject human validation of the proposal while any conflict remains.
4. WHEN a human validates a proposal without conflicts, THE API_Agent_Legacy SHALL persist it as the validated Column_Mapping, replacing any previously validated Column_Mapping.
5. WHILE in Degraded_Mode, WHEN schema discovery is requested, THE API_Agent_Legacy SHALL make zero LLM_Client calls and propose the reference Column_Mapping from Rule_Config for human validation, with conflicts flagged as in criterion 3.
6. WHILE no validated Column_Mapping exists, WHEN a Run starts, THE Migration_Pipeline SHALL process zero rows and state in the end-of-run report that Column_Mapping validation is pending.
7. WHEN a Run starts on a source file whose header column names exactly match the source column names of the validated Column_Mapping (case-sensitive, order ignored), THE Migration_Pipeline SHALL apply the validated Column_Mapping and make zero schema discovery calls to the LLM_Client.
8. IF, at Run start, the source file header contains a column name that is absent from the validated Column_Mapping, or lacks one of its source column names, THEN THE Migration_Pipeline SHALL process zero rows and report in the end-of-run report the added and missing column names and that a new schema discovery and validation is required.
9. IF the LLM_Client call fails, or returns a response from which no Column_Mapping can be read, THEN THE API_Agent_Legacy SHALL propose the reference Column_Mapping from Rule_Config for human validation and mark the proposal as the reference fallback.

### Requirement 24: Resolver agent

**User Story:** As a migration engineer, I want unknown variants resolved per pattern with a confidence gate, so that AI cost stays low and doubtful cases reach a human.

#### Acceptance Criteria

1. WHEN a field is an Unknown_Variant and its Pattern_Key has no Human_Decision and no cached resolution, THE Migration_Pipeline SHALL send the Pattern_Key to the Resolver_Agent exactly once per Run for all rows sharing that Pattern_Key.
2. WHEN the Resolver_Agent receives a Pattern_Key, THE Resolver_Agent SHALL send to the LLM_Client one request containing only the field name, the reason and the normalized raw value of the Pattern_Key and the allowed target values, and no other row data.
3. WHEN the LLM_Client returns a response, THE Resolver_Agent SHALL return a proposal made of a target value or a reject, a confidence between 0 and 1 inclusive, and a non-empty justification.
4. IF the LLM response cannot be parsed, lacks the value, the confidence or the justification, has a confidence outside 0 to 1, or proposes a value outside the allowed target values, THEN THE Resolver_Agent SHALL set the proposal confidence to 0 and the justification to the failure reason.
5. WHEN a proposal has a confidence greater than or equal to the Confidence_Threshold, THE Migration_Pipeline SHALL apply the proposed target value, or Action REJECT for a reject, to all rows of the Pattern_Key with decided_by llm, the proposal confidence and the justification as explanation, and SHALL store the proposal in the Resolution_Cache.
6. WHEN a proposal has a confidence below the Confidence_Threshold, THE Migration_Pipeline SHALL record ASK_HUMAN for all rows of the Pattern_Key, show the proposed value, confidence and justification as a suggestion in the Human_Question of the Pattern_Key, and SHALL NOT store the proposal in the Resolution_Cache.
7. WHEN a Pattern_Key has a cached resolution and no Human_Decision, THE Migration_Pipeline SHALL apply the cached resolution to all rows of the Pattern_Key without any LLM_Client call and with tokens_used 0.
8. THE Resolution_Cache SHALL retain every stored proposal across process restarts and later Runs.
9. THE Migration_Pipeline SHALL make in each Run a number of Resolver_Agent LLM_Client calls at most equal to the number of distinct Unknown_Variant Pattern_Keys that have no Human_Decision and no cached resolution at the start of the Run.
10. THE Migration_Pipeline SHALL build the Pattern_Key of an Unknown_Variant from the field name, the reason and the normalized raw value, so that each distinct normalized raw value of a field reaches the Resolver_Agent at most once per Run.

### Requirement 25: Degraded mode

**User Story:** As a team, I want the pipeline to complete without an LLM, so that the demo and the migration never depend on the provider.

#### Acceptance Criteria

1. WHEN Ollama is unreachable or the configured model is not installed when a Run starts, THE Migration_Pipeline SHALL execute the whole Run in Degraded_Mode and make zero calls to the LLM chat endpoint.
2. WHILE in Degraded_Mode, THE Migration_Pipeline SHALL apply the Rule_Engine, the Human_Decisions of the Human_Decision_Store and the Resolution_Cache entries with the same Field_Decisions as outside Degraded_Mode, and complete the Run with exactly one Row_Outcome per source row and an end-of-run report from the Report_Generator.
3. WHILE in Degraded_Mode, WHEN a field holds an Unknown_Variant, THE Migration_Pipeline SHALL record a Field_Decision with Action ASK_HUMAN and an explanation indicating Degraded_Mode.
4. IF an LLM_Client call made for a Pattern_Key returns an error, times out, or returns a response that cannot be parsed into a mapping, a confidence and a justification, THEN THE Migration_Pipeline SHALL record a Field_Decision with Action ASK_HUMAN for every field with that Pattern_Key, write no Resolution_Cache entry for that Pattern_Key, and continue the Run with the remaining rows.
5. IF no Column_Mapping proposed by the API_Agent_Legacy is available because of Degraded_Mode or a failed LLM_Client call, THEN THE Migration_Pipeline SHALL use the Column_Mapping reference held in Rule_Config.

### Requirement 26: Orchestrator routing

**User Story:** As the client application, I want one stable API whatever the phase, so that the migration is invisible to me.

#### Acceptance Criteria

1. THE Orchestrator SHALL expose the client routes (diagnostics by VIN, model reference by VIN) with the same response schema in all four phases, including the same not-found response for a VIN that has no record.
2. WHILE the current phase is 1, 2 or 3, THE Orchestrator SHALL serve diagnostics reads from the Legacy_A_API through the API_Agent_Legacy.
3. WHILE the current phase is 4, THE Orchestrator SHALL serve diagnostics reads from the Target_API and send zero requests to the Legacy_A_API.
4. THE Orchestrator SHALL serve model reference reads from the Legacy_B_API through the API_Agent_Legacy_B, in the Common_Vocabulary, in all four phases.
5. WHEN the Phase_Controller commits a phase transition, forward or rollback, THE Orchestrator SHALL route every request received after the transition to the backends of the new phase and complete every request received before the transition on the backend where the request started.
6. THE Target_API SHALL expose read routes for diagnostics by VIN in the Orchestrator response schema.
7. IF the backend selected for a client request returns an error or is unreachable, THEN THE Orchestrator SHALL return an error response in the Orchestrator response schema indicating that the backend is unavailable, and SHALL NOT send the request to any other backend.
8. WHEN the Phase_Controller commits a rollback from phase 4 to phase 3, THE Orchestrator SHALL serve every diagnostics read received after the rollback from the Legacy_A_API through the API_Agent_Legacy, with the response schema of criterion 1.

### Requirement 27: Phase controller

**User Story:** As a migration lead, I want controlled phase transitions with a one-phase rollback, so that the Strangler Fig migration proceeds in order and can step back without outage.

#### Acceptance Criteria

1. WHEN the Phase_Controller starts, THE Phase_Controller SHALL set the current phase to the last persisted phase, or to phase 1 if no phase has been persisted.
2. WHEN a transition from phase 1, 2 or 3 to the current phase plus 1 is requested, THE Phase_Controller SHALL persist that phase, set it as the current phase, and record one transition log entry containing the previous phase, the new phase and the timestamp of the switch, subject to the gate of Requirement 29 for phase 4.
3. IF a requested phase is neither a forward transition allowed by criterion 2 nor a rollback allowed by criterion 7, including a skipped phase, a move back by more than one phase, a move back to phase 1, a move back not requested by a human operator, the current phase or a phase outside 1-4, THEN THE Phase_Controller SHALL refuse the transition, keep the current phase unchanged, record no transition log entry and return the reason for the refusal.
4. WHILE the current phase is 2, WHEN a Run executes its load step, THE Migration_Pipeline SHALL load the Target_DB through the Target_Repository in batches of at most the batch size set in Rule_Config.
5. WHILE the current phase is 2, THE Orchestrator SHALL route diagnostic read requests to the Legacy_A_API.
6. IF persisting a new phase fails, THEN THE Phase_Controller SHALL keep the previous phase as the current phase, record no transition log entry and return an error indicating the persistence failure.
7. WHEN a human operator explicitly requests a rollback from phase 4 to phase 3 or from phase 3 to phase 2, THE Phase_Controller SHALL persist that phase, set it as the current phase, and record one transition log entry containing the previous phase, the new phase, the timestamp of the switch and a rollback marker, subject to criterion 8 for a rollback from phase 4 to phase 3.
8. IF a human operator requests a rollback from phase 4 to phase 3 and the Legacy_A_API is unreachable, THEN THE Phase_Controller SHALL refuse the rollback, keep phase 4 as the current phase, record no transition log entry and return a reason indicating that the Legacy_A_API is unavailable.

### Requirement 28: Reconciliation (phase 3)

**User Story:** As a migration lead, I want legacy and target compared, so that I know how aligned the new system is before cutover.

#### Acceptance Criteria

1. WHILE the current phase is 3, WHEN the Orchestrator serves a diagnostics read for a VIN, THE Reconciler SHALL compare the expected target records derived by the Rule_Engine from the Legacy A rows of that VIN with Row_Outcome MIGRATE or CORRECT against the Target_DB records of that VIN, matching records by Operation_Identity.
2. THE Reconciler SHALL count as one Gap each expected record that has no Target_DB record with the same Operation_Identity, and each expected record with at least one target attribute value different from the matched Target_DB record.
3. WHEN a Gap is found, THE Reconciler SHALL request exactly one reload of the source row of the Gap through the Migration_Pipeline and then compare that expected record with the Target_DB again.
4. IF the expected record is still missing or differing after the reload, or the reload fails, THEN THE Reconciler SHALL add one Human_Question for the Gap to the HITL_Queue, and SHALL NOT add a second Human_Question when the HITL_Queue already holds one with the same Pattern_Key.
5. WHEN a comparison completes, including any reload and re-comparison, THE Reconciler SHALL publish the compared record count, the Gap count remaining after reload and the Aligned_Percentage of that comparison rounded to one decimal place, or "not applicable" when zero records were compared.
6. WHEN a Control_Sample reconciliation is requested, THE Reconciler SHALL compare every expected record of every VIN of the Control_Sample and report the compared record count, the Gap count and the Aligned_Percentage over the whole Control_Sample.
7. IF the Legacy A rows or the Target_DB records of a VIN cannot be read during a comparison, THEN THE Reconciler SHALL publish that comparison as not performed for that VIN, count no Gap for it and exclude it from the Aligned_Percentage.

### Requirement 29: Cutover gate (phase 4)

**User Story:** As a migration lead, I want a human gate before cutover, so that Legacy A is retired only when the target is complete and aligned.

#### Acceptance Criteria

1. WHEN a transition to phase 4 is requested, THE Phase_Controller SHALL evaluate two checks: (a) the HITL_Queue holds zero open Human_Questions, an open Human_Question being one that is not closed, and a closed Human_Question being one answered with "apply a value to the group", "reject the group" or "generate a deterministic diagnosticOpID", or a group Human_Question closed by "case by case", whose per-row Human_Questions (Requirement 21.5) then count individually; (b) the most recent Reconciler comparison over the Control_Sample reports zero Gaps.
2. IF either check fails, THEN THE Phase_Controller SHALL refuse the transition, keep the current phase unchanged, and return each failing check with its count (number of open Human_Questions for check (a), number of Gaps for check (b)).
3. IF no Reconciler comparison over the Control_Sample exists, THEN THE Phase_Controller SHALL treat check (b) as failed and return it as "no reconciliation available" instead of a Gap count.
4. WHEN both checks pass, THE Phase_Controller SHALL keep the current phase unchanged and return that the transition is pending explicit approval by a human operator.
5. WHEN a human operator explicitly approves a pending transition, THE Phase_Controller SHALL re-evaluate both checks of criterion 1 and switch to phase 4 only if both still pass, applying criterion 2 if either fails.
6. IF a human operator rejects a pending transition, THEN THE Phase_Controller SHALL cancel the pending transition and keep the current phase unchanged.

### Requirement 30: Legacy B

**User Story:** As a Legacy B consumer, I want the Legacy B service unchanged and its data translated into the Common_Vocabulary, so that I see no difference during migration and the migration can use the Legacy B models.

#### Acceptance Criteria

1. THE Legacy_B_API SHALL return each Legacy B record in the Legacy B format: `CHASSIS_NO` (VIN), `MODELE` (model name in mixed case, meaning at least one uppercase and one lowercase letter, e.g. "Megane E-Tech") and `DATE_MAJ` (update date as yyyymmdd).
2. WHEN a client sends a GET request for a VIN equal to the `CHASSIS_NO` of a Legacy B record, THE Legacy_B_API SHALL respond with that Legacy B record.
3. IF a client sends a GET request for a VIN equal to no `CHASSIS_NO` of the Legacy B data, THEN THE Legacy_B_API SHALL respond with HTTP 404 and no Legacy B record in the response body.
4. IF a client sends a request with a method other than GET to any Legacy_B_API route, THEN THE Legacy_B_API SHALL respond with HTTP 405 and leave every stored Legacy B record unchanged.
5. THE Legacy_B_API SHALL return the same HTTP status and the same response body for the same VIN in each Phase_Controller phase (1, 2, 3 and 4) and before and after each Run.
6. WHEN the Legacy_B_Simulator generates the Legacy B data, THE Legacy_B_Simulator SHALL create exactly one Legacy B record per distinct R1-normalized VIN of the Legacy A source file, with that VIN as `CHASSIS_NO`, using a seed set in Rule_Config, so that two generations with the same source file and seed produce identical Legacy B data and an identical Legacy_B_Control_File.
7. THE Legacy_B_Simulator SHALL set, for every VIN whose Legacy A rows match exactly one VehicleModelsDico value through Normalized_Key and that carries no planted conflict, a mixed-case `MODELE` that the API_Agent_Legacy_B translates to that value.
8. THE Legacy_B_Simulator SHALL plant the number of model conflicts set in Rule_Config (default 15), each on a distinct VIN whose Legacy A rows match exactly one VehicleModelsDico value, with a `MODELE` that translates to a different VehicleModelsDico value, and SHALL list each planted conflict in the Legacy_B_Control_File with the VIN, the VehicleModelsDico value matched by its Legacy A rows (Legacy A model) and the planted `MODELE` (Legacy B model).
9. WHEN the API_Agent_Legacy_B receives a Legacy B record, THE API_Agent_Legacy_B SHALL return the record in the Common_Vocabulary: the R1-normalized `CHASSIS_NO` as VIN, the VehicleModelsDico entry whose Normalized_Key equals the Normalized_Key of `MODELE` as VehicleModel (e.g. "Megane E-Tech" -> `MEGANE E-TECH`), and `DATE_MAJ` as an ISO 8601 date (e.g. `20240315` -> `2024-03-15`).
10. IF the R1-normalized `CHASSIS_NO` does not match `^[0-9A-Z]{20}$`, the Normalized_Key of `MODELE` matches no VehicleModelsDico entry, or `DATE_MAJ` is not a valid yyyymmdd calendar date, THEN THE API_Agent_Legacy_B SHALL omit each untranslatable value from the translated record and return, for each such field, an error naming the field and its raw value.
11. THE Test_Suite SHALL contain an integration test checking that a Run on the Legacy A source file with the Legacy_B_Lookup enabled records a Field_Decision with Action ASK_HUMAN and rule_id R7 (Requirement 12.5) on each row of every VIN listed in the Legacy_B_Control_File.

### Requirement 31: Client app continuity

**User Story:** As a jury member, I want a client querying continuously during phase switches, so that zero outage is demonstrated.

#### Acceptance Criteria

1. WHILE the Client_App is running, THE Client_App SHALL send one diagnostics query to the Orchestrator per configurable interval, with the interval between 0.1 and 60 seconds and a default of 1 second.
2. THE Client_App SHALL send requests to the Orchestrator only.
3. WHILE the Phase_Controller moves the phase forward from 1 to 4 or rolls it back from 4 to 3 or from 3 to 2, including during each transition, THE Orchestrator SHALL answer every Client_App request with HTTP 200, or with HTTP 404 only when the requested VIN is absent from the backend serving the current phase.
4. WHEN the Client_App records a query result, THE Client_App SHALL update the displayed count of successful queries (HTTP 200 or HTTP 404) and the displayed count of failed queries.
5. WHEN a diagnostics query receives a response, THE Client_App SHALL record the HTTP status and the latency in milliseconds of that query.
6. IF a diagnostics query receives a status other than HTTP 200 or HTTP 404, gets a connection error, or gets no response within a configurable timeout (default 5 seconds), THEN THE Client_App SHALL count the query as failed, record the failure reason, and keep sending queries at the configured interval.

### Requirement 32: Streamlit UI

**User Story:** As an operator, I want one UI for stats, questions, the decision log and phases, so that the demo runs from a single screen.

#### Acceptance Criteria

1. WHEN a user selects a Run by run_id, THE UI SHALL display for that Run the row count of each of the four Row_Outcomes (MIGRATE, CORRECT, REJECT, ASK_HUMAN), the traced percentage, total LLM tokens, estimated cost, processing time in seconds, the Degraded_Mode flag, the current phase and the Aligned_Percentage, each equal to the value returned by the APIs for that run_id.
2. THE UI SHALL list every open Human_Question with all of its contents and SHALL let a human select and submit exactly one of its three options.
3. WHEN a human submits an option for an open Human_Question, THE UI SHALL send that option for the Human_Question's Pattern_Key through the APIs and SHALL remove the Human_Question from the open list once the APIs confirm the Human_Decision is persisted.
4. THE UI SHALL display the decision log with filters on run_id, Source_Row_Id, field, Action, rule_id and decided_by, SHALL show only the Decision_Records that match all set filters, and SHALL show a message indicating no match when no Decision_Record matches.
5. WHEN a user selects a Source_Row_Id, THE UI SHALL display every Decision_Record of that Source_Row_Id in the selected Run, each with all of its fields including the explanation.
6. THE UI SHALL display the current phase (1-4) and the gate status returned by the Phase_Controller and SHALL let a user request a transition to a phase different from the current one.
7. IF the Phase_Controller refuses a requested transition, THEN THE UI SHALL display the refusal reason returned by the Phase_Controller and SHALL keep displaying the unchanged current phase.
8. IF an API call made by the UI fails or the API is unreachable, THEN THE UI SHALL display an error message indicating the failed operation, SHALL keep the last successfully loaded data displayed, and SHALL keep a Human_Question whose submission failed in the open list.
9. THE UI SHALL read and write data only through the APIs and SHALL NOT connect directly to the Target_DB, the decision log, the HITL_Queue or the Human_Decision_Store.
10. WHERE the bonus chat is enabled, THE UI SHALL provide a chat panel that sends each user question to the Query_Agent and displays the answer returned by the Query_Agent.

### Requirement 33: Query agent (Optional, bonus, do last)

**User Story:** As a business user, I want to ask questions on the target data in natural language, so that I can explore results without SQL.

#### Acceptance Criteria

1. WHERE the bonus chat is enabled, WHEN a user submits a natural-language question of 1 to 1,000 characters after trimming, THE Query_Agent SHALL generate SQL through the LLM_Client.
2. WHERE the bonus chat is enabled, WHEN the LLM_Client returns SQL, THE Query_Agent SHALL validate the SQL before any execution and SHALL accept it only if it contains exactly one statement (one trailing semicolon allowed), that statement is a SELECT, and every table it references, including in joins and subqueries, is in the table allow-list held in Rule_Config.
3. WHERE the bonus chat is enabled, IF generated SQL fails validation, THEN THE Query_Agent SHALL not execute it and SHALL return the generated SQL text, zero rows and a validation reason naming the failed check: non-SELECT statement, more than one statement, or table outside the allow-list (with the table name).
4. WHERE the bonus chat is enabled, WHEN generated SQL passes validation, THE Query_Agent SHALL execute it on a read-only connection to the Target_DB and return the SQL text, at most N rows where N is the row limit in Rule_Config (integer 1 to 1,000, default 200), an indication of whether rows beyond N were omitted, and the input and output token counts reported by the LLM_Client for the request.
5. WHERE the Query_Agent is exposed to Legacy B consumers, THE Query_Agent SHALL query the Target_DB with the same validation (criteria 2, 3 and 7), read-only connection and row limit (criterion 4).
6. WHERE the bonus chat is enabled, WHILE in Degraded_Mode, WHEN a user submits a question, THE Query_Agent SHALL return a message stating that the chat is unavailable, without generating or executing SQL.
7. WHERE the bonus chat is enabled, IF a SQL string contains a statement other than SELECT, more than one statement, or a reference to a table outside the allow-list, THEN THE Query_Agent SHALL refuse its execution.
8. WHERE the bonus chat is enabled, IF a submitted question is empty after trimming or longer than 1,000 characters, THEN THE Query_Agent SHALL return an error message indicating the length constraint, without calling the LLM_Client.
9. WHERE the bonus chat is enabled, IF the LLM_Client call fails or the execution of validated SQL raises a database error, THEN THE Query_Agent SHALL return an error message indicating which step failed, the SQL text when one was generated, and zero rows.

### Requirement 34: Performance and validation target

**User Story:** As a jury member, I want measured performance against stated targets, so that the solution is credible at scale.

#### Acceptance Criteria

1. (Optional, do last) WHEN a Run processes a 100,000-row source file and the Resolution_Cache was populated by a previous Run on the same file, THE Migration_Pipeline SHALL complete the Run within 300 seconds of elapsed time on the laptop recorded in `docs/cost-performance.md`. Elapsed time runs from the start of ingestion to the production of the end-of-run report and excludes time spent waiting for Human_Decisions (A7).
2. THE Migration_Pipeline SHALL compute each field-local normalization (R1, R2, R4, R5, R6) exactly once per distinct raw value of that field per Run.
3. WHEN a Run processes the 10,000-row Legacy A source file with an empty Human_Decision_Store and the Legacy_B_Lookup disabled in Rule_Config, THE Report_Generator SHALL report the row count for each Row_Outcome (MIGRATE, CORRECT, REJECT, ASK_HUMAN), with the four counts summing to 10,000, and the count of REJECT rows whose reason is OUT_OF_SCOPE.
4. THE Project_Docs SHALL record in `docs/lessons-learnt.md` every count from criterion 3 that is not equal to its Validation_Target value. Each such entry SHALL give the measured count, the signed difference from the Validation_Target value and the cause.
5. WHERE the optional Run of criterion 1 has been performed, THE Project_Docs SHALL record in `docs/cost-performance.md` the measured elapsed time of that Run, its row count, and the CPU model and RAM size of the laptop that ran it.

### Requirement 35: Documentation

**User Story:** As a jury member, I want architecture, cost and lessons documented, so that the choices can be assessed.

#### Acceptance Criteria

1. THE Project_Docs SHALL include in `docs/architecture.md`, for each architecture choice, its justification and at least one rejected alternative with the reason for rejection, covering at least: the Strangler Fig phases enforced by the Phase_Controller and routed by the Orchestrator, the Rule_Engine as pure functions configured by Rule_Config, the LLM_Client as the only LLM caller together with the Resolution_Cache and the Confidence_Threshold, the grouping of Human_Questions by Pattern_Key, and Degraded_Mode.
2. THE Project_Docs SHALL include in `docs/cost-performance.md`, for at least 2 Runs with different source row counts, the run_id, the source row count, the processing time in seconds, the tokens consumed, the cost computed from the Rule_Config prices per token, the tokens per 100,000 records (tokens consumed × 100,000 ÷ source row count) with its difference from the 1,200-token reference, and the drivers: distinct Pattern_Key count, batch size and Resolution_Cache hit rate as a percentage. Each value SHALL equal the Report_Generator Run metrics of that run_id.
3. THE Project_Docs SHALL include in `docs/lessons-learnt.md` each anomaly found with the affected field and the decision taken on it, and each deviation from the Validation_Target with the measured value, the Validation_Target value and its cause. If no deviation occurred, the file SHALL state this explicitly.
