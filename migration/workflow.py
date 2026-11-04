"""Run_Workflow (LangGraph). Each node processes control data, one batch or one Pattern_Key, never a row.

Fan-out uses ``Send``; join nodes (``after_*``, ``freeze_context``) run once per stage. Fan-out nodes
write only to the process-local RunStore and return no state update, so the checkpoint stays small.
"""
from __future__ import annotations

import math
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from hitl.schemas import QuestionGroup
from migration.context import SharedOpIdStore, merge_partials, partial_op_ids, partial_vin_models, shared_from_usage
from migration.ingestion import IngestionError, SourceReadError, ingest
from migration.run_store import RunData, RunRepository, RunStore
from reporting.decision_log import DecisionLogger, to_record
from reporting.metrics import StageTimer
from rules.config import ConfigError, RuleConfig, load_rule_config
from rules.engine import decide_row, scan_row
from rules.fields import is_valid_vin
from rules.memo import build_field_memo, distinct_values
from rules.templates import render
from rules.timeutil import utcnow
from rules.types import (
    CANONICAL_COLUMNS, COL_LABEL, LOAD_FIELD, ROW_FIELD, CachedResolution, FieldDecision, HumanDecisionView, RowDecision,
    RuleContext,
)

ROW_OUTCOMES = ("MIGRATE", "CORRECT", "REJECT", "ASK_HUMAN")


class RunState(TypedDict, total=False):
    campaign_id: str
    run_id: str
    kind: Literal["full", "reload"]
    reload_rows: list[int]
    operator: str | None
    status: Literal["running", "failed", "mapping_pending", "completed"]
    n_batches: int
    unknown_keys: list[str]
    ask_keys: list[str]
    open_questions: int
    degraded: bool
    legacy_b_status: Literal["enabled", "disabled", "failed"]


@dataclass
class PipelineDeps:
    source_path: Path
    config_dir: Path
    runs: RunRepository
    run_store: RunStore
    decision_logger: DecisionLogger
    shared_ids: SharedOpIdStore
    # Optional components, plugged in by later tasks. ``None`` means "empty provider".
    mapping_provider: Callable[[RuleConfig, tuple[str, ...] | None], tuple[dict | None, str | None]] | None = None
    human_decisions: Callable[[], Mapping[str, HumanDecisionView]] | None = None
    cache: Any = None              # agents.resolution_cache.ResolutionCache
    resolver: Any = None           # agents.resolver_agent.ResolverAgent
    llm: Any = None                # agents.llm_client.LLMClient
    legacy_b: Any = None           # agents.api_agent_legacy_b.ApiAgentLegacyB
    hitl: Any = None               # hitl.queue.HITLQueue
    target_repo: Any = None        # target.repository.TargetRepository
    on_config: Callable[[RuleConfig], None] | None = None   # e.g. create the target schema
    report: Callable[[RunData, RunState], Any] | None = None  # report + metrics writer
    phase: Callable[[], int] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _reference_mapping(cfg: RuleConfig, _header) -> tuple[dict | None, str | None]:
    return dict(cfg.column_mapping.mapping), None


def _batches(data: RunData, run_id: str, node: str) -> list[Send]:
    return [Send(node, {"run_id": run_id, "batch": i}) for i in range(len(data.batch_bounds))]


def build_run_graph(deps: PipelineDeps, checkpointer: BaseCheckpointSaver | None):
    mapping_provider = deps.mapping_provider or _reference_mapping
    store = deps.run_store

    # ---------------------------------------------------------------- control: start of a Run
    def init_run(state: RunState) -> dict:
        run_id = str(uuid.uuid4())
        ts = utcnow()
        kind = state.get("kind", "full")
        phase = deps.phase() if deps.phase else None
        deps.runs.create_run(run_id, state["campaign_id"], kind, ts, state.get("operator"), phase)
        data = RunData(run_id=run_id, campaign_id=state["campaign_id"], kind=kind, ingestion_ts=ts,
                       operator=state.get("operator"), timer=StageTimer(),
                       reload_ids=frozenset(state.get("reload_rows") or ()))
        store.put(data)
        return {"run_id": run_id, "status": "running", "n_batches": 0, "open_questions": 0,
                "unknown_keys": [], "ask_keys": []}

    def load_config(state: RunState) -> dict:
        data = store.get(state["run_id"])
        with data.timer.stage("ingestion"):
            try:
                data.cfg = load_rule_config(deps.config_dir)
            except ConfigError as exc:
                deps.runs.finish_run(state["run_id"], "failed", error=f"Rule_Config {exc.file}: {exc.error}")
                return {"status": "failed"}
            deps.runs.update_run(state["run_id"], config_hash=data.cfg.config_hash)
            if deps.on_config:
                deps.on_config(data.cfg)
        return {}

    def check_mapping(state: RunState) -> dict:
        data = store.get(state["run_id"])
        with data.timer.stage("ingestion"):
            mapping, note = mapping_provider(data.cfg, deps.source_path)
        if mapping is None:
            data.notes.append(note or "Column_Mapping pending validation: no row processed.")
            return {"status": "mapping_pending"}
        if note:
            data.notes.append(note)
        data.extra["mapping"] = mapping
        return {}

    def ingest_node(state: RunState) -> dict:
        data = store.get(state["run_id"])
        with data.timer.stage("ingestion"):
            try:
                res = ingest(deps.source_path, data.extra["mapping"], data.cfg.column_mapping)
            except SourceReadError as exc:
                deps.runs.finish_run(state["run_id"], "failed", error=f"source read error: {exc.reason}")
                return {"status": "failed"}
            except IngestionError as exc:
                deps.runs.finish_run(state["run_id"], "failed", error=f"ingestion error: {exc.reason}")
                return {"status": "failed"}
            data.rows, data.header, data.sha256 = res.rows, res.header, res.sha256
            size = data.cfg.pipeline.batch_size
            n = len(res.rows)
            data.batch_bounds = [(i * size, min(n, (i + 1) * size)) for i in range(math.ceil(n / size))]
            deps.runs.update_run(state["run_id"], source_sha256=res.sha256, rows_read=n)
        return {"n_batches": len(data.batch_bounds)}

    def prepare(state: RunState) -> dict:
        data = store.get(state["run_id"])
        cfg = data.cfg
        with data.timer.stage("rules"):
            readable = [r for r in data.rows if not r.malformed]
            data.memo = build_field_memo(distinct_values(readable, CANONICAL_COLUMNS), cfg)
            data.human = dict(deps.human_decisions()) if deps.human_decisions else {}
            data.extra["cache_entries"] = deps.cache.count() if deps.cache is not None else 0
            data.degraded = deps.llm is None or not deps.llm.is_available()
            if not cfg.pipeline.legacy_b_lookup:
                data.legacy_b_status, data.legacy_b_index = "disabled", {}
            elif deps.legacy_b is None:
                data.legacy_b_status, data.legacy_b_index = "disabled", {}
                data.notes.append("Legacy B lookup enabled but no API_Agent_Legacy_B is configured.")
            else:
                vins = sorted({v for v in data.memo.vin.values() if is_valid_vin(v)})
                res = deps.legacy_b.model_index(vins)
                if res.status == "ok":
                    data.legacy_b_status, data.legacy_b_index = "enabled", dict(res.index)
                else:
                    data.legacy_b_status, data.legacy_b_index = "failed", {}
                    data.notes.append("Legacy B lookup failed: empty Legacy_B_Model_Index used for this Run.")
            data.extra["scan_ctx"] = RuleContext(
                config=cfg, human_decisions=data.human, legacy_b_model_index=data.legacy_b_index,
                degraded=data.degraded, memo=data.memo)
        return {"degraded": data.degraded, "legacy_b_status": data.legacy_b_status}

    # ---------------------------------------------------------------- scan and resolve
    def scan(payload: dict) -> dict:
        data = store.get(payload["run_id"])
        with data.timer.stage("rules"):
            sctx = data.extra["scan_ctx"]
            for r in data.batch(payload["batch"]):
                data.scans[r.source_row_id] = scan_row(r, sctx)
        return {}

    def after_scan(state: RunState) -> dict:
        data = store.get(state["run_id"])
        unknown: dict = {}
        for rid in sorted(data.scans):
            for uv in data.scans[rid].unknown_variants:
                unknown.setdefault(uv.pattern_key, uv)
        data.unknown = unknown
        return {"unknown_keys": list(unknown)}

    def resolve(payload: dict) -> dict:
        data = store.get(payload["run_id"])
        key = payload["key"]
        cfg = data.cfg
        with data.timer.stage("ai_resolution"):
            uv = data.unknown[key]
            if deps.cache is not None:
                data.cache_lookups += 1
                hit = deps.cache.get_many([key]).get(key)
                if hit is not None:
                    data.cache_hits += 1
                    data.resolved[key] = hit
                    return {}
            if data.degraded or deps.resolver is None or data.kind == "reload":
                data.llm_status[key] = "degraded"
                return {}
            suggestion, tokens = deps.resolver.propose(data.run_id, uv)
            auto = uv.field in cfg.pipeline.resolver_auto_apply_fields
            if suggestion.confidence <= 0.0:
                data.llm_status[key] = "failed"
                data.llm_suggestions[key] = suggestion
            elif suggestion.confidence >= cfg.pipeline.confidence_threshold and auto:
                res = CachedResolution(key, suggestion.decision, suggestion.value, suggestion.confidence,
                                       suggestion.justification)
                if deps.cache is not None:
                    deps.cache.put(res, data.run_id, cfg.pipeline.llm.model, field=uv.field,
                                   normalized_value=uv.normalized_value)
                data.resolved[key] = res
                data.llm_tokens[key] = tokens
            else:
                data.llm_status[key] = "below_threshold" if auto else "human_required"
                data.llm_suggestions[key] = suggestion
        return {}

    # ---------------------------------------------------------------- index and freeze
    def plan_index(state: RunState) -> dict:
        return {}

    def index(payload: dict) -> dict:
        data = store.get(payload["run_id"])
        with data.timer.stage("rules"):
            scans = [data.scans[r.source_row_id] for r in data.batch(payload["batch"])]
            data.partials[payload["batch"]] = (partial_vin_models(scans), partial_op_ids(scans))
        return {}

    def freeze_context(state: RunState) -> dict:
        data = store.get(state["run_id"])
        with data.timer.stage("rules"):
            parts = [data.partials[i] for i in sorted(data.partials)]
            vin_models = merge_partials(p[0] for p in parts)
            usage = merge_partials(p[1] for p in parts)
            shared = shared_from_usage(usage)
            deps.shared_ids.save(data.run_id, shared)
            data.ctx = RuleContext(
                config=data.cfg, human_decisions=data.human, cache=dict(data.resolved),
                llm_suggestions=dict(data.llm_suggestions), llm_status=dict(data.llm_status),
                llm_tokens=dict(data.llm_tokens),
                vin_model_index={k: frozenset(v) for k, v in vin_models.items()},
                legacy_b_model_index=data.legacy_b_index, shared_op_ids=shared, degraded=data.degraded,
                memo=data.memo)
        return {}

    # ---------------------------------------------------------------- decide and ask
    def decide(payload: dict) -> dict:
        data = store.get(payload["run_id"])
        records, meta = [], []
        with data.timer.stage("rules"):
            ts = utcnow()
            for r in data.batch(payload["batch"]):
                if data.kind == "reload" and r.source_row_id not in data.reload_ids:
                    continue
                dec = decide_row(r, data.ctx)
                data.decisions[r.source_row_id] = dec
                for d in dec.fields[:-1]:
                    records.append(to_record(data.run_id, r.source_row_id, d, ts))
                    meta.append({"pattern_key": d.pattern_key})
        deps.decision_logger.write(records, meta)
        return {}

    def after_decide(state: RunState) -> dict:
        data = store.get(state["run_id"])
        if data.kind == "reload" or deps.hitl is None:
            return {"ask_keys": []}
        with data.timer.stage("human_routing"):
            data.groups = build_question_groups(data)
        return {"ask_keys": list(data.groups)}

    def ask(payload: dict) -> dict:
        data = store.get(payload["run_id"])
        with data.timer.stage("human_routing"):
            group = data.groups[payload["key"]]
            deps.hitl.create_questions(data.run_id, {group.pattern_key: group})
        return {}

    # ---------------------------------------------------------------- load
    def load(payload: dict) -> dict:
        data = store.get(payload["run_id"])
        with data.timer.stage("load"):
            decs = [data.decisions[r.source_row_id] for r in data.batch(payload["batch"])
                    if r.source_row_id in data.decisions]
            failures: dict[int, str] = {}
            loaded: set[int] = set()
            loadable = [d for d in decs if d.outcome in ("MIGRATE", "CORRECT") and d.target is not None]
            if deps.target_repo is not None and loadable:
                result = deps.target_repo.load_rows(data.run_id, data.ingestion_ts, loadable)
                failures = {f.source_row_id: f.constraint for f in result.failed}
                loaded = set(result.loaded)
            ts = utcnow()
            records, meta = [], []
            for dec in decs:
                rid = dec.source_row_id
                if rid in failures:
                    dec = _load_reject(dec, failures[rid])
                    data.decisions[rid] = dec
                    load_rec = dec.fields[-2]
                    records.append(to_record(data.run_id, rid, load_rec, ts))
                    meta.append({})
                    status = "load_rejected"
                else:
                    status = "loaded" if rid in loaded else "not_loaded"
                row = dec.fields[-1]
                if status == "loaded":
                    row = replace(row, explanation=row.explanation + " Loaded into the Target_DB.")
                records.append(to_record(data.run_id, rid, row, ts))
                meta.append({"load_status": status, "pattern_key": row.pattern_key})
        deps.decision_logger.write(records, meta)
        return {}

    # ---------------------------------------------------------------- report and pause
    def report(state: RunState) -> dict:
        data = store.get(state["run_id"])
        status = state.get("status", "running")
        final = "mapping_pending" if status == "mapping_pending" else "completed"
        open_q = 0
        if deps.report is not None:
            open_q = deps.report(data, state) or 0
        else:
            counts = Counter(d.outcome for d in data.decisions.values())
            data.extra["counts"] = {k: counts.get(k, 0) for k in ROW_OUTCOMES}
        deps.runs.finish_run(data.run_id, final, rows_read=len(data.rows), open_questions=open_q)
        return {"status": final, "open_questions": open_q}

    def await_humans(state: RunState) -> dict:
        answer = interrupt({"run_id": state["run_id"], "open_questions": state.get("open_questions", 0)})
        operator = answer.get("operator") if isinstance(answer, dict) else None
        return {"operator": operator, "kind": "full"}

    # ---------------------------------------------------------------- routing
    def after_config(state: RunState) -> str:
        return END if state.get("status") == "failed" else "check_mapping"

    def after_mapping(state: RunState) -> str:
        return "report" if state.get("status") == "mapping_pending" else "ingest"

    def after_ingest(state: RunState) -> str:
        return END if state.get("status") == "failed" else "prepare"

    def to_scan(state: RunState):
        data = store.get(state["run_id"])
        return _batches(data, state["run_id"], "scan") or "report"

    def to_resolve(state: RunState):
        keys = state.get("unknown_keys") or []
        return [Send("resolve", {"run_id": state["run_id"], "key": k}) for k in keys] or "plan_index"

    def to_index(state: RunState):
        return _batches(store.get(state["run_id"]), state["run_id"], "index")

    def to_decide(state: RunState):
        return _batches(store.get(state["run_id"]), state["run_id"], "decide")

    def to_ask(state: RunState):
        keys = state.get("ask_keys") or []
        return [Send("ask", {"run_id": state["run_id"], "key": k}) for k in keys] or "after_ask"

    def to_load(state: RunState):
        return _batches(store.get(state["run_id"]), state["run_id"], "load")

    def after_report(state: RunState) -> str:
        if state.get("kind", "full") == "full" and state.get("open_questions", 0) > 0:
            return "await_humans"
        return END

    g = StateGraph(RunState)
    for name, fn in [("init_run", init_run), ("load_config", load_config), ("check_mapping", check_mapping),
                     ("ingest", ingest_node), ("prepare", prepare), ("scan", scan), ("after_scan", after_scan),
                     ("resolve", resolve), ("plan_index", plan_index), ("index", index),
                     ("freeze_context", freeze_context), ("decide", decide), ("after_decide", after_decide),
                     ("ask", ask), ("after_ask", plan_index), ("load", load), ("report", report),
                     ("await_humans", await_humans)]:
        g.add_node(name, fn)
    g.add_edge(START, "init_run")
    g.add_edge("init_run", "load_config")
    g.add_conditional_edges("load_config", after_config, ["check_mapping", END])
    g.add_conditional_edges("check_mapping", after_mapping, ["ingest", "report"])
    g.add_conditional_edges("ingest", after_ingest, ["prepare", END])
    g.add_conditional_edges("prepare", to_scan, ["scan", "report"])
    g.add_edge("scan", "after_scan")
    g.add_conditional_edges("after_scan", to_resolve, ["resolve", "plan_index"])
    g.add_edge("resolve", "plan_index")
    g.add_conditional_edges("plan_index", to_index, ["index"])
    g.add_edge("index", "freeze_context")
    g.add_conditional_edges("freeze_context", to_decide, ["decide"])
    g.add_edge("decide", "after_decide")
    g.add_conditional_edges("after_decide", to_ask, ["ask", "after_ask"])
    g.add_edge("ask", "after_ask")
    g.add_conditional_edges("after_ask", to_load, ["load"])
    g.add_edge("load", "report")
    g.add_conditional_edges("report", after_report, ["await_humans", END])
    g.add_edge("await_humans", "init_run")
    return g.compile(checkpointer=checkpointer)


def _load_reject(dec: RowDecision, constraint: str) -> RowDecision:
    """A row refused by the Target_DB becomes REJECT LOAD (Requirements 3.9, 18.5)."""
    load = FieldDecision(LOAD_FIELD, "REJECT", "LOAD", "CONSTRAINT", "", "",
                         explanation=render("LOAD", "CONSTRAINT", constraint=constraint))
    fields = dec.fields[:-1] + (load,)
    counts = Counter(d.action for d in fields)
    text = ", ".join(f"{a} {n}" for a, n in sorted(counts.items()))
    row = FieldDecision(ROW_FIELD, "REJECT", "LOAD", "CONSTRAINT", "", "",
                        explanation=render(ROW_FIELD, "ROW_OUTCOME", outcome="REJECT", rule_id="LOAD",
                                           reason="CONSTRAINT", n=len(fields), counts=text)
                        + f" Target DB refused the row: {constraint}.")
    return RowDecision(dec.source_row_id, fields + (row,), "REJECT", dec.identity, None)


def build_question_groups(data: RunData) -> dict[str, QuestionGroup]:
    """One group per distinct ASK_HUMAN Pattern_Key of the Run, rows in source order."""
    cfg = data.cfg
    n_examples = cfg.pipeline.hitl_example_rows
    rows_by_id = {r.source_row_id: r for r in data.rows}
    acc: dict[str, dict] = {}
    for rid in sorted(data.decisions):
        for d in data.decisions[rid].fields[:-1]:
            if d.action != "ASK_HUMAN" or not d.pattern_key:
                continue
            g = acc.setdefault(d.pattern_key, {"first": d, "rows": [], "raw": [], "ctx": set()})
            if not g["rows"] or g["rows"][-1] != rid:
                g["rows"].append(rid)
                g["raw"].append(d.before)
                # Homogeneous group (decisions.md): same raw value and same context (the raw Label).
                g["ctx"].add((d.before, rows_by_id[rid].values.get(COL_LABEL, "")))
    groups: dict[str, QuestionGroup] = {}
    for key, g in acc.items():
        first: FieldDecision = g["first"]
        rows = tuple(g["rows"])
        examples = tuple((rid, dict(rows_by_id[rid].values)) for rid in rows[:n_examples])
        groups[key] = QuestionGroup(
            pattern_key=key, field=first.field, reason=first.reason, rule_id=first.rule_id, rows=rows,
            examples=examples, llm_suggestion=data.llm_suggestions.get(key), llm_status=data.llm_status.get(key),
            homogeneous=len(g["ctx"]) == 1, explanation=first.explanation, raw_values=tuple(g["raw"]),
            row_values=tuple((rid, dict(rows_by_id[rid].values)) for rid in rows))
    return groups
