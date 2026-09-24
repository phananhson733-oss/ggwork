"""The mirror run's main paths (P2-5c; plan 5.2, 5.5, 1564-1567, 1569, 1574, 1629; U17, U43, U50).

PostgreSQL only: every case runs MirrorSync end to end against run_world's RealShort double on its own database, and
each case changes one thing of the consistent world to reach one outcome.
"""

import logging

import gate_world as gw
import pytest
import pytest_asyncio
from fake_realshort import v2_error
from run_world import (
    advisory_locks,
    batches,
    behind,
    control,
    fetch,
    make_sync,
    open_harness,
    recording,
    rows_of,
    schema_exists,
    shared_current,
    v1_calls,
    v2_row_calls,
    versions,
    world_fake,
)

from ggwork_pick.repository import PickRepository, stamp


@pytest_asyncio.fixture
async def harness(pg_db_url, tmp_path):
    opened = await open_harness(pg_db_url, tmp_path)
    yield opened
    await opened.engine.dispose()


async def _run(harness, world=None, **options):
    fake = world_fake(harness, world, **{key: value for key, value in options.items() if key in ("intercept", "v1_page_rows")})
    sync = make_sync(harness, fake, **{key: value for key, value in options.items() if key not in ("intercept", "v1_page_rows")})
    return await sync.run("cron"), fake


def _retitled(world, title: str):
    """The same world with other v1 content (a title G8 does not compare): a new catalog batch, not a dedupe."""
    return gw.with_v1_row(world, 0, title=title)


@pytest.mark.asyncio
async def test_a_consistent_world_publishes_the_pair(harness):
    result, fake = await _run(harness)
    assert result["status"] == "success"
    details = result["details_json"]
    assert (details["mode"], details["outcome"], details["reason"], details["version"], details["attempts"]) == ("mirror", "paired", None, 1, 1)
    assert details["consecutive_failures"] == 0 and details["alert"] is False
    assert details["gates"] == {
        name: "pass" for name in ("v1_text", "row_counts", "forbidden_columns", "mirror_text", "references", "control_totals", "v1_consistency", "empty_tables")
    }
    assert details["scrub_hits"] == {"v1": 0, "v1_rules": 0, "mirror": 0}
    assert details["warnings"] == ["catalog_import_incomplete"]
    assert set(details["stages"]) >= {"manifest_ms", "v1_ms", "v2_ms", "finalize_ms", "gates_ms", "publish_ms", "retention_ms", "series_ms"}
    assert details["series"]["needs_backfill"] is True
    [version] = await versions(harness.engine)
    assert version["status"] == "published" and (version["agent_catalog_batch_id"], version["agent_knowledge_batch_id"]) == await shared_current(harness.engine)
    assert (result["catalog_batch_id"], result["knowledge_batch_id"]) == await shared_current(harness.engine)
    assert result["rows"] == len(gw.V1_CANDIDATES) and result["source_as_of"] == details["as_of"]
    assert not await behind(harness.engine)
    assert (await control(harness.engine))["consecutive_failures"] == 0
    assert await advisory_locks(harness.engine) == 0


def _duplicate_account(world):
    first = world.tables["catalog_accounts"][0]
    return gw.with_table(world, "catalog_accounts", (first, {**first}))


# Each breaks the mirror half in one place: finalize (a duplicate key after COPY), G5, G8.
MIRROR_FAILURES = {
    "after_copy": (lambda world: _retitled(_duplicate_account(world), "换了标题的剧"), "degraded:MirrorBuildError"),
    "g5": (lambda world: _retitled(gw.with_row(world, "catalog_signals", 0, note=gw.PAN), "换了标题的剧"), "degraded:mirror_text"),
    "g8": (lambda world: gw.with_v1(world, [*world.v1_rows, gw.v1_row("c-3", ("kd",))]), "degraded:v1_consistency"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(MIRROR_FAILURES))
async def test_mirror_failure_degrades(harness, case):
    change, reason = MIRROR_FAILURES[case]
    first, _ = await _run(harness)
    harness.clock.advance(600)
    result, _ = await _run(harness, change(gw.baseline()))
    assert result["status"] == "success"
    assert (result["details_json"]["outcome"], result["details_json"]["reason"]) == ("degraded", reason)
    old, new = await versions(harness.engine)
    assert (old["status"], new["status"]) == ("published", "failed") and new["dropped_at"] is not None
    assert not await schema_exists(harness.engine, new["schema_name"])
    assert await shared_current(harness.engine) == (result["catalog_batch_id"], result["knowledge_batch_id"])
    assert result["catalog_batch_id"] != first["catalog_batch_id"]
    assert await behind(harness.engine)
    state = await control(harness.engine)
    assert (state["consecutive_failures"], state["last_failure"]) == (1, reason)
    assert result["details_json"]["consecutive_failures"] == 1
    assert await advisory_locks(harness.engine) == 0


@pytest.mark.asyncio
async def test_mirror_deadline_degrades(harness, monkeypatch):
    from ggwork_pick.mirror import run_v2

    real = run_v2.run_mirror_gates

    async def slow_gates(*args, **kwargs):
        harness.clock.advance(800)  # step 8 outlives the 750-second mirror deadline
        return await real(*args, **kwargs)

    monkeypatch.setattr(run_v2, "run_mirror_gates", slow_gates)
    results = []
    for _ in range(3):
        result, _ = await _run(harness)
        results.append(result)
        harness.clock.advance(60)
    assert [r["details_json"]["reason"] for r in results] == ["degraded:deadline"] * 3
    assert [r["details_json"]["consecutive_failures"] for r in results] == [1, 2, 3]
    assert [r["details_json"]["alert"] for r in results] == [False, False, True]
    assert all(r["status"] == "success" for r in results)
    assert [v["status"] for v in await versions(harness.engine)] == ["failed"] * 3
    assert not any([await schema_exists(harness.engine, v["schema_name"]) for v in await versions(harness.engine)])
    assert await shared_current(harness.engine) == (results[-1]["catalog_batch_id"], results[-1]["knowledge_batch_id"])
    assert await advisory_locks(harness.engine) == 0


@pytest.mark.asyncio
async def test_v1_before_v2(harness, monkeypatch):
    events = []
    real = PickRepository.publish_import

    async def logged(self, **batch):
        published = await real(self, **batch)
        events.append(f"staged:{batch['kind']}")
        return published

    monkeypatch.setattr(PickRepository, "publish_import", logged)
    result, fake = await _run(harness, intercept=recording(events))
    assert result["details_json"]["outcome"] == "paired"
    first_v2 = next(index for index, event in enumerate(events) if event in {call.resource for call in v2_row_calls(fake.calls)})
    assert events.index("staged:catalog") < first_v2 and events.index("staged:knowledge") < first_v2
    assert max(index for index, event in enumerate(events) if event == "v1") < first_v2
    assert all(call.params.get("as_of") and call.params.get("fp") for call in v1_calls(fake.calls))


@pytest.mark.asyncio
async def test_dedupe_current_then_mirror_fails(harness, monkeypatch):
    first, _ = await _run(harness)
    catalog = first["catalog_batch_id"]
    seen = []
    real = PickRepository.publish_agent_only

    async def checked(self, **arguments):
        # The deferred reuse is not written before the degraded publish's own transaction.
        seen.append([b for b in await batches(harness.engine) if b["id"] == catalog][0]["source_as_of"])
        return await real(self, **arguments)

    monkeypatch.setattr(PickRepository, "publish_agent_only", checked)
    harness.clock.advance(600)
    too_large = v2_error(500, "row_too_large", resource="rs_ids", key=["d-1"])
    result, _ = await _run(harness, intercept=lambda call: too_large if call.resource == "rs_ids" else None)
    assert (result["details_json"]["outcome"], result["details_json"]["reason"]) == ("degraded", "degraded:RowTooLargeError")
    assert result["catalog_batch_id"] == catalog and seen == [first["source_as_of"]]
    [batch] = [b for b in await batches(harness.engine) if b["id"] == catalog]
    assert batch["status"] == "published" and batch["source_as_of"] == result["source_as_of"] != first["source_as_of"]
    assert await rows_of(harness.engine, catalog) == len(gw.V1_CANDIDATES)
    assert not await behind(harness.engine)


@pytest.mark.asyncio
async def test_lock_reusable_after_run(harness):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import release_mirror_lock, try_mirror_lock

    await _run(harness)
    state = await control(harness.engine)
    assert (state["lock_holder_since"], state["lock_holder"]) == (None, None)
    conn = await open_dedicated(harness.dsn)
    try:
        assert await try_mirror_lock(conn, now=harness.clock(), holder="sync")
        assert await release_mirror_lock(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_pg_locks_zero_after_run(harness):
    await _run(harness)
    assert await advisory_locks(harness.engine) == 0
    harness.clock.advance(600)
    await _run(harness, gw.with_row(gw.baseline(), "catalog_signals", 0, note=gw.PAN))
    assert await advisory_locks(harness.engine) == 0


@pytest.mark.asyncio
async def test_db_size_cap_degrades_without_version(harness):
    from ggwork_pick.mirror.run import MirrorLimits

    result, fake = await _run(harness, limits=MirrorLimits(db_size_cap=1))
    details = result["details_json"]
    assert (result["status"], details["outcome"], details["reason"], details["version"]) == ("success", "degraded", "capacity", None)
    assert await versions(harness.engine) == []
    assert v2_row_calls(fake.calls) == []
    assert v1_calls(fake.calls) and all(call.params.get("as_of") and call.params.get("fp") for call in v1_calls(fake.calls))
    assert await shared_current(harness.engine) == (result["catalog_batch_id"], result["knowledge_batch_id"])
    state = await control(harness.engine)
    assert (state["consecutive_failures"], state["last_failure"]) == (1, "capacity")


@pytest.mark.asyncio
async def test_alert_logs_error_at_three(harness, caplog):
    caplog.set_level(logging.ERROR, logger="ggwork_pick.mirror.run")
    broken = gw.with_row(gw.baseline(), "catalog_signals", 0, note=gw.PAN)
    counts = []
    for _ in range(3):
        result, _ = await _run(harness, broken)
        counts.append(len([r for r in caplog.records if r.levelno >= logging.ERROR]))
        harness.clock.advance(60)
    assert counts == [0, 0, 1]
    [record] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert record.getMessage() == "[pick-mirror] 连续失败 3 次：degraded:mirror_text"
    assert gw.PAN not in caplog.text


@pytest.mark.asyncio
async def test_published_pair_carries_the_publish_moment(harness):
    result, _ = await _run(harness)
    [batch] = [b for b in await batches(harness.engine) if b["id"] == result["catalog_batch_id"]]
    [version] = await fetch(harness.engine, "SELECT published_at FROM pick_mirror.versions WHERE id = 1")
    assert batch["published_at"] == stamp(version["published_at"]) == stamp(harness.clock())
