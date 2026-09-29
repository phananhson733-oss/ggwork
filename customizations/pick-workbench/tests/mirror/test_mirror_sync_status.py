"""GET /api/pick/sync's mirror key (P2-8b; plan 1623; U14; P3 critique B20). Synthetic data only.

On PostgreSQL, whether the mirror is switched on or not: the current version, the curve's reach, whether the shared
batches moved past the version (behind, judged on the shared batches, never on the signed-in user's own import), the
failure count and its alert, the version's warning codes, a stuck mirror lock (three conditions, judged at request time)
and the shared catalog's source_as_of for the P3 banner. On SQLite, null. The runs go through run_world's RealShort
double end to end, so every state here is one a real run leaves.
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import gate_world as gw
import httpx
import pytest
import pytest_asyncio
from engines import host_engine
from fake_realshort import EXPORT_TOKEN, FEED_TOKEN, v2_error
from mirror_pairs import catalog_payload
from obs_status_rows import batch_row, insert_rows, set_row
from run_world import BASE, V1_RULES, fetch, make_sync, open_harness, world_fake
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_frontend_contract import _shape

from ggwork_pick.imports import Importer
from ggwork_pick.repository import PickRepository, stamp

RUN_STATUSES = {"running", "success", "failed"}  # frontend/src/core/pick/api.ts:58
# /sync as the gateway answers it, parsed by frontend/tests/unit/core/pick/sync-schema.test.ts (P2 final review seams-3).
# Regenerate with PICK_WRITE_CONTRACT=1 and PICK_TEST_PG_URL set: the mirror answers need PostgreSQL.
SYNC_FIXTURE = Path(__file__).resolve().parents[4] / "frontend/tests/unit/core/pick/fixtures/backend-sync.json"
MIRROR_KEYS = {
    "enabled",
    "current",
    "series_through",
    "trimmed_before",
    "behind",
    "consecutive_failures",
    "last_failure_at",
    "last_failure",
    "alert",
    "warnings",
    "lock_stuck",
    "shared_source_as_of",
}


@pytest_asyncio.fixture
async def harness(pg_db_url, tmp_path):
    opened = await open_harness(pg_db_url, tmp_path)
    yield opened
    await opened.engine.dispose()


@asynccontextmanager
async def _client(service):
    from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal
    from fastapi import FastAPI

    from ggwork_pick.routes import build_router

    def principal(request):
        """Test-only identity resolver, never installed by the business extension."""
        return ExtensionPrincipal(request.headers["test-owner"]) if "test-owner" in request.headers else None

    app = FastAPI()
    setattr(app.state, EXTENSION_PRINCIPAL_RESOLVER_KEY, principal)
    app.include_router(build_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _sync_status(service, owner: str = "alice") -> dict:
    async with _client(service) as client:
        response = await client.get("/api/pick/sync", headers={"test-owner": owner})
    assert response.status_code == 200, response.text
    return response.json()


async def _run(harness, world=None, *, intercept=None):
    result = await make_sync(harness, world_fake(harness, world, intercept=intercept)).run("cron")
    harness.clock.advance(600)
    return result


async def _version_row(engine, version_id: int) -> dict:
    return (await fetch(engine, "SELECT as_of, latest_snapshot, published_at FROM pick_mirror.versions WHERE id = :id", id=version_id))[0]


async def _execute(engine, statement: str, **params) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(statement), params)


# ---------------------------------------------------------------- 1. the fields, and behind after a deduplicated degrade


@pytest.mark.asyncio
async def test_mirror_fields_follow_the_runs(harness):
    paired = await _run(harness)
    await _execute(harness.engine, "UPDATE pick_mirror.series_state SET through = DATE '2026-09-22', trimmed_before = DATE '2026-06-21' WHERE id = 1")
    mirror = (await _sync_status(harness.service))["mirror"]
    assert set(mirror) == MIRROR_KEYS
    version = await _version_row(harness.engine, paired["details_json"]["version"])
    assert mirror["current"] == {
        "id": paired["details_json"]["version"],
        "as_of": paired["details_json"]["as_of"],
        "latest_snapshot": version["latest_snapshot"].strftime("%Y-%m-%d"),
        "published_at": stamp(version["published_at"]),
    }
    assert (mirror["series_through"], mirror["trimmed_before"]) == ("2026-09-22", "2026-06-21")
    assert mirror["warnings"] == ["catalog_import_incomplete"]
    failures = (mirror["consecutive_failures"], mirror["last_failure"], mirror["last_failure_at"], mirror["alert"])
    assert mirror["behind"] is False and failures == (0, None, None, False)
    assert mirror["lock_stuck"] is None and mirror["enabled"] is False
    assert mirror["shared_source_as_of"] == paired["source_as_of"]

    # The same v1 content, the mirror half failing: the degraded publish reuses the paired batches, so not behind.
    too_large = v2_error(500, "row_too_large", resource="rs_ids", key=["d-1"])
    deduped = await _run(harness, intercept=lambda call: too_large if call.resource == "rs_ids" else None)
    assert deduped["catalog_batch_id"] == paired["catalog_batch_id"]
    mirror = (await _sync_status(harness.service))["mirror"]
    assert (mirror["behind"], mirror["consecutive_failures"], mirror["last_failure"], mirror["alert"]) == (False, 1, "degraded:RowTooLargeError", False)
    assert mirror["last_failure_at"] is not None and mirror["current"]["id"] == paired["details_json"]["version"]
    assert mirror["shared_source_as_of"] == deduped["source_as_of"] != paired["source_as_of"]

    # New v1 content and G8 failing: the shared batches move past the version.
    moved = await _run(harness, gw.with_v1(gw.baseline(), [*gw.baseline().v1_rows, gw.v1_row("c-3", ("kd",))]))
    assert moved["details_json"]["outcome"] == "degraded"
    mirror = (await _sync_status(harness.service))["mirror"]
    assert (mirror["behind"], mirror["consecutive_failures"], mirror["alert"]) == (True, 2, False)
    await PickRepository.shared(harness.service.session_factory).record_mirror_failure(reason="busy", t=harness.clock())
    mirror = (await _sync_status(harness.service))["mirror"]
    assert (mirror["consecutive_failures"], mirror["last_failure"], mirror["alert"]) == (3, "busy", True)


@pytest.mark.asyncio
async def test_before_any_version_there_is_nothing_to_be_behind(harness):
    mirror = (await _sync_status(harness.service))["mirror"]
    assert set(mirror) == MIRROR_KEYS
    assert mirror["current"] is None and mirror["behind"] is False and mirror["warnings"] == []
    assert (mirror["series_through"], mirror["trimmed_before"], mirror["shared_source_as_of"], mirror["lock_stuck"]) == (None, None, None, None)
    assert (mirror["consecutive_failures"], mirror["alert"]) == (0, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flag", "export_token", "feed_token", "enabled"),
    [
        ("1", EXPORT_TOKEN, FEED_TOKEN, True),
        ("0", EXPORT_TOKEN, FEED_TOKEN, False),
        ("", EXPORT_TOKEN, FEED_TOKEN, False),
        (" 1", EXPORT_TOKEN, FEED_TOKEN, False),
        ("1", "", FEED_TOKEN, False),
        ("1", EXPORT_TOKEN, "", False),  # not configured: no sync at all, the mirror's included
    ],
)
async def test_enabled_says_whether_the_mirror_runs(harness, flag, export_token, feed_token, enabled):
    from ggwork_pick.service import SyncSettings

    harness.service.sync_settings = SyncSettings(feed_url=BASE, feed_token=feed_token, export_token=export_token, mirror_flag=flag)
    assert (await _sync_status(harness.service))["mirror"]["enabled"] is enabled


@pytest.mark.asyncio
async def test_current_is_the_latest_published_version(harness):
    first = await _run(harness)
    second = await _run(harness, gw.with_v1_row(gw.baseline(), 0, title="换了标题的剧"))
    assert (first["details_json"]["outcome"], second["details_json"]["outcome"]) == ("paired", "paired")
    mirror = (await _sync_status(harness.service))["mirror"]
    assert mirror["current"]["id"] == second["details_json"]["version"] != first["details_json"]["version"]
    assert mirror["behind"] is False


@pytest.mark.asyncio
async def test_behind_when_only_the_rules_moved(harness):
    """Same v1 rows, new rules Markdown, the mirror half failing: the catalog is reused, the knowledge batch is new."""
    paired = await _run(harness)
    too_large = v2_error(500, "row_too_large", resource="rs_ids", key=["d-1"])
    fake = world_fake(harness, intercept=lambda call: too_large if call.resource == "rs_ids" else None, v1_rules=f"{V1_RULES}\n\n补一条口径说明")
    degraded = await make_sync(harness, fake).run("cron")
    assert degraded["details_json"]["outcome"] == "degraded"
    assert degraded["catalog_batch_id"] == paired["catalog_batch_id"] and degraded["knowledge_batch_id"] != paired["knowledge_batch_id"]
    mirror = (await _sync_status(harness.service))["mirror"]
    assert mirror["behind"] is True and mirror["current"]["id"] == paired["details_json"]["version"]


@pytest.mark.asyncio
async def test_a_failed_mirror_read_leaves_the_rest_of_sync(harness, monkeypatch, caplog):
    """The mirror key is extra: if it cannot be read, /sync still answers with the v1 status and names the error class."""
    from ggwork_pick import routes

    async def broken(repo, **options):
        raise RuntimeError("relation pick_mirror.control: row 'secret-looking value'")

    caplog.set_level(logging.WARNING, logger="ggwork_pick.routes")
    paired = await _run(harness)
    monkeypatch.setattr(routes, "mirror_status", broken)
    status = await _sync_status(harness.service)
    assert status["mirror"] == {"error": "RuntimeError"}
    assert status["current"]["id"] == paired["catalog_batch_id"] and status["runs"][0]["status"] == "success"
    assert "RuntimeError" in caplog.text and "secret-looking" not in caplog.text


# ---------------------------------------------------------------- 2. SQLite


async def _sqlite_sync_status(tmp_path) -> dict:
    """/sync from a SQLite gateway after one v1 sync, the mirror switched on (it cannot run there)."""
    from test_realshort_sync import TOKEN, feed_row, feed_transport

    from ggwork_pick.service import PickService, SyncSettings

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    service = PickService(tmp_path / "files", SyncSettings(feed_url="https://realshort.test", feed_token=TOKEN, export_token=EXPORT_TOKEN, mirror_flag="1"))
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    try:
        service.sync_transport = feed_transport([feed_row(1), feed_row(2)])
        assert (await service.realshort_sync().run("cron"))["status"] == "success"
        return await _sync_status(service)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_has_no_mirror(tmp_path):
    status = await _sqlite_sync_status(tmp_path)
    assert status["mirror"] is None
    assert status["current"]["rows"] == 2 and status["runs"][0]["status"] == "success"


# ---------------------------------------------------------------- 3. a personal import does not hide the shared batches


@pytest.mark.asyncio
async def test_behind_is_judged_on_the_shared_batches(harness):
    paired = await _run(harness)
    mine = await Importer(PickRepository(harness.service.session_factory, "alice"), harness.service.data_dir).catalog(catalog_payload("mine"), "json")
    alice = await _sync_status(harness.service, "alice")
    assert alice["current"]["id"] == mine["id"] and alice["current"]["shared"] is False
    assert alice["mirror"]["behind"] is False
    assert alice["mirror"]["shared_source_as_of"] == paired["source_as_of"] != alice["current"]["source_as_of"]
    assert (await _sync_status(harness.service, "bob"))["mirror"] == alice["mirror"]


# ---------------------------------------------------------------- 4. run statuses stay the three the frontend knows


@pytest.mark.asyncio
async def test_run_statuses_stay_within_the_three(harness):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import try_mirror_lock

    await _run(harness)
    await _run(harness, gw.with_row(gw.baseline(), "catalog_signals", 0, note=gw.PAN))
    other = await open_dedicated(harness.dsn)
    try:
        assert await try_mirror_lock(other, now=harness.clock(), holder="backfill")
        await _run(harness)
    finally:
        await other.close()
    read_failed = v2_error(503, "read_failed")
    await _run(harness, intercept=lambda call: read_failed if call.resource == "manifest" else None)
    await PickRepository.shared(harness.service.session_factory).start_sync_run("realshort", "manual")
    runs = (await _sync_status(harness.service))["runs"]
    outcomes = [run["details_json"]["outcome"] for run in runs if run["details_json"]]
    assert {"paired", "degraded", "lock_busy", "fallback_v1"} <= set(outcomes)
    assert {run["status"] for run in runs} == RUN_STATUSES


# ---------------------------------------------------------------- 5. lock_stuck (U14)


async def _held_since(harness, other, minutes: int) -> dict:
    await other.execute("UPDATE pick_mirror.control SET lock_holder_since = now() - make_interval(mins => $1) WHERE id = 1", minutes)
    since = await other.fetchval("SELECT lock_holder_since FROM pick_mirror.control WHERE id = 1")
    return {"pid": await other.fetchval("SELECT pg_backend_pid()"), "holder": "backfill", "since": stamp(since)}


@pytest.mark.asyncio
async def test_lock_stuck_needs_all_three_conditions(harness):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import try_mirror_lock

    other = await open_dedicated(harness.dsn)
    try:
        assert await try_mirror_lock(other, now=datetime.now(UTC), holder="backfill")
        assert (await _sync_status(harness.service))["mirror"]["lock_stuck"] is None
        expected = await _held_since(harness, other, 81)
        assert (await _sync_status(harness.service))["mirror"]["lock_stuck"] == expected
        await _held_since(harness, other, 5)
        assert (await _sync_status(harness.service))["mirror"]["lock_stuck"] is None
        # F7: a legitimate run can hold the lock about 71.5 minutes; 75 is not stuck yet.
        await _held_since(harness, other, 75)
        assert (await _sync_status(harness.service))["mirror"]["lock_stuck"] is None
        await _held_since(harness, other, 81)
        async with harness.service.sync_lock:
            assert (await _sync_status(harness.service))["mirror"]["lock_stuck"] is None
    finally:
        await other.close()
    # The holder went without clearing its record: an old lock_holder_since with no lock held is not a stuck lock.
    left = (await fetch(harness.engine, "SELECT lock_holder_since FROM pick_mirror.control WHERE id = 1"))[0]["lock_holder_since"]
    assert left < datetime.now(UTC) - timedelta(minutes=80)
    assert (await _sync_status(harness.service))["mirror"]["lock_stuck"] is None


# ---------------------------------------------------------------- 6. the frontend's fixture (seams-3)


def _contract_shape(answers: dict):
    """What the fixture pins: every key and value type of each answer, runs' details_json aside (the frontend never
    reads it, and each run outcome words it differently)."""

    def without_details(runs: list[dict]) -> list[dict]:
        return [{key: value for key, value in run.items() if key != "details_json"} for run in runs]

    return _shape({name: {**answer, "runs": without_details(answer["runs"])} for name, answer in answers.items()})


async def _observed(harness) -> None:
    """Rows that give every field of the obs key (TR-25) a value on both channels: a live and a newer shadow set each, and
    a latest run each whose codes and mode make one banner of every level. The times are recent, so no banner depends
    on when the fixture is regenerated beyond the run's own codes and mode."""
    now = datetime.now(UTC)

    def moment(hours: float) -> str:
        return stamp(now - timedelta(hours=hours))

    await insert_rows(
        harness.service.session_factory,
        sets=[
            set_row(1, "trends", "live", moment(3)),
            set_row(2, "trends", "shadow", moment(2)),
            set_row(3, "gsc", "live", moment(1)),
            set_row(4, "gsc", "shadow", moment(0.5)),
        ],
        batches=[
            batch_row("obs-trends", "trends", "shadow", moment(5), target_date=now.date().isoformat(), codes=["extinguished_today"]),
            batch_row("obs-gsc", "gsc", "live", moment(0.75), target_date=None, codes=["gsc_gap_exceeded"]),
        ],
    )


async def _every_field_set(harness) -> dict:
    """/sync with every mirror field holding a value: a paired run, the curve's reach, a degraded run counting a
    failure, and a mirror lock another session has held for 81 minutes; and every obs field holding one too."""
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import try_mirror_lock

    await _observed(harness)
    await _run(harness)
    await _execute(harness.engine, "UPDATE pick_mirror.series_state SET through = DATE '2026-09-22', trimmed_before = DATE '2026-06-21' WHERE id = 1")
    too_large = v2_error(500, "row_too_large", resource="rs_ids", key=["d-1"])
    await _run(harness, intercept=lambda call: too_large if call.resource == "rs_ids" else None)
    other = await open_dedicated(harness.dsn)
    try:
        assert await try_mirror_lock(other, now=datetime.now(UTC), holder="backfill")
        await _held_since(harness, other, 81)
        return await _sync_status(harness.service)
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_sync_answers_match_the_frontend_fixture(harness, monkeypatch, tmp_path):
    """The real /sync answers the frontend's sync-schema parses: before any version, every mirror field set (its times in
    both notations: as_of as RealShort writes it, the rest as stamp()), a failed mirror read, and SQLite's null."""
    from ggwork_pick import routes
    from ggwork_pick.service import SyncSettings

    harness.service.sync_settings = SyncSettings(feed_url=BASE, feed_token=FEED_TOKEN, export_token=EXPORT_TOKEN, mirror_flag="1")
    answers = {"before_any_version": await _sync_status(harness.service), "every_field_set": await _every_field_set(harness)}

    async def broken(repo, **options):
        raise RuntimeError("synthetic read failure")

    with monkeypatch.context() as patched:
        patched.setattr(routes, "mirror_status", broken)
        patched.setattr(routes, "obs_status", broken)
        answers["read_error"] = await _sync_status(harness.service)
    answers["sqlite"] = await _sqlite_sync_status(tmp_path)
    mirror = answers["every_field_set"]["mirror"]
    assert set(mirror) == MIRROR_KEYS and all(value is not None for value in mirror.values()) and mirror["enabled"] is True
    assert mirror["current"]["as_of"].endswith(":00.000Z") and mirror["current"]["published_at"].endswith("+00:00")
    assert answers["before_any_version"]["mirror"]["current"] is None and answers["read_error"]["mirror"] == {"error": "RuntimeError"}
    assert answers["sqlite"]["mirror"] is None
    # The obs key (TR-25): empty before any run (production today), every field set, a failed read, and on SQLite too.
    empty = answers["before_any_version"]["obs"]["channels"]
    assert [channel["channel"] for channel in empty] == ["trends", "gsc"] and all(channel["banners"] == [] for channel in empty)
    observed = answers["every_field_set"]["obs"]["channels"]
    assert all(value is not None for channel in observed for value in channel.values())
    assert {banner["level"] for channel in observed for banner in channel["banners"]} == {"red", "warn", "info"}
    assert answers["read_error"]["obs"] == {"error": "RuntimeError"} and answers["sqlite"]["obs"]["channels"][0]["banners"] == []
    if os.environ.get("PICK_WRITE_CONTRACT"):
        SYNC_FIXTURE.write_text(json.dumps(answers, ensure_ascii=False, indent=2) + "\n")
    assert _contract_shape(json.loads(SYNC_FIXTURE.read_text())) == _contract_shape(answers)
