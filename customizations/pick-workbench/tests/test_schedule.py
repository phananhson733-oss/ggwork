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
