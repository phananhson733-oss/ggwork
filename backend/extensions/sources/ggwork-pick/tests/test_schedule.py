import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

sys.path.insert(0, str(Path(__file__).parent))


def test_next_slot_is_beijing_1140_or_2340():
    from ggwork_pick.schedule import next_slot

    assert next_slot(datetime(2026, 9, 23, 1, 0, tzinfo=UTC)) == datetime(2026, 9, 23, 3, 40, tzinfo=UTC)
    assert next_slot(datetime(2026, 9, 23, 3, 40, tzinfo=UTC)) == datetime(2026, 9, 23, 15, 40, tzinfo=UTC)
    assert next_slot(datetime(2026, 9, 23, 23, 0, tzinfo=UTC)) == datetime(2026, 9, 24, 3, 40, tzinfo=UTC)
    assert next_slot(datetime(2026, 12, 31, 16, 0, tzinfo=UTC)) == datetime(2027, 1, 1, 3, 40, tzinfo=UTC)


def test_catch_up_only_when_the_last_success_is_older_than_a_slot_gap():
    from ggwork_pick.schedule import needs_catch_up

    now = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)
    ok = {"status": "success", "started_at": (now - timedelta(hours=11)).isoformat()}
    old = {"status": "success", "started_at": (now - timedelta(hours=13)).isoformat()}
    failed = {"status": "failed", "started_at": (now - timedelta(minutes=5)).isoformat()}
    assert needs_catch_up([], now) is True
    assert needs_catch_up([failed, ok], now) is False
    assert needs_catch_up([failed, old], now) is True


class _Stop(Exception):
    pass


@pytest.mark.asyncio
async def test_schedule_catches_up_then_waits_for_each_slot():
    from ggwork_pick.schedule import run_schedule

    now = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)
    pulls, sleeps = [], []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise _Stop

    async def pull():
        pulls.append(len(sleeps))

    with pytest.raises(_Stop):
        await run_schedule(runs=lambda: asyncio.sleep(0, result=[]), pull=pull, clock=lambda: now, sleep=sleep, catch_up_delay=60)
    # catch-up after the delay, then one pull per slot wait
    assert sleeps[0] == 60 and pulls[0] == 1
    assert sleeps[1] == (datetime(2026, 9, 24, 3, 40, tzinfo=UTC) - now).total_seconds()
    assert pulls == [1, 2]


def _job(name: str, status: str = "success", last_success_at: str | None = None) -> dict:
    """One row of the native source's /status, as native_status hands it on (Node prints a pg timestamp with a Z)."""
    return {
        "name": name,
        "status": status,
        "attempted_at": last_success_at,
        "completed_at": last_success_at,
        "last_success_at": last_success_at,
        "error_code": None,
    }


def _collected(*jobs: dict) -> dict:
    return {"enabled": True, "jobs": list(jobs)}


CPS_DONE, CATALOG_DONE = "2026-10-10T00:07:50.963Z", "2026-10-10T02:48:37.768Z"


def test_collected_at_is_the_newest_success_once_nothing_is_running():
    from ggwork_pick.schedule import collected_at

    done = [_job("cps", last_success_at=CPS_DONE), _job("catalog", last_success_at=CATALOG_DONE), _job("queyu", "failed")]
    assert collected_at(_collected(*done)) == datetime(2026, 10, 10, 2, 48, 37, 768000, tzinfo=UTC)
    # A job that failed now still finished a collection earlier.
    assert collected_at(_collected(_job("cps", "failed", CPS_DONE))) == datetime(2026, 10, 10, 0, 7, 50, 963000, tzinfo=UTC)
    # While one runs the source answers source_busy, and the next job's rows would call for a second sync anyway.
    assert collected_at(_collected(*done, _job("queyu", "running", CPS_DONE))) is None
    assert collected_at({"enabled": True, "jobs": [], "error": "unavailable"}) is None
    assert collected_at(_collected()) is None and collected_at(None) is None
    assert collected_at(_collected(_job("cps", last_success_at="yesterday"), _job("catalog", "failed"))) is None


def test_a_collection_is_synced_once_any_run_started_after_it():
    from ggwork_pick.schedule import synced_since

    collected = datetime(2026, 10, 10, 2, 48, 37, 768000, tzinfo=UTC)
    before = {"status": "success", "started_at": "2026-10-09T15:40:00.148982+00:00"}
    failed_after = {"status": "failed", "started_at": "2026-10-10T03:39:59.999975+00:00"}
    assert synced_since([], collected) is False
    assert synced_since([before], collected) is False
    # A failed run counts too: a collection gets one triggered run, and the slots do the retrying.
    assert synced_since([failed_after, before], collected) is True


@pytest.mark.asyncio
async def test_the_watch_pulls_once_for_each_finished_collection():
    from ggwork_pick.schedule import run_collection_watch

    cps, catalog, later = (
        _collected(_job("cps", last_success_at=CPS_DONE)),
        _collected(_job("catalog", last_success_at=CATALOG_DONE)),
        "2026-10-10T06:08:00.000Z",
    )
    synced = [{"status": "success", "started_at": "2026-10-10T03:39:59.999975+00:00"}]
    # Each tick: what the source says, the latest run, what the pull answers.
    script = [
        ({"enabled": True, "jobs": [], "error": "unavailable"}, [], None),
        (_collected(_job("cps", "running", None)), [], None),
        (cps, [], {"status": "success"}),  # finished and never synced: pull
        (cps, [], {"status": "success"}),  # the same collection: nothing more
        (catalog, synced, None),  # a newer one, but a run started after it
        (_collected(_job("cps", last_success_at=later)), synced, {"status": "already_running"}),  # another run holds the lock
        (_collected(_job("cps", last_success_at=later)), synced, {"status": "failed"}),  # asked again; a run that failed is not
        (_collected(_job("cps", last_success_at=later)), synced, {"status": "success"}),
    ]
    tick, pulls, sleeps = -1, [], []

    async def sleep(seconds):
        nonlocal tick
        sleeps.append(seconds)
        tick += 1
        if tick == len(script):
            raise _Stop

    async def status():
        return script[tick][0]

    async def runs():
        return script[tick][1]

    async def pull():
        pulls.append(tick)
        return script[tick][2]

    with pytest.raises(_Stop):
        await run_collection_watch(status=status, runs=runs, pull=pull, sleep=sleep, interval=300)
    assert pulls == [2, 5, 6] and set(sleeps) == {300}


@pytest.mark.asyncio
async def test_the_watch_outlives_a_failing_tick(caplog):
    from ggwork_pick.schedule import run_collection_watch

    ticks, pulls = 0, []

    async def sleep(seconds):
        nonlocal ticks
        ticks += 1
        if ticks == 3:
            raise _Stop

    async def status():
        if ticks == 1:
            raise RuntimeError("the database is away")
        return _collected(_job("cps", last_success_at=CPS_DONE))

    async def pull():
        pulls.append(ticks)

    with pytest.raises(_Stop):
        await run_collection_watch(status=status, runs=lambda: asyncio.sleep(0, result=[]), pull=pull, sleep=sleep, interval=300)
    assert pulls == [2] and "[pick-sync]" in caplog.text


class _NativeSource:
    """NativeSourceProcess switched on, without the Node process it would start."""

    enabled = True

    async def start(self):
        return None

    async def stop(self):
        return None


@pytest.mark.asyncio
async def test_service_syncs_once_after_a_collection_in_native_mode(tmp_path, monkeypatch):
    from test_realshort_sync import TOKEN, feed_row, feed_transport

    from ggwork_pick import service as service_module
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService, SyncSettings

    async def finished(service):
        # Later than any run of this test, and the same every time: one collection, however often it is read.
        return _collected(_job("catalog", last_success_at="2099-01-01T00:00:00.000Z"))

    monkeypatch.setattr(service_module, "native_status", finished)
    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files", SyncSettings("https://realshort.test", TOKEN), catch_up_delay=0, collection_poll=0.02)
    service.native_source = _NativeSource()
    service.sync_transport = feed_transport([feed_row(1)])
    await service.start(SimpleNamespace(session_factory=factory, run_evidence_reader=None))
    assert service.collection_watcher is not None
    for _ in range(200):
        runs = await PickRepository.shared(factory).sync_runs()
        if sorted(run["trigger"] for run in runs if run["status"] == "success") == ["collect", "cron"]:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)  # ten more polls of the same collection
    runs = await PickRepository.shared(factory).sync_runs()
    assert sorted((run["trigger"], run["status"]) for run in runs) == [("collect", "success"), ("cron", "success")]
    await service.stop()
    assert service.collection_watcher.done()
    await engine.dispose()


@pytest.mark.asyncio
async def test_service_runs_the_schedule_only_when_the_feed_is_configured(tmp_path):
    from test_realshort_sync import TOKEN, feed_row, feed_transport

    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService, SyncSettings

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    idle = PickService(tmp_path / "idle")
    await idle.start(SimpleNamespace(session_factory=factory, run_evidence_reader=None))
    assert idle.scheduler is None
    await idle.stop()

    service = PickService(tmp_path / "files", SyncSettings("https://realshort.test", TOKEN), catch_up_delay=0)
    service.sync_transport = feed_transport([feed_row(1)])
    await service.start(SimpleNamespace(session_factory=factory, run_evidence_reader=None))
    assert service.scheduler is not None
    # Outside native mode there is no collection to watch: the two slots stay the only schedule.
    assert service.collection_watcher is None
    for _ in range(100):
        runs = await PickRepository.shared(factory).sync_runs()
        if runs and runs[0]["status"] == "success":
            break
        await asyncio.sleep(0.02)
    assert runs[0]["trigger"] == "cron" and runs[0]["status"] == "success"
    await service.stop()
    assert service.scheduler.done()
    await engine.dispose()


@pytest.mark.asyncio
async def test_stop_never_waits_past_the_grace_period(tmp_path, monkeypatch):
    from ggwork_pick import service as service_module
    from ggwork_pick.service import PickService

    monkeypatch.setattr(service_module, "STOP_GRACE_SECONDS", 0.05)
    service = PickService(tmp_path / "files")

    async def stubborn():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(3600)  # e.g. recording the outcome while the database is locked

    service.scheduler = asyncio.create_task(stubborn())
    await asyncio.sleep(0)
    await asyncio.wait_for(service.stop(), 2)
    assert service.scheduler.done()
