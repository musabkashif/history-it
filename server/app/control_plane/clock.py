"""Time helpers. Stored timestamps are UTC ISO-8601 so text ordering matches time ordering."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def add_seconds(stamp: str, seconds: int) -> str:
    parsed = datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')
