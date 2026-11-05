"""Legacy B agent: Legacy B asks the Query_Agent its own three questions about the target system.

Legacy B holds the reference vehicle model of each VIN. Its agent writes three questions for its own needs
(with the LLM, from a summary of its own records; a fixed fallback set when no LLM is available), sends them
to the Orchestrator chat route as a ``legacy_b`` consumer (same validation, read-only connection and row limit
as the UI, Requirement 33.5), and prints the answers. When an answer lists VINs with a vehicle model, the agent
compares them with its own records.

Run: ``python -m agents.legacy_b_asker`` (or ``scripts/legacy_b_asks.ps1``) with the services started.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

import httpx

from agents.resolver_agent import first_json
from agents.schemas import ChatResponse
from legacy_b.store import LegacyBStore

SYSTEM = (
    "You are the agent of Legacy B, the system that holds the reference vehicle model of each VIN (fields "
    "CHASSIS_NO, MODELE, DATE_MAJ). A new target system now holds the vehicle diagnostics migrated from Legacy A "
    "(tables vehicle, diagnostic_operation, diagnozed_ecu, diag_value_result, diag_tools_versions). Write exactly "
    "3 short questions, in English, that Legacy B needs answered from the target database for its own work: for "
    "example to compare vehicle models with its records, to find vehicles it does not know, or to get the latest "
    'diagnostics of its vehicles. Reply with JSON only: {"questions": ["...", "...", "..."]}.'
)


def summary(store: LegacyBStore, cfg) -> dict:
    from agents.api_agent_legacy_b import translate
    records = store.records()
    models = Counter(t.vehicle_model for t in (translate(r, cfg) for r in records) if t.vehicle_model)
    return {"records": len(records), "models": dict(models.most_common()),
            "sample_vins": [r.CHASSIS_NO for r in records[:3]]}


def fallback_questions(info: dict) -> list[str]:
    vin = (info.get("sample_vins") or ["?"])[0]
    return ["How many vehicles are there in the target for each vehicle model?",
            f"What is the vehicle model of VIN {vin} in the target?",
            "Which 5 vehicles have the most recent diagnostic operation, and on which date?"]


class LegacyBAsker:
    def __init__(self, llm, chat: Callable[[str], ChatResponse], store: LegacyBStore, cfg) -> None:
        self.llm, self.chat, self.store, self.cfg = llm, chat, store, cfg

    def questions(self) -> tuple[list[str], str, int]:
        """(questions, origin "llm" or "fallback: <reason>", tokens)."""
        info = summary(self.store, self.cfg)
        if self.llm is None or not self.llm.is_available():
            return fallback_questions(info), "fallback: no LLM available", 0
        res = self.llm.complete(run_id="legacy-b", purpose="legacy_b_query", system=SYSTEM,
                                prompt=json.dumps({"legacy_b_data": info}, ensure_ascii=False), max_tokens=200)
        tokens = res.input_tokens + res.output_tokens
        if not res.ok:
            return fallback_questions(info), f"fallback: LLM failure {res.error_type}", tokens
        data = first_json(res.text)
        qs = data.get("questions") if isinstance(data, dict) else None
        if not (isinstance(qs, list) and len(qs) >= 3 and all(isinstance(q, str) and 1 <= len(q.strip()) <= 1000
                                                               for q in qs[:3])):
            return fallback_questions(info), "fallback: the LLM answer was not 3 questions", tokens
        return [q.strip() for q in qs[:3]], "llm", tokens

    def compare(self, response: ChatResponse) -> str | None:
        """When rows carry a VIN and a vehicle model, compare them with the Legacy B records."""
        from agents.api_agent_legacy_b import translate
        if response.error or not response.rows:
            return None
        keys = {k.lower(): k for k in response.rows[0]}
        vin_key = next((keys[k] for k in ("vin", "chassis_no") if k in keys), None)
        model_key = next((keys[k] for k in ("vehicle_model", "model", "vehiclemodel") if k in keys), None)
        if vin_key is None or model_key is None:
            return None
        same = diff = unknown = 0
        for row in response.rows:
            rec = self.store.get(str(row[vin_key]))
            mine = translate(rec, self.cfg).vehicle_model if rec else None
            if mine is None:
                unknown += 1
            elif mine == row[model_key]:
                same += 1
            else:
                diff += 1
        return f"compared with Legacy B: {same} same model, {diff} different, {unknown} VIN(s) unknown to Legacy B"

    def run(self, out: Callable[[str], None] = print) -> list[dict]:
        questions, origin, tokens = self.questions()
        out(f"Legacy B agent: 3 questions ({origin}, {tokens} tokens)")
        results = []
        for i, q in enumerate(questions, start=1):
            r = self.chat(q)
            out(f"\n[{i}] {q}")
            out(f"    SQL: {r.sql or '-'}")
            if r.error:
                out(f"    error: {r.error.message}")
            else:
                out(f"    {len(r.rows)} row(s){' (truncated)' if r.truncated else ''}:")
                for row in r.rows[:10]:
                    out("      " + json.dumps(row, ensure_ascii=False, default=str))
                note = self.compare(r)
                if note:
                    out(f"    {note}")
            out(f"    {r.input_tokens + r.output_tokens} tokens, {r.seconds:.1f} s")
            results.append({"question": q, "response": r})
        return results


def http_chat(base_url: str, timeout_s: float = 300.0) -> Callable[[str], ChatResponse]:
    client = httpx.Client(base_url=base_url, timeout=timeout_s)

    def ask(question: str) -> ChatResponse:
        r = client.post("/ops/chat", json={"question": question, "consumer": "legacy_b"})
        r.raise_for_status()
        return ChatResponse(**r.json())
    return ask


def main(argv: list[str] | None = None) -> int:
    from agents.llm_client import LLMClient
    from legacy_b.simulator import DEFAULT_OUT, RECORDS_FILE
    from rules.config import load_rule_config
    from target.schema import make_engine, sqlite_url
    cfg = load_rule_config()
    parser = argparse.ArgumentParser(prog="python -m agents.legacy_b_asker")
    parser.add_argument("--url", default=cfg.pipeline.services.orchestrator)
    parser.add_argument("--records", type=Path, default=DEFAULT_OUT / RECORDS_FILE)
    parser.add_argument("--var", type=Path, default=Path(cfg.pipeline.paths.ops_db).parent,
                        help="database folder of the Orchestrator: the LLM calls are metered in its ops.db")
    parser.add_argument("--no-llm", action="store_true", help="use the fixed questions, no LLM call")
    args = parser.parse_args(argv)
    llm = None if args.no_llm else LLMClient(cfg.pipeline.llm, make_engine(sqlite_url(args.var / "ops.db")))
    asker = LegacyBAsker(llm, http_chat(args.url), LegacyBStore.from_csv(args.records), cfg)
    try:
        asker.run()
    except httpx.HTTPError as exc:
        print(f"Orchestrator unreachable at {args.url}: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
