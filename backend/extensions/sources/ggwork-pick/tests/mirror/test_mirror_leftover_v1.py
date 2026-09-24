"""A dead mirror run's staged pair and the v1 runs that follow it (review flow-2 / prod-2). PostgreSQL only.

A mirror run killed after staging (SIGKILL, OOM, a deploy) leaves its two batches importing, holding their content-hash
slots. Only a run under the mirror lock may fail them (clean_leftovers). The v1 RealShortSync of a rolled-back switch,
and MirrorSync's fallback when its dedicated connection failed, hold no lock of their own: they take it as cleanup
without waiting, clean, and pull. When the lock is held elsewhere the same content fails readably, not as an internal
error.
"""

import pytest
import pytest_asyncio
from fake_realshort import EXPORT_TOKEN, FEED_TOKEN
from run_world import BASE, batches, control, make_sync, open_harness, shared_current, sync_runs, world_fake

from ggwork_pick.repository import PickRepository
from ggwork_pick.service import SyncSettings


@pytest_asyncio.fixture
async def harness(pg_db_url, tmp_path):
    opened = await open_harness(pg_db_url, tmp_path)
    yield opened
    await opened.engine.dispose()


async def _dead_run_staged(harness, fake) -> list[str]:
    """What a mirror run killed between stage_v1 and the publish leaves: this content's pair, importing."""
    from ggwork_pick.mirror.client import FeedClient
    from ggwork_pick.mirror.run_v1 import V1Staged, stage_v1

    repo = PickRepository.shared(harness.service.session_factory)
    client = FeedClient(
        base_url=BASE,
        export_token=EXPORT_TOKEN,
        feed_token=FEED_TOKEN,
        transport=fake.transport(),
        clock=harness.clock,
        sleep=harness.clock.sleep,
        timer=harness.clock.timer,
    )
    async with client:
        manifest = await client.manifest_when_free()
        staged = await stage_v1(harness.service, repo, client, manifest, seconds=600)
    assert isinstance(staged, V1Staged)
    left = await batches(harness.engine)
    assert sorted((b["kind"], b["status"]) for b in left) == [("catalog", "importing"), ("knowledge", "importing")]
    return [b["id"] for b in left]


def _switched_off(harness, fake):
    """The rollback: PICK_MIRROR_ENABLED=0 on PostgreSQL, so the service hands out the v1 RealShortSync."""
    harness.service.sync_settings = SyncSettings(feed_url=BASE, feed_token=FEED_TOKEN, export_token=EXPORT_TOKEN, mirror_flag="0")
    harness.service.sync_transport = fake.transport()
    return harness.service.realshort_sync()


async def _other_holder(harness, holder="backfill"):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import try_mirror_lock

    conn = await open_dedicated(harness.dsn)
    assert await try_mirror_lock(conn, now=harness.clock(), holder=holder)
    return conn


@pytest.mark.asyncio
async def test_a_switched_off_v1_run_clears_a_dead_runs_staged_pair(harness):
    from ggwork_pick.sync import RealShortSync

    fake = world_fake(harness)
    left = await _dead_run_staged(harness, fake)
    harness.clock.advance(3600)
    sync = _switched_off(harness, fake)
    assert type(sync) is RealShortSync
    result = await sync.run("cron")
    assert (result["status"], result["error"]) == ("success", None)
    by_id = {b["id"]: b for b in await batches(harness.engine)}
    assert {by_id[batch_id]["status"] for batch_id in left} == {"failed"}
    assert await shared_current(harness.engine) == (result["catalog_batch_id"], result["knowledge_batch_id"])
    cleanup = result["details_json"]["cleanup"]
    assert cleanup["errors"] == [] and cleanup["blobs_deleted"] >= 1
    state = await control(harness.engine)
    assert (state["lock_holder"], state["lock_holder_since"]) == (None, None)


@pytest.mark.asyncio
async def test_a_switched_off_v1_run_with_nothing_left_records_no_cleanup(harness):
    fake = world_fake(harness)
    result = await _switched_off(harness, fake).run("cron")
    assert result["status"] == "success" and result["details_json"] is None


@pytest.mark.asyncio
async def test_a_held_lock_leaves_the_pair_and_the_v1_run_fails_readably(harness):
    fake = world_fake(harness)
    left = await _dead_run_staged(harness, fake)
    other = await _other_holder(harness)
    try:
        result = await _switched_off(harness, fake).run("cron")
    finally:
        await other.close()
    assert result["status"] == "failed"
    assert "暂存" in result["error"] and "mirror.admin cleanup" in result["error"]
    assert "IntegrityError" not in result["error"]
    assert result["details_json"]["cleanup"] == {"skipped": "lock_busy"}
    by_id = {b["id"]: b for b in await batches(harness.engine)}
    assert {by_id[batch_id]["status"] for batch_id in left} == {"importing"}
    [record] = await sync_runs(harness)
    assert record["status"] == "failed"


@pytest.mark.asyncio
async def test_a_failed_sweep_is_recorded_and_the_v1_run_goes_on(harness, monkeypatch):
    from ggwork_pick.mirror import run

    async def broken(conn, **options):
        raise RuntimeError("boom with a value")

    monkeypatch.setattr(run, "clean_leftover_versions", broken)
    fake = world_fake(harness)
    result = await _switched_off(harness, fake).run("cron")
    assert result["status"] == "success"
    assert result["details_json"]["cleanup"] == {"error": "RuntimeError"}
    assert "boom" not in str(result["details_json"])


@pytest.mark.asyncio
async def test_sqlite_has_no_sweep(tmp_path):
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick.service import PickService

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    service = PickService(tmp_path / "files", SyncSettings(feed_url=BASE, feed_token=FEED_TOKEN, mirror_flag="0"))
    try:
        await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
        assert service.realshort_sync().sweep is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_connection_fallback_sweeps_before_v1(harness, monkeypatch):
    from ggwork_pick.mirror import lock
    from ggwork_pick.mirror.connection import MirrorConnectionError

    fake = world_fake(harness)
    left = await _dead_run_staged(harness, fake)
    harness.clock.advance(3600)
    real_open, opened = lock.open_dedicated, []

    async def first_fails(dsn):
        opened.append(dsn)
        if len(opened) == 1:
            raise MirrorConnectionError("镜像专用连接失败：ConnectionRefusedError")
        return await real_open(dsn)

    monkeypatch.setattr(lock, "open_dedicated", first_fails)
    result = await make_sync(harness, fake).run("cron")
    details = result["details_json"]
    assert (result["status"], details["outcome"], details["fallback"]["cause"]) == ("success", "fallback_v1", "connection")
    assert details["cleanup"]["errors"] == [] and len(opened) == 2
    by_id = {b["id"]: b for b in await batches(harness.engine)}
    assert {by_id[batch_id]["status"] for batch_id in left} == {"failed"}
    assert await shared_current(harness.engine) == (result["catalog_batch_id"], result["knowledge_batch_id"])


@pytest.mark.asyncio
async def test_a_staged_duplicate_is_a_readable_error_in_the_connection_fallback(harness, monkeypatch):
    from ggwork_pick.mirror import lock
    from ggwork_pick.mirror.connection import MirrorConnectionError

    fake = world_fake(harness)
    await _dead_run_staged(harness, fake)

    async def refused(dsn):
        raise MirrorConnectionError("镜像专用连接失败：ConnectionRefusedError")

    monkeypatch.setattr(lock, "open_dedicated", refused)
    result = await make_sync(harness, fake).run("cron")
    details = result["details_json"]
    assert (result["status"], details["reason"], details["fallback"]["cause"]) == ("failed", "v1", "connection")
    assert details["cleanup"] == {"error": "MirrorConnectionError"}
    assert "暂存" in result["error"]
    assert (await control(harness.engine))["consecutive_failures"] == 1
