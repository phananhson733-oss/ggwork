"""The curve backfill (P2-7; plan 5.6, 1598-1608; brief U30 and the critique's as_of window):
`python -m ggwork_pick.mirror.series --backfill N`.

In process through backfill_series and run_backfill with the RealShort double on httpx.MockTransport, and for real: the -m
entry in a subprocess with no DEER_FLOW_* and another cwd, against the double on a loopback http.server and the per-test
PostgreSQL database.
"""

import asyncio
import io
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fake_realshort import EXPORT_TOKEN, FakeRealShort, serve, v2_error
from series_world import (
    AS_OF_DAY,
    SESSION_SETTING,
    Watched,
    add_version,
    as_points,
    close_world,
    curve,
    open_world,
    points,
    realshort_curve,
    set_state,
    snapshots,
    span,
    state,
    text,
)

EXTENSION_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_API = Path(__file__).resolve().parents[4] / "backend/packages/extension-api"
D = AS_OF_DAY
AS_OF = datetime(2026, 9, 23, 12, 32, tzinfo=UTC)
VARIABLES = ("PICK_DATABASE_URL", "PGSSLMODE", "PICK_REALSHORT_FEED_URL", "PICK_REALSHORT_EXPORT_TOKEN")
ADVISORY_HERE = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"


def ago(days: int):
    return D - timedelta(days=days)


def one(day):
    return ("d-a",)


def two(day):
    return ("d-a", "d-b")


@pytest.fixture
def dsn(pg_db_url):
    from ggwork_pick.mirror.connection import dsn_from_url

    return dsn_from_url(pg_db_url)


@pytest_asyncio.fixture
async def world(dsn):
    opened = await open_world(dsn, holder="backfill")
    yield opened
    await close_world(opened)


async def backfill(world, lookback_days: int = 90, **options):
    from ggwork_pick.mirror.series import backfill_series

    return await backfill_series(world.conn, client=world.client, lookback_days=lookback_days, clock=world.clock, sleep=world.clock.sleep, **options)


def manifests(world) -> list[str]:
    return [call.params["as_of"] for call in world.fake.calls if call.resource == "manifest"]


@pytest.mark.asyncio
async def test_the_current_versions_curve_matches_realshort_point_for_point(world):
    def ids(day):
        extra = ("d-b",) if day.toordinal() % 3 else ()  # d-b misses every third day
        return ("d-a", *extra, *(("d-new",) if day >= ago(10) else ()))

    series = snapshots(span(ago(92), D), ids)
    world.serve(series)
    await add_version(world.conn, 1, AS_OF, latest_snapshot=D)
    outcome = await backfill(world, lookback_days=92)
    assert len(outcome.merged) == 93 and outcome.missing == ()
    for drama in ("d-a", "d-b", "d-new"):
        assert await curve(world.conn, drama, as_of=AS_OF, latest_snapshot=D) == realshort_curve(series, drama, AS_OF)
    full = await curve(world.conn, "d-a", as_of=AS_OF, latest_snapshot=D)
    assert len(full) == 91 and full[0][0] == ago(90)  # as_of - 90 is the first of 91 dates
    assert await state(world.conn) == (D, ago(92))
    assert await points(world.conn) == as_points(series)


@pytest.mark.asyncio
async def test_lookback_is_an_upper_bound_and_through_is_where_it_resumes(world):
    world.serve(snapshots(span(ago(5), D), one))
    outcome = await backfill(world, lookback_days=3)
    assert outcome.merged == tuple(text(day) for day in span(ago(3), D))
    assert world.series_calls() == list(outcome.merged)
    # Run again after an interruption at D-1: only the days after through are asked for.
    await set_state(world.conn, ago(1), (await state(world.conn))[1])
    again = await backfill(world, lookback_days=90)
    assert again.merged == (text(D),) and world.series_calls()[-1:] == [text(D)]
    # RealShort keeps 91 days: a larger N only means the whole of snapshotDays.
    assert (await state(world.conn))[0] == D


@pytest.mark.asyncio
async def test_backfill_renews_as_of_after_25_minutes_and_goes_on_with_the_next_day(world):
    series = snapshots(span(ago(4), D), two)
    world.serve(series)

    def report(day, rows, seconds):
        if day == text(ago(3)):
            # Exactly 25 minutes: the client would still send (it stops past 25), RealShort would still answer (30).
            world.clock.advance((AS_OF + timedelta(minutes=25) - world.clock()).total_seconds())

    outcome = await backfill(world, report=report)
    first, second = manifests(world)
    assert first != second and outcome.renewals == 1 and world.clock.sleeps == []
    assert world.series_calls() == [text(day) for day in span(ago(4), D)]  # no day asked, or merged, twice
    later = [call.params["as_of"] for call in world.fake.calls if call.resource == "rs_series_day" and call.params["day"] >= text(ago(2))]
    assert set(later) == {second}
    assert await points(world.conn) == as_points(series)


@pytest.mark.parametrize(
    ("reply", "slept"),
    [
        pytest.param(lambda: v2_error(400, "bad_request", reason="as_of"), [], id="400-as_of"),
        pytest.param(lambda: v2_error(409, "source_changed"), [90], id="409"),
    ],
)
@pytest.mark.asyncio
async def test_backfill_renews_on_400_as_of_or_409_and_asks_for_that_day_again(dsn, reply, slept):
    third = text(ago(2))

    def once(call):
        return reply() if call.resource == "rs_series_day" and call.params.get("day") == third and call.n == 3 else None

    world = await open_world(dsn, intercept=once, holder="backfill")
    try:
        series = snapshots(span(ago(4), D), two)
        world.serve(series)
        outcome = await backfill(world)
        assert world.series_calls() == [text(ago(4)), text(ago(3)), third, third, text(ago(1)), text(D)]
        assert len(manifests(world)) == 2 and outcome.renewals == 1
        assert world.clock.sleeps == slept
        assert await points(world.conn) == as_points(series) and (await state(world.conn))[0] == D
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_a_409_on_the_second_page_of_a_day_starts_that_day_again_without_doubling_it(dsn):
    target, refused = text(ago(1)), []

    def once(call):
        if call.resource == "rs_series_day" and call.params.get("day") == target and "cursor" in call.params and not refused:
            refused.append(call.n)
            return v2_error(409, "source_changed")
        return None

    world = await open_world(dsn, intercept=once, holder="backfill")
    try:
        world.fake.page_rows = {"rs_series_day": 1}  # RealShort's 3 MB cut: a day in several pages
        series = snapshots(span(ago(2), D), lambda day: ("d-a", "d-b", "d-c"))
        world.serve(series)
        outcome = await backfill(world)
        assert refused and outcome.renewals == 1 and world.clock.sleeps == [90]
        assert world.series_calls().count(target) == 2 + 3  # page 1 and the refused page 2, then the whole day again
        # The first attempt's page 1 is forgotten: the day matches snapshotDays and no point is there twice.
        assert outcome.merged == tuple(series) and await points(world.conn) == as_points(series)
    finally:
        await close_world(world)


@pytest.mark.parametrize("refused", [pytest.param(1, id="first-manifest"), pytest.param(2, id="renewed-manifest")])
@pytest.mark.asyncio
async def test_a_409_on_the_manifest_is_drift_too(dsn, refused):
    """RealShort answers the manifest itself with 409 when a write lands while it reads (rs:src/lib/pick/export-v2.ts:497-504)."""

    def drift(call):
        return v2_error(409, "source_changed") if call.resource == "manifest" and call.n == refused else None

    world = await open_world(dsn, intercept=drift, holder="backfill")
    try:
        series = snapshots(span(ago(2), D), two)
        world.serve(series)

        def report(day, rows, seconds):
            if day == text(ago(2)) and refused == 2:
                world.clock.advance((AS_OF + timedelta(minutes=25) - world.clock()).total_seconds())

        outcome = await backfill(world, report=report)
        assert len(manifests(world)) == refused + 1 and world.clock.sleeps == [90]
        assert outcome.renewals == refused - 1 and outcome.merged == tuple(series)
        assert await points(world.conn) == as_points(series) and (await state(world.conn))[0] == D
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_a_manifest_that_keeps_drifting_ends_the_backfill(dsn):
    from ggwork_pick.mirror.errors import DriftError
    from ggwork_pick.mirror.series import BACKFILL_MAX_RESTARTS

    def drifting(call):
        if call.resource != "manifest":
            return None
        # A 401 on the 20th manifest ends what would otherwise never end (the double never suspends).
        return v2_error(401, "unauthorized") if call.n >= 20 else v2_error(409, "source_changed")

    world = await open_world(dsn, intercept=drifting, holder="backfill")
    try:
        world.serve(snapshots(span(ago(1), D), one))
        with pytest.raises(DriftError):
            await backfill(world)
        assert len(manifests(world)) == BACKFILL_MAX_RESTARTS + 1 and world.clock.sleeps == [90] * BACKFILL_MAX_RESTARTS
        assert world.series_calls() == [] and await state(world.conn) == (None, None)
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_restarts_are_counted_since_the_last_merged_day_not_for_the_whole_run(dsn):
    from ggwork_pick.mirror.series import BACKFILL_MAX_RESTARTS

    refused: set[str] = set()

    def once_a_day(call):
        day = call.params.get("day")
        if call.resource != "rs_series_day" or day in refused:
            return None
        refused.add(day)
        return v2_error(409, "source_changed")

    world = await open_world(dsn, intercept=once_a_day, holder="backfill")
    try:
        series = snapshots(span(ago(BACKFILL_MAX_RESTARTS + 1), D), one)
        world.serve(series)
        outcome = await backfill(world)
        # Every day drifts once: more new as_of in all than the limit, never more than one before a merge.
        assert outcome.renewals == len(series) > BACKFILL_MAX_RESTARTS
        assert outcome.merged == tuple(series) and await points(world.conn) == as_points(series)
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_a_backfill_clock_ahead_of_the_feed_clock_gives_up_rather_than_renewing_for_ever(dsn):
    from ggwork_pick.mirror.errors import AsOfExpiredError
    from ggwork_pick.mirror.series import BACKFILL_MAX_RESTARTS, backfill_series

    def stop_a_loop(call):
        # The double never suspends, so no timeout could end an endless loop: a 401 on the 20th manifest does.
        return v2_error(401, "unauthorized") if call.resource == "manifest" and call.n >= 20 else None

    world = await open_world(dsn, intercept=stop_a_loop, holder="backfill")
    try:
        world.serve(snapshots(span(ago(1), D), one))

        def ahead() -> datetime:
            """Every as_of the client picks looks 30 minutes old by this clock: no day could ever start."""
            return world.clock() + timedelta(minutes=30)

        with pytest.raises(AsOfExpiredError):
            await backfill_series(world.conn, client=world.client, lookback_days=90, clock=ahead, sleep=world.clock.sleep)
        assert len(manifests(world)) == BACKFILL_MAX_RESTARTS + 1 and world.series_calls() == []
        assert await points(world.conn) == set() and await state(world.conn) == (None, None)
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_backfill_refuses_a_connection_without_the_mirror_lock(dsn):
    world = await open_world(dsn, holder=None)
    try:
        world.serve(snapshots(span(ago(1), D), one))
        with pytest.raises(RuntimeError, match="镜像锁"):
            await backfill(world)
        assert world.fake.calls == [] and await points(world.conn) == set() and await state(world.conn) == (None, None)
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_backfill_names_the_days_realshort_has_no_snapshot_for(dsn):
    from ggwork_pick.mirror.series import EXIT_OK, run_backfill

    world = await open_world(dsn, holder=None)
    try:
        world.serve(snapshots([ago(3), ago(1), D], one))  # no snapshot on D-2
        out, err = io.StringIO(), io.StringIO()
        code = await run_backfill(dsn=dsn, client=world.client, lookback_days=90, clock=world.clock, sleep=world.clock.sleep, out=out, err=err)
        assert code == EXIT_OK, err.getvalue()
        assert f"缺天 1 个：{text(ago(2))}" in out.getvalue().splitlines()[-1]
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_backfill_gives_up_after_repeated_drift_and_keeps_what_it_merged(dsn):
    from ggwork_pick.mirror.series import BACKFILL_MAX_RESTARTS, EXIT_FAILED, run_backfill

    third = text(ago(2))

    def always(call):
        return v2_error(409, "source_changed") if call.resource == "rs_series_day" and call.params.get("day") == third else None

    world = await open_world(dsn, intercept=always, holder=None)
    try:
        series = snapshots(span(ago(4), D), one)
        world.serve(series)
        out, err = io.StringIO(), io.StringIO()
        code = await run_backfill(dsn=dsn, client=world.client, lookback_days=90, clock=world.clock, sleep=world.clock.sleep, out=out, err=err)
        assert code == EXIT_FAILED and "DriftError" in err.getvalue()
        assert world.series_calls().count(third) == BACKFILL_MAX_RESTARTS + 1
        assert (await state(world.conn))[0] == ago(3)
        assert {point[1] for point in await points(world.conn)} == {ago(4), ago(3)}
        assert "d-a" not in out.getvalue() + err.getvalue()
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_backfill_stops_at_a_day_whose_rows_differ_from_snapshot_days(world):
    from ggwork_pick.mirror.series import SeriesCountError

    world.serve(snapshots(span(ago(2), D), two))

    def shrink(day, rows, seconds):
        if day == text(ago(2)):
            world.fake.series = {**world.fake.series, text(ago(1)): world.fake.series[text(ago(1))][:1]}

    with pytest.raises(SeriesCountError, match=text(ago(1))):
        await backfill(world, report=shrink)
    assert (await state(world.conn))[0] == ago(2)
    assert {point[1] for point in await points(world.conn)} == {ago(2)}
    assert await world.conn.fetchval("SELECT to_regclass('pg_temp.series_new')") is None


@pytest.mark.asyncio
async def test_backfill_while_the_sync_holds_the_lock_writes_nothing(dsn):
    from ggwork_pick.mirror.series import EXIT_LOCKED, run_backfill

    world = await open_world(dsn, holder="sync")
    try:
        world.serve(snapshots(span(ago(1), D), one))
        out, err = io.StringIO(), io.StringIO()
        code = await run_backfill(dsn=dsn, client=world.client, lookback_days=90, clock=world.clock, sleep=world.clock.sleep, out=out, err=err)
        assert code == EXIT_LOCKED and "同步正在进行" in err.getvalue()
        assert world.fake.calls == []
        assert await points(world.conn) == set() and await state(world.conn) == (None, None)
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_backfill_logs_days_and_row_counts_only_and_releases_the_lock(dsn):
    from ggwork_pick.mirror.series import EXIT_OK, run_backfill

    world = await open_world(dsn, holder=None)
    try:
        world.serve(snapshots(span(ago(1), D), two))
        out, err = io.StringIO(), io.StringIO()
        code = await run_backfill(dsn=dsn, client=world.client, lookback_days=90, clock=world.clock, sleep=world.clock.sleep, out=out, err=err)
        assert code == EXIT_OK, err.getvalue()
        lines = out.getvalue().splitlines()
        assert len(lines) == 3 and lines[0].startswith(text(ago(1))) and " 2 " in lines[0] and lines[1].startswith(text(D))
        assert "d-a" not in out.getvalue() + err.getvalue() and err.getvalue() == ""
        # This test's database only: pg_locks lists the whole cluster, where other test sessions may hold theirs.
        assert await world.conn.fetchval(ADVISORY_HERE) == 0
        holder = await world.conn.fetchrow("SELECT lock_holder, lock_holder_since FROM pick_mirror.control WHERE id = 1")
        assert tuple(holder) == (None, None)
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_only_plain_statements_reach_the_backfill_connection_and_each_merge_is_vacuumed(world):
    watched = Watched(world.conn)
    world.serve(snapshots(span(ago(2), D), one))
    from ggwork_pick.mirror.series import backfill_series

    outcome = await backfill_series(watched, client=world.client, lookback_days=90, clock=world.clock, sleep=world.clock.sleep)
    assert len(outcome.merged) == 3
    assert watched.sent and [query for query in watched.sent if SESSION_SETTING.search(query)] == []
    # Measured at 40,000 dramas x 91 days: without it the table reached 3.3 GB of dead row versions (66 MB live), past the
    # capacity cap U43 checks; with it 131 MB, and the vacuums took 3 s in all.
    assert sum("VACUUM" in query for query in watched.sent) == len(outcome.merged)


@pytest.mark.asyncio
async def test_a_vacuum_that_cannot_get_its_lock_is_skipped_not_waited_for(world, dsn):
    from ggwork_pick.mirror.connection import open_dedicated

    blocker = await open_dedicated(dsn)
    try:
        # What an autovacuum of the table holds; the merges themselves (ROW EXCLUSIVE) do not conflict with it.
        transaction = blocker.transaction()
        await transaction.start()
        await blocker.execute("LOCK TABLE pick_mirror.series IN SHARE UPDATE EXCLUSIVE MODE")
        world.serve(snapshots(span(ago(1), D), one))
        outcome = await asyncio.wait_for(backfill(world), timeout=20)
        assert len(outcome.merged) == 2
        await transaction.rollback()
    finally:
        await blocker.close()


@pytest.mark.asyncio
async def test_an_unreachable_database_fails_without_echoing_the_url():
    from ggwork_pick.mirror.series import EXIT_FAILED, run_backfill

    out, err = io.StringIO(), io.StringIO()
    code = await run_backfill(
        dsn="postgresql://postgres:hunter2-secret@127.0.0.1:1/nothing", client=None, lookback_days=90, out=out, err=err, clock=lambda: AS_OF
    )
    assert code == EXIT_FAILED and "镜像专用连接失败" in err.getvalue() and "hunter2-secret" not in err.getvalue()


@pytest.mark.parametrize("argv", [[], ["--backfill"], ["--backfill", "0"], ["--backfill", "x"], ["--backfill", "90", "--more"]])
def test_bad_arguments_are_usage_errors(capsys, argv):
    from ggwork_pick.mirror.series import EXIT_USAGE, main

    assert main(argv, env={}) == EXIT_USAGE
    assert "--backfill" in capsys.readouterr().err


def _run(env: dict[str, str], cwd: Path, *args: str) -> subprocess.CompletedProcess:
    base = {"PATH": os.environ.get("PATH", ""), "HOME": str(cwd.parent / "home"), "PYTHONPATH": os.pathsep.join([str(EXTENSION_ROOT), str(EXTENSION_API)])}
    command = [sys.executable, "-m", "ggwork_pick.mirror.series", *args]
    return subprocess.run(command, cwd=cwd, env={**base, **env}, capture_output=True, text=True, timeout=120)


@pytest.fixture
def work(tmp_path):
    (tmp_path / "home").mkdir()
    folder = tmp_path / "cwd"
    folder.mkdir()
    return folder


def test_missing_variables_are_named_without_echoing_any_value(work):
    """python -m runs ggwork_pick/__init__.py first; no DEER_FLOW_*, cwd outside backend (railway ssh, plan 6.5)."""
    from ggwork_pick.mirror.series import EXIT_USAGE

    nothing = _run({}, work, "--backfill", "90")
    assert nothing.returncode == EXIT_USAGE, nothing.stderr[-2000:]
    assert all(name in nothing.stderr for name in VARIABLES)
    given = {"PICK_DATABASE_URL": "postgresql://postgres:hunter2-secret@127.0.0.1:1/x", "PGSSLMODE": "require", "PICK_REALSHORT_FEED_URL": "https://rs.test"}
    one_missing = _run(given, work, "--backfill", "90")
    assert one_missing.returncode == EXIT_USAGE and "PICK_REALSHORT_EXPORT_TOKEN" in one_missing.stderr
    assert not any(name in one_missing.stderr for name in VARIABLES[:3])
    assert "hunter2-secret" not in one_missing.stdout + one_missing.stderr and "rs.test" not in one_missing.stderr
    assert "RuntimeWarning" not in nothing.stderr + one_missing.stderr
    assert list(work.iterdir()) == []


def test_backfill_runs_for_real_in_a_clean_process(work, dsn):
    last = (datetime.now(UTC) - timedelta(minutes=5)).date() - timedelta(days=1)
    fake = FakeRealShort(series={})
    fake.series = snapshots(span(last - timedelta(days=2), last), two)
    server, base = serve(fake)
    env = {"PICK_DATABASE_URL": dsn, "PGSSLMODE": "disable", "PICK_REALSHORT_FEED_URL": base, "PICK_REALSHORT_EXPORT_TOKEN": EXPORT_TOKEN}
    try:
        result = _run(env, work, "--backfill", "90")
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stderr[-3000:]
    assert asyncio.run(_points(dsn)) == as_points(fake.series)
    lines = result.stdout.splitlines()
    assert [line.split()[0] for line in lines[:3]] == [text(day) for day in span(last - timedelta(days=2), last)]
    for secret in (EXPORT_TOKEN, "d-a", "d-b", dsn):
        assert secret not in result.stdout + result.stderr
    assert "RuntimeWarning" not in result.stderr
    assert list(work.iterdir()) == []


async def _points(dsn: str) -> set:
    from ggwork_pick.mirror.connection import open_dedicated

    conn = await open_dedicated(dsn)
    try:
        return await points(conn)
    finally:
        await conn.close()
