"""Report_Generator: end-of-run report in the exact ``product.md`` format, plus extra note lines."""
from __future__ import annotations

from sqlalchemy import Column, Engine, MetaData, String, Table, Text, delete, insert, select

from reporting.decision_log import UnknownRunError
from reporting.schemas import PURPOSE_LABELS, CostBreakdown, RunReport

SEPARATOR = "─────────────────────────"

REPORT_MD = MetaData()
run_report = Table(
    "run_report", REPORT_MD,
    Column("run_id", String(36), primary_key=True),
    Column("body", Text, nullable=False),
)


def _pct(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _duration(seconds: float) -> tuple[int, int]:
    return divmod(int(round(seconds)), 60)


def cost_eur(input_tokens: int, output_tokens: int, price_in: float, price_out: float) -> float:
    return input_tokens * price_in + output_tokens * price_out


class ReportGenerator:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        REPORT_MD.create_all(engine)

    @staticmethod
    def with_cost(report: RunReport, cost: CostBreakdown) -> RunReport:
        """The report with its AI usage taken from ``cost`` (tokens, real cost, by purpose, cloud equivalent)."""
        updated = report.model_copy(update={
            "tokens": cost.tokens, "cost_eur": cost.real_cost_eur, "tokens_by_purpose": cost.tokens_by_purpose(),
            "provider": f"{cost.provider} {cost.model}", "cloud_reference": cost.cloud_reference,
            "cloud_equivalent_cost_eur": cost.cloud_equivalent_cost_eur})
        return updated.model_copy(update={"text": ReportGenerator.format_text(updated)})

    @staticmethod
    def compose(run_id: str, *, analysed: int, counts: dict[str, int], out_of_scope: int, traced_rows: int,
                input_tokens: int, output_tokens: int, price_in: float, price_out: float, seconds: float,
                loaded: int, cache_state: str, notes: list[str], cost: CostBreakdown | None = None) -> RunReport:
        traced = 100.0 if analysed == 0 else 100.0 * traced_rows / analysed
        reject = counts.get("REJECT", 0)
        report = RunReport(
            run_id=run_id, text="", analysed=analysed, migrate=counts.get("MIGRATE", 0),
            correct=counts.get("CORRECT", 0), reject=reject, ask_human=counts.get("ASK_HUMAN", 0),
            out_of_scope=out_of_scope, traced_pct=round(traced, 1), tokens=input_tokens + output_tokens,
            cost_eur=cost_eur(input_tokens, output_tokens, price_in, price_out), seconds=round(seconds, 3),
            notes=list(notes), loaded=loaded,
            coverage_pct=0.0 if analysed == 0 else round(100.0 * loaded / analysed, 1),
            rejected_out_of_scope=out_of_scope, rejected_error=reject - out_of_scope,
            pending_human=counts.get("ASK_HUMAN", 0), cache_state=cache_state)  # type: ignore[arg-type]
        if cost is not None:
            return ReportGenerator.with_cost(report, cost)
        return report.model_copy(update={"text": ReportGenerator.format_text(report)})

    @staticmethod
    def format_text(r: RunReport) -> str:
        m, s = _duration(r.seconds)
        lines = [
            f"{r.analysed:,} records analysed",
            SEPARATOR,
            f"{r.migrate:,} migrated automatically",
            f"{r.correct:,} automatically corrected",
            f"{r.reject:,} rejected",
            f"{r.ask_human:,} requiring human decision",
            "",
            f"{_pct(r.traced_pct)}% decisions traced",
            f"Estimated AI consumption: {r.tokens:,} tokens (≈ {r.cost_eur:.4f} €)",
            f"Processing time: {m} min {s} sec",
        ]
        extra = [
            f"Coverage: {r.loaded:,} of {r.analysed:,} source rows loaded ({_pct(r.coverage_pct)}%)",
            f"Rejected: {r.rejected_out_of_scope:,} out of scope, {r.rejected_error:,} in error;"
            f" pending human decision: {r.pending_human:,}",
            f"Run type: {r.cache_state} ({'empty' if r.cache_state == 'cold' else 'filled'} Resolution_Cache)",
        ]
        if r.tokens_by_purpose:
            parts = ", ".join(f"{PURPOSE_LABELS.get(p, p)} {n:,}" for p, n in r.tokens_by_purpose.items())
            extra.append(f"AI usage by purpose (tokens): {parts}")
            extra.append(f"Cloud-equivalent cost ({r.cloud_reference} prices): {r.cloud_equivalent_cost_eur:.4f} € "
                         f"(real cost {r.cost_eur:.4f} € with {r.provider})")
        return "\n".join(lines + [""] + extra + list(r.notes))

    def save(self, report: RunReport) -> None:
        with self.engine.begin() as c:
            c.execute(delete(run_report).where(run_report.c.run_id == report.run_id))
            c.execute(insert(run_report).values(run_id=report.run_id, body=report.model_dump_json()))

    def build(self, run_id: str) -> RunReport:
        with self.engine.connect() as c:
            body = c.execute(select(run_report.c.body).where(run_report.c.run_id == run_id)).scalar()
        if body is None:
            raise UnknownRunError(run_id)
        return RunReport.model_validate_json(body)
