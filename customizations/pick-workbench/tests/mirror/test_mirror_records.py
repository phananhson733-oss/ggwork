"""A page's rows as COPY records, the meta rows and the key table of a version (P2-3; U2, U3, U4).

Nothing here needs a database; the checks that must run before SQL is sent use a connection that fails when touched.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from types import MappingProxyType

import pytest
from mirror_rows import MANIFEST, NoSql, parsed, synthetic_row

from ggwork_pick.mirror.contracts import RESOURCE_COLUMNS, row_values

META_KEYS = ("freshness", "rsCounts", "growthBaseline", "sources", "rules", "control", "scrub", "warnings")
TOP_KEYS = ("fingerprint", "sourceRevision", "latestSnapshot", "snapshotDays", "counts")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["pickm_v00012", "public", 'pickm_v000123"; DROP SCHEMA public; --'])
async def test_a_bad_schema_name_is_refused_before_any_sql(bad):
    from ggwork_pick.mirror.writer import copy_records, copy_rows, finalize_version, write_meta

    conn = NoSql()
    rows = parsed("catalog_rows", [synthetic_row("catalog_rows", 1)])
    attempts = (
        copy_rows(conn, bad, "catalog_rows", rows),
        copy_records(conn, bad, "meta", [("k", "{}")]),
        write_meta(conn, bad, MANIFEST),
        finalize_version(conn, bad),
    )
    for attempt in attempts:
        with pytest.raises(ValueError):
            await attempt
    assert conn.touched == []


def test_to_record_converts_contract_values_for_copy():
    from ggwork_pick.mirror.writer import to_record

    row = synthetic_row("catalog_signals", 3, rank=None)
    (model,) = parsed("catalog_signals", [row])
    record = to_record("catalog_signals", row_values(model))
    columns = [column.name for column in RESOURCE_COLUMNS["catalog_signals"]]
    values = dict(zip(columns, record, strict=True))
    assert values["rank"] is None and values["ord"] == 3
    # jsonb goes as the JSON text asyncpg's default codec takes; non-ASCII stays as it is.
    assert isinstance(values["payload"], str) and json.loads(values["payload"]) == row["payload"]
    assert "日榜备注 ✓" in values["payload"]

    rs_row = synthetic_row("rs_rows", 2, rr1=0, publish_at="2026-09-01T00:00:00.000Z")
    (model,) = parsed("rs_rows", [rs_row])
    values = dict(zip([column.name for column in RESOURCE_COLUMNS["rs_rows"]], to_record("rs_rows", row_values(model)), strict=True))
    assert values["publish_at"] == datetime(2026, 9, 1, tzinfo=UTC) and values["publish_at"].tzinfo is not None
    assert values["rr1"] == 0.0 and type(values["rr1"]) is float
    assert values["tag_list"] == ["甲2", "b,c", ""] and type(values["tag_list"]) is list
    assert values["listed_on"] == "2026-09-03"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("imported_at", "2026-09-24T03:40:00.123"),
        ("imported_at", datetime(2026, 9, 24, 3, 40)),
        ("imported_at", "not a time"),
        ("episodes", 2**31),
        ("episodes", -(2**31) - 1),
        ("episodes", True),
        ("episodes", 1.5),
        ("youtube", 1),
        ("title", None),
        ("title", 7),
        ("in_site_ids", "单个站内编号"),
        ("in_site_ids", ["a", 1]),
        ("listed_on", 20260901),
    ],
)
def test_to_record_refuses_what_the_column_cannot_hold(column, value):
    from ggwork_pick.mirror.writer import MirrorRecordError, to_record

    names = [c.name for c in RESOURCE_COLUMNS["catalog_rows"]]
    values = dict(zip(names, row_values(parsed("catalog_rows", [synthetic_row("catalog_rows", 1)])[0]), strict=True))
    with pytest.raises(MirrorRecordError) as failure:
        to_record("catalog_rows", tuple({**values, column: value}[name] for name in names))
    assert failure.value.table == "catalog_rows" and failure.value.column == column
    if isinstance(value, str):
        assert value not in str(failure.value)


def test_to_record_accepts_an_aware_datetime_in_any_zone():
    from ggwork_pick.mirror.writer import to_record

    names = [c.name for c in RESOURCE_COLUMNS["catalog_rows"]]
    values = dict(zip(names, row_values(parsed("catalog_rows", [synthetic_row("catalog_rows", 1)])[0]), strict=True))
    moment = datetime(2026, 9, 24, 11, 40, tzinfo=timezone(timedelta(hours=8)))
    record = to_record("catalog_rows", tuple({**values, "imported_at": moment}[name] for name in names))
    assert record[names.index("imported_at")] == moment


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("catalog_rows", "imported_at", datetime(2026, 9, 24, 3, 40)),
        ("catalog_rows", "imported_at", "2026-09-24T03:40:00.123Z"),
        ("catalog_signals", "payload", {"d": "2026-09-01"}),
        ("catalog_posted", "posts", [{"d": "2026-09-01"}]),
        ("catalog_rows", "in_site_ids", ("a", "b")),
        ("catalog_rows", "episodes", 2**31),
        ("rs_rows", "rr", float("inf")),
        ("rs_rows", "rr", 1),
        ("meta", "value", {"a": 1}),
    ],
)
def test_check_records_refuses_values_copy_would_misread(table, column, value):
    from ggwork_pick.mirror.versions import TABLE_COLUMNS
    from ggwork_pick.mirror.writer import MirrorRecordError, check_records, records_for

    names = [c.name for c in TABLE_COLUMNS[table]]
    if table == "meta":
        good = ("freshness", "{}")
    else:
        (good,) = records_for(table, parsed(table, [synthetic_row(table, 1)]))
    check_records(table, [good])
    bad = tuple(value if name == column else good[index] for index, name in enumerate(names))
    with pytest.raises(MirrorRecordError) as failure:
        check_records(table, [good, bad])
    assert (failure.value.table, failure.value.row, failure.value.column) == (table, 1, column)
    assert "2026-09-24T03:40:00.123Z" not in str(failure.value)
    with pytest.raises(MirrorRecordError):
        check_records(table, [good[:-1]])


def test_meta_records_use_the_manifest_keys_verbatim():
    from ggwork_pick.mirror.writer import MANIFEST_META_KEYS, MANIFEST_TOP_KEYS, meta_records

    assert (MANIFEST_META_KEYS, MANIFEST_TOP_KEYS) == (META_KEYS, TOP_KEYS)
    records = meta_records(MANIFEST)
    assert [key for key, _ in records] == [*META_KEYS, *TOP_KEYS]
    decoded = {key: json.loads(value) for key, value in records}
    assert decoded == {**{key: MANIFEST["meta"][key] for key in META_KEYS}, **{key: MANIFEST[key] for key in TOP_KEYS}}
    # The client's checked manifest proxies its top level (feed_shape.Manifest.row).
    assert meta_records(MappingProxyType(MANIFEST)) == records
    assert all("\\u" not in value for _, value in records)


@pytest.mark.parametrize("missing", ["counts", "meta.scrub", "meta"])
def test_meta_records_need_every_key(missing):
    from ggwork_pick.mirror.writer import meta_records

    if missing.startswith("meta."):
        manifest = {**MANIFEST, "meta": {key: value for key, value in MANIFEST["meta"].items() if key != missing[5:]}}
    else:
        manifest = {key: value for key, value in MANIFEST.items() if key != missing}
    with pytest.raises(ValueError, match=missing.split(".")[-1]):
        meta_records(manifest)


def test_table_keys_cover_every_table_on_its_own_columns():
    from ggwork_pick.mirror.versions import MIRROR_TABLES, TABLE_COLUMNS
    from ggwork_pick.mirror.writer import TABLE_KEYS, key_statements

    assert tuple(TABLE_KEYS) == MIRROR_TABLES
    for table, keys in TABLE_KEYS.items():
        columns = {column.name: column for column in TABLE_COLUMNS[table]}
        assert [name for name in keys.primary if columns[name].nullable] == [], table
        assert all(column.split()[0] in columns for group in (*keys.unique, *keys.indexes) for column in group), table
    statements = [statement for _, statement in key_statements("pickm_v000001")]
    assert len(statements) == sum(1 + len(keys.unique) + len(keys.indexes) for keys in TABLE_KEYS.values())
    # PostgreSQL cuts identifiers at 63 bytes; a cut name could collide.
    assert all(len(word) <= 63 for statement in statements for word in statement.split())


@pytest.mark.asyncio
async def test_copy_records_takes_one_page_as_a_sequence():
    from ggwork_pick.mirror.writer import MirrorRecordError, check_records, copy_records

    conn = NoSql()
    # A generator would be drained by the check and reach COPY empty.
    with pytest.raises(ValueError):
        await copy_records(conn, "pickm_v000001", "meta", (record for record in [("k", "{}")]))
    assert conn.touched == []
    with pytest.raises(MirrorRecordError):
        check_records("meta", [{"key": "k", "value": "{}"}])
