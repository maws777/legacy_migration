"""AI usage of a Run by purpose, real cost and cloud-equivalent cost.

Attribution rule (documented in docs/cost-performance.md):

- calls recorded with the run_id of a Run (Resolver_Agent, Question_Advisor on the Run's questions) belong
  to that Run;
- every other call (schema discovery, chat, Legacy B questions, advice on reconciliation gaps) belongs to the
  latest full Run started at or before the call; calls made before the first full Run belong to it.

Real cost uses the prices of the configured provider (0 EUR for Ollama); the cloud-equivalent cost applies
the Anthropic prices of Rule_Config to the same token counts (tokenizers differ, so it is an estimate).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable, Mapping

from reporting.schemas import CostBreakdown, PurposeUsage
from rules.config import LLMConfig
from rules.timeutil import as_utc

PURPOSE_ORDER = ("resolver", "schema_discovery", "question_advisor", "query_agent", "legacy_b_query",
                 "question_rephrase")


def attribute(run_id: str, runs: Iterable[Mapping], calls: Iterable[Mapping]) -> list[Mapping]:
    """The calls attributed to ``run_id``. ``runs``: dicts with run_id, kind, started_at."""
    runs = list(runs)
    run_ids = {r["run_id"] for r in runs}
    full = sorted((r for r in runs if r["kind"] == "full"), key=lambda r: as_utc(r["started_at"]))
    pos = next((i for i, r in enumerate(full) if r["run_id"] == run_id), None)
    start: datetime | None = None
    end: datetime | None = None
    if pos is not None:
        start = as_utc(full[pos]["started_at"]) if pos > 0 else None
        end = as_utc(full[pos + 1]["started_at"]) if pos + 1 < len(full) else None
    out = []
    for c in calls:
        if c["run_id"] == run_id:
            out.append(c)
        elif pos is not None and c["run_id"] not in run_ids:
            at = as_utc(c["at"])
            if (start is None or at >= start) and (end is None or at < end):
                out.append(c)
    return out


def breakdown(run_id: str, runs: Iterable[Mapping], calls: Iterable[Mapping], llm: LLMConfig) -> CostBreakdown:
    sums: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for c in attribute(run_id, runs, calls):
        s = sums[c["purpose"]]
        s[0] += int(c["input_tokens"] or 0)
        s[1] += int(c["output_tokens"] or 0)
        s[2] += 1
    order = list(PURPOSE_ORDER) + sorted(p for p in sums if p not in PURPOSE_ORDER)
    usage = [PurposeUsage(purpose=p, input_tokens=sums[p][0], output_tokens=sums[p][1], calls=sums[p][2])
             for p in order if p in sums or p in PURPOSE_ORDER[:4]]
    tin = sum(u.input_tokens for u in usage)
    tout = sum(u.output_tokens for u in usage)
    real_in, real_out = llm.price_per_token()
    return CostBreakdown(
        run_id=run_id, by_purpose=usage, input_tokens=tin, output_tokens=tout, tokens=tin + tout,
        calls=sum(u.calls for u in usage), provider=llm.provider, model=llm.model,
        real_cost_eur=tin * real_in + tout * real_out,
        cloud_reference=f"anthropic {llm.cloud_reference_model}",
        cloud_equivalent_cost_eur=tin * llm.price_eur_per_input_token + tout * llm.price_eur_per_output_token)
