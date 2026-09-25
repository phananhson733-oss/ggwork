"""TR-01: the injectable clock, random source and chunked sleep (plan 5 clock.py; design 3.3).

A breaker pause can last four hours while the lease is five minutes long: every sleep is cut into chunks of at most
60 seconds and the lease is renewed on each wake-up, so a pause never outlives the lease.
"""

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest

START = datetime(2026, 9, 25, 22, 0, tzinfo=UTC)


class LeaseLost(Exception):
    pass


@pytest.mark.asyncio
async def test_chunked_sleep_renews_on_every_wake():
    from ggwork_pick.observe.clock import MAX_SLEEP_CHUNK_SECONDS, ManualClock, sleep_in_chunks

    clock = ManualClock(START)
    wakes = []

    async def renew():
        wakes.append(clock.now())

    await sleep_in_chunks(clock, 4 * 3600, on_wake=renew)
    assert MAX_SLEEP_CHUNK_SECONDS == 60
    assert clock.now() == START + timedelta(hours=4)
    assert max(clock.sleeps) <= 60 and sum(clock.sleeps) == 4 * 3600
    assert len(wakes) == len(clock.sleeps) == 240
    assert wakes[-1] == clock.now()


@pytest.mark.asyncio
async def test_chunked_sleep_tail_chunk_and_custom_bound():
    from ggwork_pick.observe.clock import ManualClock, sleep_in_chunks

    clock = ManualClock(START)
    renewals = []

    async def renew():
        renewals.append(1)

    await sleep_in_chunks(clock, 130.5, on_wake=renew, chunk=45)
    assert clock.sleeps == (45, 45, 40.5)
    assert len(renewals) == 3


@pytest.mark.asyncio
async def test_chunked_sleep_stops_when_renewal_fails():
    """A lost lease ends the sleep at once: the process must stop, not doze on and then write."""
    from ggwork_pick.observe.clock import ManualClock, sleep_in_chunks

    clock = ManualClock(START)
    calls = []

    async def renew():
        calls.append(1)
        if len(calls) == 2:
            raise LeaseLost

    with pytest.raises(LeaseLost):
        await sleep_in_chunks(clock, 600, on_wake=renew)
    assert clock.sleeps == (60, 60)


@pytest.mark.asyncio
async def test_chunked_sleep_keeps_to_the_wall_clock():
    """Slow wakes (a renewal taking time) shorten the remaining sleep instead of adding to it."""
    from ggwork_pick.observe.clock import ManualClock, sleep_in_chunks

    clock = ManualClock(START)

    async def slow_renew():
        clock.advance(5)

    await sleep_in_chunks(clock, 130, on_wake=slow_renew)
    assert clock.now() == START + timedelta(seconds=130)
    assert clock.sleeps == (60, 60)


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [0, -5])
async def test_chunked_sleep_nothing_to_wait(seconds):
    from ggwork_pick.observe.clock import ManualClock, sleep_in_chunks

    clock = ManualClock(START)

    async def renew():
        raise AssertionError("no wake without a sleep")

    await sleep_in_chunks(clock, seconds, on_wake=renew)
    assert clock.sleeps == () and clock.now() == START


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", [0, -1, 61, float("nan")])
async def test_chunk_bound_is_enforced(chunk):
    from ggwork_pick.observe.clock import ManualClock, sleep_in_chunks

    async def renew():
        pass

    with pytest.raises(ValueError, match="chunk"):
        await sleep_in_chunks(ManualClock(START), 120, on_wake=renew, chunk=chunk)


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [float("inf"), float("nan")])
async def test_sleep_length_must_be_finite(seconds):
    from ggwork_pick.observe.clock import ManualClock, sleep_in_chunks

    async def renew():
        pass

    with pytest.raises(ValueError, match="finite"):
        await sleep_in_chunks(ManualClock(START), seconds, on_wake=renew)


def test_manual_clock_is_utc_and_moves_only_forward():
    from ggwork_pick.observe.clock import ManualClock

    with pytest.raises(ValueError, match="aware"):
        ManualClock(datetime(2026, 9, 25, 22, 0))
    shanghai = ManualClock(datetime(2026, 9, 26, 6, 0, tzinfo=timezone(timedelta(hours=8))))
    assert shanghai.now() == START and shanghai.now().tzinfo is UTC
    with pytest.raises(ValueError, match="backwards"):
        shanghai.advance(-1)


@pytest.mark.asyncio
async def test_system_clock_is_aware_utc_and_really_sleeps():
    from ggwork_pick.observe.clock import SystemClock

    clock = SystemClock()
    before = clock.now()
    assert before.tzinfo is UTC
    started = asyncio.get_running_loop().time()
    await clock.sleep(0.01)
    assert asyncio.get_running_loop().time() - started >= 0.01
    assert clock.now() >= before


def test_random_source_is_injectable_and_reproducible():
    from ggwork_pick.observe.clock import random_source

    first, second = random_source(seed=7), random_source(seed=7)
    assert [first.uniform(1.5, 3.0) for _ in range(5)] == [second.uniform(1.5, 3.0) for _ in range(5)]
    assert random_source() is not random_source()
