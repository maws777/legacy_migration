"""UTC helpers. SQLite stores naive datetimes; they are always UTC in this project."""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def iso_z(dt: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SS.ffffffZ``."""
    return naive_utc(dt).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
