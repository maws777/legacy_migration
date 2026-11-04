"""Pattern_Key format: ``<field>|<reason>[|<detail>][#row=<source_row_id>]``.

``detail`` is percent-encoded for ``%``, ``|`` and ``#`` (design "Frozen contracts" 3).
"""
from __future__ import annotations

import re

from rules.types import PatternKeyParts

ROW_SUFFIX = "#row="
_ENCODE = {"%": "%25", "|": "%7C", "#": "%23"}
_DECODE = {"25": "%", "7C": "|", "23": "#"}
_DECODE_RE = re.compile(r"%(25|7C|23)")
_SAFE_PART = re.compile(r"^[^|#%]+$")


def _encode(detail: str) -> str:
    return "".join(_ENCODE.get(c, c) for c in detail)


def _decode(detail: str) -> str:
    return _DECODE_RE.sub(lambda m: _DECODE[m.group(1)], detail)


def make_key(field: str, reason: str, detail: str | None = None) -> str:
    """Build a group Pattern_Key. ``field`` and ``reason`` must not contain ``|``, ``#`` or ``%``."""
    for part in (field, reason):
        if not _SAFE_PART.match(part):
            raise ValueError(f"invalid Pattern_Key component: {part!r}")
    key = f"{field}|{reason}"
    if detail is not None:
        key += "|" + _encode(detail)
    return key


def extend_key(key: str, source_row_id: int) -> str:
    """Extend a group Pattern_Key with a Source_Row_Id (case-by-case question)."""
    if ROW_SUFFIX in key:
        raise ValueError(f"Pattern_Key is already row-scoped: {key!r}")
    return f"{key}{ROW_SUFFIX}{int(source_row_id)}"


def group_key(key: str) -> str:
    return key.split(ROW_SUFFIX, 1)[0]


def parse_key(key: str) -> PatternKeyParts:
    base, sep, row = key.partition(ROW_SUFFIX)
    source_row_id = int(row) if sep else None
    parts = base.split("|", 2)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid Pattern_Key: {key!r}")
    detail = _decode(parts[2]) if len(parts) == 3 else None
    return PatternKeyParts(field=parts[0], reason=parts[1], detail=detail,
                           source_row_id=source_row_id, group_key=base)
