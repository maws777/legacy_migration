"""Read-only in-memory store of the Legacy B records."""
from __future__ import annotations

import csv
from pathlib import Path

from legacy_b.schemas import LegacyBRecord


class LegacyBStore:
    def __init__(self, records: list[LegacyBRecord]) -> None:
        self._by_chassis = {r.CHASSIS_NO: r for r in records}

    @classmethod
    def from_csv(cls, path: Path | str) -> "LegacyBStore":
        with open(path, encoding="utf-8", newline="") as fh:
            return cls([LegacyBRecord(**row) for row in csv.DictReader(fh, delimiter=";")])

    def get(self, chassis_no: str) -> LegacyBRecord | None:
        return self._by_chassis.get(chassis_no)

    def __len__(self) -> int:
        return len(self._by_chassis)
