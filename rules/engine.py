"""Rule_Engine: ``scan_row`` and ``decide_row`` (design "Rule engine design"). Pure: no I/O.

Evaluation order per row (steps 0-11) is fixed, so the Field_Decision order is deterministic.
A REJECT does not stop later steps (Requirement 30.11).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from datetime import datetime
from typing import Callable, Mapping, Sequence, TypeVar

from rules import fields as F
from rules.config import RuleConfig
from rules.labels import label_features
from rules.opid import generate_op_id
from rules.pattern_key import extend_key, make_key
from rules.templates import human_explanation, render
from rules.types import (
    ACTIONS, COL_DATE, COL_ECU_CODE, COL_ECU_NAME, COL_LABEL, COL_MODEL, COL_OP_ID, COL_TOOL,
    COL_TOOL_VERSION, COL_VALUE_REF, COL_VIN, ROW_FIELD, AllowedValues, FieldDecision, LabelFeatures,
    OperationIdentity, RawRow, RowDecision, RowOutcome, RowScan, RuleContext, TargetValues, UnknownVariant,
)

RANK = {"REJECT": 4, "ASK_HUMAN": 3, "CORRECT": 2, "MIGRATE": 1, "TRANSFORM": 1}
FORMAT2_RE = re.compile(r"Id[0-9]{3}")
DATE_FORMAT = "dd/mm/yyyy hh:mm"
ISO_MINUTE = "%Y-%m-%dT%H:%M"
IDENTITY_FIELDS = (COL_VIN, COL_ECU_NAME, COL_ECU_CODE, COL_DATE, COL_VALUE_REF, COL_TOOL, COL_TOOL_VERSION)
T = TypeVar("T")


def row_outcome(decisions: Sequence[FieldDecision]) -> RowOutcome:
    """Highest priority Action: REJECT > ASK_HUMAN > CORRECT > MIGRATE (TRANSFORM counts as MIGRATE)."""
    if not decisions:
        raise ValueError("row_outcome needs at least one Field_Decision")
    top = max(RANK[d.action] for d in decisions)
    return {4: "REJECT", 3: "ASK_HUMAN", 2: "CORRECT", 1: "MIGRATE"}[top]  # type: ignore[return-value]


def final_checks(values: Mapping[str, str], cfg: RuleConfig) -> list[FieldDecision]:
    """FC1-FC4 on final values; one REJECT per failed check (``before`` = checked value)."""
    out: list[FieldDecision] = []

    def fail(rule: str, field: str, value: str) -> None:
        out.append(FieldDecision(field, "REJECT", rule, "FAILED", value, "",
                                 explanation=render(rule, "FAILED", value=value)))

    vin = values.get(COL_VIN, "")
    if not F.is_valid_vin(vin):
        fail("FC1", COL_VIN, vin)
    tool = values.get(COL_TOOL, "")
    if tool not in cfg.tools:
        fail("FC2", COL_TOOL, tool)
    vd = values.get(COL_ECU_CODE, "")
    if vd not in cfg.value_codes:
        fail("FC3", COL_ECU_CODE, vd)
    ref = values.get(COL_VALUE_REF, "")
    if vd in cfg.value_codes and vd != "ID0001" and not F.is_valid_value_ref(ref):
        fail("FC4", COL_VALUE_REF, ref)
    return out


class _Eval:
    """Evaluation of one row against one read-only context."""

    def __init__(self, row: RawRow, ctx: RuleContext) -> None:
        self.row, self.ctx, self.cfg = row, ctx, ctx.config
        self.decisions: list[FieldDecision] = []
        self.values: dict[str, str] = {}
        self.ask_fields: set[str] = set()
        self.unknown: list[UnknownVariant] = []
        self.generated = False
        self.norm_vin = ""
        self.vin_ok = False
        self.decided_model: str | None = None
        self.fc_passed = False

    # ---------------------------------------------------------------- helpers
    def raw(self, col: str) -> str:
        return self.row.values.get(col, "")

    def memo(self, attr: str, raw: str, fn: Callable[[str], T]) -> T:
        memo = self.ctx.memo
        if memo is not None:
            table = getattr(memo, attr)
            if raw in table:
                return table[raw]
        return fn(raw)

    def label(self) -> tuple[str, LabelFeatures]:
        raw = self.raw(COL_LABEL)
        return raw, self.memo("label", raw, lambda s: label_features(s, self.cfg.ecus))

    def add(self, d: FieldDecision) -> FieldDecision:
        self.decisions.append(d)
        if d.after:
            self.values[d.field] = d.after
        if d.action == "ASK_HUMAN":
            self.ask_fields.add(d.field)
        return d

    def convert(self, field: str, value: str | None) -> str:
        """Target form of a value chosen outside the rules (dates become ISO minute)."""
        value = value or ""
        if field == COL_DATE:
            dt = F.parse_creation_date(value)
            if dt is not None:
                return F.iso_minute(dt)
        return value

    def llm_text(self, key: str) -> str:
        ctx = self.ctx
        status = ctx.llm_status.get(key)
        s = ctx.llm_suggestions.get(key)
        thr = self.cfg.pipeline.confidence_threshold
        if status == "below_threshold" and s is not None:
            return (f"LLM proposal {s.decision} '{s.value or ''}' has confidence {s.confidence:.2f}, below the "
                    f"threshold {thr}; a human decides.")
        if status == "human_required" and s is not None:
            return (f"LLM proposal {s.decision} '{s.value or ''}' (confidence {s.confidence:.2f}) is shown as a "
                    "suggestion; this field is never applied automatically.")
        if status == "failed":
            return f"LLM call failed ({s.justification if s else 'error'}); a human decides."
        if status == "degraded" or ctx.degraded:
            return "Degraded_Mode: no LLM provider is available; a human decides."
        return "No LLM proposal was obtained; a human decides."

    def resolve(self, key: str, field: str, rule_id: str, reason: str, before: str, explanation: str,
                uv: UnknownVariant | None = None) -> FieldDecision:
        """Extended-key Human_Decision > group-key Human_Decision > cached resolution > ASK_HUMAN."""
        ctx = self.ctx
        ext = extend_key(key, self.row.source_row_id)
        used = ext if ext in ctx.human_decisions else key
        hd = ctx.human_decisions.get(used)
        if hd is not None and hd.kind in ("value", "reject"):
            if hd.kind == "value":
                return FieldDecision(field, "CORRECT", rule_id, "HUMAN_VALUE", before, self.convert(field, hd.value),
                                     "human", None, human_explanation(used, "apply_value", hd.value), 0, used)
            return FieldDecision(field, "REJECT", rule_id, "HUMAN_REJECT", before, "", "human", None,
                                 human_explanation(used, "reject_group"), 0, used)
        if uv is not None:
            cached = ctx.cache.get(key)
            trace = ctx.llm_traces.get(key)
            if cached is not None:
                fresh = key in ctx.llm_tokens
                tokens = ctx.llm_tokens.get(key, 0)
                why = cached.justification + (f" {trace}" if fresh and trace else "")
                if cached.decision == "map":
                    return FieldDecision(field, "CORRECT", rule_id, "LLM_VALUE" if fresh else "CACHE_VALUE", before,
                                         self.convert(field, cached.value), "llm", cached.confidence,
                                         why, tokens, key)
                return FieldDecision(field, "REJECT", rule_id, "LLM_REJECT" if fresh else "CACHE_REJECT", before, "",
                                     "llm", cached.confidence, why, tokens, key)
            self.unknown.append(uv)
            explanation = f"{explanation} {self.llm_text(key)}" + (f" {trace}" if trace else "")
        return FieldDecision(field, "ASK_HUMAN", rule_id, reason, before, "", "rule", None, explanation, 0, key,
                             ctx.llm_suggestions.get(key))

    def unknown_variant(self, field: str, rule_id: str, raw: str, normalized: str,
                        allowed: AllowedValues, **params: object) -> FieldDecision:
        key = make_key(field, "UNKNOWN_VARIANT", normalized)
        uv = UnknownVariant(key, field, normalized, allowed)
        expl = render(rule_id, "UNKNOWN_VARIANT", before=raw, normalized=normalized, **params)
        return self.add(self.resolve(key, field, rule_id, "UNKNOWN_VARIANT", raw, expl, uv))

    def identity(self) -> OperationIdentity:
        v = self.values
        return OperationIdentity(
            vin=self.norm_vin if self.vin_ok else None, ecu_name=v.get(COL_ECU_NAME),
            value_description=v.get(COL_ECU_CODE), creation_date=v.get(COL_DATE),
            value_reference=v.get(COL_VALUE_REF), diag_tool_name=v.get(COL_TOOL),
            diag_tool_version=v.get(COL_TOOL_VERSION))

    # ---------------------------------------------------------------- steps 1-8
    def step_vin(self) -> None:
        raw = self.raw(COL_VIN)
        self.norm_vin = self.memo("vin", raw, F.normalize_vin)
        d = self.add(F.r1_vin(raw, self.norm_vin))
        self.vin_ok = d.action != "REJECT"

    def step_tool(self) -> None:
        raw = self.raw(COL_TOOL)
        r2 = self.memo("tool", raw, lambda s: F.r2_tool(s, self.cfg))
        if r2.kind == "unreadable":
            self.unreadable(COL_TOOL, raw)
        elif r2.kind == "mapped":
            self.add(FieldDecision(COL_TOOL, "TRANSFORM", "R2", "MAPPED", raw, r2.value or "",
                                   explanation=render("R2", "MAPPED", before=raw, after=r2.value)))
        elif r2.kind == "migrate":
            self.add(FieldDecision(COL_TOOL, "MIGRATE", "R2", "OK", raw, r2.value or "",
                                   explanation=render("R2", "OK", after=r2.value)))
        else:
            self.unknown_variant(COL_TOOL, "R2", raw, r2.trimmed, AllowedValues(values=self.cfg.tools))

    def unreadable(self, field: str, raw: str) -> None:
        expl = render("R8", "UNREADABLE", field=field, before=raw)
        self.add(self.resolve(make_key(field, "UNREADABLE"), field, "R8", "UNREADABLE", raw, expl))

    def step_tool_version(self) -> None:
        raw = self.raw(COL_TOOL_VERSION)
        if F.is_unreadable(raw):
            self.unreadable(COL_TOOL_VERSION, raw)
        else:
            self.add(FieldDecision(COL_TOOL_VERSION, "MIGRATE", "R8", "COPIED", raw, raw,
                                   explanation=render("R8", "COPIED", field=COL_TOOL_VERSION, before=raw)))

    def step_date(self) -> None:
        raw = self.raw(COL_DATE)
        if F.is_unreadable(raw):
            self.unreadable(COL_DATE, raw)
            return
        dt = self.memo("date", raw, F.parse_creation_date)
        if dt is not None:
            iso = F.iso_minute(dt)
            self.add(FieldDecision(COL_DATE, "TRANSFORM", "R8", "DATE_PARSED", raw, iso,
                                   explanation=render("R8", "DATE_PARSED", before=raw, after=iso)))
        else:
            self.unknown_variant(COL_DATE, "R8", raw, raw.strip(), AllowedValues(format=DATE_FORMAT))

    def step_ecu_code(self) -> None:
        raw = self.raw(COL_ECU_CODE)
        ec = raw.strip()
        label, lf = self.label()
        did, did_map = lf.did, self.cfg.mappings.did_map
        codes = self.cfg.value_codes
        if ec in codes:
            note = ""
            if did in did_map and did_map[did] != ec:
                note = f" Label DID {did} maps to {did_map[did]}: inconsistency logged."
            self.add(FieldDecision(COL_ECU_CODE, "MIGRATE", "R3", "FORMAT1", raw, ec,
                                   explanation=render("R3", "FORMAT1", after=ec, note=note)))
        elif FORMAT2_RE.fullmatch(ec):
            if did in did_map:
                self.add(FieldDecision(COL_ECU_CODE, "TRANSFORM", "R3", "DID_MAPPED", raw, did_map[did],
                                       explanation=render("R3", "DID_MAPPED", before=raw, did=did, after=did_map[did])))
            elif did:
                self.out_of_scope(raw, did)
            else:
                expl = render("R3", "FORMAT2_NO_DID", before=raw, label=label)
                self.add(self.resolve(make_key(COL_ECU_CODE, "FORMAT2_NO_DID"), COL_ECU_CODE, "R3",
                                      "FORMAT2_NO_DID", raw, expl))
        elif F.is_unreadable(raw):
            if did in did_map:
                self.add(FieldDecision(COL_ECU_CODE, "CORRECT", "R3", "DEDUCED_DID", raw, did_map[did],
                                       explanation=render("R3", "DEDUCED_DID", before=raw, did=did, after=did_map[did])))
            elif did:
                self.out_of_scope(raw, did)
            elif label.strip() in self.cfg.mappings.vin_labels:
                self.add(FieldDecision(COL_ECU_CODE, "CORRECT", "R3", "DEDUCED_VIN_LABEL", raw, "ID0001",
                                       explanation=render("R3", "DEDUCED_VIN_LABEL", before=raw, label=label)))
            else:
                expl = render("R3", "UNREADABLE", before=raw, label=label)
                self.add(self.resolve(make_key(COL_ECU_CODE, "UNREADABLE", lf.template), COL_ECU_CODE, "R3",
                                      "UNREADABLE", raw, expl))
        else:
            self.unknown_variant(COL_ECU_CODE, "R3", raw, ec, AllowedValues(values=codes))

    def out_of_scope(self, raw: str, did: str) -> None:
        self.add(FieldDecision(COL_ECU_CODE, "REJECT", "R3", "OUT_OF_SCOPE", raw, "",
                               explanation=render("R3", "OUT_OF_SCOPE", did=did)))

    def step_value_ref(self) -> None:
        vd = self.values.get(COL_ECU_CODE)
        if vd is None:
            return
        raw = self.raw(COL_VALUE_REF)
        if vd == "ID0001":
            action = "MIGRATE" if raw == self.norm_vin else "TRANSFORM"
            self.add(FieldDecision(COL_VALUE_REF, action, "R4", "VIN_AS_VALUE", raw, self.norm_vin,
                                   explanation=render("R4", "VIN_AS_VALUE", before=raw, after=self.norm_vin)))
            return
        r4 = self.memo("value_ref", raw, F.r4_classify)
        if r4.reason:
            bad = "".join(sorted({c for c in r4.value if not ("0" <= c <= "9" or "A" <= c <= "Z")}))
            self.add(FieldDecision(COL_VALUE_REF, "REJECT", "R4", r4.reason, raw, "",
                                   explanation=render("R4", r4.reason, before=raw, value=r4.value,
                                                      length=len(r4.value), chars=bad)))
        elif r4.value == raw:
            self.add(FieldDecision(COL_VALUE_REF, "MIGRATE", "R4", "OK", raw, raw,
                                   explanation=render("R4", "OK", before=raw)))
        else:
            self.add(FieldDecision(COL_VALUE_REF, "CORRECT", "R4", "NORMALIZED", raw, r4.value,
                                   explanation=render("R4", "NORMALIZED", before=raw, after=r4.value)))

    def step_ecu_name(self) -> None:
        raw = self.raw(COL_ECU_NAME)
        m = self.memo("ecu_name", raw, lambda s: F.dict_lookup(s, self.cfg.ecus))
        label, lf = self.label()
        if m is not None:
            if raw == m:
                self.add(FieldDecision(COL_ECU_NAME, "MIGRATE", "R5", "OK", raw, m,
                                       explanation=render("R5", "OK", after=m)))
            else:
                self.add(FieldDecision(COL_ECU_NAME, "CORRECT", "R5", "DICT_NORMALIZED", raw, m,
                                       explanation=render("R5", "DICT_NORMALIZED", before=raw, after=m)))
        elif lf.ecu is not None:
            self.add(FieldDecision(COL_ECU_NAME, "CORRECT", "R5", "LABEL_ECU", raw, lf.ecu,
                                   explanation=render("R5", "LABEL_ECU", before=raw, after=lf.ecu)))
        elif F.is_unreadable(raw):
            expl = render("R5", "UNREADABLE", before=raw, label=label)
            self.add(self.resolve(make_key(COL_ECU_NAME, "UNREADABLE"), COL_ECU_NAME, "R5", "UNREADABLE", raw, expl))
        else:
            self.unknown_variant(COL_ECU_NAME, "R5", raw, F.normalized_key(raw), AllowedValues(values=self.cfg.ecus))

    def step_model(self, with_r7: bool) -> None:
        raw = self.raw(COL_MODEL)
        ctx = self.ctx
        m = self.memo("vehmodel", raw, lambda s: F.dict_lookup(s, self.cfg.vehicle_models))
        idx = ctx.vin_model_index.get(self.norm_vin, frozenset())
        lb = ctx.legacy_b_model_index.get(self.norm_vin)
        rule_line = False
        if m is not None:
            rule_line, self.decided_model = True, m
            if raw == m:
                self.add(FieldDecision(COL_MODEL, "MIGRATE", "R6", "OK", raw, m, explanation=render("R6", "OK", after=m)))
            else:
                self.add(FieldDecision(COL_MODEL, "CORRECT", "R6", "DICT_NORMALIZED", raw, m,
                                       explanation=render("R6", "DICT_NORMALIZED", before=raw, after=m)))
        elif len(idx) == 1:
            (model,) = tuple(idx)
            rule_line, self.decided_model = True, model
            self.add(FieldDecision(COL_MODEL, "CORRECT", "R6", "VIN_SIBLING", raw, model,
                                   explanation=render("R6", "VIN_SIBLING", before=raw, after=model, vin=self.norm_vin)))
        elif F.is_unreadable(raw):
            if lb:
                self.add(FieldDecision(COL_MODEL, "CORRECT", "R6", "LEGACY_B", raw, lb,
                                       explanation=render("R6", "LEGACY_B", after=lb, vin=self.norm_vin)))
            else:
                expl = render("R6", "UNREADABLE", before=raw, vin=self.norm_vin)
                self.add(self.resolve(make_key(COL_MODEL, "UNREADABLE"), COL_MODEL, "R6", "UNREADABLE", raw, expl))
        else:
            self.unknown_variant(COL_MODEL, "R6", raw, F.normalized_key(raw),
                                 AllowedValues(values=self.cfg.vehicle_models), vin=self.norm_vin)
        if not with_r7:
            return
        if len(idx) >= 2:
            models = ", ".join(sorted(idx))
            expl = render("R7", "VIN_MULTI_MODEL", vin=self.norm_vin, n=len(idx), models=models)
            self.add(self.resolve(make_key(COL_MODEL, "VIN_MULTI_MODEL"), COL_MODEL, "R7", "VIN_MULTI_MODEL", raw, expl))
        if rule_line and lb and lb != self.decided_model:
            expl = render("R7", "LEGACY_B_CONFLICT", decided=self.decided_model, legacy_b=lb, vin=self.norm_vin)
            self.add(self.resolve(make_key(COL_MODEL, "LEGACY_B_CONFLICT"), COL_MODEL, "R7", "LEGACY_B_CONFLICT",
                                  raw, expl))

    # ---------------------------------------------------------------- step 9
    def step_op_id(self) -> None:
        raw = self.raw(COL_OP_ID)
        identity = self.identity()
        id_ask = any(f in self.ask_fields for f in IDENTITY_FIELDS)
        if F.is_unreadable(raw):
            if not id_ask:
                gid = generate_op_id(identity)
                self.generated = True
                self.add(FieldDecision(COL_OP_ID, "CORRECT", "R8", "GENERATED_ID", raw, gid,
                                       explanation=render("R8", "GENERATED_ID", after=gid)))
            return
        self.add(FieldDecision(COL_OP_ID, "MIGRATE", "R8", "COPIED", raw, raw,
                               explanation=render("R8", "COPIED", field=COL_OP_ID, before=raw)))
        if raw.strip() not in self.ctx.shared_op_ids:
            return
        key = make_key(COL_OP_ID, "SHARED_OP_ID")
        ext = extend_key(key, self.row.source_row_id)
        used = ext if ext in self.ctx.human_decisions else key
        hd = self.ctx.human_decisions.get(used)
        if hd is not None and hd.kind == "generate":
            if not id_ask:
                gid = generate_op_id(identity)
                self.generated = True
                self.add(FieldDecision(COL_OP_ID, "CORRECT", "R7", "HUMAN_GENERATE", raw, gid, "human", None,
                                       human_explanation(used, "generate_op_id", gid), 0, used))
            return
        expl = render("R7", "SHARED_OP_ID", before=raw)
        self.add(self.resolve(key, COL_OP_ID, "R7", "SHARED_OP_ID", raw, expl))

    # ---------------------------------------------------------------- steps 10-11
    def step_final_checks(self) -> None:
        if any(d.action in ("REJECT", "ASK_HUMAN") for d in self.decisions):
            return
        failed = final_checks(self.values, self.cfg)
        for d in failed:
            self.add(replace(d, before=self.raw(d.field)))
        self.fc_passed = not failed

    def row_record(self, outcome: RowOutcome) -> FieldDecision:
        top = RANK[outcome]
        best = next(d for d in self.decisions if RANK[d.action] == top)
        counts = Counter(d.action for d in self.decisions)
        text = ", ".join(f"{a} {counts[a]}" for a in ACTIONS if counts[a])
        checks = " Final checks FC1-FC4 passed." if self.fc_passed else ""
        expl = render(ROW_FIELD, "ROW_OUTCOME", outcome=outcome, rule_id=best.rule_id, reason=best.reason,
                      n=len(self.decisions), counts=text, checks=checks)
        return FieldDecision(ROW_FIELD, outcome, best.rule_id, best.reason, "", "", best.decided_by,
                             best.confidence if best.decided_by == "llm" else None, expl, 0, best.pattern_key)

    def target(self) -> TargetValues:
        v = self.values
        try:
            return TargetValues(
                vin=v[COL_VIN], vehicle_model=v[COL_MODEL], ecu_name=v[COL_ECU_NAME], diag_tool_name=v[COL_TOOL],
                diag_tool_version=v[COL_TOOL_VERSION], diagnostic_op_id=v[COL_OP_ID], op_id_generated=self.generated,
                creation_date=datetime.strptime(v[COL_DATE], ISO_MINUTE), value_description=v[COL_ECU_CODE],
                value_reference=v[COL_VALUE_REF], source_origin_tool=self.raw(COL_TOOL))
        except (KeyError, ValueError) as exc:  # pragma: no cover - guarded by the rule tables
            raise ValueError(f"row {self.row.source_row_id}: target value missing or invalid: {exc}") from None

    def run_fields(self, with_r7: bool) -> None:
        self.step_vin()
        self.step_tool()
        self.step_tool_version()
        self.step_date()
        self.step_ecu_code()
        self.step_value_ref()
        self.step_ecu_name()
        self.step_model(with_r7)


_EMPTY_IDENTITY = OperationIdentity(None, None, None, None, None, None, None)


def scan_row(row: RawRow, ctx: RuleContext) -> RowScan:
    """Field-local evaluation: normalized VIN, R6 dictionary match, trimmed op id, identity, Unknown_Variants."""
    if row.malformed:
        return RowScan("", None, None, _EMPTY_IDENTITY, ())
    ev = _Eval(row, ctx)
    ev.run_fields(with_r7=False)
    raw_id = row.values.get(COL_OP_ID, "")
    model_raw = row.values.get(COL_MODEL, "")
    matched = ev.memo("vehmodel", model_raw, lambda s: F.dict_lookup(s, ctx.config.vehicle_models))
    return RowScan(norm_vin=ev.norm_vin, matched_model=matched,
                   trimmed_op_id=None if F.is_unreadable(raw_id) else raw_id.strip(),
                   identity=ev.identity(), unknown_variants=tuple(ev.unknown))


def decide_row(row: RawRow, ctx: RuleContext) -> RowDecision:
    """Full decision of one row: Field_Decisions in evaluation order, then the ``_row`` record."""
    if row.malformed:
        d = FieldDecision(ROW_FIELD, "REJECT", "INGEST", "MALFORMED_ROW", "", "",
                          explanation=render("INGEST", "MALFORMED_ROW"))
        return RowDecision(row.source_row_id, (d,), "REJECT", _EMPTY_IDENTITY, None)
    ev = _Eval(row, ctx)
    ev.run_fields(with_r7=True)
    ev.step_op_id()
    ev.step_final_checks()
    outcome = row_outcome(ev.decisions)
    record = ev.row_record(outcome)
    target = ev.target() if outcome in ("MIGRATE", "CORRECT") else None
    return RowDecision(row.source_row_id, tuple(ev.decisions) + (record,), outcome, ev.identity(), target)
