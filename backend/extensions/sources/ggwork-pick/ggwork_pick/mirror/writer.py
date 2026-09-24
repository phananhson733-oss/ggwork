"""Writing one mirror version: rows COPYed page by page, the meta rows, then keys, indexes and ANALYZE (plan 3.3, 5.2 steps 6-7).

Each call takes one page and keeps nothing: the caller holds a single page of rows at a time (plan 5.2 step 6). The
rows arrive as contracts.parse_page made them; to_record turns their values into what asyncpg's COPY encoders take, and
check_records holds every record to that before anything is sent, so a wrong Python type fails the whole page with
nothing written:

- ts: an aware datetime (asyncpg would store a naive one as if it were UTC); RealShort's text goes through fromisoformat;
- day and text: str; int: an int within integer's range (U3); float: a finite float; bool: a bool;
- text[]: a list of str; json: the JSON text (asyncpg's default jsonb codec takes str, never a dict).

Conversion and checking are CPU work and run in asyncio.to_thread. Primary keys and indexes are built after COPY (U4), so
a duplicate key surfaces in finalize_version as a MirrorBuildError: a mirror-side failure. Every statement runs
autocommitted on the dedicated connection with its own timeout; errors name the table, the step, the exception class and
the SQLSTATE, never a value.
"""

import asyncio
import math
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from ggwork_pick.mirror.contracts import COUNTED_RESOURCES, Column, MirrorRow, row_values
from ggwork_pick.mirror.versions import META_TABLE, MIRROR_TABLES, TABLE_COLUMNS, MirrorBuildError, check_schema_name, describe_error, json_text

# Seconds (plan 5.2 step 1): a COPY per page, then each key, index and ANALYZE statement.
COPY_TIMEOUT = 60
FINALIZE_TIMEOUT = 120
INT4_MIN = -(2**31)
INT4_MAX = 2**31 - 1
_COPY_STATUS = re.compile(r"COPY ([0-9]+)")
# The meta table's keys, verbatim from the manifest (U2): meta's eight, then five of the top level.
MANIFEST_META_KEYS = ("freshness", "rsCounts", "growthBaseline", "sources", "rules", "control", "scrub", "warnings")
MANIFEST_TOP_KEYS = ("fingerprint", "sourceRevision", "latestSnapshot", "snapshotDays", "counts")


@dataclass(frozen=True, slots=True)
class TableKeys:
    """A table's primary key, unique constraints and other btree indexes; an index column may carry DESC."""

    primary: tuple[str, ...]
    unique: tuple[tuple[str, ...], ...] = ()
    indexes: tuple[tuple[str, ...], ...] = ()


# plan 3.3 and implementation note P2-3 (the same keys and indexes RealShort's tables have).
TABLE_KEYS = MappingProxyType(
    {
        "catalog_rows": TableKeys(("row_key",), indexes=(("platform", "lang"), ("has_signal", "latest_evidence_on"), ("title_key",))),
        "catalog_signals": TableKeys(("row_key", "kind", "ord"), indexes=(("kind", "evidence_on"),)),
        "catalog_posted": TableKeys(("sd",)),
        "catalog_accounts": TableKeys(("id",)),
        "rs_rows": TableKeys(("row_key",), unique=(("drama_id",),), indexes=(("has_signal", "latest_evidence_on"), ("locale",), ("publish_at",))),
        "rs_ids": TableKeys(("id",), indexes=(("canonical_id",),)),
        "rs_clicks14": TableKeys(("drama_id", "day")),
        "rs_bill_orders": TableKeys(("bill_date", "book_id", "promotion_type"), indexes=(("canonical_id", "bill_date DESC"),)),
        META_TABLE: TableKeys(("key",)),
    }
)


class MirrorRecordError(ValueError):
    """A value COPY cannot take as it is; names the table, the row's index in its page and the column, never the value."""

    def __init__(self, table: str, row: int | None, column: str, reason: str):
        where = f"第 {row} 行" if row is not None else "一行"
        super().__init__(f"镜像表 {table} {where}的 {column}：{reason}")
        self.table = table
        self.row = row
        self.column = column


class _Refused(Exception):
    """A converter's refusal; the reason is fixed text."""


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise _Refused("应是字符串")
    return value


def _timestamp(value: object) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise _Refused("不是 ISO 8601 时间") from None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise _Refused("时间必须带时区")
    return value


def _integer(value: object) -> int:
    if type(value) is not int or not INT4_MIN <= value <= INT4_MAX:
        raise _Refused("应是 integer 范围内的整数")
    return value


def _double(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise _Refused("应是有限数")
    return float(value)


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise _Refused("应是布尔")
    return value


def _text_array(value: object) -> list[str]:
    if type(value) is not list or not all(isinstance(item, str) for item in value):
        raise _Refused("应是字符串数组")
    return list(value)


def _json(value: object) -> str:
    if not isinstance(value, dict | list):
        raise _Refused("应是 JSON 对象或数组")
    try:
        return json_text(value, "json")
    except ValueError:
        raise _Refused("不是可存成 jsonb 的 JSON 值") from None


_CONVERT: Mapping[str, Callable[[object], object]] = MappingProxyType(
    {"text": _text, "day": _text, "ts": _timestamp, "int": _integer, "float": _double, "bool": _boolean, "text[]": _text_array, "json": _json}
)


def _is_text(value: object) -> bool:
    return isinstance(value, str)


def _is_aware(value: object) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def _is_integer(value: object) -> bool:
    return type(value) is int and INT4_MIN <= value <= INT4_MAX


def _is_double(value: object) -> bool:
    return type(value) is float and math.isfinite(value)


def _is_boolean(value: object) -> bool:
    return type(value) is bool


def _is_text_array(value: object) -> bool:
    return type(value) is list and all(isinstance(item, str) for item in value)


# What each column type must be once converted, exactly: COPY would take more (a naive datetime, an int for a float).
_READY: Mapping[str, Callable[[object], bool]] = MappingProxyType(
    {
        "text": _is_text,
        "day": _is_text,
        "ts": _is_aware,
        "int": _is_integer,
        "float": _is_double,
        "bool": _is_boolean,
        "text[]": _is_text_array,
        "json": _is_text,
    }
)
_READY_AS = MappingProxyType(
    {
        "text": "str",
        "day": "str",
        "ts": "带时区的 datetime",
        "int": "integer 范围内的 int",
        "float": "有限 float",
        "bool": "bool",
        "text[]": "list[str]",
        "json": "JSON 文本 str",
    }
)


def _columns(table: str) -> tuple[Column, ...]:
    if table not in TABLE_COLUMNS:
        raise ValueError("镜像版本里没有这张表")
    return TABLE_COLUMNS[table]


def _converted(table: str, row: int | None, column: Column, value: object) -> object:
    if value is None:
        if column.nullable:
            return None
        raise MirrorRecordError(table, row, column.name, "不能为空")
    try:
        return _CONVERT[column.type](value)
    except _Refused as refused:
        reason = str(refused)
    raise MirrorRecordError(table, row, column.name, reason)


def _record(table: str, values: Sequence[object], row: int | None) -> tuple:
    columns = _columns(table)
    if len(values) != len(columns):
        raise MirrorRecordError(table, row, "*", f"应有 {len(columns)} 个值")
    return tuple(_converted(table, row, column, value) for column, value in zip(columns, values, strict=True))


def to_record(table: str, values: Sequence[object]) -> tuple:
    """One row's values in the table's column order (contracts.row_values) as COPY takes them."""
    return _record(table, values, row=None)


def _row_record(table: str, index: int, row: object) -> tuple:
    if not isinstance(row, MirrorRow) or row.resource != table:
        raise MirrorRecordError(table, index, "*", "不是这张表的行")
    return _record(table, row_values(row), index)


def records_for(table: str, rows: Sequence[MirrorRow]) -> list[tuple]:
    """A page of parsed rows as COPY records; a row of another resource is refused. CPU work: run it in a thread."""
    return [_row_record(table, index, row) for index, row in enumerate(rows)]


def check_records(table: str, records: Sequence[Sequence[object]]) -> None:
    """Every record holds exactly what COPY must get for each column (see the module text); CPU work, for a thread."""
    columns = _columns(table)
    for index, record in enumerate(records):
        if not isinstance(record, tuple | list) or len(record) != len(columns):
            raise MirrorRecordError(table, index, "*", f"应是 {len(columns)} 个值的元组")
        for column, value in zip(columns, record, strict=True):
            if value is None and column.nullable:
                continue
            if value is None or not _READY[column.type](value):
                raise MirrorRecordError(table, index, column.name, f"应是 {_READY_AS[column.type]}")


async def copy_records(conn, schema_name: str, table: str, records: Sequence[Sequence[object]], *, timeout: float = COPY_TIMEOUT) -> int:
    """COPY one page of ready records into schema_name.table in one statement; returns the rows written.

    The records are checked first, in a thread: a single wrong value refuses the page before anything is sent.
    """
    name = check_schema_name(schema_name)
    columns = [column.name for column in _columns(table)]
    if not isinstance(records, Sequence):
        raise ValueError("records 必须是一页记录的序列（列表或元组），不能是迭代器")
    if not records:
        return 0
    await asyncio.to_thread(check_records, table, records)
    try:
        status = await conn.copy_records_to_table(table, schema_name=name, columns=columns, records=records, timeout=timeout)
    except Exception as exc:
        raise MirrorBuildError(f"写入镜像表 {name}.{table} 失败：{describe_error(exc)}") from None
    return _copied(status, len(records), f"{name}.{table}")


def _copied(status: object, expected: int, where: str) -> int:
    """The page's size, when COPY's own status ("COPY n") reports exactly that many rows."""
    reported = _COPY_STATUS.fullmatch(status) if isinstance(status, str) else None
    if reported is None or int(reported.group(1)) != expected:
        raise MirrorBuildError(f"写入镜像表 {where} 的行数与这一页不符")
    return expected


async def copy_rows(conn, schema_name: str, resource: str, rows: Sequence[MirrorRow], *, timeout: float = COPY_TIMEOUT) -> int:
    """COPY one page of parsed rows (contracts.parse_page) of `resource`; returns the rows written. An empty page sends nothing."""
    name = check_schema_name(schema_name)
    if resource not in COUNTED_RESOURCES:
        raise ValueError("只有 manifest.counts 里的资源有镜像表")
    if not rows:
        return 0
    records = await asyncio.to_thread(records_for, resource, rows)
    return await copy_records(conn, name, resource, records, timeout=timeout)


def meta_records(manifest: Mapping[str, object]) -> list[tuple[str, str]]:
    """The meta table's rows from the manifest object, keys verbatim (U2): (key, JSON text of the value)."""
    meta = manifest.get("meta") if isinstance(manifest, Mapping) else None
    if not isinstance(meta, Mapping):
        raise ValueError("manifest 缺少 meta")
    missing = [f"meta.{key}" for key in MANIFEST_META_KEYS if key not in meta] + [key for key in MANIFEST_TOP_KEYS if key not in manifest]
    if missing:
        raise ValueError(f"manifest 缺少 {missing[0]}")
    pairs = [*((key, meta[key]) for key in MANIFEST_META_KEYS), *((key, manifest[key]) for key in MANIFEST_TOP_KEYS)]
    return [(key, json_text(value, f"manifest 的 {key}")) for key, value in pairs]


async def write_meta(conn, schema_name: str, manifest: Mapping[str, object], *, timeout: float = COPY_TIMEOUT) -> int:
    """COPY the meta rows (13) of the manifest this version was built from."""
    name = check_schema_name(schema_name)
    records = await asyncio.to_thread(meta_records, manifest)
    return await copy_records(conn, name, META_TABLE, records, timeout=timeout)


def _index_name(table: str, columns: tuple[str, ...], suffix: str) -> str:
    return "_".join((table, *(column.split()[0] for column in columns), suffix))


def key_statements(schema_name: str) -> Iterator[tuple[str, str]]:
    """(table, statement) for every primary key, unique constraint and index of the version, in TABLE_KEYS order."""
    name = check_schema_name(schema_name)
    for table in MIRROR_TABLES:
        keys = TABLE_KEYS[table]
        yield table, f"ALTER TABLE {name}.{table} ADD CONSTRAINT {table}_pkey PRIMARY KEY ({', '.join(keys.primary)})"
        for columns in keys.unique:
            yield table, f"ALTER TABLE {name}.{table} ADD CONSTRAINT {_index_name(table, columns, 'key')} UNIQUE ({', '.join(columns)})"
        for columns in keys.indexes:
            yield table, f"CREATE INDEX {_index_name(table, columns, 'idx')} ON {name}.{table} ({', '.join(columns)})"


async def _run(conn, statement: str, *, where: str, step: str, timeout: float) -> None:
    try:
        await conn.execute(statement, timeout=timeout)
    except Exception as exc:
        raise MirrorBuildError(f"镜像表 {where} {step}失败：{describe_error(exc)}") from None


async def finalize_version(conn, schema_name: str, *, timeout: float = FINALIZE_TIMEOUT, timer: Callable[[], float] = time.monotonic) -> dict[str, float]:
    """Keys and indexes, then ANALYZE of every table (plan 3.3 build order; P2-3 step 7); returns the seconds each took.

    Each statement commits on its own. A duplicate primary key or drama_id fails here, as a MirrorBuildError naming the
    table and SQLSTATE 23505: a mirror-side failure (plan 5.5).
    """
    name = check_schema_name(schema_name)
    started = timer()
    for table, statement in key_statements(name):
        await _run(conn, statement, where=f"{name}.{table}", step="建主键与索引", timeout=timeout)
    keyed = timer()
    for table in MIRROR_TABLES:
        # Fresh tables have no statistics, and RealShort's IN (subquery) plans depend on them (plan 3.3).
        await _run(conn, f"ANALYZE {name}.{table}", where=f"{name}.{table}", step="ANALYZE ", timeout=timeout)
    return {"keys_seconds": round(keyed - started, 3), "analyze_seconds": round(timer() - keyed, 3)}
