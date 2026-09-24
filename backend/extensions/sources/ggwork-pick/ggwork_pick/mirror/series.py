"""The curve: pick_mirror.series folded from RealShort's per-day snapshots (plan 3.2, 5.6; brief P2-7, U29, U30, U38).

pick_mirror.series holds one row per drama_id, canonical or not, with three arrays sorted by day (days, revenue_cents,
promoters): every metrics_valid snapshot point rs_series_day returned for that id. The profile page resolves the canonical
id from the version's rs_ids and reads that id's points from the version's as_of day - 90 to its latest_snapshot, as
RealShort's loadDramaDetail does (rs:src/lib/observe/queries.ts:900-927). pick_mirror.series_state.through is the last
folded day; trimmed_before is the day before which no point is kept any more.

Two writers read, modify and write back the arrays, so both hold the mirror lock:
- fold_series, the sync's step 11 on its dedicated connection, after the version retention: at most three days a run,
  the earliest after through and contiguous, and no new day once as_of + 27 minutes has passed (U38). In effect the
  client stops at 25 (client.AS_OF_MAX_AGE): a day or page it refuses past that ends the fold the same way. A run that
  fell back to v1 or timed out on busy has no manifest and does not fold; a degraded run does (U29), except one over
  the size cap (F10: series is {"skipped": "capacity"}). A failure only lands in details_json.series: the published
  version never depends on the curve.
- backfill_series, `python -m ggwork_pick.mirror.series --backfill [N]` (main below; N is 92 when left out): every day
  in snapshotDays from the first as_of day - N on, one transaction a day, so a run that is cut off resumes at the next
  day. as_of is chosen again when it is 25 minutes old, on 400 reason=as_of and on 409, a page's or the manifest's (U30;
  RealShort judges each request against its own now, rs:src/lib/pick/export-v2-page.ts:27-35); a fourth new as_of in a
  row with no day merged ends the run.

A day is fetched page by page into a session temp table, every statement committing on its own (no transaction spans an
HTTP request: deerflow_app's idle_in_transaction_session_timeout is 5 minutes, bootstrap.sql:42), and is merged only when
its row count equals the manifest's snapshotDays entry and no drama_id repeats. The merge is one transaction: new points
replace any old point of the same day (so folding a day twice is harmless), points before the cutoff go, rows left empty
are deleted and series_state moves. The cutoff is the earlier of the oldest published version's as_of day - 90 and the
new through - 92 (93 days kept), and never earlier than the trimmed_before already recorded: those points are gone. On
the first merge it is never earlier than the first day merged either: no point before that day was ever folded.
Every merge rewrites every drama's row, so a VACUUM follows it. Only plain statements run on the dedicated connection,
never a session-level SET.

Through railway ssh (plan 5.6; a dropped session ends the process, and a rerun resumes):
    cd /app/backend && DEER_FLOW_HOME=/data python -m ggwork_pick.mirror.series --backfill 92
or, to let it finish on its own, with only days, row counts and seconds in the log:
    cd /app/backend && DEER_FLOW_HOME=/data nohup python -m ggwork_pick.mirror.series --backfill 92 \\
        > /data/pick/backfill-$(date -u +%Y%m%d).log 2>&1 &
92 (BACKFILL_DAYS) is the default and the recommended N: --backfill alone means it. It is all of snapshotDays (as_of day
- 92 to as_of day, rs:src/lib/pick/export-v2.ts:440-447), every day RealShort keeps. RealShort keeps 91 dates, but
prunes right after a UTC day's first snapshot (rs:src/lib/sync.ts:643-645): before it, as_of day - 91 is still there,
and 90 would leave it out (trimmed_before then says so).
It needs PICK_DATABASE_URL, PGSSLMODE, PICK_REALSHORT_FEED_URL and PICK_REALSHORT_EXPORT_TOKEN; not while a sync runs.
"""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import NoReturn, TextIO

from ggwork_pick.mirror.client import AS_OF_MAX_AGE, SERIES_RESOURCE, FeedClient, Manifest
from ggwork_pick.mirror.connection import MirrorConnectionError, dsn_from_env
from ggwork_pick.mirror.contracts import parse_page
from ggwork_pick.mirror.errors import AsOfExpiredError, ConfigError, ContractError, DriftError, FeedError
from ggwork_pick.mirror.feed_shape import SERIES_DAY_SPAN
from ggwork_pick.mirror.lock import lock_status, mirror_lock

logger = logging.getLogger(__name__)

FOLD_DAYS_PER_RUN = 3  # plan 5.6
# U38: no new day is fetched after as_of + 27 minutes. What takes effect first is the client's own pre-check: FeedClient
# sends nothing once as_of is 25 minutes old (client.AS_OF_MAX_AGE, 5 short of RealShort's 30), and a day or page it
# refuses that way ends the fold as stopped="deadline" (_cut_short). The 27 minutes are only the backstop between days.
FOLD_DEADLINE = timedelta(minutes=27)
SERIES_DAYS = 90  # rs:src/lib/observe/metrics.ts:28: a version's window starts at its as_of day - 90
MIN_KEPT_DAYS = 92  # plan 3.2: the cutoff is never later than through - 92, so 93 days are kept
BACKFILL_AS_OF_RENEW = AS_OF_MAX_AGE  # 25 minutes, 5 short of RealShort's 30
BACKFILL_MAX_RESTARTS = 3  # a fourth new as_of in a row with no day merged (400 as_of, 409, 25 minutes) ends the run
DRIFT_BACKOFF_SECONDS = 90  # plan 5.2 step 3: RealShort is mid-deploy or a source is writing
MAX_LOOKBACK_DAYS = 366
BACKFILL_DAYS = SERIES_DAY_SPAN - 1  # 92: snapshotDays runs from the as_of day back 92 days, all RealShort keeps
STATEMENT_TIMEOUT = 60.0
COPY_TIMEOUT = 120.0
MERGE_TIMEOUT = 300.0
ERROR_TEXT_MAX = 300
# rs_series_day's columns (contracts.RESOURCE_COLUMNS, rs:src/lib/pick/export-v2-map.ts:187-191); a test keeps them equal.
SERIES_COLUMNS = ("drama_id", "revenue_cents", "promoters_cnt")
EXIT_OK, EXIT_LOCKED, EXIT_USAGE, EXIT_FAILED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130
PROG = "python -m ggwork_pick.mirror.series"
USAGE = (
    f"用法：{PROG} --backfill [N]（N 是 1 到 {MAX_LOOKBACK_DAYS} 的整数：只回填 as_of 当天往前 N 天以内的日子；"
    f"缺省 {BACKFILL_DAYS}，也是推荐值：snapshotDays 最早到往前 {BACKFILL_DAYS} 天，{BACKFILL_DAYS} 即全部）"
)
FEED_URL_ENV = "PICK_REALSHORT_FEED_URL"
EXPORT_TOKEN_ENV = "PICK_REALSHORT_EXPORT_TOKEN"
# Present, whatever its value: production sets require; the DSN never carries ssl* parameters (pick_entrypoint.py).
SSL_MODE_ENV = "PGSSLMODE"
REQUIRED_ENV = ("PICK_DATABASE_URL", SSL_MODE_ENV, FEED_URL_ENV, EXPORT_TOKEN_ENV)

_TEMP = "series_new"
_TEMP_COLUMNS = ("drama_id", "day", "rc", "p")
_CREATE_TEMP = f"CREATE TEMP TABLE IF NOT EXISTS {_TEMP} (drama_id text NOT NULL, day date NOT NULL, rc float8 NOT NULL, p integer NOT NULL)"
_EMPTY_TEMP = f"TRUNCATE pg_temp.{_TEMP}"
_DROP_TEMP = f"DROP TABLE IF EXISTS pg_temp.{_TEMP}"
_FORGET_DAY = f"DELETE FROM pg_temp.{_TEMP} WHERE day = $1::date"
_COUNT_DAY = f"SELECT count(*) AS n, count(DISTINCT drama_id) AS ids FROM pg_temp.{_TEMP} WHERE day = $1::date"
_STATE = "SELECT through, trimmed_before FROM pick_mirror.series_state WHERE id = 1"
_SET_STATE = "UPDATE pick_mirror.series_state SET through = $1::date, trimmed_before = $2::date, updated_at = $3::timestamptz WHERE id = 1"
_OLDEST_PUBLISHED = "SELECT min((as_of AT TIME ZONE 'UTC')::date) FROM pick_mirror.versions WHERE status = 'published'"
_BACKEND = "SELECT pg_backend_pid()"
# $1 cutoff, $2 now, $3 the days being merged: only their staged rows are taken (a day cut short or off by a row may still
# be in the temp table), and their old points give way to the new ones.
_UPSERT = f"""
WITH touched AS (SELECT DISTINCT drama_id FROM pg_temp.{_TEMP} WHERE day = ANY($3::date[])),
kept AS (
    SELECT o.drama_id, u.d, u.rc, u.p
    FROM pick_mirror.series AS o
    JOIN touched AS t ON t.drama_id = o.drama_id
    CROSS JOIN LATERAL unnest(o.days, o.revenue_cents, o.promoters) AS u(d, rc, p)
    WHERE u.d >= $1::date AND u.d <> ALL($3::date[])
),
merged AS (
    SELECT drama_id, d, rc, p FROM kept
    UNION ALL
    SELECT drama_id, day, rc, p FROM pg_temp.{_TEMP} WHERE day = ANY($3::date[]) AND day >= $1::date
)
INSERT INTO pick_mirror.series AS s (drama_id, days, revenue_cents, promoters, updated_at)
SELECT drama_id, array_agg(d ORDER BY d), array_agg(rc ORDER BY d), array_agg(p ORDER BY d), $2::timestamptz
FROM merged GROUP BY drama_id
ON CONFLICT (drama_id) DO UPDATE SET
    days = EXCLUDED.days, revenue_cents = EXCLUDED.revenue_cents, promoters = EXCLUDED.promoters, updated_at = EXCLUDED.updated_at
"""
# Every other row whose first (earliest) point is before the cutoff; $1 cutoff, $2 now.
_TRIM = """
UPDATE pick_mirror.series AS s
SET (days, revenue_cents, promoters) = (
        SELECT coalesce(array_agg(u.d ORDER BY u.d), '{}'), coalesce(array_agg(u.rc ORDER BY u.d), '{}'), coalesce(array_agg(u.p ORDER BY u.d), '{}')
        FROM unnest(s.days, s.revenue_cents, s.promoters) AS u(d, rc, p)
        WHERE u.d >= $1::date
    ),
    updated_at = $2::timestamptz
WHERE s.days[1] < $1::date
"""
_DROP_EMPTY = "DELETE FROM pick_mirror.series WHERE cardinality(days) = 0"
# Every drama gets a point a day, so each merge rewrites every row. Measured at 40,000 dramas: 91 daily merges left 3.3 GB
# of dead versions (66 MB live) before autovacuum caught up, past the capacity cap U43 checks; a VACUUM after each merge
# kept it at 131 MB for 3 s in all. SKIP_LOCKED: an autovacuum already at it does the same job, never worth waiting for.
_VACUUM = "VACUUM (SKIP_LOCKED) pick_mirror.series"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _text(day: date | None) -> str | None:
    return day.strftime("%Y-%m-%d") if day is not None else None


def describe(exc: BaseException) -> str:
    """Safe to show every signed-in user: feed errors are written that way (errors.py); a database error gives only its
    class and SQLSTATE, since PostgreSQL's own message can quote a value."""
    if isinstance(exc, FeedError | MirrorConnectionError):
        return f"{type(exc).__name__}：{str(exc)[:ERROR_TEXT_MAX]}"
    sqlstate = getattr(exc, "sqlstate", None)
    return f"{type(exc).__name__}（SQLSTATE {sqlstate}）" if isinstance(sqlstate, str) else type(exc).__name__


def _unexpected(exc: BaseException) -> bool:
    """Neither a feed nor a database error: most likely a bug, worth its traceback in the server log (never in details)."""
    return not isinstance(exc, FeedError | MirrorConnectionError) and not isinstance(getattr(exc, "sqlstate", None), str)


class SeriesCountError(ContractError):
    """A day's rows are not what snapshotDays promised: another count, or a drama_id twice. The day is not merged."""


@dataclass(frozen=True)
class SeriesState:
    through: date | None
    trimmed_before: date | None


def trim_cutoff(*, through: date, oldest_published: date | None, trimmed_before: date | None) -> date:
    """Plan 3.2: the earlier of (oldest published as_of day - 90) and (through - 92); never before what is already trimmed."""
    by_through = through - timedelta(days=MIN_KEPT_DAYS)
    cutoff = by_through if oldest_published is None else min(oldest_published - timedelta(days=SERIES_DAYS), by_through)
    return cutoff if trimmed_before is None else max(cutoff, trimmed_before)


async def read_state(conn) -> SeriesState:
    row = await conn.fetchrow(_STATE, timeout=STATEMENT_TIMEOUT)
    if row is None:
        raise RuntimeError("pick_mirror.series_state 缺少 id=1 这一行")
    return SeriesState(row["through"], row["trimmed_before"])


async def holds_mirror_lock(conn, *, now: datetime) -> bool:
    """Whether this very connection holds the mirror lock (any holder): the arrays are only rewritten under it."""
    status = await lock_status(conn, now=now)
    return bool(status["held"]) and status["holder_pid"] == await conn.fetchval(_BACKEND, timeout=STATEMENT_TIMEOUT)


def _record(row, day: date) -> tuple:
    values = row.model_dump(by_alias=True)
    drama_id, revenue_cents, promoters = (values[name] for name in SERIES_COLUMNS)
    return drama_id, day, float(revenue_cents), promoters


def _page_records(body: dict, day: date) -> tuple[tuple, ...]:
    """One rs_series_day page as temp-table records; pydantic validation is CPU-bound, so this runs in a worker thread."""
    return tuple(_record(row, day) for row in parse_page(SERIES_RESOURCE, body))


def _gaps(previous: date | None, days: Sequence[date]) -> tuple[str, ...]:
    """Calendar days between previous and each folded day that RealShort has no snapshot for."""
    marks = [previous, *days] if previous is not None else list(days)
    return tuple(_text(start + timedelta(days=n)) for start, end in zip(marks, marks[1:]) for n in range(1, (end - start).days))


async def _create_temp(conn) -> None:
    await conn.execute(_CREATE_TEMP, timeout=STATEMENT_TIMEOUT)
    await conn.execute(_EMPTY_TEMP, timeout=STATEMENT_TIMEOUT)


async def _drop_temp(conn) -> None:
    try:
        await conn.execute(_DROP_TEMP, timeout=STATEMENT_TIMEOUT)
    except Exception as exc:
        # Session-local: it goes with the connection, which the caller closes. Not worth hiding the first error for.
        logger.warning("[pick-mirror] dropping the curve temp table failed: %s", describe(exc))


async def _stage_day(conn, client: FeedClient, manifest: Manifest, day: str) -> int:
    """Every page of rs_series_day for `day` into the temp table, then the count check; returns the day's rows."""
    on = date.fromisoformat(day)
    async with aclosing(client.pages(SERIES_RESOURCE, manifest=manifest, day=day)) as pages:
        async for page in pages:
            records = await asyncio.to_thread(_page_records, page.body, on)
            if records:
                await conn.copy_records_to_table(_TEMP, schema_name="pg_temp", columns=_TEMP_COLUMNS, records=records, timeout=COPY_TIMEOUT)
    counted = await conn.fetchrow(_COUNT_DAY, on, timeout=STATEMENT_TIMEOUT)
    expected = manifest.snapshot_days[day]
    if counted["n"] != expected or counted["ids"] != counted["n"]:
        message = f"{SERIES_RESOURCE} {day} 有 {counted['n']} 行（不同的 drama_id {counted['ids']} 个），manifest 的 snapshotDays 记 {expected} 行"
        raise SeriesCountError(message, resource=SERIES_RESOURCE)
    return counted["n"]


def _floor(before: SeriesState, days: Sequence[date]) -> date | None:
    """What the cutoff may not go below: what is already trimmed, and on the first merge (through NULL: the backfill's
    first day) that day too, since no point before it was ever folded. trimmed_before then tells the profile page's
    banner the truth (plan 3.2, 373): a window that starts before it is missing points."""
    if before.through is not None:
        return before.trimmed_before
    return max(day for day in (before.trimmed_before, min(days)) if day is not None)


async def _merge(conn, *, days: Sequence[date], now: datetime) -> SeriesState:
    """The staged days into pick_mirror.series and series_state, in one transaction; then VACUUM the rewritten rows."""
    async with conn.transaction():
        before = await read_state(conn)
        oldest = await conn.fetchval(_OLDEST_PUBLISHED, timeout=STATEMENT_TIMEOUT)
        through = max(days)
        cutoff = trim_cutoff(through=through, oldest_published=oldest, trimmed_before=_floor(before, days))
        await conn.execute(_UPSERT, cutoff, now, list(days), timeout=MERGE_TIMEOUT)
        await conn.execute(_TRIM, cutoff, now, timeout=MERGE_TIMEOUT)
        await conn.execute(_DROP_EMPTY, timeout=MERGE_TIMEOUT)
        await conn.execute(_SET_STATE, through, cutoff, now, timeout=STATEMENT_TIMEOUT)
    await _vacuum(conn)
    return SeriesState(through, cutoff)


async def _vacuum(conn) -> None:
    """After the merge committed (VACUUM refuses to run in a transaction); a failure costs disk, not the merged days."""
    try:
        await conn.execute(_VACUUM, timeout=MERGE_TIMEOUT)
    except Exception as exc:
        logger.warning("[pick-mirror] vacuuming pick_mirror.series failed: %s", describe(exc))


# ---------------------------------------------------------------- the regular fold


@dataclass(frozen=True)
class FoldOutcome:
    """What one fold did; details() is the run's details_json.series (U37: days, counts and safe error text only)."""

    folded: tuple[str, ...] = ()
    rows: tuple[int, ...] = ()
    missing: tuple[str, ...] = ()
    pending: int = 0
    through: str | None = None
    trimmed_before: str | None = None
    needs_backfill: bool = False
    stopped: str | None = None
    error: str | None = None

    def details(self) -> dict:
        return {
            "folded": list(self.folded),
            "rows": dict(zip(self.folded, self.rows, strict=True)),
            "missing": list(self.missing),
            "pending": self.pending,
            "through": self.through,
            "trimmed_before": self.trimmed_before,
            "needs_backfill": self.needs_backfill,
            "stopped": self.stopped,
            "error": self.error,
        }


@dataclass(frozen=True)
class _Staged:
    days: tuple[str, ...] = ()
    rows: tuple[int, ...] = ()
    stopped: str | None = None
    error: str | None = None


async def fold_series(
    conn,
    *,
    manifest: Manifest,
    client: FeedClient,
    clock: Callable[[], datetime] = _utc_now,
    max_days: int = FOLD_DAYS_PER_RUN,
    deadline: timedelta = FOLD_DEADLINE,
) -> FoldOutcome:
    """The sync's step 11 on its dedicated connection, which must hold the mirror lock. Never raises but on cancellation:
    whatever goes wrong is in the outcome's error, and whatever was merged before it stays merged."""
    try:
        return await _fold(conn, manifest=manifest, client=client, clock=clock, max_days=max_days, deadline=deadline)
    except Exception as exc:
        logger.warning("[pick-mirror] the curve fold failed: %s", describe(exc), exc_info=_unexpected(exc))
        return FoldOutcome(error=f"曲线折叠失败：{describe(exc)}")


async def _fold(conn, *, manifest: Manifest, client: FeedClient, clock, max_days: int, deadline: timedelta) -> FoldOutcome:
    if not await holds_mirror_lock(conn, now=clock()):
        return FoldOutcome(error="本连接没有持有镜像锁，不折叠曲线")
    before = await read_state(conn)
    if before.through is None:
        return FoldOutcome(needs_backfill=True, pending=len(manifest.snapshot_days))
    todo = tuple(day for day in sorted(manifest.snapshot_days) if date.fromisoformat(day) > before.through)
    if not todo:
        return FoldOutcome(through=_text(before.through), trimmed_before=_text(before.trimmed_before))
    await _create_temp(conn)
    try:
        staged = await _stage_days(conn, client=client, manifest=manifest, days=todo[:max_days], clock=clock, deadline=manifest.as_of + deadline)
        folded = [date.fromisoformat(day) for day in staged.days]
        after = await _merge(conn, days=folded, now=clock()) if folded else before
    finally:
        await _drop_temp(conn)
    return FoldOutcome(
        folded=staged.days,
        rows=staged.rows,
        missing=_gaps(before.through, folded),
        pending=len(todo) - len(staged.days),
        through=_text(after.through),
        trimmed_before=_text(after.trimmed_before),
        stopped=staged.stopped,
        error=staged.error,
    )


async def _stage_days(conn, *, client: FeedClient, manifest: Manifest, days: Sequence[str], clock, deadline: datetime) -> _Staged:
    """Day after day until one fails or the deadline passes: through may never jump over a day that was not merged. Rows
    of the day that failed stay in the temp table; the merge takes only the days listed, and the table goes with the fold."""
    staged = _Staged()
    for day in days:
        if clock() >= deadline:
            return replace(staged, stopped="deadline")
        try:
            rows = await _stage_day(conn, client, manifest, day)
        except Exception as exc:
            return _cut_short(staged, day, exc)
        staged = replace(staged, days=(*staged.days, day), rows=(*staged.rows, rows))
    return staged


def _cut_short(staged: _Staged, day: str, exc: Exception) -> _Staged:
    """The client sends nothing once as_of is 25 minutes old (client.AS_OF_MAX_AGE), before U38's 27: a day started, or a
    page asked for, past that is the same deadline stop. RealShort's own 400 as_of (a status) says the clocks disagree."""
    if isinstance(exc, AsOfExpiredError) and exc.status is None:
        logger.info("[pick-mirror] curve day %s not folded: as_of is past the client's %s", day, AS_OF_MAX_AGE)
        return replace(staged, stopped="deadline")
    logger.warning("[pick-mirror] curve day %s not folded: %s", day, describe(exc), exc_info=_unexpected(exc))
    return replace(staged, error=f"{day} 未合并：{describe(exc)}")


# ---------------------------------------------------------------- the backfill


@dataclass(frozen=True)
class BackfillOutcome:
    merged: tuple[str, ...] = ()
    rows: tuple[int, ...] = ()
    missing: tuple[str, ...] = ()
    renewals: int = 0
    through: str | None = None
    trimmed_before: str | None = None


Report = Callable[[str, int, float], None]


async def backfill_series(
    conn,
    *,
    client: FeedClient,
    lookback_days: int = BACKFILL_DAYS,
    clock: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    report: Report | None = None,
) -> BackfillOutcome:
    """Every day of snapshotDays after through, from the first as_of day - lookback_days on, one transaction a day.

    conn must hold the mirror lock. report(day, rows, seconds) is called after each merged day. A day whose rows do not
    match, a fourth new as_of in a row with no day merged, a busy timeout or any other failure raises; the days merged
    before it stay, so a second run goes on from there.
    """
    if not await holds_mirror_lock(conn, now=clock()):
        raise RuntimeError("本连接没有持有镜像锁，不回填曲线")
    manifest, _ = await _new_manifest(client, sleep=sleep)
    start = manifest.as_of.astimezone(UTC).date() - timedelta(days=lookback_days)
    await _create_temp(conn)
    try:
        return await _backfill_days(conn, client=client, manifest=manifest, start=start, clock=clock, sleep=sleep, report=report)
    finally:
        await _drop_temp(conn)


def _backfill_todo(manifest: Manifest, through: date | None, start: date) -> tuple[str, ...]:
    days = (day for day in sorted(manifest.snapshot_days) if date.fromisoformat(day) >= start)
    return tuple(day for day in days if through is None or date.fromisoformat(day) > through)


async def _backfill_days(conn, *, client: FeedClient, manifest: Manifest, start: date, clock, sleep, report: Report | None) -> BackfillOutcome:
    state = await read_state(conn)
    outcome = BackfillOutcome(through=_text(state.through), trimmed_before=_text(state.trimmed_before))
    stalls = 0  # new as_of chosen since the last merged day
    while todo := _backfill_todo(manifest, state.through, start):
        day, began = todo[0], clock()
        staged = await _attempt(conn, client, manifest, day, now=began)
        if isinstance(staged, FeedError):
            manifest, stalls = await _new_manifest(client, sleep=sleep, cause=staged, stalls=_stall(stalls, staged))
            outcome = replace(outcome, renewals=outcome.renewals + 1)
            continue
        previous, state = state.through, await _merge(conn, days=[date.fromisoformat(day)], now=clock())
        await conn.execute(_EMPTY_TEMP, timeout=STATEMENT_TIMEOUT)
        stalls, outcome = 0, _merged(outcome, day, staged, previous=previous, state=state)
        if report is not None:
            report(day, staged, (clock() - began).total_seconds())
    return outcome


async def _attempt(conn, client: FeedClient, manifest: Manifest, day: str, *, now: datetime) -> int | FeedError:
    """The day staged and counted (its rows), or why it needs a new as_of first: as_of is 25 minutes old, or a request got
    400 as_of or 409. The rows of an attempt cut short are forgotten, so the day's next attempt cannot double them."""
    if now - manifest.as_of >= BACKFILL_AS_OF_RENEW:
        minutes = BACKFILL_AS_OF_RENEW.total_seconds() / 60
        return AsOfExpiredError(f"{day} 没有开拉：as_of 选定后已满 {minutes:g} 分钟（换了 as_of 仍如此，说明回填与拉取的时钟不一致）", resource=SERIES_RESOURCE)
    try:
        return await _stage_day(conn, client, manifest, day)
    except (AsOfExpiredError, DriftError) as exc:
        await conn.execute(_FORGET_DAY, date.fromisoformat(day), timeout=STATEMENT_TIMEOUT)
        return exc


def _stall(stalls: int, cause: FeedError) -> int:
    """One more new as_of with no day merged since the last; a fourth in a row raises its cause, and the run ends."""
    if stalls + 1 > BACKFILL_MAX_RESTARTS:
        raise cause
    logger.warning("[pick-mirror] the backfill chooses a new as_of: %s", type(cause).__name__)
    return stalls + 1


async def _new_manifest(client: FeedClient, *, sleep, cause: FeedError | None = None, stalls: int = 0) -> tuple[Manifest, int]:
    """A new as_of and its manifest; the client waits out busy. Drift first waits 90 seconds (plan 5.2 step 3), and a 409
    on the manifest itself, a write landing while RealShort reads it (rs:src/lib/pick/export-v2.ts:497-504), is drift
    too: it counts as one more new as_of."""
    while True:
        if isinstance(cause, DriftError):
            await sleep(DRIFT_BACKOFF_SECONDS)
        try:
            return await client.manifest_when_free(), stalls
        except DriftError as exc:
            cause, stalls = exc, _stall(stalls, exc)


def _merged(outcome: BackfillOutcome, day: str, rows: int, *, previous: date | None, state: SeriesState) -> BackfillOutcome:
    return replace(
        outcome,
        merged=(*outcome.merged, day),
        rows=(*outcome.rows, rows),
        missing=(*outcome.missing, *_gaps(previous, [date.fromisoformat(day)])),
        through=_text(state.through),
        trimmed_before=_text(state.trimmed_before),
    )


# ---------------------------------------------------------------- the command


def _say(stream: TextIO, line: str) -> None:
    print(line, file=stream, flush=True)


def _day_lines(out: TextIO) -> Report:
    def report(day: str, rows: int, seconds: float) -> None:
        _say(out, f"{day} 行数 {rows} 耗时 {seconds:.1f} 秒")

    return report


def _summary(outcome: BackfillOutcome) -> str:
    missing = f"：{'、'.join(outcome.missing)}" if outcome.missing else ""
    return (
        f"回填完成：合并 {len(outcome.merged)} 天，through {outcome.through or '无'}，trimmed_before {outcome.trimmed_before or '无'}，"
        f"缺天 {len(outcome.missing)} 个{missing}，换 as_of {outcome.renewals} 次"
    )


async def run_backfill(
    *,
    dsn: str,
    client: FeedClient,
    lookback_days: int = BACKFILL_DAYS,
    clock: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """The command's body: take the mirror lock as backfill on a dedicated connection, backfill, report. Lines name days,
    row counts and seconds only; errors their class and safe text. Returns the exit status."""
    out, err = out or sys.stdout, err or sys.stderr
    try:
        async with mirror_lock(dsn, holder="backfill", clock=clock) as conn:
            if conn is None:
                _say(err, "镜像锁被占用：同步正在进行（或 cleanup 在跑）。回填退出，没有写入任何行；等同步结束再跑")
                return EXIT_LOCKED
            outcome = await backfill_series(conn, client=client, lookback_days=lookback_days, clock=clock, sleep=sleep, report=_day_lines(out))
    except Exception as exc:
        if _unexpected(exc):
            logger.warning("[pick-mirror] the backfill stopped on an unexpected error", exc_info=True)
        _say(err, f"回填中止：{describe(exc)}。已合并的日子保留，重跑会从下一个没合并的日子续跑")
        return EXIT_FAILED
    _say(out, _summary(outcome))
    return EXIT_OK


class UsageError(Exception):
    """A bad command line or a missing variable; the message names flags and variables, never a value."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's own message can quote what was typed; the fixed usage line says all that is needed.
        raise UsageError(USAGE)


@dataclass(frozen=True)
class _Settings:
    dsn: str = field(repr=False)
    feed_url: str = field(repr=False)
    export_token: str = field(repr=False)


def _lookback(argv: Sequence[str] | None) -> int:
    parser = _Parser(prog=PROG, description="回填选剧镜像的曲线（pick_mirror.series）", add_help=True)
    parser.add_argument("--backfill", required=True, nargs="?", const=BACKFILL_DAYS, type=int, metavar="N", help=USAGE)
    days = parser.parse_args(argv).backfill
    if not 1 <= days <= MAX_LOOKBACK_DAYS:
        raise UsageError(USAGE)
    return days


def _settings(environ: Mapping[str, str]) -> _Settings:
    missing = [name for name in REQUIRED_ENV if not environ.get(name, "").strip()]
    if missing:
        raise UsageError(f"缺少环境变量 {'、'.join(missing)}")
    try:
        dsn = dsn_from_env(environ)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    return _Settings(dsn=dsn, feed_url=environ[FEED_URL_ENV].strip(), export_token=environ[EXPORT_TOKEN_ENV].strip())


async def _command(settings: _Settings, lookback_days: int) -> int:
    try:
        client = FeedClient(base_url=settings.feed_url, export_token=settings.export_token)
    except ConfigError as exc:
        _say(sys.stderr, f"{PROG}: {exc}")
        return EXIT_USAGE
    async with client:
        return await run_backfill(dsn=settings.dsn, client=client, lookback_days=lookback_days)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`python -m ggwork_pick.mirror.series --backfill [N]` (N is 92 when left out): 0 done, 1 the lock is taken, 2 usage
    or a missing variable, 3 the backfill stopped (rerun it: it resumes), 130 interrupted. Reads no app config and no
    DEER_FLOW_* (plan 6.5)."""
    try:
        lookback_days = _lookback(argv)
        settings = _settings(os.environ if env is None else env)
    except UsageError as exc:
        _say(sys.stderr, f"{PROG}: {exc}")
        return EXIT_USAGE
    try:
        return asyncio.run(_command(settings, lookback_days))
    except KeyboardInterrupt:
        _say(sys.stderr, f"{PROG}: 已中断。已合并的日子保留，重跑会续跑")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
