"""The plain-dict form of the Trends state machines (plan TR-03: "状态能序列化成 dict").

pacing, breaker and budget keep immutable dataclasses; the persisted form (TR-04's state file, TR-13's runtime and
budget rows) is a dict of JSON scalars. Reading one back is strict: exact keys, exact types (a bool is not a count, a
float is not an int), aware instants only. Anything else raises ValueError, which the state stores turn into
StateUnavailable: a state that cannot be read never starts the day afresh (design 4.3).

Writing is as strict: the states refuse a naive datetime when they are built (aware_or_none in __post_init__), and
encode_instant refuses one too, because its meaning depends on the host's zone.
"""

import math
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any

_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # fromisoformat alone also takes an ISO week date, 2026-W39-5


def require_aware(value: datetime, name: str) -> datetime:
    """`value` in UTC; a naive datetime is refused, because its meaning depends on the host's zone."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def aware_or_none(values: Iterable[datetime | None], name: str) -> None:
    """For a state's __post_init__: every one of `values` is None or timezone-aware."""
    for value in values:
        if value is not None:
            require_aware(value, name)


def encode_instant(value: datetime | None) -> str | None:
    """repository.stamp()'s shape (UTC, always six fractional digits), without importing the database layer."""
    return None if value is None else require_aware(value, "instant").isoformat(timespec="microseconds")


def decode_instant(value: Any, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp or null")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not an ISO timestamp") from exc
    return require_aware(parsed, name)


def encode_day(value: date | None) -> str | None:
    return None if value is None else f"{value.year:04d}-{value.month:02d}-{value.day:02d}"


def decode_day(value: Any, name: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _ISO_DAY.fullmatch(value):
        raise ValueError(f"{name} must be a YYYY-MM-DD date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not a YYYY-MM-DD date") from exc


def exact_keys(data: Any, keys: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{name} must be a mapping")
    if set(data) != keys:
        raise ValueError(f"{name} must have exactly the keys {sorted(keys)}")
    return data


def count(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def flag(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return value


def finite(value: Any, name: str, *, minimum: float | None = None) -> float:
    """A finite float (an int is accepted: JSON writes 8.0 as 8 in some encoders); bools are refused."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return float(value)


def choice(value: Any, allowed: tuple[str, ...], name: str) -> str | None:
    if value is not None and value not in allowed:
        raise ValueError(f"{name} must be null or one of {list(allowed)}")
    return value


def ascending(values: tuple, name: str) -> tuple:
    if any(later < earlier for earlier, later in zip(values, values[1:], strict=False)):
        raise ValueError(f"{name} must be in ascending order")
    return values
