"""Legacy A CSV reader with the stdlib ``csv`` module (design "Key Decisions").

``pandas.read_csv`` pads short rows silently, which would hide MALFORMED_ROW (A9).
"""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from rules.types import RawRow

BOM = "\ufeff"


class SourceReadError(Exception):
    """The source file cannot be opened or is not valid UTF-8."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SourceFile:
    header: tuple[str, ...]
    rows: tuple[RawRow, ...]           # values keyed by header column names
    sha256: str


def read_source(path: Path | str) -> SourceFile:
    """Read the file bytes once (never written), decode UTF-8, drop a leading BOM, split on ``;``.

    Cells are exact strings. A row whose cell count differs from the header is flagged malformed.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SourceReadError(f"cannot open {path}: {exc.strerror or exc}") from None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceReadError(f"invalid UTF-8 at byte {exc.start}: {exc.reason}") from None
    if text.startswith(BOM):
        text = text[1:]
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=";")
    try:
        header = tuple(next(reader))
    except StopIteration:
        raise SourceReadError("file is empty: no header line") from None
    width = len(header)
    rows: list[RawRow] = []
    for i, cells in enumerate(reader, start=1):
        rows.append(RawRow(
            source_row_id=i,
            values=dict(zip(header, cells)),
            malformed=len(cells) != width,
        ))
    return SourceFile(header=header, rows=tuple(rows), sha256=hashlib.sha256(data).hexdigest())
