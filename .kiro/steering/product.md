# Product

## Context

Hackathon 2026 "Information Systems that evolve. Continuity that never breaks." (ESILV x Capgemini x Dassault Systèmes x Renault Group). 48 hours, team of 7 students.

## Problem

Legacy A is a vehicle-diagnostics system whose export is a dirty CSV: two record formats merged in one file, French-Excel damage (`#NOM?`, `2,04E+12`), naming variants, empty fields and cardinality conflicts. The data must move to a new, constrained target model while the client application and Legacy B keep working without interruption. Every decision taken on the data must be explainable.

## Goals

- **G1 Accuracy**: migrate the dataset to the target model with the deterministic rules R1-R8 and the final checks in `migration-rules.md`.
- **G2 Traceability**: 100% of decisions (rule, LLM or human) are logged with an explanation.
- **G3 Idempotence**: re-running the migration never creates duplicates.
- **G4 Grouped HITL**: one question per pattern, never per row. Human decisions are persisted and reapplied as rules, so run 2 asks fewer questions.
- **G5 Frugal AI**: the LLM works only on distinct patterns (schema discovery, unknown variants), with a cache. The system runs without an LLM in degraded mode: anything no rule can decide goes to a human.
- **G6 Continuity**: Strangler Fig migration in 4 phases with zero outage for the client app and Legacy B.
- **G7 Cost/performance**: processing time and token use are measured and documented.

## Non-goals

- Calling the LLM once per row.
- Production authentication, multi-tenant security or deployment infrastructure.

## Validation target (10,000-row dataset)

| Outcome | Expected |
|---|---|
| Migrated automatically | ~6,940 |
| Automatically corrected | ~630 |
| Rejected (incl. ~990 out of scope) | ~1,920 |
| Requiring human decision | ~510 |

The four outcomes are disjoint and sum to 10,000. They are computed with the Legacy B lookup disabled. Scale target: 100,000 rows in a few minutes. Cost reference from the brief: ~1,200 tokens per 100k records.

## End-of-run report (exact format)

```text
10,000 records analysed
─────────────────────────
6,9xx migrated automatically
6xx automatically corrected
1,9xx rejected
5xx requiring human decision

100% decisions traced
Estimated AI consumption: N tokens (≈ X €)
Processing time: m min s sec
```

## Strangler Fig phases

1. **Phase 1**: client app -> orchestrator agent -> API agents -> Legacy A + Legacy B.
2. **Phase 2**: New System A appears behind the same API agent. Legacy stays master (reads from legacy). Migration feeds the new system in batches.
3. **Phase 3**: realignment. The orchestrator compares legacy and new responses. Gaps are corrected or sent to a human. The aligned percentage is tracked.
4. **Phase 4**: cutover after a human gate (all decisions taken, zero gaps on the control sample). Reads come from the new system and Legacy A is retired. The client app and Legacy B see no difference.

An operator can roll back one phase (4 -> 3, 3 -> 2). Rollbacks are logged like other transitions.

## Grading

| Criterion | Weight |
|---|---|
| Agentic architecture & justification | 25% |
| Migration accuracy | 15% |
| Unexpected anomaly detection/resolution | 15% |
| Explainability/traceability | 10% |
| HITL quality | 10% |
| Lessons learnt | 10% |
| Performance/cost | 5% |
| Demo/UX | 5% |
| Perspectives | 5% |
| DB chat | bonus |

## Demo scenario (5 min)

1. The client app queries continuously ("diagnostics for vehicle VIN X") and is never interrupted.
2. Run 1, then the end-of-run report.
3. A human answers a few grouped questions in the UI.
4. Run 2: zero duplicates, fewer questions, human decisions reapplied.
5. Live switch through phases 2 -> 3 -> 4 with no client outage.
6. Decision log: open one corrected, one rejected and one ASK_HUMAN row to show their explanations.
7. Bonus: a natural-language question on the target DB.

## Inputs

- Dataset: `data/legacy/hackathon-2026-digital-continuity-dataset.csv`
- Brief: `docs/brief/hackathon-2026-digital-continuity-en.pptx`