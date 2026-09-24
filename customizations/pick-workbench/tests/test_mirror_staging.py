"""P2-5a: staged v1 batches, the paired publish, the agent-only publish, staged-batch cleanup and pairing-aware pruning.

PostgreSQL-only cases take pg_db_url; the rest run on both dialects. SQLite has no pick_mirror, so the mirror parts
there are skipped or refused (U35).
"""

import asyncio
from datetime import timedelta

import pytest
import pytest_asyncio
from mirror_pairs import (
    AS_OF_TEXT,
    batch,
    batch_meta,
    building_version,
    catalog_payload,
    control,
    degrade,
    fetch,
    now,
    open_service,
    publish_pair,
    row_count,
    stage_pair,
    version,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest_asyncio.fixture
async def world(pick_db_url, tmp_path):
    engine, service, shared, importer = await open_service(pick_db_url, tmp_path)
    yield engine, service, shared, importer
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_world(pg_db_url, tmp_path):
    engine, service, shared, importer = await open_service(pg_db_url, tmp_path)
    yield engine, service, shared, importer
    await engine.dispose()


def _postgres(engine) -> bool:
    return engine.dialect.name == "postgresql"


@pytest.mark.asyncio
async def test_stage_is_invisible(world):
    engine, _, shared, importer = world
    current = await importer.catalog(catalog_payload("a"), "json")
    staged = await importer.catalog(catalog_payload("b"), "json", source_as_of=AS_OF_TEXT, meta=batch_meta("b"), stage=True)
    assert staged["staged"] is True and staged["deferred"] is None
    assert (await shared.current_batch("catalog"))["id"] == current["id"]
    row = await batch(engine, staged["id"])
    assert row["status"] == "importing" and row["published_at"] is None
    assert row["source_as_of"] == AS_OF_TEXT and row["validation_json"]["scope"] == "scope-b" and row["validation_json"]["rows"] == 3
    # The thin wrapper named in the plan does the same.
    direct = await shared.stage_import(kind="catalog", content_hash="f" * 64, raw_blob_path=row["raw_blob_path"], rows=[])
    assert direct["staged"] is True and (await batch(engine, direct["id"]))["status"] == "importing"


@pytest.mark.asyncio
async def test_stage_dedupe_defers_reuse(world):
    engine, _, shared, importer = world
    current = await importer.catalog(catalog_payload("a"), "json", source_as_of="2026-09-23T03:38:00.000Z", meta=batch_meta("old"))
    before = await batch(engine, current["id"])
    staged = await importer.catalog(catalog_payload("a"), "json", source_as_of=AS_OF_TEXT, meta=batch_meta("new"), stage=True)
    assert staged["id"] == current["id"] and staged["staged"] is False
    assert staged["deferred"] == {"source_as_of": AS_OF_TEXT, "validation_json": {**batch_meta("new"), "rows": 3}}
    assert await batch(engine, current["id"]) == before


@pytest.mark.asyncio
async def test_pair_publish_atomic_visibility(pg_world):
    engine, _, shared, importer = pg_world
    old_version, old = await publish_pair(engine, shared, importer, "a")
    version_id, schema = await building_version(engine)
    staged = await stage_pair(importer, "b")
    async with engine.connect() as holder:
        # Holding the control row stops the publish at its last statement, with everything before it done.
        await holder.execute(text("SELECT 1 FROM pick_mirror.control WHERE id = 1 FOR UPDATE"))
        publishing = asyncio.create_task(shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now()))
        try:
            await _wait_for_a_lock_waiter(engine)
            assert await _current(engine, shared) == (old[0]["id"], old[1]["id"], old_version)
            assert (await version(engine, version_id))["status"] == "building"
            assert not publishing.done()
        finally:
            await holder.rollback()
    await asyncio.wait_for(publishing, 10)
    assert await _current(engine, shared) == (staged[0]["id"], staged[1]["id"], version_id)
    assert (await version(engine, old_version))["superseded_at"] is not None


async def _current(engine, shared) -> tuple:
    """What another session reads as current: the shared pair and the newest published version."""
    rows = await fetch(engine, "SELECT id FROM pick_mirror.versions WHERE status = 'published' ORDER BY published_at DESC, id DESC LIMIT 1")
    catalog, knowledge = await shared.current_batch("catalog"), await shared.current_batch("knowledge")
    return catalog["id"], knowledge["id"], rows[0]["id"] if rows else None


async def _wait_for_a_lock_waiter(engine, timeout: float = 5.0) -> None:
    waiting = text("SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid WHERE a.datname = current_database() AND NOT l.granted")
    deadline = asyncio.get_running_loop().time() + timeout
    async with engine.connect() as conn:
        while (await conn.execute(waiting)).scalar_one() == 0:
            assert asyncio.get_running_loop().time() < deadline, "the publish never queued behind the held control row"
            await conn.rollback()
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_pair_publish_reuse_old_batch_becomes_current(pg_world):
    from ggwork_pick.repository import stamp

    engine, _, shared, importer = pg_world
    _, first = await publish_pair(engine, shared, importer, "a")
    await publish_pair(engine, shared, importer, "b")
    staged = await stage_pair(importer, "a")
    assert [item["id"] for item in staged] == [item["id"] for item in first] and staged[0]["staged"] is False
    version_id, schema = await building_version(engine)
    t = now()
    published = await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=t)
    assert published["catalog_batch_id"] == first[0]["id"] and published["published_at"] == stamp(t)
    row = await batch(engine, first[0]["id"])
    assert row["published_at"] == stamp(t) and row["source_as_of"] == AS_OF_TEXT
    assert await _current(engine, shared) == (first[0]["id"], first[1]["id"], version_id)


@pytest.mark.asyncio
async def test_pair_publish_rowcount_guard(pg_world):
    from ggwork_pick.mirror.publish import MirrorPublishError

    engine, _, shared, importer = pg_world
    version_id, schema = await building_version(engine)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE pick_mirror.versions SET status = 'failed' WHERE id = :id"), {"id": version_id})
    staged = await stage_pair(importer, "a")
    with pytest.raises(MirrorPublishError):
        await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now())
    assert [(await batch(engine, item["id"]))["status"] for item in staged] == ["importing", "importing"]
    assert (await version(engine, version_id))["status"] == "failed"
    # A batch that is neither staged nor published stops the publish the same way.
    version_id, schema = await building_version(engine)
    await shared.fail_staged([staged[0]["id"]])
    with pytest.raises(MirrorPublishError):
        await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now())
    assert (await version(engine, version_id))["status"] == "building"
    assert (await batch(engine, staged[1]["id"]))["status"] == "importing"


@pytest.mark.asyncio
async def test_pair_publish_refuses_bad_names_before_any_sql(pg_world):
    engine, _, shared, importer = pg_world
    version_id, schema = await building_version(engine)
    staged = await stage_pair(importer, "a")
    with pytest.raises(ValueError, match="schema"):
        await shared.publish_mirror_pair(version_id=version_id, schema_name="public", batches=staged, t=now())
    with pytest.raises(ValueError, match="PICK_MIRROR_READER_ROLE"):
        await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now(), reader_role='x"; DROP')
    with pytest.raises(ValueError, match="批次"):
        await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged[:1], t=now())
    with pytest.raises(ValueError, match="时区"):
        await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now().replace(tzinfo=None))
    assert (await version(engine, version_id))["status"] == "building"


@pytest.mark.asyncio
async def test_grant_skipped_without_role(pg_world):
    engine, _, shared, importer = pg_world
    version_id, schema = await building_version(engine)
    staged = await stage_pair(importer, "a")
    published = await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now(), reader_role="pick_board_reader_absent")
    assert published["reader_granted"] is False
    assert (await version(engine, version_id))["status"] == "published"


@pytest.mark.asyncio
async def test_grant_granted_with_role(pg_world, pg_reader_role):
    engine, _, shared, importer = pg_world
    version_id, schema = await building_version(engine)
    staged = await stage_pair(importer, "a")
    assert await _privileges(engine, pg_reader_role, schema) == (False, False)
    published = await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now())
    assert published["reader_granted"] is True
    assert await _privileges(engine, pg_reader_role, schema) == (True, True)


async def _privileges(engine, role: str, schema: str) -> tuple[bool, bool]:
    rows = await fetch(
        engine,
        "SELECT has_schema_privilege(:r, :s, 'USAGE') AS usage, has_table_privilege(:r, :t, 'SELECT') AS can_select",
        r=role,
        s=schema,
        t=f"{schema}.meta",
    )
    return rows[0]["usage"], rows[0]["can_select"]


@pytest.mark.asyncio
async def test_agent_only_increments_failures(world):
    engine, _, shared, importer = world
    paired = None
    if _postgres(engine):
        paired, _ = await publish_pair(engine, shared, importer, "a")
        paired_before = await version(engine, paired)
    staged = await stage_pair(importer, "b")
    t = now()
    count = await shared.publish_agent_only(batches=staged, reason="degraded:G5", t=t)
    assert [(await batch(engine, item["id"]))["status"] for item in staged] == ["published", "published"]
    assert (await shared.current_batch("catalog"))["id"] == staged[0]["id"]
    if not _postgres(engine):
        assert count is None
        return
    state = await control(engine)
    assert count == 1 and state["consecutive_failures"] == 1
    assert state["last_failure"] == "degraded:G5" and state["last_failure_at"] == t
    assert await version(engine, paired) == paired_before


@pytest.mark.asyncio
async def test_fail_staged_only_own_importing(world):
    engine, _, shared, importer = world
    reused = await importer.catalog(catalog_payload("a"), "json")
    fresh = await importer.catalog(catalog_payload("b"), "json", stage=True)
    again = await importer.catalog(catalog_payload("a"), "json", stage=True)
    assert again["id"] == reused["id"] and again["staged"] is False
    assert await row_count(engine, fresh["id"]) == 3
    await shared.fail_staged([fresh["id"], again["id"]])
    failed = await batch(engine, fresh["id"])
    assert failed["status"] == "failed" and failed["content_hash"] == "failed-" + fresh["id"]
    assert await row_count(engine, fresh["id"]) == 0
    kept = await batch(engine, reused["id"])
    assert kept["status"] == "published" and kept["content_hash"] == reused["content_hash"] and await row_count(engine, reused["id"]) == 3


@pytest.mark.asyncio
async def test_fail_staged_returns_only_unused_blobs(world):
    engine, _, shared, importer = world
    fresh = await importer.catalog(catalog_payload("b"), "json", stage=True)
    path = (await batch(engine, fresh["id"]))["raw_blob_path"]
    assert await shared.fail_staged([fresh["id"]]) == [path]
    assert await shared.fail_staged([fresh["id"]]) == []
    assert await shared.fail_staged([]) == []


@pytest.mark.asyncio
async def test_fail_staged_frees_dedupe_slot(world):
    _, _, shared, importer = world
    first = await importer.catalog(catalog_payload("b"), "json", stage=True)
    await shared.fail_staged([first["id"]])
    second = await importer.catalog(catalog_payload("b"), "json", stage=True)
    assert second["staged"] is True and second["id"] != first["id"]


@pytest.mark.asyncio
async def test_prune_deletes_blob_of_failed_batch(world):
    engine, _, shared, importer = world
    # A staged batch fails first; the same content is later published normally and ages out.
    failed = await importer.catalog(catalog_payload("x"), "json", stage=True)
    await shared.fail_staged([failed["id"]])
    published = await importer.catalog(catalog_payload("x"), "json")
    await importer.catalog(catalog_payload("y"), "json")
    path = (await batch(engine, failed["id"]))["raw_blob_path"]
    assert path == (await batch(engine, published["id"]))["raw_blob_path"]
    assert await shared.prune_shared("catalog", 1) == [path]
    assert (await batch(engine, published["id"]))["status"] == "pruned"


@pytest.mark.asyncio
async def test_leftover_importing_blocks_stage_until_cleaned(world):
    engine, _, shared, importer = world
    leftover = await importer.catalog(catalog_payload("b"), "json", stage=True)
    with pytest.raises(IntegrityError):
        await importer.catalog(catalog_payload("b"), "json", stage=True)
    assert await shared.fail_leftover_staged() == [(await batch(engine, leftover["id"]))["raw_blob_path"]]
    assert (await batch(engine, leftover["id"]))["status"] == "failed"
    retried = await importer.catalog(catalog_payload("b"), "json", stage=True)
    assert retried["staged"] is True
    assert await shared.fail_leftover_staged() != []
    assert await shared.fail_leftover_staged() == []


async def _set_accept_empty(engine, set_at) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE pick_mirror.control SET accept_empty_once = true, accept_empty_set_at = :t, consecutive_failures = 2 WHERE id = 1"), {"t": set_at}
        )


@pytest.mark.asyncio
async def test_accept_empty_kept_if_reset_after_gate(pg_world):
    engine, _, shared, importer = pg_world
    seen = now() - timedelta(minutes=5)
    await _set_accept_empty(engine, seen)
    version_id, schema = await building_version(engine)
    staged = await stage_pair(importer, "a")
    # The operator runs accept-empty again after the gate read seen.
    await _set_accept_empty(engine, now())
    await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now(), accept_empty_used=True, accept_empty_seen=seen)
    state = await control(engine)
    assert state["accept_empty_once"] is True and state["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_accept_empty_consumed_when_seen(pg_world):
    engine, _, shared, importer = pg_world
    seen = now() - timedelta(minutes=5)
    await _set_accept_empty(engine, seen)
    version_id, schema = await building_version(engine)
    staged = await stage_pair(importer, "a")
    await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now(), accept_empty_used=True, accept_empty_seen=seen)
    state = await control(engine)
    assert state["accept_empty_once"] is False and state["accept_empty_set_at"] == seen and state["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_accept_empty_untouched_when_not_used(pg_world):
    engine, _, shared, importer = pg_world
    seen = now() - timedelta(minutes=5)
    await _set_accept_empty(engine, seen)
    version_id, schema = await building_version(engine)
    staged = await stage_pair(importer, "a")
    await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now(), accept_empty_used=False, accept_empty_seen=seen)
    state = await control(engine)
    assert state["accept_empty_once"] is True and state["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_record_mirror_failure(world):
    engine, _, shared, _ = world
    if not _postgres(engine):
        assert await shared.record_mirror_failure(reason="busy", t=now()) is None
        return
    reasons = ["busy", "fallback_v1", "degraded:DriftError"]
    counts = [await shared.record_mirror_failure(reason=reason, t=now()) for reason in reasons]
    assert counts == [1, 2, 3]
    state = await control(engine)
    assert state["consecutive_failures"] == 3 and state["last_failure"] == "degraded:DriftError"
    for bad in ("", "oops", "degraded:", "degraded:a b", "capacity\n"):
        with pytest.raises(ValueError, match="失败代号"):
            await shared.record_mirror_failure(reason=bad, t=now())
    with pytest.raises(ValueError, match="时区"):
        await shared.record_mirror_failure(reason="v1", t=now().replace(tzinfo=None))
    assert (await control(engine))["consecutive_failures"] == 3


@pytest.mark.asyncio
async def test_pair_publish_is_postgres_only(tmp_path):
    engine, _, shared, importer = await open_service(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}", tmp_path)
    try:
        staged = await stage_pair(importer, "a")
        with pytest.raises(RuntimeError, match="PostgreSQL"):
            await shared.publish_mirror_pair(version_id=1, schema_name="pickm_v000001", batches=staged, t=now())
        assert [(await batch(engine, item["id"]))["status"] for item in staged] == ["importing", "importing"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prune_keeps_batches_paired_with_published_versions(world):
    engine, _, shared, importer = world
    if _postgres(engine):
        _, paired = await publish_pair(engine, shared, importer, "a")
    else:
        paired = [await importer.catalog(catalog_payload("a"), "json"), await importer.knowledge_bundle([(b"# a", "realshort-rules.md", "rules")])]
    for tag in ("b", "c", "d", "e"):
        await degrade(shared, importer, tag)
    assert await shared.prune_shared("catalog", 3) != []
    await shared.prune_shared("knowledge", 3)
    statuses = [(await batch(engine, item["id"]))["status"] for item in paired]
    if _postgres(engine):
        assert statuses == ["published", "published"]
        assert await row_count(engine, paired[0]["id"]) == 3 and await row_count(engine, paired[1]["id"]) == 1
    else:
        assert statuses == ["pruned", "pruned"]
        assert await row_count(engine, paired[0]["id"]) == 0
