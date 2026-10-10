"""Twice-daily RealShort pull inside the gateway process, and in native mode one more after each collection.

Running the schedule here keeps every credential on the backend: no inbound call, so the host's
internal token never has to leave Railway. One gateway process is assumed; a second replica would
pull twice, which the content-hash dedupe absorbs.

The native source collects on its own clock (customizations/pick-source/runtime/schedule.ts) and cannot call the
gateway, so run_collection_watch asks it instead: when a collection finished after the latest run started, one run
publishes what it brought. Without that, rows collected just after a slot wait up to twelve hours for the page.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

SLOTS_UTC = ((3, 40), (15, 40))  # 北京时间 11:40 / 23:40
CATCH_UP_AFTER = timedelta(hours=12)
CATCH_UP_DELAY_SECONDS = 60
CRON_TRIGGER = "cron"
# The run a finished collection starts (ggwp_sync_runs.trigger; the imports tab prints it as 采集后).
COLLECT_TRIGGER = "collect"
# How often the native source is asked what it finished. A collection takes minutes and runs every few hours, so five
# minutes loses nothing, and each question is one loopback request (and one line in the gateway log).
COLLECTION_POLL_SECONDS = 300


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


async def guarded_pull(sync_factory: Callable[[], object | None], trigger: str = CRON_TRIGGER) -> dict | None:
    """One scheduled pull; a failure is recorded on the run and logged, never ends the schedule. Returns what the run
    returned (its record, or {"status": "already_running"} when another run holds the lock), None when there was none."""
    sync = sync_factory()
    if sync is None:
        return None
    try:
        return await sync.run(trigger)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("[pick-sync] scheduled pull failed")
        return None


def _moment(value: object) -> datetime | None:
    """A timestamp of the source's /status (Node prints a pg timestamptz as ISO with a Z) or of a run record (stamp())."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def collected_at(status: Mapping | None) -> datetime | None:
    """When the native source last finished a collection (native_source.native_status), the newest last_success_at of
    its jobs. None while that cannot be told or should wait: the source did not answer, no job has succeeded yet, or one
    is running. A running job makes the source answer source_busy, and its rows would call for a second run anyway."""
    if not isinstance(status, Mapping) or status.get("error"):
        return None
    jobs = [job for job in status.get("jobs") or [] if isinstance(job, Mapping)]
    if any(job.get("status") == "running" for job in jobs):
        return None
    moments = [moment for job in jobs if (moment := _moment(job.get("last_success_at"))) is not None]
    return max(moments, default=None)


def synced_since(runs: list[dict], collected: datetime) -> bool:
    """Whether the latest run (runs is newest first) started after that collection finished. A failed run counts too:
    a collection gets one run, and what that run could not publish is left to the slots, not asked for every poll."""
    started = _moment(runs[0]["started_at"]) if runs else None
    return started is not None and started >= collected


async def run_collection_watch(
    *,
    status: Callable[[], Awaitable[Mapping | None]],
    runs: Callable[[], Awaitable[list[dict]]],
    pull: Callable[[], Awaitable[dict | None]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    interval: float = COLLECTION_POLL_SECONDS,
) -> None:
    """Native mode: one pull for each collection the source finishes, on top of the two slots. The run record decides
    across restarts (synced_since); `handled` keeps one collection from being pulled twice within this process, whatever
    the database's and the gateway's clocks say about which came first. A pull that found another run holding the lock
    is asked again next time. A tick that fails is logged and the watch goes on."""
    handled: datetime | None = None
    while True:
        await sleep(interval)
        try:
            collected = collected_at(await status())
            if collected is None or collected == handled:
                continue
            if synced_since(await runs(), collected):
                handled = collected
                continue
            result = await pull()
            if not (isinstance(result, Mapping) and result.get("status") == "already_running"):
                handled = collected
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[pick-sync] the collection watch failed this time; it goes on")
