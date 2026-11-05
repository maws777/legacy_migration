"""MigrationPipeline: builds the dependencies and drives the Run_Workflow."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agents.api_agent_legacy import LocalLegacyASource, SchemaDiscoveryAgent, mapping_provider_for
from agents.llm_client import LLMClient, read_call_rows
from reporting.cost import breakdown as cost_breakdown
from reporting.schemas import CostBreakdown
from agents.resolution_cache import ResolutionCache
from agents.resolver_agent import ResolverAgent
from hitl.queue import HITLQueue
from legacy_a.store import LegacyAStore
from hitl.validation import SharedIdView
from migration.context import SharedOpIdStore
from migration.run_store import RunRepository, RunStore
from migration.workflow import PipelineDeps, build_run_graph
from reporting.decision_log import DecisionLogError, DecisionLogger, UnknownRunError
from reporting.metrics import MetricsStore
from reporting.report import ReportGenerator
from reporting.schemas import RunInfo, RunMetrics
from rules.config import DEFAULT_CONFIG_DIR, load_rule_config
from target.repository import TargetRepository
from target.schema import create_target_schema, make_engine, sqlite_url

ROW_OUTCOMES = ("MIGRATE", "CORRECT", "REJECT", "ASK_HUMAN")


class CampaignNotPaused(Exception):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(f"campaign {campaign_id} is not paused")
        self.campaign_id = campaign_id

RECURSION_LIMIT = 100_000


class MigrationPipeline:
    def __init__(self, source_path: Path | str, var_dir: Path | str = "var",
                 config_dir: Path | str = DEFAULT_CONFIG_DIR, **components) -> None:
        self.var_dir = Path(var_dir)
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.ops_engine = make_engine(sqlite_url(self.var_dir / "ops.db"))
        self.runs = RunRepository(self.ops_engine)
        self.decision_logger = DecisionLogger(self.ops_engine)
        self.metrics = MetricsStore(self.ops_engine)
        self.reports = ReportGenerator(self.ops_engine)
        self.target_engine = make_engine(sqlite_url(self.var_dir / "target.db"))   # no connection yet
        self._target_md = None
        self.target_repo = TargetRepository(self.target_engine)
        self.deps = PipelineDeps(source_path=Path(source_path), config_dir=Path(config_dir), runs=self.runs,
                                 run_store=RunStore(), decision_logger=self.decision_logger,
                                 shared_ids=SharedOpIdStore(self.ops_engine), target_repo=self.target_repo,
                                 on_config=self._on_config, report=self._report)
        self._cfg_cache: dict = {}
        self.hitl = HITLQueue(self.ops_engine, self.config, self._shared_view)
        self.deps.hitl = self.hitl
        self.deps.human_decisions = self.hitl.decisions.views
        self.cache = ResolutionCache(self.ops_engine)
        self.deps.cache = self.cache
        source = Path(source_path)
        self.schema_agent = SchemaDiscoveryAgent(LocalLegacyASource(lambda: LegacyAStore.from_path(source)),
                                                 self.ops_engine, self.config)
        self.deps.mapping_provider = mapping_provider_for(self.schema_agent)
        for name, value in components.items():
            setattr(self.deps, name, value)
        if self.deps.llm is not None:
            self.schema_agent.llm = self.deps.llm
        self._ckpt_conn = sqlite3.connect(str(self.var_dir / "checkpoints.db"), check_same_thread=False)
        self.checkpointer = SqliteSaver(self._ckpt_conn)
        self.graph = build_run_graph(self.deps, self.checkpointer)

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}, "max_concurrency": 1,
                "recursion_limit": RECURSION_LIMIT}

    def _invoke(self, payload, campaign_id: str) -> RunInfo:
        try:
            self.graph.invoke(payload, self._config(campaign_id))
        except DecisionLogError as exc:
            self.runs.finish_run(exc.run_id, "failed", error=str(exc))
            self.runs.set_campaign_status(campaign_id, "done")
            return self.run_info(exc.run_id)
        return self._after_invoke(campaign_id)

    def start_campaign(self, operator: str | None = None) -> RunInfo:
        campaign_id = str(uuid.uuid4())
        self.runs.create_campaign(campaign_id, operator)
        return self._invoke({"campaign_id": campaign_id, "kind": "full", "operator": operator}, campaign_id)

    def resume(self, campaign_id: str, operator: str) -> RunInfo:
        """Resume a paused campaign: ``Command(resume=...)``; the next Run gets a new run_id."""
        if self.runs.campaign_status(campaign_id) != "paused":
            raise CampaignNotPaused(campaign_id)
        self.runs.set_campaign_status(campaign_id, "running")
        return self._invoke(Command(resume={"operator": operator}), campaign_id)

    def reload_rows(self, source_row_ids) -> RunInfo:
        """Run of kind ``reload``: full context, load and log only the listed rows, no Human_Question."""
        campaign_id = f"reload-{uuid.uuid4()}"
        self.runs.create_campaign(campaign_id, "reconciler")
        return self._invoke({"campaign_id": campaign_id, "kind": "reload", "operator": "reconciler",
                             "reload_rows": sorted({int(i) for i in source_row_ids})}, campaign_id)

    def config(self):
        """Current Rule_Config (cached per file hash)."""
        cfg = load_rule_config(self.deps.config_dir)
        return self._cfg_cache.setdefault(cfg.config_hash, cfg)

    def _shared_view(self) -> SharedIdView:
        latest = self.runs.latest_full_run()
        if latest is None:
            return SharedIdView()
        shared = self.deps.shared_ids.load(latest["run_id"])
        used: frozenset[str] = frozenset()
        if self.deps.run_store.has(latest["run_id"]):
            scans = self.deps.run_store.get(latest["run_id"]).scans.values()
            used = frozenset(s.trimmed_op_id for s in scans if s.trimmed_op_id)
        return SharedIdView(shared=shared, used=used)

    def _after_invoke(self, campaign_id: str) -> RunInfo:
        snapshot = self.graph.get_state(self._config(campaign_id))
        paused = bool(snapshot.next)
        self.runs.set_campaign_status(campaign_id, "paused" if paused else "done")
        return self.run_info(self.runs.latest_run(campaign_id)["run_id"])

    def run_info(self, run_id: str) -> RunInfo:
        r = self.runs.get_run(run_id)
        if r is None:
            raise KeyError(run_id)
        return RunInfo(run_id=r["run_id"], campaign_id=r["campaign_id"], kind=r["kind"], status=r["status"],
                       campaign_status=self.runs.campaign_status(r["campaign_id"]) or "done",
                       started_at=r["started_at"], ended_at=r["ended_at"],
                       open_questions=r["open_questions"] or 0, error=r["error"])

    def run_data(self, run_id: str):
        return self.deps.run_store.get(run_id)

    # ---------------------------------------------------------------- hooks used by the workflow
    def ensure_llm(self, cfg=None):
        """Default LLM_Client and Resolver_Agent, built from Rule_Config unless injected."""
        if self.deps.llm is None:
            cfg = cfg or self.config()
            self.deps.llm = LLMClient(cfg.pipeline.llm, self.ops_engine)
            self.schema_agent.llm = self.deps.llm
        if self.deps.resolver is None:
            cfg = cfg or self.config()
            self.deps.resolver = ResolverAgent(self.deps.llm, cfg.pipeline.llm.max_tokens,
                                               cfg.pipeline.resolver_max_iterations)
        return self.deps.llm

    def seed_reference_mapping(self, operator: str = "setup"):
        """Discover (reference fallback without LLM) and validate the Column_Mapping."""
        proposal = self.schema_agent.discover()
        return self.schema_agent.validate(proposal.proposal_id, operator)

    def _on_config(self, cfg) -> None:
        self.ensure_llm(cfg)
        if self._target_md is None:
            self._target_md = create_target_schema(self.target_engine, cfg)
            self.target_repo._md = self._target_md

    def _report(self, data, state) -> int:
        """Report node: supersede earlier questions, then report and metrics from the same values."""
        cfg = data.cfg
        deps = self.deps
        with data.timer.stage("reporting"):
            if deps.hitl is not None and data.kind == "full":
                deps.hitl.supersede_before(data.run_id)
            raw_counts = self.decision_logger.row_counts(data.run_id)
            counts = {k: raw_counts.get(k, 0) for k in ROW_OUTCOMES}
            out_of_scope = self.decision_logger.rejected_out_of_scope(data.run_id)
            loaded = self.decision_logger.loaded_rows(data.run_id)
            cost = self.cost_breakdown(data.run_id, cfg)
            tin, tout, calls = cost.input_tokens, cost.output_tokens, cost.calls
            open_q = deps.hitl.open_count(data.run_id) if deps.hitl is not None and data.kind == "full" else 0
            keys = {d.pattern_key.split("#row=")[0] for dec in data.decisions.values() for d in dec.fields
                    if d.pattern_key}
            notes = list(data.notes)
            if data.degraded:
                reason = data.extra.get("degraded_reason") or "no LLM provider available"
                notes.append(f"Degraded_Mode: {reason}; Unknown_Variants were sent to humans.")
        seconds = data.timer.total()
        # Cost from the Rule_Config prices of the chosen provider (0 EUR for a local Ollama model).
        price_in, price_out = cfg.pipeline.llm.price_per_token() if cfg is not None else (0.0, 0.0)
        report = ReportGenerator.compose(
            data.run_id, analysed=len(data.rows), counts=counts, out_of_scope=out_of_scope,
            traced_rows=sum(counts.values()), input_tokens=tin, output_tokens=tout,
            price_in=price_in, price_out=price_out, seconds=seconds, loaded=loaded,
            cache_state="warm" if data.extra.get("cache_entries", 0) else "cold", notes=notes, cost=cost)
        self.metrics.save(RunMetrics(
            run_id=data.run_id, total_seconds=report.seconds, stage_seconds=dict(data.timer.seconds),
            input_tokens=tin, output_tokens=tout, llm_calls=calls, distinct_pattern_keys=len(keys),
            cache_hit_rate=(data.cache_hits / data.cache_lookups) if data.cache_lookups else 0.0,
            batch_size=cfg.pipeline.batch_size if cfg is not None else 0, out_of_scope_rejects=out_of_scope,
            degraded_mode=data.degraded, legacy_b_lookup=data.legacy_b_status, counts=counts))
        self.reports.save(report)
        return open_q

    def report_text(self, run_id: str) -> str | None:
        try:
            return self.reports.build(run_id).text
        except UnknownRunError:
            return None

    # ---------------------------------------------------------------- AI cost (reporting/cost.py)
    def _llm_rows(self) -> list[dict]:
        llm = self.deps.llm
        if llm is not None and hasattr(llm, "call_rows") and not isinstance(llm, LLMClient):
            return llm.call_rows()                    # test fake or NoLLM: no shared llm_call table
        return read_call_rows(self.ops_engine)

    def cost_breakdown(self, run_id: str, cfg=None) -> CostBreakdown:
        """Tokens attributed to the Run by purpose, real cost and cloud-equivalent cost."""
        cfg = cfg or self.config()
        runs = self.runs.list_runs(limit=1_000_000)
        return cost_breakdown(run_id, runs, self._llm_rows(), cfg.pipeline.llm)

    def report_now(self, run_id: str):
        """The stored end-of-run report with the AI usage recomputed now (advice and chat made after the Run
        are included). Same CostBreakdown as ``metrics_now``, so the two always agree."""
        report = self.reports.build(run_id)
        cost = self.cost_breakdown(run_id)
        return ReportGenerator.with_cost(report, cost)

    def metrics_now(self, run_id: str):
        m = self.metrics.get(run_id)
        if m is None:
            return None
        cost = self.cost_breakdown(run_id)
        return m.model_copy(update={
            "input_tokens": cost.input_tokens, "output_tokens": cost.output_tokens, "llm_calls": cost.calls,
            "tokens_by_purpose": cost.tokens_by_purpose(), "real_cost_eur": cost.real_cost_eur,
            "cloud_equivalent_cost_eur": cost.cloud_equivalent_cost_eur})

    def close(self) -> None:
        self._ckpt_conn.close()
        self.ops_engine.dispose()
        self.target_engine.dispose()
