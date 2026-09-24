"""Which mirror versions stay, and the cleanup of what a dead run left behind (plan 3.6, 5.2 step 10; U23, U24, U41).

Both run on the dedicated connection while it holds the mirror lock: retention under the sync's hold, the leftover
cleanup under the sync's or the cleanup command's. Each first checks that this very connection holds the lock as one
of those holders; otherwise it does nothing at all and says so in its report.

Retention keeps, among the published versions ordered by published_at DESC, id DESC:
- the latest three (the current one and the two before it);
- any superseded within the last hour, so someone paging through it does not meet the DROP;
- any a candidate snapshot recorded in the last seven days, by version number, not by batch: after deduplication many
  versions share one batch, and counting by batch would keep them all.
Over ten, only versions kept for a reference alone give way, oldest by published_at, id first; the latest three and the
grace hour never do, even when that leaves more than ten (U23). A card whose version is dropped still names it.
ggwp batches are not touched here: repository.prune_shared keeps the ones paired with published versions (U25).

Every drop is its own transaction: BEGIN; SET LOCAL lock_timeout and statement_timeout; DROP SCHEMA ... CASCADE; the
versions row; COMMIT. lock_timeout counts each lock on its own and the DROP takes one per table, so readers letting go
of one table after another could hold it far longer; statement_timeout, set to the same, bounds the DROP as a whole.
Whether the wait ends in the lock timeout (55P03), the statement timeout (57014) or a deadlock with a reader whose one
statement joins two of the version's tables (40P01), a reader was in the way: that version is skipped until next time
and the rest go on. Readers arriving meanwhile queue behind the DROP for as long (U24, within the reader's 8-second
statement_timeout). Any other error is raised. SET LOCAL ends with the transaction, so the connection keeps its
defaults. Between drops the connection is idle: nothing uncommitted is left for the ORM's publish transaction to wait on.

Only names of the writer's own shape, pickm_v and six ASCII digits, are ever dropped; retention drops only schemas
registered in versions, and the leftover cleanup only pickm_v* schemas no building or published version claims.
"""

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ggwork_pick.mirror.lock import lock_status
from ggwork_pick.repository import stamp

logger = logging.getLogger(__name__)

KEEP_LATEST = 3
VERSION_CAP = 10
SUPERSEDED_GRACE = timedelta(hours=1)
REFERENCED_WITHIN = timedelta(days=7)
DROP_LOCK_TIMEOUT = timedelta(seconds=5)
# Whose hold each may run under (U14 holder names).
RETENTION_HOLDERS = ("sync",)
CLEANUP_HOLDERS = ("sync", "cleanup")
LOCK_NOT_HELD = "lock_not_held"
LEFTOVER_ERROR = "遗留的 building 版本：上次运行没有收尾"
# SQLSTATEs that mean a reader was in the DROP's way: lock_not_available (the lock_timeout ran out), query_canceled
# (the statement_timeout ran out), deadlock_detected (a reader's statement took the version's tables in the other order).
_READER_IN_THE_WAY = frozenset({"55P03", "57014", "40P01"})
_OLDEST = datetime.min.replace(tzinfo=UTC)
# The writer's schema names, as migration 0006's CHECK has them. [0-9], not \d: Python's \d takes any Unicode digit.
# P2-3 合并后改用 versions.py 的 schema 名校验。
_VERSION_SCHEMA = re.compile(r"pickm_v[0-9]{6}")

_PUBLISHED = "SELECT id, schema_name, published_at, superseded_at FROM pick_mirror.versions WHERE status = 'published' ORDER BY published_at DESC, id DESC"
# created_at is a stamp() string, compared as text like repository.prune_shared.
_REFERENCED = "SELECT DISTINCT mirror_version FROM ggwp_candidate_sets WHERE mirror_version IS NOT NULL AND created_at >= $1"
_MARK_DROPPED = "UPDATE pick_mirror.versions SET status = 'dropped', dropped_at = $2 WHERE id = $1 AND status = 'published'"
_FAIL_BUILDING = "UPDATE pick_mirror.versions SET status = 'failed', error = $1 WHERE status = 'building' RETURNING id"
# Schemas of the writer's shape no building or published version claims, and failed versions not yet cleared.
_ORPHANS = (
    "SELECT n.nspname::text AS schema_name FROM pg_namespace AS n WHERE n.nspname ~ '^pickm_v[0-9]{6}$'"
    " AND NOT EXISTS (SELECT 1 FROM pick_mirror.versions AS v WHERE v.schema_name = n.nspname AND v.status IN ('building', 'published'))"
    " UNION SELECT v.schema_name FROM pick_mirror.versions AS v WHERE v.status = 'failed' AND v.dropped_at IS NULL"
)
_MARK_CLEARED = "UPDATE pick_mirror.versions SET dropped_at = $2 WHERE schema_name = $1 AND status = 'failed' AND dropped_at IS NULL"


@dataclass(frozen=True)
class PublishedVersion:
    id: int
    schema_name: str
    published_at: datetime | None
    superseded_at: datetime | None


@dataclass(frozen=True)
class RetentionPlan:
    keep: tuple[int, ...]  # newest first
    drop: tuple[PublishedVersion, ...]  # oldest first
    over_cap: int


@dataclass(frozen=True)
class RetentionReport:
    """What retention did; details() is safe for the run's details_json (version numbers and codes only)."""

    skipped: str | None = None
    kept: tuple[int, ...] = ()
    dropped: tuple[int, ...] = ()
    lock_busy: tuple[int, ...] = ()
    over_cap: int = 0

    def details(self) -> dict:
        return {"skipped": self.skipped, "kept": list(self.kept), "dropped": list(self.dropped), "lock_busy": list(self.lock_busy), "over_cap": self.over_cap}


@dataclass(frozen=True)
class LeftoverReport:
    """What the leftover cleanup did; details() is safe for details_json (version numbers and pickm_v names only)."""

    skipped: str | None = None
    failed_building: tuple[int, ...] = ()
    dropped_schemas: tuple[str, ...] = ()
    lock_busy: tuple[str, ...] = ()

    def details(self) -> dict:
        return {
            "skipped": self.skipped,
            "failed_building": list(self.failed_building),
            "dropped_schemas": list(self.dropped_schemas),
            "lock_busy": list(self.lock_busy),
        }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def plan_retention(
    published: Sequence[PublishedVersion],
    referenced: frozenset[int],
    *,
    now: datetime,
    keep_latest: int = KEEP_LATEST,
    cap: int = VERSION_CAP,
    grace: timedelta = SUPERSEDED_GRACE,
) -> RetentionPlan:
    """Which of `published` stay, given the version numbers cards recorded.

    `published` is put newest first here, published_at DESC, id DESC as the query orders it, a missing published_at
    first as PostgreSQL's DESC has it.
    """
    _require_aware(now)
    _require_limits(keep_latest=keep_latest, cap=cap, windows=(grace,))
    ordered = tuple(sorted(published, key=_newest_first, reverse=True))
    protected = frozenset(v.id for v in ordered[:keep_latest]) | frozenset(v.id for v in ordered if _in_grace(v, now, grace))
    cited_oldest_first = tuple(v.id for v in reversed(ordered) if v.id in referenced and v.id not in protected)
    room = max(cap - len(protected), 0)
    evicted = frozenset(cited_oldest_first[: max(len(cited_oldest_first) - room, 0)])
    kept_ids = protected | (frozenset(cited_oldest_first) - evicted)
    keep = tuple(v.id for v in ordered if v.id in kept_ids)
    drop = tuple(v for v in reversed(ordered) if v.id not in kept_ids)
    return RetentionPlan(keep=keep, drop=drop, over_cap=max(len(keep) - cap, 0))


async def prune_versions(
    conn,
    *,
    clock: Callable[[], datetime] = _utc_now,
    keep_latest: int = KEEP_LATEST,
    cap: int = VERSION_CAP,
    grace: timedelta = SUPERSEDED_GRACE,
    referenced_within: timedelta = REFERENCED_WITHIN,
    lock_timeout: timedelta = DROP_LOCK_TIMEOUT,
) -> RetentionReport:
    """Drop the published versions retention no longer keeps, each in its own transaction (plan 3.6).

    `conn` is the sync's dedicated asyncpg connection, outside any transaction and holding the mirror lock as sync;
    otherwise nothing is read or dropped and the report says lock_not_held. A version whose DROP a reader held up past
    `lock_timeout` (or deadlocked with) stays published and is listed in lock_busy.
    """
    now = clock()
    _require_aware(now)
    _require_limits(keep_latest=keep_latest, cap=cap, windows=(grace, referenced_within))
    timeout_ms = _millis(lock_timeout)
    _require_autocommit(conn)
    if not await _holds_mirror_lock(conn, now=now, holders=RETENTION_HOLDERS):
        logger.warning("[pick-mirror] retention skipped: this connection does not hold the mirror lock for the sync")
        return RetentionReport(skipped=LOCK_NOT_HELD)
    published = tuple(PublishedVersion(**dict(row)) for row in await conn.fetch(_PUBLISHED))
    referenced = frozenset(row["mirror_version"] for row in await conn.fetch(_REFERENCED, stamp(now - referenced_within)))
    plan = plan_retention(published, referenced, now=now, keep_latest=keep_latest, cap=cap, grace=grace)
    # Every name is checked before the first DROP: a bad one stops retention with nothing half done.
    victims = tuple((version, _quoted_schema(version.schema_name)) for version in plan.drop)
    outcomes = tuple([(version.id, await _drop_published(conn, version.id, quoted, timeout_ms=timeout_ms, clock=clock)) for version, quoted in victims])
    report = RetentionReport(
        kept=plan.keep,
        dropped=tuple(n for n, done in outcomes if done),
        lock_busy=tuple(n for n, done in outcomes if not done),
        over_cap=plan.over_cap,
    )
    _log_retention(report, cap=cap)
    return report


async def clean_leftover_versions(conn, *, clock: Callable[[], datetime] = _utc_now, lock_timeout: timedelta = DROP_LOCK_TIMEOUT) -> LeftoverReport:
    """Fail the building versions a dead run left, then drop every pickm_v* schema no building or published version claims.

    The schema half of U41; importing batches and blobs are the staging code's. Call it under the sync's or the cleanup
    command's hold before this hold creates a version of its own: every building version is taken for a leftover. A
    failed version's dropped_at is written once its schema is gone (or was never created). Without the lock on this
    connection nothing changes and the report says lock_not_held.
    """
    now = clock()
    _require_aware(now)
    timeout_ms = _millis(lock_timeout)
    _require_autocommit(conn)
    if not await _holds_mirror_lock(conn, now=now, holders=CLEANUP_HOLDERS):
        logger.warning("[pick-mirror] leftover cleanup skipped: this connection does not hold the mirror lock for the sync or cleanup")
        return LeftoverReport(skipped=LOCK_NOT_HELD)
    failed = tuple(sorted(row["id"] for row in await conn.fetch(_FAIL_BUILDING, LEFTOVER_ERROR)))
    names = tuple(sorted(name for name in {row["schema_name"] for row in await conn.fetch(_ORPHANS)} if _VERSION_SCHEMA.fullmatch(name)))
    outcomes = tuple([(name, await _drop_leftover(conn, name, timeout_ms=timeout_ms, clock=clock)) for name in names])
    report = LeftoverReport(
        failed_building=failed,
        dropped_schemas=tuple(name for name, done in outcomes if done),
        lock_busy=tuple(name for name, done in outcomes if not done),
    )
    if report.failed_building or report.dropped_schemas or report.lock_busy:
        logger.warning("[pick-mirror] leftover cleanup: %s", report.details())
    return report


async def _drop_published(conn, version_id: int, quoted: str, *, timeout_ms: int, clock: Callable[[], datetime]) -> bool:
    async def mark() -> None:
        # Under the mirror lock nothing else changes versions; if the row moved anyway, the DROP must not stand.
        if await conn.execute(_MARK_DROPPED, version_id, clock()) != "UPDATE 1":
            raise RuntimeError(f"版本 {version_id} 在保留途中不再是 published，本次删除已回滚")

    return await _drop_in_own_transaction(conn, quoted, timeout_ms=timeout_ms, then=mark)


async def _drop_leftover(conn, name: str, *, timeout_ms: int, clock: Callable[[], datetime]) -> bool:
    async def mark() -> None:
        await conn.execute(_MARK_CLEARED, name, clock())

    return await _drop_in_own_transaction(conn, _quoted_schema(name), timeout_ms=timeout_ms, then=mark)


async def _drop_in_own_transaction(conn, quoted: str, *, timeout_ms: int, then: Callable[[], Awaitable[None]]) -> bool:
    """DROP SCHEMA and `then` in one transaction; False, with both rolled back, when a reader was in the way."""
    try:
        async with conn.transaction():
            # SET LOCAL: gone at COMMIT or ROLLBACK, so the connection keeps its defaults.
            await conn.execute(f"SET LOCAL lock_timeout = '{timeout_ms}ms'")
            await conn.execute(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
            await conn.execute(f"DROP SCHEMA IF EXISTS {quoted} CASCADE")
            await then()
    except Exception as exc:
        if getattr(exc, "sqlstate", None) in _READER_IN_THE_WAY:
            return False
        raise
    return True


async def _holds_mirror_lock(conn, *, now: datetime, holders: tuple[str, ...]) -> bool:
    """This connection's backend holds the mirror lock, taken as one of `holders`."""
    status = await lock_status(conn, now=now)
    if not status["held"] or status["holder"] not in holders:
        return False
    return status["holder_pid"] == await conn.fetchval("SELECT pg_backend_pid()")


def _log_retention(report: RetentionReport, *, cap: int) -> None:
    if report.lock_busy:
        logger.warning("[pick-mirror] retention kept versions %s for now: a reader was in the way of their DROP", list(report.lock_busy))
    if report.over_cap:
        logger.warning("[pick-mirror] retention keeps %d versions, %d over the cap of %d (U23)", len(report.kept), report.over_cap, cap)


def _quoted_schema(name: object) -> str:
    # P2-3 合并后改用 versions.py 的 schema_for 与名字校验。
    if not isinstance(name, str) or not _VERSION_SCHEMA.fullmatch(name):
        raise ValueError("版本 schema 名必须是 pickm_v 加 6 位数字")
    return f'"{name}"'


def _newest_first(version: PublishedVersion) -> tuple[bool, datetime, int]:
    return (version.published_at is None, version.published_at or _OLDEST, version.id)


def _in_grace(version: PublishedVersion, now: datetime, grace: timedelta) -> bool:
    return version.superseded_at is not None and version.superseded_at > now - grace


def _millis(lock_timeout: timedelta) -> int:
    if not isinstance(lock_timeout, timedelta) or lock_timeout < timedelta(milliseconds=1):
        raise ValueError("lock_timeout 至少 1 毫秒")
    return lock_timeout // timedelta(milliseconds=1)


def _require_limits(*, keep_latest: int, cap: int, windows: tuple[timedelta, ...]) -> None:
    if keep_latest < 1 or cap < keep_latest:
        raise ValueError("keep_latest 至少为 1，cap 不能小于 keep_latest")
    if any(not isinstance(window, timedelta) or window < timedelta(0) for window in windows):
        raise ValueError("grace 与 referenced_within 必须是非负的时间段")


def _require_aware(now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须带时区")


def _require_autocommit(conn) -> None:
    if conn.is_in_transaction():
        raise RuntimeError("镜像专用连接正处在事务里：保留与遗留清理的每一步都要自动提交")
