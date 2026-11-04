"""Client_App: continuous diagnostics queries to the Orchestrator only.

Two indicators (decisions.md): availability (HTTP 200 or 404 within the timeout) and functional
parity (200 with the expected body for each control VIN; a control VIN answered 404 is a parity failure).
Expected bodies come from ``--expected FILE`` or from the first 200 answer of each VIN.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

DEFAULT_VINS = Path(__file__).resolve().parent / "vins.txt"


@dataclass
class QueryResult:
    vin: str
    status: int | None
    latency_ms: float
    available: bool
    parity: bool
    reason: str | None


@dataclass
class Stats:
    queries: int = 0
    available: int = 0
    unavailable: int = 0
    parity_ok: int = 0
    parity_failed: int = 0
    reasons: Counter = field(default_factory=Counter)

    def line(self) -> str:
        return (f"queries {self.queries} | availability ok {self.available} failed {self.unavailable} | "
                f"parity ok {self.parity_ok} failed {self.parity_failed}")


class ClientApp:
    def __init__(self, base_url: str, vins: list[str], *, interval_s: float = 1.0, timeout_s: float = 5.0,
                 expected: dict[str, dict] | None = None, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, out: Callable[[str], None] = print) -> None:
        if not 0.1 <= interval_s <= 60:
            raise ValueError("interval must be between 0.1 and 60 seconds")
        if not vins:
            raise ValueError("at least one VIN is required")
        self.vins = vins
        self.interval_s = interval_s
        self.expected = dict(expected or {})
        self.http = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        self.timeout_s = timeout_s
        self.sleep, self.out = sleep, out
        self.stats = Stats()
        self.results: list[QueryResult] = []

    def query(self, vin: str) -> QueryResult:
        t0 = time.perf_counter()
        status, reason, body = None, None, None
        try:
            r = self.http.get(f"/api/vehicles/{vin}/diagnostics", timeout=self.timeout_s)
            status = r.status_code
            if status == 200:
                body = r.json()
            elif status != 404:
                reason = f"HTTP {status}"
        except httpx.TimeoutException:
            reason = "timeout"
        except httpx.HTTPError as exc:
            reason = type(exc).__name__
        latency = (time.perf_counter() - t0) * 1000
        available = reason is None
        if status == 200:
            self.expected.setdefault(vin, body)
            parity = body == self.expected[vin]
            if not parity:
                reason = reason or "parity: body differs from the expected answer"
        else:
            parity = False
            if status == 404:
                reason = "parity: control VIN answered 404"
        return QueryResult(vin, status, round(latency, 1), available, parity, reason)

    def record(self, res: QueryResult) -> None:
        s = self.stats
        s.queries += 1
        s.available += res.available
        s.unavailable += not res.available
        s.parity_ok += res.parity
        s.parity_failed += not res.parity
        if res.reason:
            s.reasons[res.reason] += 1
        self.results.append(res)
        self.out(f"{res.vin} -> {res.status if res.status is not None else '---'} in {res.latency_ms} ms"
                 + (f" ({res.reason})" if res.reason else "") + f" | {s.line()}")

    def run(self, max_queries: int | None = None) -> Stats:
        i = 0
        while max_queries is None or i < max_queries:
            self.record(self.query(self.vins[i % len(self.vins)]))
            i += 1
            if max_queries is None or i < max_queries:
                self.sleep(self.interval_s)
        return self.stats


def load_vins(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def main(argv: list[str] | None = None) -> int:
    from rules.config import load_rule_config
    cfg = load_rule_config().pipeline
    parser = argparse.ArgumentParser(prog="python -m client_app")
    parser.add_argument("--url", default=cfg.services.orchestrator)
    parser.add_argument("--vins", type=Path, default=DEFAULT_VINS)
    parser.add_argument("--interval", type=float, default=cfg.client_app.interval_s)
    parser.add_argument("--timeout", type=float, default=cfg.client_app.timeout_s)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--count", type=int)
    args = parser.parse_args(argv)
    expected = json.loads(args.expected.read_text(encoding="utf-8")) if args.expected else None
    app = ClientApp(args.url, load_vins(args.vins), interval_s=args.interval, timeout_s=args.timeout,
                    expected=expected)
    try:
        stats = app.run(args.count)
    except KeyboardInterrupt:
        stats = app.stats
    print(stats.line())
    return 0
