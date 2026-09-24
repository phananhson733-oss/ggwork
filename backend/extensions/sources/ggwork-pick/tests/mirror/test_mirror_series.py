"""The regular curve fold (P2-7; plan 3.2, 5.6, 1598-1608; brief U29, U38): run by the sync on its dedicated connection,
under the mirror lock, after the version retention.

PostgreSQL only (pick_mirror exists nowhere else); each test gets its own database. RealShort is the double in
fake_realshort.py; its clock starts at 2026-09-23 12:34:56 UTC, so every manifest's as_of is 12:32 that day.
"""

import json
import threading
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from fake_realshort import Clock, v2_error
from series_world import (
    AS_OF_DAY,
    SESSION_SETTING,
    Watched,
    add_version,
    as_points,
    close_world,
    curve,
    observation,
    open_world,
    points,
    realshort_curve,
    seed,
    set_state,
    snapshots,
    span,
    state,
    text,
    versions,
)

from ggwork_pick.mirror.client import select_as_of

D = AS_OF_DAY
AS_OF = datetime(2026, 9, 23, 12, 32, tzinfo=UTC)
TEMP_TABLE = "SELECT to_regclass('pg_temp.series_new')"
MY_LOCKS = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"


def ago(days: int) -> date:
    return D - timedelta(days=days)


def two(day: date) -> tuple[str, ...]:
    return ("d-a", "d-b")


def one(day: date) -> tuple[str, ...]:
    return ("d-a",)


@pytest.fixture
def dsn(pg_db_url):
    from ggwork_pick.mirror.connection import dsn_from_url

    return dsn_from_url(pg_db_url)


@pytest_asyncio.fixture
async def world(dsn):
    opened = await open_world(dsn)
    yield opened
    await close_world(opened)


async def untouched(world) -> tuple:
    return await points(world.conn), await state(world.conn), await versions(world.conn)


def test_constants_follow_the_brief_and_the_row_contract():
    from ggwork_pick.mirror.contracts import RESOURCE_COLUMNS
    from ggwork_pick.mirror.series import FOLD_DAYS_PER_RUN, FOLD_DEADLINE, SERIES_COLUMNS

    assert FOLD_DAYS_PER_RUN == 3
    assert FOLD_DEADLINE == timedelta(minutes=27)  # U38
    assert tuple(column.name for column in RESOURCE_COLUMNS["rs_series_day"]) == SERIES_COLUMNS
    assert [column.type for column in RESOURCE_COLUMNS["rs_series_day"]] == ["text", "float", "int"]


@pytest.mark.asyncio
async def test_appends_the_new_day_and_moves_through(world):
    old = snapshots(span(ago(3), ago(1)), two)
    new = snapshots([D], lambda day: ("d-a", "d-b", "d-c"))
    await seed(world.conn, old)
    await set_state(world.conn, ago(1))
    world.serve({**old, **new})
    outcome = await world.fold()
    assert (outcome.folded, outcome.rows, outcome.error) == ((text(D),), (3,), None)
    assert await points(world.conn) == as_points({**old, **new})
    assert (await state(world.conn))[0] == D
    assert world.series_calls() == [text(D)]
    details = outcome.details()
    assert details["folded"] == [text(D)] and details["rows"] == {text(D): 3}
    assert (details["through"], details["missing"], details["pending"], details["needs_backfill"]) == (text(D), [], 0, False)
    # Arrays stay sorted by day: the reader and the trim both rely on days[1] being the earliest point.
    unsorted = "SELECT count(*) FROM pick_mirror.series WHERE days <> (SELECT array_agg(x ORDER BY x) FROM unnest(days) AS x)"
    assert await world.conn.fetchval(unsorted) == 0


@pytest.mark.asyncio
async def test_takes_the_earliest_three_missing_days(world):
    await set_state(world.conn, ago(5))
    world.serve(snapshots(span(ago(4), D), one))
    outcome = await world.fold()
    first_three = [text(ago(n)) for n in (4, 3, 2)]
    assert list(outcome.folded) == first_three and world.series_calls() == first_three
    assert outcome.pending == 2 and outcome.details()["pending"] == 2
    assert (await state(world.conn))[0] == ago(2)
    assert {point[1] for point in await points(world.conn)} == {date.fromisoformat(day) for day in first_three}


@pytest.mark.parametrize(
    ("published", "cutoff"),
    [
        # 09-10 - 90 = 06-12 is earlier than through - 92 = 06-23: the oldest published version decides.
        pytest.param([datetime(2026, 9, 10, 3, 40, tzinfo=UTC), AS_OF], date(2026, 6, 12), id="oldest-version"),
        # 09-23 - 90 = 06-25 is later than 06-23: the new through decides, 93 days kept.
        pytest.param([AS_OF], date(2026, 6, 23), id="through-minus-92"),
        pytest.param([], date(2026, 6, 23), id="no-published-version"),
    ],
)
@pytest.mark.asyncio
async def test_cutoff_is_the_earlier_of_the_two_rules_and_trimmed_before_follows(world, published, cutoff):
    for number, as_of in enumerate(published, 1):
        await add_version(world.conn, number, as_of)
    # Versions that are not published never hold the cutoff back.
    for number, status in ((90, "failed"), (91, "dropped"), (92, "building")):
        await add_version(world.conn, number, datetime(2026, 6, 1, tzinfo=UTC), status=status)
    await seed(world.conn, snapshots(span(date(2026, 6, 10), ago(1)), one))
    await set_state(world.conn, ago(1))
    world.serve(snapshots([D], one))
    outcome = await world.fold()
    assert await state(world.conn) == (D, cutoff)
    assert outcome.trimmed_before == text(cutoff) and outcome.details()["trimmed_before"] == text(cutoff)
    assert min(point[1] for point in await points(world.conn)) == cutoff


@pytest.mark.asyncio
async def test_cutoff_never_moves_back_before_what_is_already_trimmed(world):
    await add_version(world.conn, 1, datetime(2026, 9, 10, 3, 40, tzinfo=UTC))
    await seed(world.conn, snapshots(span(date(2026, 7, 1), ago(1)), one))
    await set_state(world.conn, ago(1), date(2026, 7, 1))
    world.serve(snapshots([D], one))
    await world.fold()
    # Points before 07-01 are already gone: saying 06-12 would promise points that no longer exist.
    assert await state(world.conn) == (D, date(2026, 7, 1))


@pytest.mark.asyncio
async def test_a_version_referenced_six_days_ago_keeps_its_91_days_through_six_daily_folds(dsn):
    world = await open_world(dsn, clock=Clock(datetime(2026, 9, 18, 3, 42, 30, tzinfo=UTC)))
    try:
        referenced = datetime(2026, 9, 17, 3, 40, tzinfo=UTC)
        history = snapshots(span(referenced.date() - timedelta(days=90), referenced.date()), one)
        await seed(world.conn, history)
        await set_state(world.conn, referenced.date())
        await add_version(world.conn, 1, referenced, latest_snapshot=referenced.date())
        everything = dict(history)
        for run in range(6):
            today = world.clock().date()
            everything = {**everything, **snapshots([today], one)}
            # RealShort keeps 91 days (rs:src/lib/observe/snapshot.ts:23).
            world.serve({day: rows for day, rows in everything.items() if date.fromisoformat(day) >= today - timedelta(days=90)})
            await add_version(world.conn, 2 + run, select_as_of(world.clock()), latest_snapshot=today)
            outcome = await world.fold()
            assert (outcome.folded, outcome.error) == ((text(today),), None)
            world.clock.advance(86400)
        assert (await state(world.conn))[0] == D
        window = await curve(world.conn, "d-a", as_of=referenced, latest_snapshot=referenced.date())
        assert len(window) == 91 and window[0][0] == referenced.date() - timedelta(days=90)
        assert window == realshort_curve(everything, "d-a", referenced)
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_a_renewed_reference_keeps_its_window_and_a_dropped_version_does_not_hold_the_cutoff(world):
    expired = datetime(2026, 9, 15, 3, 40, tzinfo=UTC)  # its references ran out: retention dropped it
    renewed = datetime(2026, 9, 16, 3, 40, tzinfo=UTC)  # "换一批" referenced it again: still published
    await add_version(world.conn, 1, expired, status="dropped", latest_snapshot=expired.date())
    await add_version(world.conn, 2, renewed, latest_snapshot=renewed.date())
    await add_version(world.conn, 3, AS_OF, latest_snapshot=D)
    history = snapshots(span(date(2026, 6, 10), ago(1)), one)
    await seed(world.conn, history)
    await set_state(world.conn, ago(1))
    world.serve(snapshots([D], one))
    await world.fold()
    assert await state(world.conn) == (D, date(2026, 6, 18))  # 09-16 - 90
    window = await curve(world.conn, "d-a", as_of=renewed, latest_snapshot=renewed.date())
    assert len(window) == 91 and window == realshort_curve(history, "d-a", renewed)
    # 06-17, where the dropped version's window began, is gone.
    assert min(point[1] for point in await points(world.conn)) == date(2026, 6, 18)


def _trim_history_ids(day: date) -> tuple[str, ...]:
    """d-a every day; d-gone only early on; d-edge only on the day before the cutoff (06-23) and on the cutoff itself."""
    gone = ("d-gone",) if day < date(2026, 6, 5) else ()
    edge = ("d-edge",) if day in (date(2026, 6, 22), date(2026, 6, 23)) else ()
    return ("d-a", *gone, *edge)


@pytest.mark.asyncio
async def test_every_trimmed_point_is_before_trimmed_before_and_every_later_point_is_kept(world):
    history = snapshots(span(date(2026, 6, 1), ago(1)), _trim_history_ids)
    await seed(world.conn, history)
    await set_state(world.conn, ago(1), date(2026, 6, 1))
    world.serve(snapshots([D], one))  # d-gone and d-edge are not in the new day: only the whole-table trim reaches them
    before = await points(world.conn)
    await world.fold()
    after = await points(world.conn)
    _, trimmed_before = await state(world.conn)
    assert trimmed_before == date(2026, 6, 23)
    assert all(point[1] >= trimmed_before for point in after)
    removed = before - after
    assert removed and all(point[1] < trimmed_before for point in removed)
    assert {point for point in before if point[1] >= trimmed_before} <= after
    # The boundary of the whole-table trim: the day before the cutoff goes, the cutoff day stays.
    assert {point[1] for point in after if point[0] == "d-edge"} == {date(2026, 6, 23)}
    # A drama left without points loses its row rather than keeping empty arrays.
    assert await world.conn.fetchval("SELECT count(*) FROM pick_mirror.series WHERE drama_id = 'd-gone'") == 0


@pytest.mark.asyncio
async def test_folding_the_same_day_twice_changes_nothing(world):
    history = snapshots(span(ago(5), ago(1)), two)
    await seed(world.conn, history)
    await set_state(world.conn, ago(1))
    world.serve({**history, **snapshots([D], two)})
    await world.fold()
    once = (await points(world.conn), await state(world.conn))
    # As if the state update had been lost after the points were written.
    await set_state(world.conn, ago(1), once[1][1])
    await world.fold()
    assert (await points(world.conn), await state(world.conn)) == once
    assert world.series_calls() == [text(D), text(D)]
    repeated = "SELECT count(*) FROM pick_mirror.series WHERE cardinality(days) <> (SELECT count(DISTINCT x) FROM unnest(days) AS x)"
    assert await world.conn.fetchval(repeated) == 0


@pytest.mark.asyncio
async def test_a_day_missing_from_snapshot_days_is_recorded(world):
    await set_state(world.conn, ago(3))
    world.serve(snapshots([ago(1), D], one))  # RealShort has no snapshot for D-2
    outcome = await world.fold()
    assert outcome.folded == (text(ago(1)), text(D))
    assert outcome.missing == (text(ago(2)),) and outcome.details()["missing"] == [text(ago(2))]
    assert (await state(world.conn))[0] == D


@pytest.mark.parametrize("change", ["fewer", "more", "duplicate-id"])
@pytest.mark.asyncio
async def test_a_day_whose_rows_differ_from_snapshot_days_is_not_merged(world, change):
    await add_version(world.conn, 1, AS_OF, latest_snapshot=ago(1))
    history = snapshots([ago(1)], two)
    await seed(world.conn, history)
    await set_state(world.conn, ago(1))
    promised = snapshots([D], lambda day: ("d-a", "d-b", "d-c"))
    world.serve({**history, **promised})
    manifest = await world.client.manifest_when_free()
    rows = promised[text(D)]
    served = {
        "fewer": rows[:2],
        "more": [*rows, observation("d-d", D)],
        "duplicate-id": [rows[0], {**rows[1], "drama_id": rows[0]["drama_id"]}, rows[2]],
    }[change]
    world.serve({**history, text(D): served})
    before = await untouched(world)
    from ggwork_pick.mirror.series import fold_series

    outcome = await fold_series(world.conn, manifest=manifest, client=world.client, clock=world.clock)
    assert outcome.folded == () and text(D) in outcome.error
    assert await untouched(world) == before
    assert await world.conn.fetchval(TEMP_TABLE) is None
    assert "d-a" not in json.dumps(outcome.details(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_only_the_days_that_passed_their_count_are_merged_page_after_page(world):
    # One row a page, as RealShort's 3 MB cut can make it: every day of the run is staged in the one temp table.
    world.fake.page_rows = {"rs_series_day": 1}
    earlier = snapshots([ago(2)], lambda day: ("d-x",))
    await seed(world.conn, earlier)
    await set_state(world.conn, ago(2))
    promised = snapshots([ago(1), D], lambda day: ("d-a", "d-b", "d-c") if day < D else ("d-a", "d-b", "d-c", "d-x"))
    world.serve(promised)
    manifest = await world.client.manifest_when_free()
    world.serve({**promised, text(D): promised[text(D)][1:]})  # D is staged in full, d-a short of snapshotDays
    row_x = "SELECT * FROM pick_mirror.series WHERE drama_id = 'd-x'"
    before_x = tuple(await world.conn.fetchrow(row_x))
    from ggwork_pick.mirror.series import fold_series

    outcome = await fold_series(world.conn, manifest=manifest, client=world.client, clock=world.clock)
    assert outcome.folded == (text(ago(1)),) and text(D) in outcome.error
    assert world.series_calls() == [text(ago(1))] * 3 + [text(D)] * 3
    # D's staged rows never reach the curve: through stops at D-1, and so do the points.
    assert await points(world.conn) == as_points({**earlier, text(ago(1)): promised[text(ago(1))]})
    # d-x is only in D: its row is not even rewritten.
    assert tuple(await world.conn.fetchrow(row_x)) == before_x
    assert (await state(world.conn))[0] == ago(1)


@pytest.mark.asyncio
async def test_points_of_non_canonical_ids_are_kept_and_the_canonical_curve_matches_realshort(world):
    # rs_ids of the version: d-a-old was merged into d-a; the snapshot keeps writing both ids (no canonical filter, plan 3.2).
    rs_ids = {"d-a": "d-a", "d-a-old": "d-a", "d-b": "d-b"}
    history = snapshots(span(date(2026, 6, 25), ago(3)), lambda day: ("d-a", "d-a-old", "d-b"))
    new = snapshots(span(ago(2), D), lambda day: ("d-a", "d-a-old", "d-b"))
    await seed(world.conn, history)
    await set_state(world.conn, ago(3))
    world.serve({**history, **new})
    await world.fold()
    everything = {**history, **new}
    assert {point for point in await points(world.conn) if point[0] == "d-a-old"} == {p for p in as_points(everything) if p[0] == "d-a-old"}
    for asked in ("d-a-old", "d-a", "d-b"):
        canonical = rs_ids[asked]
        assert await curve(world.conn, canonical, as_of=AS_OF, latest_snapshot=D) == realshort_curve(everything, canonical, AS_OF)


@pytest.mark.asyncio
async def test_a_failing_merge_is_only_recorded(world):
    await world.conn.execute(
        "CREATE FUNCTION pick_mirror.refuse() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'refused %', NEW.drama_id; END$$"
    )
    await world.conn.execute("CREATE TRIGGER refuse BEFORE INSERT OR UPDATE ON pick_mirror.series FOR EACH ROW EXECUTE FUNCTION pick_mirror.refuse()")
    await add_version(world.conn, 1, AS_OF, latest_snapshot=ago(1))
    await set_state(world.conn, ago(1))
    world.serve(snapshots([D], one))
    before = await untouched(world)
    outcome = await world.fold()
    assert outcome.folded == () and "RaiseError" in outcome.error and "P0001" in outcome.error
    assert "refused" not in outcome.error and "d-a" not in json.dumps(outcome.details(), ensure_ascii=False)
    assert await untouched(world) == before
    # The run carries on: no transaction left open, the temp table gone, the lock still held.
    assert not world.conn.is_in_transaction()
    assert await world.conn.fetchval(TEMP_TABLE) is None
    assert await world.conn.fetchval(MY_LOCKS) == 1


@pytest.mark.asyncio
async def test_a_feed_error_mid_fold_keeps_the_days_before_it(dsn):
    second = text(ago(1))

    def drift(call):
        return v2_error(409, "source_changed") if call.resource == "rs_series_day" and call.params.get("day") == second else None

    world = await open_world(dsn, intercept=drift)
    try:
        await set_state(world.conn, ago(3))
        world.serve(snapshots(span(ago(2), D), one))
        outcome = await world.fold()
        assert outcome.folded == (text(ago(2)),) and "DriftError" in outcome.error and second in outcome.error
        assert world.series_calls() == [text(ago(2)), second]  # D is never asked: through must not jump over D-1
        assert (await state(world.conn))[0] == ago(2)
        assert await world.conn.fetchval(TEMP_TABLE) is None
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_a_page_that_breaks_the_row_contract_is_not_merged(world):
    await set_state(world.conn, ago(1))
    world.serve({text(D): [{"drama_id": "d-a", "revenue_cents": "12", "promoters_cnt": 1}]})
    outcome = await world.fold()
    # The contract error names the column, never the value.
    assert outcome.folded == () and text(D) in outcome.error and "revenue_cents" in outcome.error and '"12"' not in outcome.error
    assert await points(world.conn) == set() and (await state(world.conn))[0] == ago(1)


@pytest.mark.asyncio
async def test_without_through_nothing_is_folded_until_the_backfill(world):
    world.serve(snapshots([D], one))
    outcome = await world.fold()
    assert outcome.needs_backfill and outcome.folded == () and outcome.error is None
    assert outcome.details()["needs_backfill"] is True
    assert world.series_calls() == [] and await points(world.conn) == set() and await state(world.conn) == (None, None)


@pytest.mark.parametrize("served", [pytest.param([ago(1), D], id="latest-equals-through"), pytest.param([], id="no-snapshot")])
@pytest.mark.asyncio
async def test_nothing_to_fold_when_the_latest_snapshot_is_not_after_through(world, served):
    await set_state(world.conn, D, date(2026, 6, 23))
    world.serve(snapshots(served, one))
    outcome = await world.fold()
    assert (outcome.folded, outcome.error, outcome.needs_backfill) == ((), None, False)
    assert outcome.through == text(D) and world.series_calls() == []


@pytest.mark.asyncio
async def test_no_new_day_is_started_27_minutes_after_as_of(dsn):
    clock = Clock()
    first = text(ago(2))

    def slow(call):
        if call.resource == "rs_series_day" and call.params.get("day") == first:
            clock.advance(25 * 60)  # as_of 12:32, now 12:59:56: past as_of + 27 minutes
        return None

    world = await open_world(dsn, clock=clock, intercept=slow)
    try:
        await set_state(world.conn, ago(3))
        world.serve(snapshots(span(ago(2), D), one))
        outcome = await world.fold()
        assert outcome.folded == (first,) and outcome.stopped == "deadline" and outcome.error is None
        assert outcome.details()["stopped"] == "deadline" and outcome.pending == 2
        assert world.series_calls() == [first] and (await state(world.conn))[0] == ago(2)
    finally:
        await close_world(world)


@pytest.mark.parametrize("where", ["between-days", "mid-day"])
@pytest.mark.asyncio
async def test_the_clients_25_minute_as_of_stop_ends_the_fold_like_the_deadline(dsn, where):
    """U38's 27 minutes are only reached through the client, which sends nothing once as_of is past 25 minutes
    (client.AS_OF_MAX_AGE): a day started, or a page asked for, in between is a deadline stop, not an error."""
    clock = Clock()
    first = text(ago(2))

    def slow(call):
        if call.resource == "rs_series_day" and call.params.get("day") == first and "cursor" not in call.params:
            clock.advance(24 * 60)  # as_of 12:32, now 12:58:56: past the client's 25 minutes, short of U38's 27
        return None

    world = await open_world(dsn, clock=clock, intercept=slow)
    try:
        world.fake.page_rows = {"rs_series_day": 1} if where == "mid-day" else {}
        await set_state(world.conn, ago(3))
        world.serve(snapshots(span(ago(2), D), two))
        outcome = await world.fold()
        folded = (first,) if where == "between-days" else ()
        assert (outcome.folded, outcome.stopped, outcome.error) == (folded, "deadline", None)
        assert world.series_calls() == [first] and outcome.pending == 3 - len(folded)
        assert (await state(world.conn))[0] == (ago(2) if folded else ago(3))
        assert {point[1] for point in await points(world.conn)} == ({ago(2)} if folded else set())
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_realshorts_own_as_of_refusal_stays_an_error(dsn):
    first = text(ago(1))

    def refuse(call):
        return v2_error(400, "bad_request", reason="as_of") if call.resource == "rs_series_day" and call.params.get("day") == first else None

    world = await open_world(dsn, intercept=refuse)
    try:
        await set_state(world.conn, ago(2))
        world.serve(snapshots(span(ago(1), D), one))
        outcome = await world.fold()
        # RealShort's clock disagrees with ours by minutes: worth an error, unlike the client's own stop.
        assert outcome.folded == () and outcome.stopped is None and "AsOfExpiredError" in outcome.error
    finally:
        await close_world(world)


@pytest.mark.asyncio
async def test_refuses_to_fold_on_a_connection_without_the_mirror_lock(world, dsn):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.series import fold_series

    other = await open_dedicated(dsn)
    try:
        await set_state(world.conn, ago(1))
        world.serve(snapshots([D], one))
        manifest = await world.client.manifest_when_free()
        outcome = await fold_series(other, manifest=manifest, client=world.client, clock=world.clock)
        assert outcome.folded == () and "镜像锁" in outcome.error
        assert world.series_calls() == [] and (await state(world.conn))[0] == ago(1)
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_pages_are_parsed_off_the_event_loop(world, monkeypatch):
    from ggwork_pick.mirror import series

    threads, parse = [], series._page_records

    def recording(*args):
        threads.append(threading.get_ident())
        return parse(*args)

    monkeypatch.setattr(series, "_page_records", recording)
    await set_state(world.conn, ago(1))
    world.serve(snapshots([D], two))
    assert (await world.fold()).folded == (text(D),)
    assert threads and threading.get_ident() not in threads


@pytest.mark.asyncio
async def test_only_plain_statements_reach_the_dedicated_connection(world):
    from ggwork_pick.mirror.series import fold_series

    watched = Watched(world.conn)
    settings = "SELECT current_setting('statement_timeout'), current_setting('lock_timeout'), current_setting('search_path')"
    before = tuple(await world.conn.fetchrow(settings))
    await set_state(world.conn, ago(1))
    world.serve(snapshots([D], two))
    manifest = await world.client.manifest_when_free()
    assert (await fold_series(watched, manifest=manifest, client=world.client, clock=world.clock)).folded == (text(D),)
    assert watched.sent and [query for query in watched.sent if SESSION_SETTING.search(query)] == []
    # Every drama gets a point a day, so each merge rewrites every row: the dead versions go right away.
    assert sum("VACUUM" in query for query in watched.sent) == 1
    assert tuple(await world.conn.fetchrow(settings)) == before
    assert not world.conn.is_in_transaction() and await world.conn.fetchval(TEMP_TABLE) is None
