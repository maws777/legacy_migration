"""HITL_Queue: one Human_Question per Pattern_Key per Run, answers stored as Human_Decisions."""
from __future__ import annotations

import json
import uuid
from typing import Callable, Mapping

from sqlalchemy import (
    Column, DateTime, Engine, ForeignKey, Integer, MetaData, String, Table, Text, UniqueConstraint, and_, func,
    insert, select, update,
)

from hitl.schemas import AnswerResult, DecisionEntry, ExampleRow, HumanAnswer, HumanDecision, HumanQuestion, LLMSuggestionOut, QuestionGroup
from hitl.store import HumanDecisionStore, STORE_MD
from hitl.validation import SharedIdView, allowed_values, validate_value
from rules.config import RuleConfig
from rules.pattern_key import ROW_SUFFIX, extend_key, parse_key
from rules.timeutil import as_utc, naive_utc, utcnow

QUEUE_MD = STORE_MD
human_question = Table(
    "human_question", QUEUE_MD,
    Column("question_id", String(36), primary_key=True),
    Column("run_id", String(36)),
    Column("pattern_key", Text, nullable=False),
    Column("field", String(32), nullable=False),
    Column("reason", String(32), nullable=False),
    Column("rule_id", String(8), nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("example_rows", Text, nullable=False),
    Column("missing", Text, nullable=False),
    Column("why", Text, nullable=False),
    Column("llm_suggestion", Text),
    Column("llm_status", String(32), nullable=False),
    Column("allowed_values", Text),
    Column("options", Text, nullable=False),
    Column("status", String(12), nullable=False),
    Column("parent_question_id", String(36)),
    Column("parent_pattern_key", Text),
    Column("source_row_id", Integer),
    Column("scope", String(8), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("closed_at", DateTime),
    UniqueConstraint("run_id", "pattern_key", name="uq_question_run_key"),
)
human_question_row = Table(
    "human_question_row", QUEUE_MD,
    Column("question_id", String(36), ForeignKey("human_question.question_id"), primary_key=True),
    Column("source_row_id", Integer, primary_key=True),
    Column("row_values", Text, nullable=False),
)

MISSING = {
    ("ecuCode", None): "ValueDescription (ID0001-ID0005) of the operation.",
    ("ecuName", None): "ECU name (an ECUDico entry).",
    ("Vehmodel", None): "Vehicle model (a VehicleModelsDico entry).",
    ("Vehmodel", "VIN_MULTI_MODEL"): "The single model of the vehicle (a vehicle has exactly one model).",
    ("Vehmodel", "LEGACY_B_CONFLICT"): "The model to keep when Legacy A and Legacy B disagree.",
    ("originTool", None): "Diagnostic tool name (a ToolDico entry).",
    ("toolVersion", None): "Diagnostic tool version.",
    ("Creation_date", None): "Operation creation date (dd/mm/yyyy hh:mm).",
    ("diagnosticFieId", None): "A unique diagnosticOpID for each operation of the group.",
    ("_row", "RECON_GAP"): "Confirmation of the record stored in the target system.",
}
LLM_STATUS = {"below_threshold": "proposed_below_threshold", "human_required": "proposed_human_only",
              "failed": "failed", "degraded": "degraded"}
# decisions.md: R6, R7 and the empty fields of R8 get only "reject the group" and "case by case".
NO_GROUP_VALUE_RULES = {"R6", "R7"}


class AnswerRefused(Exception):
    """409: closed or superseded question, or option not allowed."""


class ValueRejected(Exception):
    """422: the value failed the validation rule of its field."""

    def __init__(self, failed_rule: str) -> None:
        super().__init__(failed_rule)
        self.failed_rule = failed_rule


def _missing(field: str, reason: str) -> str:
    return MISSING.get((field, reason)) or MISSING.get((field, None)) or f"A value for {field}."


def _why(group: QuestionGroup) -> str:
    text = f"{group.rule_id} {group.reason}: {group.explanation}"
    s = group.llm_suggestion
    if group.reason != "UNKNOWN_VARIANT" and s is None:
        text += " No LLM proposal is requested for this case (not an Unknown_Variant)."
    elif s is not None and "LLM proposal" not in text:
        text += f" LLM proposal: {s.decision} '{s.value or ''}' (confidence {s.confidence:.2f})."
    return text


def group_options(group: QuestionGroup, key: str) -> list[str]:
    parts = parse_key(key)
    if parts.field == "_row" and parts.reason == "RECON_GAP":
        return ["apply_value", "reject_group"]
    shared = parts.field == "diagnosticFieId" and parts.reason == "SHARED_OP_ID"
    if parts.source_row_id is not None:                       # case-by-case question of one row
        return ["apply_value", "reject_group"] + (["generate_op_id"] if shared else [])
    options = []
    no_value = group.rule_id in NO_GROUP_VALUE_RULES or (group.rule_id == "R8" and parts.reason == "UNREADABLE")
    if group.homogeneous and not no_value:
        options.append("apply_value")
    options += ["reject_group", "case_by_case"]
    if shared:
        options.append("generate_op_id")
    return options


class HITLQueue:
    def __init__(self, engine: Engine, config: Callable[[], RuleConfig],
                 shared_view: Callable[[], SharedIdView] | None = None) -> None:
        self.engine = engine
        self._config = config
        self._shared_view = shared_view or SharedIdView
        self.decisions = HumanDecisionStore(engine)
        QUEUE_MD.create_all(engine)

    # ---------------------------------------------------------------- creation
    def _row(self, run_id: str | None, key: str, group: QuestionGroup, *, parent: HumanQuestion | None = None,
             source_row_id: int | None = None) -> dict:
        cfg = self._config()
        n = cfg.pipeline.hitl_example_rows
        s = group.llm_suggestion
        return {
            "question_id": str(uuid.uuid4()), "run_id": run_id, "pattern_key": key, "field": group.field,
            "reason": group.reason, "rule_id": group.rule_id, "row_count": len(group.rows),
            "example_rows": json.dumps([{"source_row_id": rid, "values": v} for rid, v in group.examples[:n]],
                                       ensure_ascii=False),
            "missing": _missing(group.field, group.reason), "why": _why(group),
            "llm_suggestion": json.dumps(s.__dict__, ensure_ascii=False) if s is not None else None,
            "llm_status": LLM_STATUS.get(group.llm_status or "", "not_applicable"),
            "allowed_values": json.dumps(allowed_values(key, cfg)),
            "options": json.dumps(group_options(group, key)), "status": "open",
            "parent_question_id": parent.question_id if parent else None,
            "parent_pattern_key": parent.pattern_key if parent else None, "source_row_id": source_row_id,
            "scope": "row" if source_row_id is not None else "group", "created_at": naive_utc(utcnow()),
            "closed_at": None}

    def _insert(self, conn, row: dict, members: tuple[tuple[int, dict], ...]) -> None:
        conn.execute(insert(human_question).values(**row))
        if members:
            conn.execute(insert(human_question_row), [
                {"question_id": row["question_id"], "source_row_id": rid,
                 "row_values": json.dumps(v, ensure_ascii=False)} for rid, v in members])

    def create_questions(self, run_id: str | None, groups: Mapping[str, QuestionGroup]) -> int:
        """One question per Pattern_Key and Run; existing (run_id, Pattern_Key) questions are kept."""
        created = 0
        with self.engine.begin() as conn:
            for key, group in groups.items():
                cond = human_question.c.pattern_key == key
                cond = and_(cond, human_question.c.run_id == run_id) if run_id is not None else and_(
                    cond, human_question.c.status == "open")
                if conn.execute(select(human_question.c.question_id).where(cond)).first():
                    continue
                members = group.row_values or tuple((rid, {}) for rid in group.rows)
                self._insert(conn, self._row(run_id, key, group), members)
                created += 1
        return created

    # ---------------------------------------------------------------- reads
    def get(self, question_id: str) -> HumanQuestion | None:
        with self.engine.connect() as c:
            r = c.execute(select(human_question).where(human_question.c.question_id == question_id)).mappings().first()
        return self._checked(self._model(r)) if r else None

    def list(self, status: str | None = None, run_id: str | None = None) -> list[HumanQuestion]:
        q = select(human_question).order_by(human_question.c.created_at, human_question.c.pattern_key)
        if status:
            q = q.where(human_question.c.status == status)
        if run_id:
            q = q.where(human_question.c.run_id == run_id)
        with self.engine.connect() as c:
            return [self._checked(self._model(r)) for r in c.execute(q).mappings().all()]

    def open_questions(self, run_id: str | None = None) -> list[HumanQuestion]:
        return self.list("open", run_id)

    def open_count(self, run_id: str | None = None) -> int:
        q = select(func.count()).select_from(human_question).where(human_question.c.status == "open")
        if run_id:
            q = q.where(human_question.c.run_id == run_id)
        with self.engine.connect() as c:
            return c.execute(q).scalar_one()

    def members(self, question_id: str) -> list[tuple[int, dict]]:
        with self.engine.connect() as c:
            rows = c.execute(select(human_question_row).where(human_question_row.c.question_id == question_id)
                             .order_by(human_question_row.c.source_row_id)).mappings().all()
        return [(r["source_row_id"], json.loads(r["row_values"])) for r in rows]

    def supersede_before(self, run_id: str) -> int:
        """Mark open questions of earlier full Runs superseded (gap questions have no run_id)."""
        with self.engine.begin() as c:
            res = c.execute(update(human_question).where(
                human_question.c.status == "open", human_question.c.run_id.is_not(None),
                human_question.c.run_id != run_id).values(status="superseded", closed_at=naive_utc(utcnow())))
        return res.rowcount or 0

    def _checked(self, q: HumanQuestion) -> HumanQuestion:
        """Run the LLM proposal through the same rules as a human answer; flag it when it would be refused."""
        s = q.llm_suggestion
        if s is None:
            return q
        if s.decision == "reject":
            issue = None if "reject_group" in q.options else "rejeter n'est pas permis pour ce groupe"
        elif "apply_value" not in q.options:
            issue = "appliquer une valeur n'est pas permis pour ce groupe"
        else:
            issue = validate_value(q.pattern_key, s.value, self._config(), self._shared_view())
        return q.model_copy(update={"suggestion_issue": issue}) if issue else q

    @staticmethod
    def _model(r) -> HumanQuestion:
        s = json.loads(r["llm_suggestion"]) if r["llm_suggestion"] else None
        return HumanQuestion(
            question_id=r["question_id"], run_id=r["run_id"], pattern_key=r["pattern_key"], field=r["field"],
            reason=r["reason"], rule_id=r["rule_id"], row_count=r["row_count"],
            example_rows=[ExampleRow(**e) for e in json.loads(r["example_rows"])], missing=r["missing"],
            why=r["why"], llm_suggestion=LLMSuggestionOut(**s) if s else None, llm_status=r["llm_status"],
            allowed_values=json.loads(r["allowed_values"]) if r["allowed_values"] else None,
            options=json.loads(r["options"]), status=r["status"], parent_question_id=r["parent_question_id"],
            parent_pattern_key=r["parent_pattern_key"], source_row_id=r["source_row_id"], scope=r["scope"],
            created_at=as_utc(r["created_at"]), closed_at=as_utc(r["closed_at"]))

    # ---------------------------------------------------------------- decisions
    def list_decisions(self) -> list[DecisionEntry]:
        """Stored human answers, newest first, with the field and row count of the question they closed."""
        entries = []
        for d in self.decisions.all().values():
            q = self.get(d.question_id)
            entries.append(DecisionEntry(**d.model_dump(), field=q.field if q else None,
                                         row_count=q.row_count if q else None))
        return sorted(entries, key=lambda e: e.answered_at, reverse=True)

    def revoke(self, pattern_key: str) -> bool:
        """Forget a human answer: the next full Run asks the question again."""
        return self.decisions.delete(pattern_key)

    # ---------------------------------------------------------------- answers
    def answer(self, question_id: str, answer: HumanAnswer) -> AnswerResult:
        q = self.get(question_id)
        if q is None:
            raise KeyError(question_id)
        if q.status != "open":
            raise AnswerRefused(f"question {question_id} is {q.status}")
        if answer.option not in q.options:
            raise AnswerRefused(f"option {answer.option} is not allowed on {q.pattern_key}")
        now = utcnow()
        if answer.option == "case_by_case":
            return self._case_by_case(q, now)
        if answer.option == "apply_value":
            failed = validate_value(q.pattern_key, answer.value, self._config(), self._shared_view())
            if failed:
                raise ValueRejected(failed)
            kind, value = "value", answer.value
        elif answer.option == "reject_group":
            kind, value = "reject", None
        else:
            kind, value = "generate", None
        decision = HumanDecision(pattern_key=q.pattern_key, kind=kind, value=value, answered_at=now,
                                 operator=answer.operator, question_id=q.question_id)
        with self.engine.begin() as c:
            self.decisions.put(decision, conn=c)
            c.execute(update(human_question).where(human_question.c.question_id == q.question_id)
                      .values(status="closed", closed_at=naive_utc(now)))
        return AnswerResult(question=self.get(question_id), decision=decision)

    def _case_by_case(self, q: HumanQuestion, now) -> AnswerResult:
        if ROW_SUFFIX in q.pattern_key:
            raise AnswerRefused("case_by_case is not allowed on a row-scoped question")
        members = self.members(q.question_id)
        created = []
        with self.engine.begin() as c:
            c.execute(update(human_question).where(human_question.c.question_id == q.question_id)
                      .values(status="closed", closed_at=naive_utc(now)))
            for rid, values in members:
                key = extend_key(q.pattern_key, rid)
                group = QuestionGroup(
                    pattern_key=key, field=q.field, reason=q.reason, rule_id=q.rule_id, rows=(rid,),
                    examples=((rid, values),), homogeneous=True, explanation=q.why,
                    llm_suggestion=None, llm_status=None)
                row = self._row(q.run_id, key, group, parent=q, source_row_id=rid)
                row["why"] = q.why
                row["llm_status"] = q.llm_status
                row["llm_suggestion"] = q.llm_suggestion.model_dump_json() if q.llm_suggestion else None
                self._insert(c, row, ((rid, values),))
                created.append(row["question_id"])
        return AnswerResult(question=self.get(q.question_id), decision=None, created_questions=created)
