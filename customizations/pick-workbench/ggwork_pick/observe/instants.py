"""Moments for the radar's pure rules (plan TR-10, D10): time is always an explicit, timezone-aware input.

Stored stamps are repository.stamp() strings (UTC, six fractional digits); a caller's `now` is an aware datetime. Both
come through instant(), which refuses a naive value instead of guessing its zone.
"""

from datetime import UTC, datetime, timedelta

MINUTE = timedelta(minutes=1)


def instant(value: str | datetime) -> datetime:
    """An aware datetime in UTC from a stored stamp or a datetime; a naive one is refused."""
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"需要带时区的时间：{value!r}")
    return moment.astimezone(UTC)


def stamp(moment: datetime) -> str:
    """repository.stamp()'s stored form (UTC, always six fractional digits) of an aware moment; a naive one is refused.
    For the collectors, which never import the gateway's repository (TR-13, test_write_paths)."""
    return instant(moment).isoformat(timespec="microseconds")


def whole_minutes(delta: timedelta) -> int:
    """Whole minutes of a span, rounded toward zero: 90 seconds is 1, minus 90 seconds is -1."""
    size = abs(delta) // MINUTE
    return -size if delta < timedelta(0) else size
