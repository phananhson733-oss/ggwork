"""Twice-daily RealShort pull inside the gateway process.

Running the schedule here keeps every credential on the backend: no inbound call, so the host's
internal token never has to leave Railway. One gateway process is assumed; a second replica would
pull twice, which the content-hash dedupe absorbs.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

SLOTS_UTC = ((3, 40), (15, 40))  # 北京时间 11:40 / 23:40
CATCH_UP_AFTER = timedelta(hours=12)
CATCH_UP_DELAY_SECONDS = 60


def next_slot(now: datetime) -> datetime:
    for offset in (0, 1):
        day = (now + timedelta(days=offset)).date()
        for hour, minute in SLOTS_UTC:
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
            if slot > now:
                return slot
    raise AssertionError("a slot always exists within a day")


def needs_catch_up(runs: list[dict], now: datetime) -> bool:
    """A deploy or crash across a slot would otherwise leave data stale until the next one."""
    last = next((run for run in runs if run["status"] == "success"), None)
    return last is None or now - datetime.fromisoformat(last["started_at"]) > CATCH_UP_AFTER


async def run_schedule(
    *,
    runs: Callable[[], Awaitable[list[dict]]],
    pull: Callable[[], Awaitable[None]],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    catch_up_delay: float = CATCH_UP_DELAY_SECONDS,
) -> None:
    if needs_catch_up(await runs(), clock()):
        await sleep(catch_up_delay)
        await pull()
    while True:
        now = clock()
        await sleep((next_slot(now) - now).total_seconds())
        await pull()


async def guarded_pull(sync_factory: Callable[[], object | None]) -> None:
    """One scheduled pull; a failure is recorded on the run and logged, never ends the schedule."""
    sync = sync_factory()
    if sync is None:
        return
    try:
        await sync.run("cron")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("[pick-sync] scheduled pull failed")
