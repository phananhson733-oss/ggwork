"""Feedback publication is immutable, fenced and never shared with another owner."""

from datetime import UTC, datetime, timedelta

import pytest
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.fixture
def payload():
    from ggwork_pick.feedback.contracts import TABLES_V1 as TABLES
    from ggwork_pick.feedback.contracts import FeedbackSnapshot

    return FeedbackSnapshot.model_validate(
        {
            "scan_started_at": "2026-10-07T12:00:00Z",
            "scan_completed_at": "2026-10-07T12:00:08Z",
            "consistency": "bounded_scan",
            "tables": [{"table_id": t.table_id, "fields": [], "records": [], "complete": True, "pages": 1} for t in TABLES],
        }
    )


@pytest.mark.asyncio
async def test_publish_reuse_and_owner_isolation(pick_db_url, tmp_path, payload):
    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    alice, bob = FeedbackRepository(factory, "alice"), FeedbackRepository(factory, "bob")
    now = datetime(2026, 10, 7, 12, tzinfo=UTC)
    run = await alice.claim("manual", now=now)
    version = await alice.publish(run["id"], payload, now=now + timedelta(seconds=10))
    assert (await alice.current())["id"] == version["id"]
    assert await bob.current() is None
    with pytest.raises(LookupError):
        await bob.snapshot(version["id"])
    assert len((await alice.snapshot(version["id"])).tables) == 15
    run2 = await alice.claim("manual", now=now + timedelta(hours=1))
    same = await alice.publish(run2["id"], payload, now=now + timedelta(hours=1, seconds=10))
    assert same["id"] == version["id"]
    assert (await alice.status())["last_verified_at"].startswith("2026-10-07T13:")
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_writer_cannot_publish_or_release_new_writer(pick_db_url, tmp_path, payload):
    from ggwork_pick.feedback.repository import FeedbackRepository, LostFeedbackLease
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    repo = FeedbackRepository(factory, "alice")
    now = datetime(2026, 10, 7, 12, tzinfo=UTC)
    first = await repo.claim("manual", now=now, lease_seconds=5)
    assert await repo.claim("manual", now=now + timedelta(seconds=1)) is None
    second = await repo.claim("manual", now=now + timedelta(seconds=6))
    with pytest.raises(LostFeedbackLease):
        await repo.publish(first["id"], payload, now=now + timedelta(seconds=7))
    await repo.fail(first["id"], "refresh_failed", now=now + timedelta(seconds=8))
    assert (await repo.status(now=now + timedelta(seconds=9)))["running"]["id"] == second["id"]
    await repo.publish(second["id"], payload, now=now + timedelta(seconds=10))
    assert (await repo.status())["running"] is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_refresh_preserves_current_snapshot(pick_db_url, tmp_path, payload):
    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    repo = FeedbackRepository(factory, "alice")
    run = await repo.claim("manual")
    before = await repo.publish(run["id"], payload)
    failed = await repo.claim("manual")
    await repo.fail(failed["id"], "auth_required")
    assert (await repo.current())["id"] == before["id"]
    assert (await repo.status())["last_run"]["error_code"] == "auth_required"
    await engine.dispose()


@pytest.mark.parametrize("owner", ["", "default", "system:shared", "x" * 129, "bad\x00owner"])
def test_feedback_never_uses_shared_or_missing_owner(owner):
    from ggwork_pick.feedback.repository import FeedbackRepository

    with pytest.raises(ValueError):
        FeedbackRepository(None, owner)


@pytest.mark.asyncio
async def test_expired_lease_is_not_reported_as_running(pick_db_url, tmp_path):
    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    repo = FeedbackRepository(factory, "alice")
    now = datetime(2026, 10, 7, 12, tzinfo=UTC)
    await repo.claim("manual", now=now, lease_seconds=5)
    status = await repo.status(now=now + timedelta(seconds=6))
    assert status["running"] is None
    assert status["lease_expired"] is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_failure_and_replacement_claim_do_not_deadlock(pg_db_url, tmp_path):
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.service import PickService

    engine = host_engine(pg_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    now = datetime(2026, 10, 7, 12, tzinfo=UTC)
    first = await FeedbackRepository(factory, "alice").claim("manual", now=now, lease_seconds=5)
    first_update, release = asyncio.Event(), asyncio.Event()

    class PausedFailureSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            if getattr(statement, "is_update", False) and not first_update.is_set():
                first_update.set()
                await release.wait()
            return result

    paused = async_sessionmaker(engine, class_=PausedFailureSession, expire_on_commit=False)
    failure = asyncio.create_task(FeedbackRepository(paused, "alice").fail(first["id"], "refresh_failed", now=now + timedelta(seconds=6)))
    replacement = None
    try:
        await asyncio.wait_for(first_update.wait(), 3)
        replacement = asyncio.create_task(FeedbackRepository(factory, "alice").claim("manual", now=now + timedelta(seconds=7)))
        # Wait for a real PostgreSQL lock wait, not a scheduling sleep that might miss the race.
        async with asyncio.timeout(3):
            while True:
                async with engine.connect() as connection:
                    waiting = await connection.scalar(
                        text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND pid<>pg_backend_pid()")
                    )
                if waiting:
                    break
                await asyncio.sleep(0.01)
        release.set()
        await asyncio.wait_for(failure, 4)
        assert (await asyncio.wait_for(replacement, 4))["id"] != first["id"]
    finally:
        release.set()
        await asyncio.gather(*(task for task in (failure, replacement) if task is not None), return_exceptions=True)
        await engine.dispose()
