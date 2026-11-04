"""Read-only in-memory store of the Legacy A source file."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from legacy_a.csv_reader import SourceFile, read_source
from rules.fields import normalize_vin
from rules.types import RawRow


class LegacyAStore:
    def __init__(self, source: SourceFile, vin_column: str = "VIN") -> None:
        self.source = source
        by_vin: dict[str, list[RawRow]] = defaultdict(list)
        for row in source.rows:
            by_vin[normalize_vin(row.values.get(vin_column, ""))].append(row)
        self._by_vin = dict(by_vin)

    @classmethod
    def from_path(cls, path: Path | str, vin_column: str = "VIN") -> "LegacyAStore":
        return cls(read_source(path), vin_column)

    @property
    def header(self) -> tuple[str, ...]:
        return self.source.header

    @property
    def total_rows(self) -> int:
        return len(self.source.rows)

    def page(self, page: int, page_size: int) -> tuple[RawRow, ...]:
        start = (page - 1) * page_size
        return self.source.rows[start:start + page_size]

    def rows_by_vin(self, vin: str) -> tuple[RawRow, ...]:
        return tuple(self._by_vin.get(normalize_vin(vin), ()))
