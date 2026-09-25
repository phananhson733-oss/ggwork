"""The injectable clock and random source, and the chunked sleep (plan 5 clock.py; design 3.3, 4.2, 4.3).

Every time the observe code reads comes from a Clock passed in, never from the system clock at the call site, so a
test runs a whole night on a ManualClock (D10: time is always an explicit input). Stored stamps still go through
repository.stamp(). A breaker pause can last four hours while
the lease lasts five minutes: sleep_in_chunks never sleeps longer than 60 seconds at a time and renews on each wake.
"""

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

MAX_SLEEP_CHUNK_SECONDS = 60  # the lease renewal interval (design 3.3)


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware, in UTC."""
        ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """The real clock: UTC wall time and asyncio's sleep."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ManualClock:
    """A clock that moves only when told to; sleep() moves it at once and records how long it slept."""

    def __init__(self, start: datetime):
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("ManualClock needs a timezone-aware start")
        self._now = start.astimezone(UTC)
        self._sleeps: tuple[float, ...] = ()

    @property
    def sleeps(self) -> tuple[float, ...]:
        return self._sleeps

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("ManualClock never goes backwards")
        self._now = self._now + timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        self._sleeps = (*self._sleeps, seconds)


def random_source(seed: int | None = None) -> random.Random:
    """A private random source for jitter (design 4.2); a seed makes a test reproducible. Never the global one."""
    return random.Random(seed)


async def sleep_in_chunks(
    clock: Clock,
    seconds: float,
    *,
    on_wake: Callable[[], Awaitable[object]],
    chunk: float = MAX_SLEEP_CHUNK_SECONDS,
) -> None:
    """Sleep until `seconds` from now by the clock, in chunks of at most `chunk` (<= 60) seconds, awaiting on_wake()
    after each one. on_wake renews the lease; whatever it raises (a lost lease) ends the sleep at once and propagates.
    The target is fixed up front, so time spent in on_wake shortens the rest of the sleep instead of adding to it."""
    if not (0 < chunk <= MAX_SLEEP_CHUNK_SECONDS):  # also refuses NaN
        raise ValueError(f"chunk must be within (0, {MAX_SLEEP_CHUNK_SECONDS}] seconds")
    if not math.isfinite(seconds):
        raise ValueError("seconds must be finite")
    until = clock.now() + timedelta(seconds=max(seconds, 0))
    while (remaining := (until - clock.now()).total_seconds()) > 0:
        await clock.sleep(min(remaining, chunk))
        await on_wake()
