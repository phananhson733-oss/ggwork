"""Version retention and leftover cleanup on the sync's dedicated connection (P2-6; plan 3.6; U23, U24, U41).

PostgreSQL only: every test skips when PICK_TEST_PG_URL is unset.
"""

import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

T0 = datetime(2026, 9, 24, 3, 40, tzinfo=UTC)
FINGERPRINT = "0123456789abcdef" * 4
MIRROR_SOURCE = Path(__file__).resolve().parents[2] / "ggwork_pick" / "mirror"
MIRROR_SCHEMAS = "select nspname from pg_namespace where nspname like 'pickm%' order by nspname"
VERSIONS = "select id, status, dropped_at, error from pick_mirror.versions order by id"
# Session-level settings outlive the transaction on the connection; only SET LOCAL is allowed (0.3).
SESSION_SETTING = re.compile(r"(?is)^\s*(SET\s+(?!LOCAL\b)|RESET\b)|\bset_config\s*\([^)]*,\s*(false|0)\s*\)")
# The same, as a string literal anywhere in a line of source.
LITERAL_SETTING = re.compile(r"(?i)[\"']\s*(SET\s+(?!LOCAL\b)|RESET\b)|\bset_config\s*\([^)]*,\s*(false|0)\s*\)")


@pytest.fixture
def dsn(pg_db_url):
    from ggwork_pick.mirror.connection import dsn_from_url

    return dsn_from_url(pg_db_url)


@pytest_asyncio.fixture
async def observer(dsn):
    """A plain connection that seeds and inspects; it never takes the mirror lock."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def dedicated(dsn):
    """A dedicated connection that does not take the lock by itself."""
    from ggwork_pick.mirror.connection import open_dedicated

    conn = await open_dedicated(dsn)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def sync_conn(dedicated):
    """The sync's dedicated connection, holding the mirror lock as sync."""
    from ggwork_pick.mirror.lock import try_mirror_lock

    assert await try_mirror_lock(dedicated, now=T0, holder="sync")
    return dedicated


def _schema(n: int) -> str:
    return f"pickm_v{n:06d}"


def _bounded_by(budget_ms: int) -> list[str]:
    """What opens each DROP's transaction: the lock wait and the whole statement both bounded by `budget_ms` (U24)."""
    return ["BEGIN", f"SET LOCAL lock_timeout = {budget_ms}", f"SET LOCAL statement_timeout = {budget_ms}"]


def _drop_heads(sent: list[str]) -> list[list[str]]:
    return [sent[index - 3 : index] for index, query in enumerate(sent) if query.startswith("DROP SCHEMA")]


async def _create_schema(observer, name: str) -> None:
    await observer.execute(f'create schema "{name}"; create table "{name}".meta (key text primary key, value jsonb not null)')


async def _version(observer, n: int, *, status: str = "published", published=None, superseded=None, batch: str | None = None, schema: bool = True):
    await observer.execute(
        "insert into pick_mirror.versions (id, schema_name, status, as_of, fingerprint, created_at, published_at, superseded_at, agent_catalog_batch_id)"
        " values ($1, $2, $3, $4, $5, $4, $6, $7, $8)",
        n,
        _schema(n),
        status,
        (published or T0) - timedelta(minutes=5),
        FINGERPRINT,
        published,
        superseded,
        batch,
    )
    if schema:
        await _create_schema(observer, _schema(n))


async def _chain(observer, count: int, *, batch: str | None = None) -> None:
    """Versions 1..count published two hours apart, each superseded by the next; the last one is current."""
    published = [T0 - timedelta(hours=2 * (count - n + 1)) for n in range(1, count + 1)]
    for n in range(1, count + 1):
        superseded = published[n] if n < count else None
        await _version(observer, n, published=published[n - 1], superseded=superseded, batch=batch)


async def _cite(observer, version: int, *, at: datetime, batch: str = "batch-a") -> None:
    """A candidate snapshot that paired `version` with `batch` when it was created at `at`."""
    from ggwork_pick.repository import stamp

    await observer.execute(
        "insert into ggwp_candidate_sets (id, owner_id, thread_id, run_id, tool_call_id, catalog_batch_id, rule_version, ranking_version,"
        " conditions_json, ordered_items_json, created_at, mirror_version)"
        " values ($1, 'alice', 'thread-1', 'run-1', $1, $2, 'rules-1', 'rank-1', '{}'::json, '[]'::json, $3, $4)",
        uuid4().hex,
        batch,
        stamp(at),
        version,
    )


async def _schemas(observer) -> list[str]:
    return [row["nspname"] for row in await observer.fetch(MIRROR_SCHEMAS)]


async def _statuses(observer) -> dict[int, str]:
    return {row["id"]: row["status"] for row in await observer.fetch(VERSIONS)}


async def _prune(conn, **options):
    from ggwork_pick.mirror.retention import prune_versions

    return await prune_versions(conn, clock=lambda: T0, **options)


async def _clean(conn, **options):
    from ggwork_pick.mirror.retention import clean_leftover_versions

    return await clean_leftover_versions(conn, clock=lambda: T0, **options)


# ---- which published versions stay (plan 3.6, U23) ----


@pytest.mark.asyncio
async def test_the_latest_three_stay_and_the_rest_are_dropped(sync_conn, observer):
    # P2-6 test 1.
    await _chain(observer, 6)
    report = await _prune(sync_conn)
    assert (report.skipped, report.kept, report.dropped, report.lock_busy, report.over_cap) == (None, (6, 5, 4), (1, 2, 3), (), 0)
    assert await _schemas(observer) == [_schema(4), _schema(5), _schema(6)]
    rows = {row["id"]: (row["status"], row["dropped_at"]) for row in await observer.fetch(VERSIONS)}
    assert rows == {1: ("dropped", T0), 2: ("dropped", T0), 3: ("dropped", T0), 4: ("published", None), 5: ("published", None), 6: ("published", None)}
    # Nothing left to do the second time round.
    again = await _prune(sync_conn)
    assert (again.kept, again.dropped) == ((6, 5, 4), ())


@pytest.mark.asyncio
async def test_references_count_by_version_not_by_batch(sync_conn, observer):
    # P2-6 test 2: two versions share one batch; only the version a card recorded is kept.
    await _chain(observer, 5, batch="batch-a")
    await _cite(observer, 1, at=T0 - timedelta(days=1), batch="batch-a")
    report = await _prune(sync_conn)
    assert (report.kept, report.dropped) == ((5, 4, 3, 1), (2,))
    assert await _statuses(observer) == {1: "published", 2: "dropped", 3: "published", 4: "published", 5: "published"}
    assert _schema(1) in await _schemas(observer) and _schema(2) not in await _schemas(observer)


@pytest.mark.asyncio
async def test_a_reference_older_than_seven_days_no_longer_keeps_a_version(sync_conn, observer):
    # P2-6 test 3: created_at >= stamp(now - 7 days), compared as text like repository.prune_shared.
    await _chain(observer, 6)
    await _cite(observer, 1, at=T0 - timedelta(days=7, microseconds=1))
    await _cite(observer, 2, at=T0 - timedelta(days=7))
    await _cite(observer, 3, at=T0 - timedelta(days=9))
    await _cite(observer, 3, at=T0 - timedelta(days=8))
    report = await _prune(sync_conn)
    assert (report.kept, report.dropped) == ((6, 5, 4, 2), (1, 3))


@pytest.mark.asyncio
async def test_a_version_superseded_within_the_hour_stays(sync_conn, observer):
    # P2-6 test 4: someone paging through it must not meet the DROP; superseded_at > now - 1 hour, strictly.
    await _version(observer, 1, published=T0 - timedelta(hours=5), superseded=T0 - timedelta(minutes=61))
    await _version(observer, 2, published=T0 - timedelta(minutes=61), superseded=T0 - timedelta(minutes=60))
    await _version(observer, 3, published=T0 - timedelta(minutes=60), superseded=T0 - timedelta(minutes=59))
    await _version(observer, 4, published=T0 - timedelta(minutes=59), superseded=T0 - timedelta(minutes=30))
    await _version(observer, 5, published=T0 - timedelta(minutes=30), superseded=T0 - timedelta(minutes=10))
    await _version(observer, 6, published=T0 - timedelta(minutes=10))
    report = await _prune(sync_conn)
    assert (report.kept, report.dropped) == ((6, 5, 4, 3), (1, 2))


@pytest.mark.asyncio
async def test_over_ten_the_oldest_referenced_go_first(sync_conn, observer):
    # P2-6 test 5: twelve, all referenced: down to ten, oldest first.
    await _chain(observer, 12)
    for n in range(1, 13):
        await _cite(observer, n, at=T0 - timedelta(days=1))
    report = await _prune(sync_conn)
    assert report.dropped == (1, 2)
    assert report.kept == tuple(range(12, 2, -1)) and report.over_cap == 0
    assert len(await _schemas(observer)) == 10


@pytest.mark.asyncio
async def test_oldest_means_published_at_then_id(sync_conn, observer):
    # U23: not the id order. Versions 3, 7 and 9 share the oldest published_at; the lower ids go first.
    order = [3, 7, 9, 1, 2, 4, 5, 6, 8, 10, 11, 12]  # oldest first
    oldest = T0 - timedelta(days=2)
    for rank, n in enumerate(order):
        published = oldest if n in (3, 7, 9) else oldest + timedelta(hours=rank)
        superseded = None if n == 12 else published + timedelta(hours=1, minutes=30)
        await _version(observer, n, published=published, superseded=superseded)
        await _cite(observer, n, at=T0 - timedelta(days=1))
    report = await _prune(sync_conn)
    assert report.dropped == (3, 7)
    assert report.kept[:3] == (12, 11, 10) and set(report.kept) == set(order) - {3, 7}


@pytest.mark.asyncio
async def test_the_latest_three_and_the_grace_hour_never_go_even_over_the_cap(sync_conn, observer, caplog):
    # U23: rather over ten than drop what is current or just superseded; only referenced-only versions give way.
    await _version(observer, 1, published=T0 - timedelta(days=3), superseded=T0 - timedelta(days=2))
    await _version(observer, 2, published=T0 - timedelta(days=2), superseded=T0 - timedelta(minutes=58))
    for n in range(3, 14):
        superseded = None if n == 13 else T0 - timedelta(minutes=58 - 4 * (n - 2))
        await _version(observer, n, published=T0 - timedelta(minutes=58 - 4 * (n - 3)), superseded=superseded)
    await _cite(observer, 1, at=T0 - timedelta(hours=1))
    await _cite(observer, 2, at=T0 - timedelta(hours=1))
    caplog.set_level(logging.WARNING, logger="ggwork_pick.mirror.retention")
    report = await _prune(sync_conn)
    # Version 2 was superseded 58 minutes ago: in the grace hour with 3..13, twelve protected in all.
    assert report.dropped == (1,)
    assert report.kept == tuple(range(13, 1, -1)) and report.over_cap == 2
    assert report.details()["over_cap"] == 2
    # The alert (U23) carries counts only.
    warnings = [r.getMessage() for r in caplog.records if r.name == "ggwork_pick.mirror.retention" and r.levelno == logging.WARNING]
    assert warnings == ["[pick-mirror] retention keeps 12 versions, 2 over the cap of 10 (U23)"]


@pytest.mark.asyncio
async def test_a_reader_holding_a_schema_skips_that_version_after_five_seconds(sync_conn, observer, dsn):
    # P2-6 test 6 (U24): SET LOCAL lock_timeout = 5s; that one waits and is skipped, the rest go; the setting does not stay.
    import asyncpg

    await _chain(observer, 5)
    before = await sync_conn.fetchval("show lock_timeout")
    reader = await asyncpg.connect(dsn)
    try:
        transaction = reader.transaction()
        await transaction.start()
        await reader.fetchval(f"select count(*) from {_schema(1)}.meta")
        started = time.monotonic()
        report = await _prune(sync_conn)
        elapsed = time.monotonic() - started
        assert (report.dropped, report.lock_busy) == ((2,), (1,))
        assert 4.5 <= elapsed < 15
        assert await sync_conn.fetchval("show lock_timeout") == before == "0"
        assert not sync_conn.is_in_transaction()
        assert (await _statuses(observer))[1] == "published" and _schema(1) in await _schemas(observer)
        await transaction.rollback()
    finally:
        await reader.close()
    # Next time round it goes.
    assert (await _prune(sync_conn)).dropped == (1,)
    assert await _schemas(observer) == [_schema(3), _schema(4), _schema(5)]


@pytest.mark.asyncio
async def test_without_the_mirror_lock_nothing_is_dropped(dedicated, observer, dsn):
    # P2-6 test 7: another connection holds it; then nobody holds it.
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import release_mirror_lock, try_mirror_lock

    await _chain(observer, 6)
    holder = await open_dedicated(dsn)
    try:
        assert await try_mirror_lock(holder, now=T0, holder="sync")
        report = await _prune(dedicated)
        assert (report.skipped, report.kept, report.dropped) == ("lock_not_held", (), ())
        assert await release_mirror_lock(holder)
    finally:
        await holder.close()
    assert (await _prune(dedicated)).skipped == "lock_not_held"
    assert set((await _statuses(observer)).values()) == {"published"}
    assert len(await _schemas(observer)) == 6


@pytest.mark.parametrize("holder", ["backfill", "cleanup"])
@pytest.mark.asyncio
async def test_retention_runs_only_under_the_syncs_hold(dedicated, observer, holder):
    # The lock is on this very connection, but held by the backfill or the cleanup command, not by the sync.
    from ggwork_pick.mirror.lock import try_mirror_lock

    await _chain(observer, 5)
    assert await try_mirror_lock(dedicated, now=T0, holder=holder)
    assert (await _prune(dedicated)).skipped == "lock_not_held"
    assert len(await _schemas(observer)) == 5


@pytest.mark.asyncio
async def test_ggwp_batches_are_left_alone(sync_conn, observer):
    # P2-6 test 8: batch retention is repository.prune_shared's (U25); this touches none of the ggwp tables.
    await _chain(observer, 6, batch="batch-a")
    for n, batch in enumerate(["batch-a", "batch-b", "batch-c", "batch-d"]):
        published = f"2026-09-2{n}T03:40:00.000000+00:00"
        await observer.execute(
            "insert into ggwp_import_batches (id, owner_id, kind, content_hash, raw_blob_path, status, source_as_of, created_at, published_at, validation_json)"
            " values ($1, 'system:shared', 'catalog', $2, $3, 'published', $4, $4, $4, '{}'::json)",
            batch,
            f"hash-{batch}",
            f"/data/pick/{batch}.json",
            published,
        )
        await observer.execute("insert into ggwp_drama_versions (batch_id, identity, payload_json) values ($1, 'synthetic:1', '{}'::json)", batch)
    await _cite(observer, 2, at=T0 - timedelta(days=1), batch="batch-a")
    await _cite(observer, None, at=T0 - timedelta(days=20), batch="batch-b")
    tables = ("ggwp_import_batches", "ggwp_drama_versions", "ggwp_knowledge_versions", "ggwp_candidate_sets")
    before = {table: [tuple(row) for row in await observer.fetch(f"select * from {table} order by 1, 2")] for table in tables}
    report = await _prune(sync_conn)
    assert report.dropped == (1, 3)
    assert {table: [tuple(row) for row in await observer.fetch(f"select * from {table} order by 1, 2")] for table in tables} == before


@pytest.mark.asyncio
async def test_only_published_versions_count(sync_conn, observer):
    # P2-6 test 9: a building or failed version is not current and takes no place among the latest three.
    await _chain(observer, 4)
    await _version(observer, 5, status="failed", schema=False)
    await _version(observer, 6, status="building")
    report = await _prune(sync_conn)
    assert (report.kept, report.dropped) == ((4, 3, 2), (1,))
    assert await _statuses(observer) == {1: "dropped", 2: "published", 3: "published", 4: "published", 5: "failed", 6: "building"}
    assert _schema(6) in await _schemas(observer)


@pytest.mark.asyncio
async def test_retention_drops_only_schemas_registered_in_versions(sync_conn, observer):
    # An unregistered pickm_v* schema is the leftover cleanup's, never retention's; other names are nobody's.
    await _chain(observer, 4)
    for name in (_schema(99), "pickm_v1234567", "pickm_other"):
        await _create_schema(observer, name)
    assert (await _prune(sync_conn)).dropped == (1,)
    assert await _schemas(observer) == sorted([_schema(2), _schema(3), _schema(4), _schema(99), "pickm_v1234567", "pickm_other"])


@pytest.mark.asyncio
async def test_a_registered_name_outside_the_writers_shape_stops_before_any_drop(sync_conn, observer):
    # The CHECK on versions.schema_name normally rules it out; without it the name is refused before any SQL uses it.
    await observer.execute("alter table pick_mirror.versions drop constraint pick_mirror_versions_schema_name")
    await _chain(observer, 5)
    odd = "pickm_v00000６"  # a fullwidth digit: Python's \d would take it, [0-9] does not
    await observer.execute("update pick_mirror.versions set schema_name = $1 where id = 2", odd)
    await _create_schema(observer, odd)
    with pytest.raises(ValueError, match="pickm_v"):
        await _prune(sync_conn)
    assert set((await _statuses(observer)).values()) == {"published"}
    assert len(await _schemas(observer)) == 6


@pytest.mark.asyncio
async def test_every_setting_is_set_local_inside_its_own_transaction(sync_conn, observer):
    # The bare asyncpg connection is not covered by conftest's SQLAlchemy guard (0.3): record what goes out.
    await _chain(observer, 6)
    recorder = _Recorder(sync_conn)
    report = await _prune(recorder)
    assert report.dropped == (1, 2, 3)
    assert _drop_heads(recorder.sent) == [_bounded_by(5000)] * 3
    assert len([query for query in recorder.sent if re.match(r"(?i)\s*set\b", query)]) == 6
    assert not any(SESSION_SETTING.search(query) for query in recorder.sent)
    # Idle between steps and after: nothing uncommitted is left for the ORM's publish to wait on.
    assert not sync_conn.is_in_transaction()
    assert await observer.fetchval("select state from pg_stat_activity where pid = $1", await sync_conn.fetchval("select pg_backend_pid()")) == "idle"


@pytest.mark.asyncio
async def test_a_version_that_stops_being_published_midway_keeps_its_schema(sync_conn, observer):
    # Under the mirror lock nothing else changes versions; if a row moves anyway, its DROP is rolled back, not left standing.
    await _chain(observer, 5)
    changing = _ChangesRowBeforeDrop(sync_conn, observer, schema=_schema(2))
    with pytest.raises(RuntimeError, match="published"):
        await _prune(changing)
    assert await _statuses(observer) == {1: "dropped", 2: "failed", 3: "published", 4: "published", 5: "published"}
    assert await _schemas(observer) == [_schema(2), _schema(3), _schema(4), _schema(5)]
    assert await sync_conn.fetchval("show lock_timeout") == "0" and not sync_conn.is_in_transaction()


# 55P03 the lock_timeout ran out; 40P01 a reader's one statement over two of its tables met the DROP from the other
# end; 57014 the statement_timeout ended a DROP that waited on one table after another. All a reader in the way (U24).
@pytest.mark.parametrize("error", ["LockNotAvailableError", "DeadlockDetectedError", "QueryCanceledError"])
@pytest.mark.asyncio
async def test_a_drop_a_reader_ended_is_skipped_however_the_wait_ended(sync_conn, observer, error):
    import asyncpg

    await _chain(observer, 6)
    failing = _FailsDrop(sync_conn, schema=_schema(2), error=getattr(asyncpg.exceptions, error)("synthetic"))
    report = await _prune(failing)
    assert (report.dropped, report.lock_busy) == ((1, 3), (2,))
    assert await _statuses(observer) == {1: "dropped", 2: "published", 3: "dropped", 4: "published", 5: "published", 6: "published"}
    assert _schema(2) in await _schemas(observer)
    assert await sync_conn.fetchval("show statement_timeout") == "0" and not sync_conn.is_in_transaction()


@pytest.mark.parametrize("step", ["prune", "clean"])
@pytest.mark.asyncio
async def test_any_other_database_error_is_raised_not_taken_for_a_reader(sync_conn, observer, step):
    import asyncpg

    await (_chain(observer, 6) if step == "prune" else _leftovers(observer))
    target = _schema(2 if step == "prune" else 5)
    row = "select status, dropped_at from pick_mirror.versions where schema_name = $1"
    before = await observer.fetchrow(row, target)
    failing = _FailsDrop(sync_conn, schema=target, error=asyncpg.exceptions.InsufficientPrivilegeError("synthetic"))
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await (_prune if step == "prune" else _clean)(failing)
    assert await observer.fetchrow(row, target) == before and target in await _schemas(observer)
    assert await sync_conn.fetchval("show lock_timeout") == "0" and not sync_conn.is_in_transaction()


@pytest.mark.asyncio
async def test_the_whole_drop_waits_at_most_the_timeout_not_once_per_table(sync_conn, observer, dsn):
    # lock_timeout counts each lock on its own, and DROP ... CASCADE takes one per table. Two readers, each on one table,
    # letting go one after the other inside the timeout, would hold the DROP, and every reader queued behind it, for
    # both waits together. The statement_timeout bounds the DROP as a whole (U24).
    import asyncpg

    await _chain(observer, 4)
    await observer.execute(f'create table "{_schema(1)}".other (x int)')
    readers = [await asyncpg.connect(dsn) for _ in range(2)]
    try:
        holding = {}
        for reader, table in zip(readers, ("meta", "other"), strict=True):
            transaction = reader.transaction()
            await transaction.start()
            await reader.fetchval(f'select count(*) from "{_schema(1)}".{table}')
            holding[await reader.fetchval("select pg_backend_pid()")] = transaction
        dropper = await sync_conn.fetchval("select pg_backend_pid()")
        pruning = asyncio.create_task(_prune(sync_conn, lock_timeout=timedelta(seconds=1)))
        for _ in range(2):
            blocker = await _blocker(observer, dropper, among=holding)
            if blocker is None:
                break
            await asyncio.sleep(0.6)
            await holding.pop(blocker).rollback()
        report = await pruning
    finally:
        for reader in readers:
            await reader.close()
    assert (report.dropped, report.lock_busy) == ((), (1,))
    assert (await _statuses(observer))[1] == "published" and _schema(1) in await _schemas(observer)
    assert await sync_conn.fetchval("show statement_timeout") == "0"


async def _blocker(observer, pid: int, *, among: dict, within: float = 3.0) -> int | None:
    """Which of `among` the backend `pid` is waiting on, once it waits; None if it never does within `within` seconds."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        blocking = [blocker for blocker in await observer.fetchval("select pg_blocking_pids($1)", pid) if blocker in among]
        if blocking:
            return blocking[0]
        await asyncio.sleep(0.02)
    return None


@pytest.mark.parametrize("options, timeout", [({}, 5000), ({"lock_timeout": timedelta(milliseconds=200)}, 200)])
@pytest.mark.asyncio
async def test_the_cleanup_bounds_each_drop_by_the_timeout_it_is_given(sync_conn, observer, options, timeout):
    await _leftovers(observer)
    recorder = _Recorder(sync_conn)
    assert (await _clean(recorder, **options)).dropped_schemas == LEFTOVER_SCHEMAS + (_schema(99),)
    assert _drop_heads(recorder.sent) == [_bounded_by(timeout)] * 6


@pytest.mark.asyncio
async def test_the_reference_window_is_the_one_passed_in(sync_conn, observer):
    await _chain(observer, 5)
    await _cite(observer, 1, at=T0 - timedelta(hours=12))
    await _cite(observer, 2, at=T0 - timedelta(days=2))
    report = await _prune(sync_conn, referenced_within=timedelta(days=1))
    assert (report.kept, report.dropped) == ((5, 4, 3, 1), (2,))


@pytest.mark.parametrize(
    "options, cited, kept, dropped",
    [
        ({"keep_latest": 1}, (), (7,), (1, 2, 3, 4, 5, 6)),
        ({"cap": 4}, (1, 2, 3, 4), (7, 6, 5, 4), (1, 2, 3)),
    ],
)
@pytest.mark.asyncio
async def test_keep_latest_and_the_cap_are_the_ones_passed_in(sync_conn, observer, options, cited, kept, dropped):
    await _chain(observer, 7)
    for n in cited:
        await _cite(observer, n, at=T0 - timedelta(days=1))
    report = await _prune(sync_conn, **options)
    assert (report.kept, report.dropped, report.over_cap) == (kept, dropped, 0)


def _published(n: int, hours_ago: int | None) -> object:
    from ggwork_pick.mirror.retention import PublishedVersion

    published = None if hours_ago is None else T0 - timedelta(hours=hours_ago)
    return PublishedVersion(id=n, schema_name=_schema(n), published_at=published, superseded_at=None)


@pytest.mark.parametrize("options", [{"keep_latest": 0}, {"cap": 2}, {"grace": timedelta(seconds=-1)}, {"grace": 3600}])
def test_plan_retention_refuses_bad_limits_by_itself(options):
    from ggwork_pick.mirror.retention import plan_retention

    with pytest.raises(ValueError):
        plan_retention((_published(1, 3), _published(2, 2), _published(3, 1)), frozenset(), now=T0, **options)


def test_plan_retention_orders_what_it_is_given_by_itself():
    # published_at DESC, id DESC as the query has it, a missing published_at first (PostgreSQL's DESC puts NULL first).
    from ggwork_pick.mirror.retention import plan_retention

    shuffled = (_published(2, 8), _published(5, 4), _published(1, 10), _published(4, 2), _published(3, 8), _published(6, None))
    plan = plan_retention(shuffled, frozenset(), now=T0)
    assert (plan.keep, tuple(v.id for v in plan.drop), plan.over_cap) == ((6, 4, 5), (1, 2, 3), 0)


def test_the_modules_never_set_a_session_level_setting():
    # Retention drops through versions.drop_in_transaction, which sends both settings as SET LOCAL.
    retention, versions = ((MIRROR_SOURCE / name).read_text() for name in ("retention.py", "versions.py"))
    assert "SET LOCAL lock_timeout" in versions and "SET LOCAL statement_timeout" in versions
    assert not re.search(r"[\"']SET LOCAL", retention), "retention sends no setting of its own"
    assert [line for source in (retention, versions) for line in source.splitlines() if LITERAL_SETTING.search(line)] == []


@pytest.mark.asyncio
async def test_a_connection_inside_a_transaction_is_refused_before_any_change(sync_conn, observer):
    await _chain(observer, 5)
    await _version(observer, 6, status="building")
    async with sync_conn.transaction():
        with pytest.raises(RuntimeError, match="事务"):
            await _prune(sync_conn)
        with pytest.raises(RuntimeError, match="事务"):
            await _clean(sync_conn)
    assert await _statuses(observer) == {1: "published", 2: "published", 3: "published", 4: "published", 5: "published", 6: "building"}
    assert len(await _schemas(observer)) == 6


BAD_OPTIONS = [
    ("lock_timeout", timedelta(0)),
    ("lock_timeout", timedelta(microseconds=10)),
    ("lock_timeout", 5),
    ("keep_latest", 0),
    ("cap", 2),
    ("grace", timedelta(seconds=-1)),
    ("referenced_within", timedelta(days=-1)),
    ("referenced_within", 7),
]


@pytest.mark.parametrize("option, value", BAD_OPTIONS)
@pytest.mark.asyncio
async def test_bad_retention_options_are_refused_before_any_sql(option, value):
    from ggwork_pick.mirror.retention import prune_versions

    conn = _Recorder(None)
    with pytest.raises(ValueError):
        await prune_versions(conn, clock=lambda: T0, **{option: value})
    assert conn.sent == []


@pytest.mark.parametrize("value", [timedelta(0), timedelta(microseconds=999), 5])
@pytest.mark.asyncio
async def test_a_bad_cleanup_lock_timeout_is_refused_before_any_sql(value):
    from ggwork_pick.mirror.retention import clean_leftover_versions

    conn = _Recorder(None)
    with pytest.raises(ValueError, match="lock_timeout"):
        await clean_leftover_versions(conn, clock=lambda: T0, lock_timeout=value)
    assert conn.sent == []


@pytest.mark.asyncio
async def test_now_must_be_timezone_aware():
    from ggwork_pick.mirror.retention import clean_leftover_versions, prune_versions

    conn = _Recorder(None)
    with pytest.raises(ValueError, match="时区"):
        await prune_versions(conn, clock=lambda: T0.replace(tzinfo=None))
    with pytest.raises(ValueError, match="时区"):
        await clean_leftover_versions(conn, clock=lambda: T0.replace(tzinfo=None))
    assert conn.sent == []


@pytest.mark.asyncio
async def test_the_details_carry_version_numbers_and_codes_only(sync_conn, observer):
    await _chain(observer, 5)
    details = (await _prune(sync_conn)).details()
    assert details == {"skipped": None, "kept": [5, 4, 3], "dropped": [1, 2], "lock_busy": [], "over_cap": 0}
    assert json.loads(json.dumps(details)) == details


# ---- leftovers of a run that never finished (U41, the schema half) ----


# The registered leftovers whose schemas go: building 3 and 4 once failed, failed 5, and 8 and 9 cleared on paper only.
LEFTOVER_SCHEMAS = tuple(_schema(n) for n in (3, 4, 5, 8, 9))


async def _leftovers(observer) -> None:
    await _version(observer, 1, published=T0 - timedelta(hours=4), superseded=T0 - timedelta(hours=2))
    await _version(observer, 2, published=T0 - timedelta(hours=2))
    await _version(observer, 3, status="building")
    await _version(observer, 4, status="building", schema=False)  # died between the row and CREATE SCHEMA
    await _version(observer, 5, status="failed")  # mark_failed could not drop it: the connection was gone
    await observer.execute("update pick_mirror.versions set error = 'MirrorGateError' where id = 5")
    await _version(observer, 6, status="failed", schema=False)
    await observer.execute("update pick_mirror.versions set dropped_at = $1 where id = 6", T0 - timedelta(days=1))
    await _version(observer, 7, status="dropped", schema=False)
    await observer.execute("update pick_mirror.versions set dropped_at = $1 where id = 7", T0 - timedelta(days=2))
    # Cleared on paper but the schema is still there: mark_failed's DROP was skipped, or a restore put it back.
    await _version(observer, 8, status="failed")
    await _version(observer, 9, status="dropped")
    await observer.execute("update pick_mirror.versions set dropped_at = $1::timestamptz - (id - 5) * interval '1 day' where id in (8, 9)", T0)
    for name in (_schema(99), "pickm_v1234567", "pickm_vx", "pickm_v00000６"):
        await _create_schema(observer, name)


@pytest.mark.asyncio
async def test_leftover_building_versions_fail_and_orphan_schemas_go(sync_conn, observer):
    from ggwork_pick.mirror.retention import LEFTOVER_ERROR

    await _leftovers(observer)
    report = await _clean(sync_conn)
    assert (report.skipped, report.failed_building, report.lock_busy) == (None, (3, 4), ())
    assert report.dropped_schemas == LEFTOVER_SCHEMAS + (_schema(99),)
    rows = {row["id"]: (row["status"], row["dropped_at"], row["error"]) for row in await observer.fetch(VERSIONS)}
    assert rows == {
        1: ("published", None, None),
        2: ("published", None, None),
        3: ("failed", T0, LEFTOVER_ERROR),
        4: ("failed", T0, LEFTOVER_ERROR),
        5: ("failed", T0, "MirrorGateError"),
        6: ("failed", T0 - timedelta(days=1), None),
        7: ("dropped", T0 - timedelta(days=2), None),
        # Their schemas went; the dropped_at already written stays as it was.
        8: ("failed", T0 - timedelta(days=3), None),
        9: ("dropped", T0 - timedelta(days=4), None),
    }
    assert await _schemas(observer) == sorted([_schema(1), _schema(2), "pickm_v1234567", "pickm_vx", "pickm_v00000６"])
    assert report.details() == {
        "skipped": None,
        "failed_building": [3, 4],
        "dropped_schemas": [*LEFTOVER_SCHEMAS, _schema(99)],
        "lock_busy": [],
    }
    # A second pass finds nothing.
    again = await _clean(sync_conn)
    assert (again.failed_building, again.dropped_schemas) == ((), ())


@pytest.mark.asyncio
async def test_leftovers_are_cleaned_only_under_the_lock(dedicated, observer, dsn):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import try_mirror_lock

    await _leftovers(observer)
    before = (await observer.fetch(VERSIONS), await _schemas(observer))
    holder = await open_dedicated(dsn)
    try:
        assert await try_mirror_lock(holder, now=T0, holder="backfill")
        assert (await _clean(dedicated)).skipped == "lock_not_held"
    finally:
        await holder.close()
    assert (await _clean(dedicated)).skipped == "lock_not_held"
    assert (await observer.fetch(VERSIONS), await _schemas(observer)) == before


@pytest.mark.parametrize("holder, cleans", [("sync", True), ("cleanup", True), ("backfill", False)])
@pytest.mark.asyncio
async def test_the_sync_and_the_cleanup_command_may_clean_leftovers(dsn, observer, holder, cleans):
    from ggwork_pick.mirror.lock import mirror_lock

    await _leftovers(observer)
    async with mirror_lock(dsn, holder=holder, clock=lambda: T0) as conn:
        report = await _clean(conn)
    assert (report.skipped is None) == cleans
    assert (_schema(99) in await _schemas(observer)) != cleans


@pytest.mark.asyncio
async def test_a_reader_on_an_orphan_skips_it_and_the_setting_does_not_stay(sync_conn, observer, dsn):
    import asyncpg

    await _leftovers(observer)
    reader = await asyncpg.connect(dsn)
    try:
        async with reader.transaction():
            await reader.fetchval(f"select count(*) from {_schema(99)}.meta")
            started = time.monotonic()
            report = await _clean(sync_conn, lock_timeout=timedelta(milliseconds=200))
            # The timeout passed in, not the default five seconds.
            assert time.monotonic() - started < 2
        assert report.lock_busy == (_schema(99),) and report.dropped_schemas == LEFTOVER_SCHEMAS
        assert await sync_conn.fetchval("show lock_timeout") == "0" and not sync_conn.is_in_transaction()
    finally:
        await reader.close()
    assert (await _clean(sync_conn)).dropped_schemas == (_schema(99),)


class _Recorder:
    """The dedicated connection with every statement it is sent recorded; each DROP's BEGIN and COMMIT are statements too."""

    def __init__(self, conn):
        self.conn = conn
        self.sent = []

    def __getattr__(self, name):
        return getattr(self.conn, name)

    async def execute(self, query, *args, **kwargs):
        self.sent.append(query)
        return await self.conn.execute(query, *args, **kwargs)

    async def fetch(self, query, *args, **kwargs):
        self.sent.append(query)
        return await self.conn.fetch(query, *args, **kwargs)

    async def fetchrow(self, query, *args, **kwargs):
        self.sent.append(query)
        return await self.conn.fetchrow(query, *args, **kwargs)

    async def fetchval(self, query, *args, **kwargs):
        self.sent.append(query)
        return await self.conn.fetchval(query, *args, **kwargs)


class _FailsDrop(_Recorder):
    """The DROP of one schema fails with `error` inside its transaction, as if the server had raised it."""

    def __init__(self, conn, *, schema: str, error: Exception):
        super().__init__(conn)
        self.schema = schema
        self.error = error

    async def execute(self, query, *args, **kwargs):
        if query.startswith("DROP SCHEMA") and self.schema in query:
            self.sent.append(query)
            raise self.error
        return await super().execute(query, *args, **kwargs)


class _ChangesRowBeforeDrop(_Recorder):
    """Another writer fails one published version just before its DROP goes out."""

    def __init__(self, conn, observer, *, schema: str):
        super().__init__(conn)
        self.observer = observer
        self.schema = schema

    async def execute(self, query, *args, **kwargs):
        if query.startswith("DROP SCHEMA") and self.schema in query:
            await self.observer.execute("update pick_mirror.versions set status = 'failed' where schema_name = $1", self.schema)
        return await super().execute(query, *args, **kwargs)
