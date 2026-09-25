"""The injectable clock and random source, and the chunked sleep (plan 5 clock.py; design 3.3, 4.2, 4.3).

Every time the observe code reads comes from a Clock passed in, never from the system clock at the call site, so a
test runs a whole night on a ManualClock (D10: time is always an explicit input). Stored stamps still go through
repository.stamp(). A breaker pause can last four hours while the lease lasts five minutes: sleep_in_chunks never
sleeps longer than 60 seconds at a time and renews on each wake.

Two readings of time: now() is the wall clock, for stamps, target dates and the 01:45 hard deadline; monotonic() only
measures how long something took, so a step of the system time (NTP, an operator) neither stretches a sleep nor cuts
it short.
"""

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

MAX_SLEEP_CHUNK_SECONDS = 60  # the lease renewal interval (design 3.3)


class Clock(Protocol):
    def now(self) -> datetime:
        """The current wall time, timezone-aware, in UTC."""
        ...

    def monotonic(self) -> float:
        """Seconds on a clock that never steps; only differences mean anything."""
        ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """The real clock: UTC wall time, time.monotonic (asyncio's own clock) and asyncio's sleep."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ManualClock:
    """A clock that moves only when told to; sleep() moves it at once and records how long it slept.

    advance() moves both readings; step_wall() moves only the wall clock, as an NTP or operator step would."""

    def __init__(self, start: datetime):
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("ManualClock needs a timezone-aware start")
        self._now = start.astimezone(UTC)
        self._monotonic = 0.0
        self._sleeps: tuple[float, ...] = ()

    @property
    def sleeps(self) -> tuple[float, ...]:
        return self._sleeps

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("ManualClock never goes backwards")
        self._now = self._now + timedelta(seconds=seconds)
        self._monotonic = self._monotonic + seconds

    def step_wall(self, seconds: float) -> None:
        """Step the wall clock by `seconds`, either way; monotonic time does not move."""
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
    """Sleep `seconds` of monotonic time, in chunks of at most `chunk` (<= 60) seconds, awaiting on_wake() after each
    one. on_wake renews the lease; whatever it raises (a lost lease) ends the sleep at once and propagates.

    The target is fixed up front, so time spent in on_wake shortens the rest of the sleep instead of adding to it. A
    wall-clock deadline is the caller's: sleep at most until it, or raise from on_wake once it has passed. on_wake runs
    after every chunk, the last and a short sleep's only one included, so it should cost nothing while the lease is
    fresh (renew only once 60 seconds have passed, not on every call)."""
    if not (0 < chunk <= MAX_SLEEP_CHUNK_SECONDS):  # also refuses NaN
        raise ValueError(f"chunk must be within (0, {MAX_SLEEP_CHUNK_SECONDS}] seconds")
    if not math.isfinite(seconds):
        raise ValueError("seconds must be finite")
    until = clock.monotonic() + max(seconds, 0)
    while (remaining := until - clock.monotonic()) > 0:
        await clock.sleep(min(remaining, chunk))
        await on_wake()
