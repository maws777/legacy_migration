"""Run context builders: VIN_Model_Index and diagnosticOpID usage over all rows of the Run."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from sqlalchemy import Column, Engine, MetaData, String, Table, delete, insert, select

from rules.types import OperationIdentity, RowScan

CTX_MD = MetaData()
run_shared_op_id = Table(
    "run_shared_op_id", CTX_MD,
    Column("run_id", String(36), primary_key=True),
    Column("op_id", String(256), primary_key=True),
)


def partial_vin_models(scans: Iterable[RowScan]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for s in scans:
        if s.matched_model is not None and s.norm_vin:
            out[s.norm_vin].add(s.matched_model)
    return dict(out)


def partial_op_ids(scans: Iterable[RowScan]) -> dict[str, set[OperationIdentity]]:
    out: dict[str, set[OperationIdentity]] = defaultdict(set)
    for s in scans:
        if s.trimmed_op_id is not None:
            out[s.trimmed_op_id].add(s.identity)
    return dict(out)


def merge_partials(parts: Iterable[dict[str, set]]) -> dict[str, set]:
    merged: dict[str, set] = defaultdict(set)
    for p in parts:
        for k, v in p.items():
            merged[k] |= v
    return dict(merged)


def build_vin_model_index(scans: Iterable[RowScan]) -> dict[str, frozenset[str]]:
    """Normalized VIN -> canonical models matched by R6 on the rows of the Run (dictionary matches only)."""
    return {k: frozenset(v) for k, v in partial_vin_models(scans).items()}


def shared_from_usage(usage: dict[str, set[OperationIdentity]]) -> frozenset[str]:
    return frozenset(k for k, ids in usage.items() if len(ids) >= 2)


def build_shared_op_ids(scans: Iterable[RowScan]) -> frozenset[str]:
    """Trimmed readable ids carried by at least two rows with different Operation_Identity tuples."""
    return shared_from_usage(partial_op_ids(scans))


class SharedOpIdStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        CTX_MD.create_all(engine)

    def save(self, run_id: str, ids: Iterable[str]) -> None:
        with self.engine.begin() as c:
            c.execute(delete(run_shared_op_id).where(run_shared_op_id.c.run_id == run_id))
            rows = [{"run_id": run_id, "op_id": i} for i in sorted(ids)]
            if rows:
                c.execute(insert(run_shared_op_id), rows)

    def load(self, run_id: str) -> frozenset[str]:
        with self.engine.connect() as c:
            return frozenset(c.execute(select(run_shared_op_id.c.op_id)
                                       .where(run_shared_op_id.c.run_id == run_id)).scalars())
