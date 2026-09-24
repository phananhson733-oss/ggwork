"""A mirror version's life before publishing: its pick_mirror.versions row and its pickm_vNNNNNN schema (plan 3.2, 3.3, 3.5).

create_version draws the id, records the row as building and creates the empty tables of ddl.sql; mark_failed records a
failure and drops the schema. Publishing (the GRANTs and the status flip) is P2-5a's, on the ORM connection.

Everything runs on the dedicated asyncpg connection (connection.open_dedicated), one autocommitted step at a time: only
the CREATE SCHEMA + CREATE TABLE step and the DROP SCHEMA step are transactions, and each commits before the call returns.
The ORM's publish transaction runs GRANT with a 30-second command timeout; an uncommitted DDL here would hold it up. Every
statement is given its own timeout, BEGIN and COMMIT included (sent here, not by asyncpg's transaction(), which gives
them none), and the only settings ever sent are SET LOCAL.

Every DROP of a version schema, here and in retention (P2-6), is drop_in_transaction: BEGIN; SET LOCAL lock_timeout and
SET LOCAL statement_timeout to one budget; DROP SCHEMA IF EXISTS ... CASCADE; the versions row that records it; COMMIT.

A schema name reaches SQL only after check_schema_name: nothing else is ever substituted into a statement. Error texts
name the step, the exception class and the SQLSTATE, never a value (plan 5.5; sync._safe_error).
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import resources
from types import MappingProxyType

from ggwork_pick.contracts import storable
from ggwork_pick.mirror.contracts import COUNTED_RESOURCES, RESOURCE_COLUMNS, Column

logger = logging.getLogger(__name__)

# ASCII digits only: Python's \d also takes other scripts' digits, and $ would let a trailing newline through.
SCHEMA_NAME = re.compile(r"pickm_v[0-9]{6}")
SCHEMA_PLACEHOLDER = "__SCHEMA__"
DDL_FILE = "ddl.sql"
MAX_VERSION_ID = 999_999
# The contract type -> the column type (plan 3.3; U3: int is integer, float is double precision).
SQL_TYPES = MappingProxyType(
    {"text": "text", "day": "text", "ts": "timestamptz", "bool": "boolean", "int": "integer", "float": "double precision", "text[]": "text[]", "json": "jsonb"}
)
META_TABLE = "meta"
META_COLUMNS = (Column("key", "text", False), Column("value", "json", False))
# The eight counted resources and meta; rs_series_day folds into pick_mirror.series (P2-7), not into a version.
MIRROR_TABLES = (*COUNTED_RESOURCES, META_TABLE)
TABLE_COLUMNS = MappingProxyType({**{table: RESOURCE_COLUMNS[table] for table in COUNTED_RESOURCES}, META_TABLE: META_COLUMNS})

# Seconds. Row and sequence statements are single-row; the DDL creates nine empty tables.
STATEMENT_TIMEOUT = 30
DDL_TIMEOUT = 60
DROP_TIMEOUT = 60
# A DROP's budget, sent as both SET LOCAL lock_timeout and SET LOCAL statement_timeout: readers queue behind it for at
# most this long (U24; their own statement_timeout is 8 s). lock_timeout alone would not do: it limits each lock wait on
# its own, and the DROP takes the nine tables' locks one after another; statement_timeout bounds the DROP as a whole.
DROP_STATEMENT_TIMEOUT_MS = 5000
# A DROP that gave up behind readers, nothing dropped: its statement_timeout (57014), its lock_timeout (55P03), or a
# deadlock with a reader whose one statement took two of the version's tables in the other order (40P01).
DROP_BLOCKED_SQLSTATES = frozenset({"57014", "55P03", "40P01"})
ERROR_MAX_LENGTH = 500

_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_NEXT_ID = "SELECT nextval(pg_get_serial_sequence('pick_mirror.versions', 'id'))"
_INSERT = (
    "INSERT INTO pick_mirror.versions"
    " (id, schema_name, status, as_of, fingerprint, counts, latest_snapshot, freshness, warnings, sync_run_id, created_at)"
    " VALUES ($1, $2, 'building', $3, $4, $5, $6, $7, $8, $9, $10)"
)
# A building version, or a failed one whose schema is still there (a DROP that timed out): the first reason stays.
_FAIL = (
    "UPDATE pick_mirror.versions SET status = 'failed', error = coalesce(error, $2)"
    " WHERE id = $1 AND status IN ('building', 'failed') AND dropped_at IS NULL RETURNING schema_name"
)
_DROPPED = "UPDATE pick_mirror.versions SET dropped_at = $2 WHERE id = $1 AND dropped_at IS NULL"


Then = Callable[[], Awaitable[None]]


class MirrorBuildError(RuntimeError):
    """Building a version failed in the database; a mirror-side failure (plan 5.5). The text holds no value."""


class MirrorDropBlocked(MirrorBuildError):
    """DROP SCHEMA gave up behind readers after DROP_STATEMENT_TIMEOUT_MS: nothing dropped, a later try may succeed."""


@dataclass(frozen=True, slots=True)
class MirrorVersion:
    id: int
    schema_name: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def check_schema_name(name: object) -> str:
    """name when it is a version schema name (pickm_ and six ASCII digits); ValueError, without the value, otherwise."""
    if not isinstance(name, str) or SCHEMA_NAME.fullmatch(name) is None:
        raise ValueError("镜像版本的 schema 名必须是 pickm_v 加 6 位数字")
    return name


def schema_for(version_id: int) -> str:
    """The schema of version `version_id` (plan 3.3: made from the id alone)."""
    if isinstance(version_id, bool) or not isinstance(version_id, int) or not 1 <= version_id <= MAX_VERSION_ID:
        raise ValueError(f"镜像版本号必须是 1 到 {MAX_VERSION_ID} 的整数")
    return check_schema_name(f"pickm_v{version_id:06d}")


def ddl_template() -> str:
    """ddl.sql as packaged (importlib.resources: the source tree in tests, the wheel in the image)."""
    return resources.files(__package__).joinpath(DDL_FILE).read_text(encoding="utf-8")


def ddl_for(schema_name: str) -> str:
    """ddl.sql for one version: the placeholder replaced by a checked schema name."""
    return ddl_template().replace(SCHEMA_PLACEHOLDER, check_schema_name(schema_name))


def describe_error(exc: BaseException) -> str:
    """The exception class and, from PostgreSQL, the SQLSTATE: what a stored error may say about a database failure."""
    sqlstate = getattr(exc, "sqlstate", None)
    return f"{type(exc).__name__}（SQLSTATE {sqlstate}）" if isinstance(sqlstate, str) else type(exc).__name__


def safe_error_text(error: str) -> str:
    """Error text the versions row can hold: NUL and lone surrogates replaced, at most ERROR_MAX_LENGTH characters."""
    return storable(str(error))[:ERROR_MAX_LENGTH]


def json_text(value: object, what: str) -> str:
    """value as the JSON text asyncpg's default jsonb codec takes: non-ASCII kept, NaN and Infinity refused.

    Mappings (the client's read-only manifest views) are written as objects; anything else not JSON is a ValueError
    naming `what`, never the value.
    """
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=_plain)
    except (TypeError, ValueError):
        raise ValueError(f"{what} 不是可存成 jsonb 的 JSON 值") from None


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("not JSON")


def check_moment(moment: object) -> datetime:
    """An aware datetime, as created_at and dropped_at take from the injected clock: asyncpg would store a naive one as if
    it were UTC. ValueError otherwise."""
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ValueError("时间必须是带时区的 datetime")
    return moment


def _check_as_of(as_of: object) -> datetime:
    check_moment(as_of)
    if as_of.second or as_of.microsecond:
        raise ValueError("as_of 必须是整分钟（与 RealShort 的 asOf 相同，U6）")
    return as_of


def _check_counts(counts: object) -> str:
    if not isinstance(counts, Mapping) or set(counts) != set(COUNTED_RESOURCES):
        raise ValueError(f"counts 必须正好有 {len(COUNTED_RESOURCES)} 个资源键")
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts.values()):
        raise ValueError("counts 的值必须是非负整数")
    return json_text({name: counts[name] for name in COUNTED_RESOURCES}, "counts")


def _check_day(day: object) -> date | None:
    if day is None or (isinstance(day, date) and not isinstance(day, datetime)):
        return day
    if isinstance(day, str) and _DAY.fullmatch(day):
        try:
            return date.fromisoformat(day)
        except ValueError:
            pass
    raise ValueError("latest_snapshot 必须是 YYYY-MM-DD 的真实日期或 null")


def _version_values(*, as_of, fingerprint, counts, latest_snapshot, freshness, warnings, sync_run_id) -> tuple:
    """The INSERT's $3-$9, each checked; ValueError before any statement is sent."""
    if not isinstance(fingerprint, str) or _FINGERPRINT.fullmatch(fingerprint) is None:
        raise ValueError("fingerprint 必须是 64 位小写十六进制（U1）")
    if not isinstance(freshness, Mapping):
        raise ValueError("freshness 必须是对象（manifest.meta.freshness，U5）")
    if not isinstance(warnings, Sequence) or isinstance(warnings, str | bytes):
        raise ValueError("warnings 必须是数组（manifest.meta.warnings，U5）")
    if sync_run_id is not None and not isinstance(sync_run_id, str):
        raise ValueError("sync_run_id 必须是字符串或 null")
    return (
        _check_as_of(as_of),
        fingerprint,
        _check_counts(counts),
        _check_day(latest_snapshot),
        json_text(freshness, "freshness"),
        json_text(list(warnings), "warnings"),
        sync_run_id,
    )


async def create_version(
    conn,
    *,
    as_of: datetime,
    fingerprint: str,
    counts: Mapping[str, int],
    latest_snapshot: str | date | None,
    freshness: Mapping[str, object],
    warnings: Sequence[object],
    sync_run_id: str | None,
    clock: Callable[[], datetime] = _utc_now,
    timeout: float = STATEMENT_TIMEOUT,
) -> MirrorVersion:
    """A new building version with its empty tables (P2-3 steps 1-3); the values are the manifest's (U1, U5, U6).

    1. the id from the versions sequence; 2. the row, status building, committed; 3. CREATE SCHEMA and ddl.sql in one
    transaction. Any failure in the database is a MirrorBuildError. Before step 3 there is nothing to clean up; when
    step 3 fails, the version is marked failed on the way out, which also drops a same-named schema no versions row
    names (the cleanup's rule for orphans, P2-5c). A cancellation leaves the building row to the next run's cleanup,
    which drops the schema if it exists.
    """
    values = _version_values(
        as_of=as_of, fingerprint=fingerprint, counts=counts, latest_snapshot=latest_snapshot, freshness=freshness, warnings=warnings, sync_run_id=sync_run_id
    )
    created_at = check_moment(clock())
    template = ddl_template()  # a packaging fault stops here, before a version row exists
    version = await _register(conn, values, created_at, timeout=timeout)
    try:
        await _create_tables(conn, version.schema_name, template)
    except Exception as exc:
        error = MirrorBuildError(f"建镜像版本 {version.schema_name} 的表失败：{describe_error(exc)}")
        await _fail_on_the_way_out(conn, version.id, str(error), clock)
        raise error from None
    return version


async def _register(conn, values: tuple, created_at: datetime, *, timeout: float) -> MirrorVersion:
    """Steps 1 and 2, each autocommitted: the id, then the building row. A failure is a MirrorBuildError like step 3's."""
    try:
        version_id = await conn.fetchval(_NEXT_ID, timeout=timeout)
        version = MirrorVersion(id=version_id, schema_name=schema_for(version_id))
        await conn.execute(_INSERT, version.id, version.schema_name, *values, created_at, timeout=timeout)
    except Exception as exc:
        raise MirrorBuildError(f"登记镜像版本失败：{describe_error(exc)}") from None
    return version


async def _create_tables(conn, schema_name: str, template: str) -> None:
    name = check_schema_name(schema_name)
    await _in_transaction(conn, (f"CREATE SCHEMA {name}", template.replace(SCHEMA_PLACEHOLDER, name)), timeout=DDL_TIMEOUT)


async def _in_transaction(conn, statements: Sequence[str], *, timeout: float, then: Then | None = None) -> None:
    """BEGIN, the statements, then(), COMMIT: every statement sent with `timeout`. Any failure, cancellation too, rolls
    back first.

    A caller's open transaction is refused: BEGIN inside it would only warn, and this COMMIT would commit its work.
    """
    if conn.is_in_transaction():
        raise MirrorBuildError("镜像专用连接上还有未结束的事务")
    await conn.execute("BEGIN", timeout=timeout)
    try:
        for statement in statements:
            await conn.execute(statement, timeout=timeout)
        if then is not None:
            await then()
        await conn.execute("COMMIT", timeout=timeout)
    except BaseException:
        await _roll_back(conn, timeout=timeout)
        raise


async def _roll_back(conn, *, timeout: float) -> None:
    if conn.is_closed() or not conn.is_in_transaction():
        return
    try:
        await conn.execute("ROLLBACK", timeout=timeout)
    except Exception:
        logger.warning("[pick-mirror] ROLLBACK on the dedicated connection failed", exc_info=True)


async def _fail_on_the_way_out(conn, version_id: int, error: str, clock: Callable[[], datetime]) -> None:
    try:
        await mark_failed(conn, version_id, error=error, clock=clock)
    except Exception:
        logger.warning("[pick-mirror] marking version %s failed did not finish; the next run's cleanup will", version_id, exc_info=True)


async def mark_failed(conn, version_id: int, *, error: str, clock: Callable[[], datetime] = _utc_now, timeout: float = STATEMENT_TIMEOUT) -> bool:
    """Record a building version as failed with `error` (safe text), then drop its schema and stamp dropped_at in the
    DROP's own transaction (P2-3, 5.5); dropped_at is the injected clock's, read before any statement.

    True when this call dropped the schema (or found it already gone). False when there was nothing to do: the version
    is published, dropped, unknown, or already cleaned up; or the connection is closed, in which case the next run's
    cleanup does it under the lock (P2-5c). A statement cut short by asyncpg's timeout or by cancelling its task leaves
    the connection usable, so the mirror deadline path (plan 5.5) calls this right after; only a closed connection, or
    one known to be broken, is left to the next run. A DROP that gives up behind readers raises MirrorDropBlocked and
    leaves the version failed with dropped_at NULL; calling again finishes it.
    """
    if isinstance(version_id, bool) or not isinstance(version_id, int):
        raise ValueError("镜像版本号必须是整数")
    text = safe_error_text(error)
    dropped_at = check_moment(clock())
    if conn.is_closed():
        return False
    schema_name = await conn.fetchval(_FAIL, version_id, text, timeout=timeout)
    if schema_name is None:
        return False

    async def stamp_dropped() -> None:
        await conn.execute(_DROPPED, version_id, dropped_at, timeout=timeout)

    await drop_version_schema(conn, schema_name, then=stamp_dropped)
    return True


def drop_blocked(exc: BaseException) -> bool:
    """The DROP gave up behind readers (DROP_BLOCKED_SQLSTATES): nothing was dropped, a later try may succeed."""
    return getattr(exc, "sqlstate", None) in DROP_BLOCKED_SQLSTATES


def drop_statements(schema_name: str, *, budget_ms: int) -> tuple[str, str, str]:
    """SET LOCAL lock_timeout and statement_timeout to budget_ms, then the DROP; ValueError for a bad name or budget."""
    name = check_schema_name(schema_name)
    if isinstance(budget_ms, bool) or not isinstance(budget_ms, int) or budget_ms < 1:
        raise ValueError("删除镜像版本的时限必须是至少 1 的整数毫秒")
    return (f"SET LOCAL lock_timeout = {budget_ms}", f"SET LOCAL statement_timeout = {budget_ms}", f"DROP SCHEMA IF EXISTS {name} CASCADE")


async def drop_in_transaction(conn, schema_name: str, *, budget_ms: int | None = None, then: Then | None = None, timeout: float = DROP_TIMEOUT) -> None:
    """The one DROP of a version schema: BEGIN, drop_statements, then() (the versions row that records it), COMMIT.

    budget_ms defaults to DROP_STATEMENT_TIMEOUT_MS (U24). Errors are raised as they came, after the ROLLBACK:
    drop_blocked(exc) tells a reader in the way. SET LOCAL ends with the transaction; the connection keeps its settings.
    """
    budget = DROP_STATEMENT_TIMEOUT_MS if budget_ms is None else budget_ms
    await _in_transaction(conn, drop_statements(schema_name, budget_ms=budget), timeout=timeout, then=then)


async def drop_version_schema(conn, schema_name: str, *, then: Then | None = None, timeout: float = DROP_TIMEOUT) -> None:
    """drop_in_transaction with the default budget, its errors as mirror errors: MirrorDropBlocked when readers were in
    the way, with nothing dropped; any other failure (then()'s included) MirrorBuildError. The text holds no value.
    """
    name = check_schema_name(schema_name)
    try:
        await drop_in_transaction(conn, name, then=then, timeout=timeout)
    except Exception as exc:
        failure = MirrorDropBlocked if drop_blocked(exc) else MirrorBuildError
        raise failure(f"删除镜像版本 {name} 失败：{describe_error(exc)}") from None
