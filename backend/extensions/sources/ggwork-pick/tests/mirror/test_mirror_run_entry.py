"""How the mirror run is switched on and what it leaves behind (P2-5c; plan 735, 774, 1575; U31, U37, U39, U40; brief 0.3).

The switch cases run on either dialect where they can; the rest are PostgreSQL only and go through run_world's double.
"""

import asyncio
import io
import json
import threading
import time

import gate_world as gw
import pytest
import pytest_asyncio
from engines import host_engine
from fake_realshort import EXPORT_TOKEN, FEED_TOKEN, Clock, v2_error
from run_world import BASE, WorldRealShort, control, fetch, make_sync, open_harness, world_fake
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from ggwork_pick.service import PickService, SyncSettings

RUN_STATUSES = {"running", "success", "failed"}  # frontend/src/core/pick/api.ts:58


@pytest_asyncio.fixture
async def harness(pg_db_url, tmp_path):
    opened = await open_harness(pg_db_url, tmp_path)
    yield opened
    await opened.engine.dispose()


async def _run(harness, world=None, *, intercept=None, **overrides):
    fake = world_fake(harness, world, intercept=intercept)
    return await make_sync(harness, fake, **overrides).run("cron"), fake


# ---------------------------------------------------------------- the switch (U31, U39)


SWITCH_CASES = {
    "unset": ("", "postgres", EXPORT_TOKEN, None),
    "zero": ("0", "postgres", EXPORT_TOKEN, None),
    "true": ("true", "postgres", EXPORT_TOKEN, None),
    "spaced": (" 1", "postgres", EXPORT_TOKEN, None),
    "sqlite": ("1", "sqlite", EXPORT_TOKEN, "not_postgresql"),
    "no_token": ("1", "postgres", "", "missing_export_token"),
}


@pytest.fixture
def switch_url(request, tmp_path, case):
    """The case's database, resolved before the test's event loop runs (pg_template is a session-loop fixture)."""
    if SWITCH_CASES[case][1] == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}"
    return request.getfixturevalue("pg_db_url")


async def _switched_service(url: str, tmp_path, flag: str, export_token: str):
    engine = host_engine(url)
    settings = SyncSettings(feed_url=BASE, feed_token=FEED_TOKEN, export_token=export_token, mirror_flag=flag)
    service = PickService(tmp_path / "files", settings)
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    return engine, service


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(SWITCH_CASES))
async def test_flag_values(switch_url, tmp_path, case):
    from ggwork_pick.sync import RealShortSync

    flag, _, export_token, disabled = SWITCH_CASES[case]
    engine, service = await _switched_service(switch_url, tmp_path, flag, export_token)
    fake = WorldRealShort(now=Clock())
    service.sync_transport = fake.transport()
    try:
        sync = service.realshort_sync()
        assert type(sync) is RealShortSync
        result = await sync.run("cron")
    finally:
        await engine.dispose()
    assert result["status"] == "success"
    assert {call.resource for call in fake.calls} == {"v1"}
    assert not any("as_of" in call.params for call in fake.calls)
    assert result["details_json"] == ({"mirror_disabled": disabled} if disabled else None)


@pytest.mark.asyncio
async def test_flag_one_on_postgresql_with_a_token_runs_the_mirror(pg_db_url, tmp_path):
    from ggwork_pick.mirror.run import MirrorSync

    engine = host_engine(pg_db_url)
    settings = SyncSettings(feed_url=BASE, feed_token=FEED_TOKEN, export_token=EXPORT_TOKEN, mirror_flag="1", db_size_cap=12345)
    service = PickService(tmp_path / "files", settings)
    try:
        await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
        sync = service.realshort_sync()
    finally:
        await engine.dispose()
    assert type(sync) is MirrorSync and sync.limits.db_size_cap == 12345
    assert "@" not in repr(sync) and EXPORT_TOKEN not in repr(sync) and FEED_TOKEN not in repr(settings)


def test_settings_read_the_environment():
    env = {
        "PICK_REALSHORT_FEED_URL": f" {BASE} ",
        "PICK_REALSHORT_FEED_TOKEN": "feed",
        "PICK_REALSHORT_EXPORT_TOKEN": " export ",
        "PICK_MIRROR_ENABLED": "1",
        "PICK_DB_SIZE_CAP_BYTES": "4096",
    }
    settings = SyncSettings.from_env(env)
    assert (settings.feed_url, settings.export_token, settings.mirror_flag, settings.db_size_cap) == (BASE, "export", "1", 4096)
    assert SyncSettings.from_env({}).db_size_cap is None and SyncSettings.from_env({}).mirror_flag == ""
    assert SyncSettings.from_env({"PICK_DB_SIZE_CAP_BYTES": "6GiB"}).db_size_cap is None
    assert SyncSettings.from_env({"PICK_DB_SIZE_CAP_BYTES": "0"}).db_size_cap is None


# ---------------------------------------------------------------- accept-empty (plan 1575, U40)


async def _accept_empty(harness) -> None:
    """The operator's command (python -m ggwork_pick.mirror.admin accept-empty), its body run in process."""
    from ggwork_pick.mirror.admin import EXIT_OK, accept_empty

    assert await accept_empty(harness.dsn, out=io.StringIO(), err=io.StringIO()) == EXIT_OK


@pytest.mark.asyncio
async def test_accept_empty_consumed_once(harness):
    emptied = gw.empty_posted(gw.baseline())
    outcomes = []
    for world, accept in ((gw.baseline(), False), (emptied, False), (emptied, True), (gw.baseline(), False), (emptied, False)):
        if accept:
            await _accept_empty(harness)
        result, _ = await _run(harness, world)
        outcomes.append((result["details_json"]["outcome"], result["details_json"]["reason"], (await control(harness.engine))["accept_empty_once"]))
        harness.clock.advance(600)
    assert outcomes == [
        ("paired", None, False),
        ("degraded", "degraded:empty_tables", False),
        ("paired", None, False),
        ("paired", None, False),
        ("degraded", "degraded:empty_tables", False),
    ]


# ---------------------------------------------------------------- details_json (plan 774, U37, U49)

PASSWORD = "pw-p25c-never-shown"
SECRETS = (EXPORT_TOKEN, FEED_TOKEN, PASSWORD, "pan.baidu.com", "提取码", "的剧名", gw.PAN)


def _with_password(dsn: str) -> str:
    """The test cluster's own password when it checks one (CI), else a marker; either way the run must not show it."""
    url = make_url(dsn)
    return dsn if url.password else url.set(password=PASSWORD).render_as_string(hide_password=False)


def _secrets(dsn: str) -> tuple[str, ...]:
    password = make_url(dsn).password
    return SECRETS + ((password,) if password and password != PASSWORD else ())


DETAIL_CASES = {
    "paired": (lambda: gw.baseline(), None, ("version", "gates", "stages", "scrub_hits")),
    "degraded": (lambda: gw.with_row(gw.baseline(), "catalog_signals", 0, note=gw.PAN), None, ("version", "gates", "stages", "scrub_hits")),
    "v1_failed": (lambda: gw.with_v1_row(gw.baseline(), 0, title=gw.PAN), None, ("version", "gates")),
    "fallback": (lambda: gw.baseline(), lambda c: v2_error(404, "not_found") if c.resource == "manifest" else None, ("fallback",)),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(DETAIL_CASES))
async def test_details_json_safe(harness, caplog, case):
    world, intercept, keys = DETAIL_CASES[case]
    caplog.set_level("DEBUG")
    dsn = _with_password(harness.dsn)
    result, _ = await _run(harness, world(), intercept=intercept, dsn=dsn)
    details = result["details_json"]
    assert result["status"] in RUN_STATUSES
    assert {"mode", "outcome", "reason", "consecutive_failures", "alert", "cleanup", "cpu_ms"} <= set(details) and set(keys) <= set(details)
    shown = json.dumps(details, ensure_ascii=False) + str(result.get("error")) + caplog.text
    assert not [secret for secret in _secrets(dsn) if secret in shown]
    runs = await fetch(harness.engine, "SELECT status FROM ggwp_sync_runs")
    assert {run["status"] for run in runs} <= RUN_STATUSES


@pytest.mark.asyncio
async def test_degraded_details_name_the_gate_by_path_and_row(harness):
    result, _ = await _run(harness, gw.with_row(gw.baseline(), "catalog_signals", 0, note=gw.PAN))
    gate = result["details_json"]["gates"]["mirror_text"]
    assert gate["consequence"] == "mirror" and gate["paths"] == {"catalog_signals.note": 1}
    assert gate["rows"] == [["catalog_signals", "c-1", "kd", 0]] and gate["total"] == 1
    assert result["details_json"]["scrub_hits"]["mirror"] == 1


# ---------------------------------------------------------------- CPU work off the event loop (brief 0.3)


@pytest.mark.asyncio
async def test_scan_runs_off_event_loop(harness, monkeypatch):
    from ggwork_pick.mirror import pan

    real, threads, loop_thread = pan.scan_value, [], threading.get_ident()

    def slow_scan(value, prefix=""):
        threads.append(threading.get_ident())
        if len(threads) <= 2:
            time.sleep(0.25)
        return real(value, prefix)

    monkeypatch.setattr(pan, "scan_value", slow_scan)
    gaps, done = [], asyncio.Event()

    async def ticker():
        last = asyncio.get_running_loop().time()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = asyncio.get_running_loop().time()
            gaps.append(now - last)
            last = now

    ticking = asyncio.create_task(ticker())
    try:
        result, _ = await _run(harness)
    finally:
        done.set()
        await ticking
    assert result["details_json"]["outcome"] == "paired"
    assert threads and loop_thread not in threads
    assert max(gaps) < 0.2


# ---------------------------------------------------------------- pull_v1 (the brief's P2-5c sync.py)


@pytest.mark.asyncio
async def test_realshort_sync_pulls_through_pull_v1(pick_db_url, tmp_path, monkeypatch):
    from ggwork_pick import sync as sync_module

    calls, real = [], sync_module.pull_v1

    async def recorded(service, repo, **options):
        calls.append(options)
        return await real(service, repo, **options)

    monkeypatch.setattr(sync_module, "pull_v1", recorded)
    engine = host_engine(pick_db_url)
    service = PickService(tmp_path / "files", SyncSettings(feed_url=BASE, feed_token=FEED_TOKEN))
    try:
        await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
        service.sync_transport = WorldRealShort(now=Clock()).transport()
        result = await service.realshort_sync().run("manual")
    finally:
        await engine.dispose()
    assert result["status"] == "success" and result["rows"] == len(gw.V1_CANDIDATES)
    assert [(c["base_url"], c["token"], c.get("stage", False)) for c in calls] == [(BASE, FEED_TOKEN, False)]
