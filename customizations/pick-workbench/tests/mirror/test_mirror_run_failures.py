"""A mirror run that cannot publish a pair (P2-5c; plan 5.5, 782, 802, 1568, 1571-1573; U15-U17, U42, U46, U31).

PostgreSQL only. Each case makes run_world's RealShort double, the lock or the process fail in one way and checks
what is published (nothing, the agent batches alone, or v1 directly), the run record and the failure count.
"""

import asyncio

import gate_world as gw
import pytest
import pytest_asyncio
from fake_realshort import busy, v1_error, v2_error
from mirror_pairs import stage_pair
from mirror_rows import version_args
from run_world import (
    V1_RULES,
    V2_ROW_RESOURCES,
    advisory_locks,
    batches,
    control,
    make_sync,
    open_harness,
    schema_exists,
    shared_current,
    sync_runs,
    v1_calls,
    v2_row_calls,
    versions,
    world_fake,
)

from ggwork_pick.imports import Importer
from ggwork_pick.repository import PickRepository


@pytest_asyncio.fixture
async def harness(pg_db_url, tmp_path):
    opened = await open_harness(pg_db_url, tmp_path)
    yield opened
    await opened.engine.dispose()


async def _run(harness, world=None, *, intercept=None, v1_page_rows=1000, v1_rules=V1_RULES, **overrides):
    fake = world_fake(harness, world, intercept=intercept, v1_page_rows=v1_page_rows, v1_rules=v1_rules)
    return await make_sync(harness, fake, **overrides).run("cron"), fake


async def _other_holder(harness, holder="backfill"):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import try_mirror_lock

    conn = await open_dedicated(harness.dsn)
    assert await try_mirror_lock(conn, now=harness.clock(), holder=holder)
    return conn


@pytest.mark.asyncio
async def test_v1_failure_publishes_nothing(harness):
    first, _ = await _run(harness)
    harness.clock.advance(600)
    read_failed = v1_error(503, "read_failed")
    result, _ = await _run(
        harness, gw.with_v1_row(gw.baseline(), 0, title="新标题"), v1_page_rows=1, intercept=lambda c: read_failed if c.resource == "v1" and c.n >= 3 else None
    )
    assert (result["status"], result["details_json"]["outcome"], result["details_json"]["reason"]) == ("failed", "failed", "v1")
    assert "read_failed" in result["error"]
    assert harness.clock.sleeps.count(5) == 1
    assert [v["status"] for v in await versions(harness.engine)] == ["published", "failed"]
    assert await shared_current(harness.engine) == (first["catalog_batch_id"], first["knowledge_batch_id"])
    assert not [b for b in await batches(harness.engine) if b["status"] == "importing"]
    assert (await control(harness.engine))["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_a_half_staged_pair_is_failed(harness, monkeypatch):
    async def refuse(self, files, **options):
        raise ValueError("知识批次需1至50份文件，总量不超过25MB")

    monkeypatch.setattr(Importer, "knowledge_bundle", refuse)
    result, _ = await _run(harness)
    assert (result["status"], result["details_json"]["reason"]) == ("failed", "v1")
    [catalog] = await batches(harness.engine)
    assert (catalog["kind"], catalog["status"], catalog["content_hash"]) == ("catalog", "failed", f"failed-{catalog['id']}")
    assert [v["status"] for v in await versions(harness.engine)] == ["failed"]


@pytest.mark.asyncio
async def test_drift_twice_in_v1_stage_publishes_nothing(harness):
    changed = v1_error(409, "source_changed")
    result, fake = await _run(harness, intercept=lambda c: changed if c.resource == "v1" else None)
    details = result["details_json"]
    assert (result["status"], details["reason"], details["attempts"]) == ("failed", "drift", 2)
    assert details["drift"] == {"count": 2, "stages": ["v1", "v1"]}
    assert harness.clock.sleeps == [90]
    assert [v["status"] for v in await versions(harness.engine)] == ["failed", "failed"]
    assert await batches(harness.engine) == []
    assert v2_row_calls(fake.calls) == []
    state = await control(harness.engine)
    assert (state["consecutive_failures"], state["last_failure"]) == (1, "drift")


@pytest.mark.asyncio
async def test_drift_twice_in_v2_stage_degrades(harness):
    changed = v2_error(409, "source_changed")
    result, _ = await _run(harness, intercept=lambda c: changed if c.resource in V2_ROW_RESOURCES else None)
    details = result["details_json"]
    assert (result["status"], details["outcome"], details["reason"], details["attempts"]) == ("success", "degraded", "degraded:drift", 2)
    assert details["drift"] == {"count": 2, "stages": ["v2", "v2"]}
    assert harness.clock.sleeps == [90]
    assert [v["status"] for v in await versions(harness.engine)] == ["failed", "failed"]
    staged = await batches(harness.engine)
    assert [b["status"] for b in staged] == ["failed", "failed", "published", "published"]
    assert await shared_current(harness.engine) == (result["catalog_batch_id"], result["knowledge_batch_id"])
    assert (await control(harness.engine))["consecutive_failures"] == 1
    # The fold would read rs_series_day at a fingerprint the drift made stale (U29).
    assert "series" not in details and "series_ms" not in details["stages"]


@pytest.mark.asyncio
async def test_manifest_drift_twice_publishes_nothing(harness):
    changed = v2_error(409, "source_changed")
    result, fake = await _run(harness, intercept=lambda c: changed if c.resource == "manifest" else None)
    details = result["details_json"]
    assert (result["status"], details["outcome"], details["reason"], details["attempts"]) == ("failed", "failed", "drift", 2)
    assert details["drift"] == {"count": 2, "stages": ["manifest", "manifest"]}
    assert harness.clock.sleeps == [90]
    assert [c.resource for c in fake.calls] == ["manifest", "manifest"]
    assert await batches(harness.engine) == [] and await versions(harness.engine) == []
    assert "series" not in details
    state = await control(harness.engine)
    assert (state["consecutive_failures"], state["last_failure"]) == (1, "drift")


@pytest.mark.asyncio
async def test_manifest_drift_once_then_pairs(harness):
    changed = v2_error(409, "source_changed")
    result, fake = await _run(harness, intercept=lambda c: changed if c.resource == "manifest" and c.n == 1 else None)
    details = result["details_json"]
    assert (result["status"], details["outcome"], details["reason"], details["attempts"]) == ("success", "paired", None, 2)
    assert details["drift"] == {"count": 1, "stages": ["manifest"]}
    assert harness.clock.sleeps == [90]
    assert {c.params["as_of"] for c in v1_calls(fake.calls)} == {details["as_of"]}
    assert [v["status"] for v in await versions(harness.engine)] == ["published"]
    assert "series" in details
    assert (await control(harness.engine))["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_manifest_non_busy_failure_falls_back(harness):
    read_failed = v2_error(503, "read_failed")
    result, fake = await _run(harness, intercept=lambda c: read_failed if c.resource == "manifest" else None)
    details = result["details_json"]
    assert (result["status"], details["outcome"], details["reason"]) == ("success", "fallback_v1", "fallback_v1")
    assert details["fallback"]["cause"] == "manifest:SourceReadError"
    assert harness.clock.sleeps == [5]
    assert v1_calls(fake.calls) and not any("as_of" in c.params or "fp" in c.params for c in v1_calls(fake.calls))
    assert await shared_current(harness.engine) == (result["catalog_batch_id"], result["knowledge_batch_id"])
    assert len(await sync_runs(harness)) == 1
    assert await versions(harness.engine) == []
    state = await control(harness.engine)
    assert (state["consecutive_failures"], state["last_failure"]) == (1, "fallback_v1")


@pytest.mark.asyncio
async def test_busy_timeout_publishes_nothing(harness):
    result, fake = await _run(harness, intercept=lambda c: busy() if c.resource == "manifest" else None)
    assert (result["status"], result["details_json"]["reason"]) == ("failed", "busy")
    assert harness.clock.sleeps == [60] * 20
    assert v1_calls(fake.calls) == [] and v2_row_calls(fake.calls) == []
    assert await batches(harness.engine) == [] and await versions(harness.engine) == []
    state = await control(harness.engine)
    assert (state["consecutive_failures"], state["last_failure"]) == (1, "busy")


@pytest.mark.asyncio
async def test_lock_busy_not_counted(harness):
    other = await _other_holder(harness)
    try:
        pid = await other.fetchval("SELECT pg_backend_pid()")
        result, fake = await _run(harness)
    finally:
        await other.close()
    details = result["details_json"]
    assert (result["status"], details["outcome"]) == ("failed", "lock_busy")
    assert "镜像锁被占用" in result["error"]
    assert (details["lock_holder"]["pid"], details["lock_holder"]["holder"]) == (pid, "backfill")
    assert details["lock_holder"]["since"] is not None
    assert fake.calls == []
    assert (await control(harness.engine))["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_leftovers_cleaned_only_under_lock(harness):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.versions import create_version

    conn = await open_dedicated(harness.dsn)
    try:
        leftover = await create_version(conn, **version_args())
    finally:
        await conn.close()
    shared = PickRepository.shared(harness.service.session_factory)
    staged = await stage_pair(Importer(shared, harness.service.data_dir), "left")
    other = await _other_holder(harness)
    try:
        blocked, _ = await _run(harness)
    finally:
        await other.close()
    assert blocked["details_json"]["outcome"] == "lock_busy"
    assert [v["status"] for v in await versions(harness.engine)] == ["building"]
    assert {b["status"] for b in await batches(harness.engine)} == {"importing"}
    result, _ = await _run(harness)
    assert result["details_json"]["cleanup"]["versions"]["failed_building"] == [leftover.id]
    assert result["details_json"]["outcome"] == "paired"
    left = [v for v in await versions(harness.engine) if v["id"] == leftover.id][0]
    assert left["status"] == "failed" and not await schema_exists(harness.engine, leftover.schema_name)
    by_id = {b["id"]: b for b in await batches(harness.engine)}
    assert {by_id[batch["id"]]["status"] for batch in staged} == {"failed"}


@pytest.mark.asyncio
async def test_cancel_cleans_and_unlocks(harness, monkeypatch):
    from ggwork_pick.mirror import lock, run_v2

    opened, started = [], asyncio.Event()
    real_open = lock.open_dedicated

    async def remembered(dsn):
        conn = await real_open(dsn)
        opened.append(conn)
        return conn

    async def slow_copy(conn, *args, **kwargs):
        started.set()
        await conn.execute("SELECT pg_sleep(30)")

    monkeypatch.setattr(lock, "open_dedicated", remembered)
    monkeypatch.setattr(run_v2, "copy_rows", slow_copy)
    fake = world_fake(harness)
    task = asyncio.create_task(make_sync(harness, fake).run("cron"))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    [version] = await versions(harness.engine)
    assert version["status"] == "failed" and version["dropped_at"] is not None
    assert not await schema_exists(harness.engine, version["schema_name"])
    [run] = await sync_runs(harness)
    assert run["status"] == "failed" and run["details_json"]["outcome"] == "cancelled"
    assert {b["status"] for b in await batches(harness.engine)} == {"failed"}
    assert (await control(harness.engine))["consecutive_failures"] == 0
    assert await advisory_locks(harness.engine) == 0
    assert [conn.is_closed() for conn in opened] == [True]


@pytest.mark.asyncio
async def test_dedicated_connection_failure_falls_back(harness):
    from sqlalchemy.engine import make_url

    missing = make_url(harness.dsn).set(database="no_such_database_p25c").render_as_string(hide_password=False)
    result, fake = await _run(harness, dsn=missing)
    details = result["details_json"]
    assert (result["status"], details["outcome"], details["fallback"]["cause"]) == ("success", "fallback_v1", "connection")
    assert [c.resource for c in fake.calls] == ["v1"]
    assert (await control(harness.engine))["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_permission_error_in_cleanup_is_recorded_and_the_run_goes_on(harness, monkeypatch):
    from asyncpg.exceptions import InsufficientPrivilegeError

    from ggwork_pick.mirror import run

    async def refused(conn, **options):
        raise InsufficientPrivilegeError("must be owner of schema")

    monkeypatch.setattr(run, "clean_leftover_versions", refused)
    result, _ = await _run(harness)
    cleanup = result["details_json"]["cleanup"]
    assert result["details_json"]["outcome"] == "paired"
    assert cleanup["versions"] is None and cleanup["errors"] == ["leftover_versions：InsufficientPrivilegeError（SQLSTATE 42501）"]
    assert "must be owner" not in str(result["details_json"])


@pytest.mark.asyncio
async def test_other_cleanup_errors_fail_the_run(harness, monkeypatch):
    from ggwork_pick.mirror import run

    async def broken(conn, **options):
        raise RuntimeError("boom")

    monkeypatch.setattr(run, "clean_leftover_versions", broken)
    with pytest.raises(RuntimeError):
        await _run(harness)
    [record] = await sync_runs(harness)
    assert record["status"] == "failed" and record["error"] == "同步内部错误：RuntimeError"
    assert await advisory_locks(harness.engine) == 0


@pytest.mark.asyncio
async def test_already_running_leaves_no_record(harness):
    async with harness.service.sync_lock:
        result, fake = await _run(harness)
    assert result == {"status": "already_running"}
    assert await sync_runs(harness) == [] and fake.calls == []


# ---------------------------------------------------------------- the other ways a half fails


@pytest.mark.asyncio
async def test_pair_publish_refused_degrades(harness, monkeypatch):
    from ggwork_pick.mirror.publish import MirrorPublishError

    async def refused(self, **arguments):
        raise MirrorPublishError("镜像版本不在 building 状态，本次不发布")

    monkeypatch.setattr(PickRepository, "publish_mirror_pair", refused)
    result, _ = await _run(harness)
    assert (result["status"], result["details_json"]["reason"]) == ("success", "degraded:MirrorPublishError")
    [version] = await versions(harness.engine)
    assert version["status"] == "failed" and not await schema_exists(harness.engine, version["schema_name"])
    assert await shared_current(harness.engine) == (result["catalog_batch_id"], result["knowledge_batch_id"])


@pytest.mark.asyncio
async def test_version_build_failure_degrades(harness, monkeypatch):
    from ggwork_pick.mirror import run
    from ggwork_pick.mirror.versions import MirrorBuildError

    async def refused(conn, **arguments):
        raise MirrorBuildError("登记镜像版本失败：UniqueViolationError（SQLSTATE 23505）")

    monkeypatch.setattr(run, "create_version", refused)
    result, fake = await _run(harness)
    assert (result["status"], result["details_json"]["reason"], result["details_json"]["version"]) == ("success", "degraded:MirrorBuildError", None)
    assert v2_row_calls(fake.calls) == [] and await versions(harness.engine) == []
    assert await shared_current(harness.engine) == (result["catalog_batch_id"], result["knowledge_batch_id"])


@pytest.mark.asyncio
async def test_a_hanging_mirror_half_ends_at_the_deadline(harness, monkeypatch):
    from ggwork_pick.mirror import run_v2
    from ggwork_pick.mirror.run import MirrorLimits

    async def hanging(conn, *args, **kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(run_v2, "copy_rows", hanging)
    result, _ = await _run(harness, limits=MirrorLimits(mirror_deadline=0.3))
    assert (result["status"], result["details_json"]["reason"]) == ("success", "degraded:deadline")
    [version] = await versions(harness.engine)
    assert version["status"] == "failed" and version["dropped_at"] is not None
    assert await advisory_locks(harness.engine) == 0


@pytest.mark.asyncio
async def test_low_disk_fails_before_any_request(harness):
    from ggwork_pick.mirror.run import MirrorLimits

    result, fake = await _run(harness, limits=MirrorLimits(min_free_bytes=10**18))
    assert (result["status"], result["details_json"]["reason"], result["details_json"]["blocked"]) == ("failed", "v1", "disk")
    assert "磁盘剩余空间不足" in result["error"] and fake.calls == []
    assert (await control(harness.engine))["last_failure"] == "v1"


@pytest.mark.asyncio
async def test_missing_rules_publish_nothing(harness):
    result, _ = await _run(harness, v1_rules=None)
    assert (result["status"], result["details_json"]["reason"]) == ("failed", "v1") and "U8" in result["error"]
    assert await batches(harness.engine) == []
    assert [v["status"] for v in await versions(harness.engine)] == ["failed"]


@pytest.mark.asyncio
async def test_v1_pan_text_publishes_nothing(harness):
    result, fake = await _run(harness, gw.with_v1_row(gw.baseline(), 0, title=gw.PAN))
    gate = result["details_json"]["gates"]["v1_text"]
    assert (result["status"], result["details_json"]["reason"]) == ("failed", "v1")
    assert gate["consequence"] == "v1" and gate["pan"]["rows"] == [gw.b64url("c-1")] and gate["pan"]["paths"] == {"v1.rows[*].title": 1}
    assert result["details_json"]["scrub_hits"] == {"v1": 1, "v1_rules": 0}
    assert await batches(harness.engine) == [] and v2_row_calls(fake.calls) == []


@pytest.mark.asyncio
async def test_a_bad_export_token_falls_back(harness):
    result, fake = await _run(harness, export_token="has a space")
    assert (result["status"], result["details_json"]["outcome"], result["details_json"]["fallback"]["cause"]) == ("success", "fallback_v1", "client")
    assert [c.resource for c in fake.calls] == ["v1"]


@pytest.mark.asyncio
async def test_a_failed_fallback_is_a_v1_failure(harness):
    missing = v2_error(404, "not_found")
    result, _ = await _run(harness, intercept=lambda c: missing if c.resource == "manifest" else v1_error(404, "not_found"))
    details = result["details_json"]
    assert (result["status"], details["reason"], details["fallback"]["cause"]) == ("failed", "v1", "manifest:ConfigError")
    assert result["error"] == "RealShort feed 返回 HTTP 404"
    assert (await control(harness.engine))["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_retention_error_after_publishing_is_reported(harness, monkeypatch):
    from ggwork_pick.mirror import run

    real, calls = run.prune_versions, []

    async def second_fails(conn, **options):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("版本 1 在保留途中不再是 published，本次删除已回滚")
        return await real(conn, **options)

    monkeypatch.setattr(run, "prune_versions", second_fails)
    result, _ = await _run(harness)
    assert (result["status"], result["details_json"]["outcome"]) == ("success", "paired")
    assert result["details_json"]["retention_after"] == {"error": "RuntimeError"}


@pytest.mark.asyncio
async def test_the_v1_half_ends_at_the_run_deadline(harness, monkeypatch):
    """The v1 half gets what is left of the 900 seconds from as_of (Budget.run_left), not a fresh 900."""
    from ggwork_pick.mirror import run, run_v1

    real_create, real_scan = run.create_version, run_v1.scanned_v1_page

    async def late_version(conn, **arguments):
        harness.clock.advance(900 - 0.3)  # all but 0.3 s of the run's 900 are gone before v1 starts
        return await real_create(conn, **arguments)

    async def slow_scan(scan, body):
        await asyncio.sleep(3)
        return await real_scan(scan, body)

    monkeypatch.setattr(run, "create_version", late_version)
    monkeypatch.setattr(run_v1, "scanned_v1_page", slow_scan)
    result, fake = await _run(harness)
    details = result["details_json"]
    assert (result["status"], details["outcome"], details["reason"]) == ("failed", "failed", "v1")
    assert "总时限" in result["error"]
    assert [v["status"] for v in await versions(harness.engine)] == ["failed"]
    assert await batches(harness.engine) == [] and v2_row_calls(fake.calls) == []
    state = await control(harness.engine)
    assert (state["consecutive_failures"], state["last_failure"]) == (1, "v1")
