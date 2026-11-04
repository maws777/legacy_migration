"""Ingestion_Module: read the source file and rename columns to canonical names."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from legacy_a.csv_reader import SourceReadError, read_source
from rules.config import ReferenceMapping
from rules.types import RawRow

__all__ = ["IngestResult", "IngestionError", "SourceReadError", "ingest"]


@dataclass(frozen=True)
class IngestResult:
    rows: tuple[RawRow, ...]           # values keyed by canonical column names
    header: tuple[str, ...]
    sha256: str


class IngestionError(Exception):
    def __init__(self, reason: str, missing_columns: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.missing_columns = missing_columns


def _mapping_dict(mapping) -> Mapping[str, str | None]:
    return getattr(mapping, "mapping", mapping)


def ingest(path: Path | str, mapping, reference: ReferenceMapping) -> IngestResult:
    """Read ``path`` and rename source columns through ``mapping`` (source column -> target attribute).

    Canonical names are the reference source names of each target attribute. Raises
    ``SourceReadError`` or ``IngestionError`` (every missing required column, exact names).
    """
    source = read_source(path)
    src_map = _mapping_dict(mapping)
    rename: dict[str, str] = {}
    for column, target in src_map.items():
        if target is None:
            continue
        rename[column] = reference.canonical_for_target(target)
    unmapped = [t for t in reference.required_targets if t not in set(src_map.values())]
    if unmapped:
        raise IngestionError(f"required target attributes without a source column: {unmapped}")
    header = set(source.header)
    missing = tuple(c for c, t in src_map.items() if t is not None
                    and t in reference.required_targets and c not in header)
    if missing:
        raise IngestionError(f"missing required columns: {', '.join(missing)}", missing)
    rows = tuple(
        RawRow(r.source_row_id, {rename[c]: v for c, v in r.values.items() if c in rename}, r.malformed)
        for r in source.rows
    )
    return IngestResult(rows=rows, header=source.header, sha256=source.sha256)
