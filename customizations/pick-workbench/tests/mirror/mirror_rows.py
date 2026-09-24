"""Synthetic rows, a manifest and a recording connection for the version writer tests (P2-3).

Every value here is made up: no business data. The manifest and one RealShort-shaped row per resource come from
export_v2_contract.json, generated from RealShort 816ca2e's own test fixtures (export_v2_contract.gen.mjs).
"""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from ggwork_pick.mirror.contracts import RESOURCE_COLUMNS, parse_page, ts_datetime

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CONTRACT = json.loads((FIXTURES / "export_v2_contract.json").read_text(encoding="utf-8"))
MANIFEST = CONTRACT["manifest"]
# The eight tables a version holds; rs_series_day folds into pick_mirror.series instead (P2-7).
TABLES = ("catalog_rows", "catalog_signals", "catalog_posted", "catalog_accounts", "rs_rows", "rs_ids", "rs_clicks14", "rs_bill_orders")
AS_OF = datetime(2026, 9, 23, 10, 15, tzinfo=UTC)
T0 = datetime(2026, 9, 24, 3, 40, 0, 123456, tzinfo=UTC)
# Primary keys of the P2-3 finalize table, written out by hand.
PRIMARY_KEYS = {
    "catalog_rows": ("row_key",),
    "catalog_signals": ("row_key", "kind", "ord"),
    "catalog_posted": ("sd",),
    "catalog_accounts": ("id",),
    "rs_rows": ("row_key",),
    "rs_ids": ("id",),
    "rs_clicks14": ("drama_id", "day"),
    "rs_bill_orders": ("bill_date", "book_id", "promotion_type"),
    "meta": ("key",),
}

_PAYLOAD = {"d": "2026-09-01", "h": [["2026-09-01", 3, "日榜备注 ✓"], ["2026-09-02", 4, ""]], "best": 1, "qy": 12.5, "pid": "p-1"}
_POSTS = [{"d": "2026-09-01", "acct": "acct-1", "views": 10, "url": "https://example.test/p/1", "note": '备注 "引号"'}, {"d": "2026-09-02"}]


def _value(column, index: int):
    if column.type == "json":
        return copy.deepcopy(_PAYLOAD if column.name == "payload" else _POSTS)
    samples = {
        "text": f"{column.name}-{index}-文本",
        "day": f"2026-09-{index % 28 + 1:02d}",
        "ts": f"2026-09-{index % 28 + 1:02d}T03:40:{index % 60:02d}.123Z",
        "int": index % 1000,
        "float": index + 0.25,
        "bool": index % 2 == 0,
        "text[]": [f"甲{index}", "b,c", ""],
    }
    return samples[column.type]


def synthetic_row(resource: str, index: int, *, nulls: bool = False, **overrides) -> dict:
    """A row of every column of `resource`; nulls=True leaves each nullable column NULL."""
    row = {column.name: None if nulls and column.nullable else _value(column, index) for column in RESOURCE_COLUMNS[resource]}
    return {**row, **overrides}


def realshort_row(resource: str) -> dict:
    """The row RealShort's own mapping produced for this resource in the contract fixture.

    The fixture fills every text column with a pan snippet; rs_clicks14.day must be a real day (contracts), so it gets one.
    """
    row = copy.deepcopy(CONTRACT["v2_rows"][resource]["output"])
    return {**row, "day": "2026-09-01"} if resource == "rs_clicks14" else row


def parsed(resource: str, rows: list[dict]) -> tuple:
    return parse_page(resource, {"rows": rows})


def expected_values(resource: str, row: dict) -> dict:
    """What reading `row` back from PostgreSQL gives, jsonb decoded: timestamps aware, floats floats."""
    values = {}
    for column in RESOURCE_COLUMNS[resource]:
        value = row[column.name]
        if value is not None and column.type == "ts":
            value = ts_datetime(value)
        elif value is not None and column.type == "float":
            value = float(value)
        values[column.name] = value
    return values


def version_args(**overrides) -> dict:
    """create_version's arguments from the fixture manifest."""
    meta = MANIFEST["meta"]
    arguments = {
        "as_of": AS_OF,
        "fingerprint": MANIFEST["fingerprint"],
        "counts": MANIFEST["counts"],
        "latest_snapshot": MANIFEST["latestSnapshot"],
        "freshness": meta["freshness"],
        "warnings": meta["warnings"],
        "sync_run_id": "run-1",
        "clock": lambda: T0,
    }
    return {**arguments, **overrides}


class Recording:
    """An asyncpg connection that records each statement it forwards and the timeout it was given.

    Only the calls the writer is meant to make are forwarded; anything else fails the test with AttributeError. There is
    no transaction(): asyncpg's sends BEGIN and COMMIT without a timeout, so the writer sends its own.
    """

    def __init__(self, conn):
        self._conn = conn
        self.calls: list[tuple[str, str, object]] = []

    def _record(self, method: str, statement: str, kwargs: dict) -> None:
        self.calls = [*self.calls, (method, statement, kwargs.get("timeout"))]

    async def execute(self, query, *args, **kwargs):
        self._record("execute", query, kwargs)
        return await self._conn.execute(query, *args, **kwargs)

    async def fetchval(self, query, *args, **kwargs):
        self._record("fetchval", query, kwargs)
        return await self._conn.fetchval(query, *args, **kwargs)

    async def fetchrow(self, query, *args, **kwargs):
        self._record("fetchrow", query, kwargs)
        return await self._conn.fetchrow(query, *args, **kwargs)

    async def copy_records_to_table(self, table_name, **kwargs):
        self._record("copy", table_name, kwargs)
        return await self._conn.copy_records_to_table(table_name, **kwargs)

    def is_closed(self) -> bool:
        return self._conn.is_closed()

    def is_in_transaction(self) -> bool:
        return self._conn.is_in_transaction()


class FakeCopy:
    """A connection with only COPY and no database: it answers `status(records)`, asyncpg's "COPY n" by default."""

    def __init__(self, status=lambda records: f"COPY {len(records)}"):
        self._status = status
        self.copied: tuple[tuple[str, int], ...] = ()

    async def copy_records_to_table(self, table_name, *, records, **kwargs):
        self.copied = (*self.copied, (table_name, len(records)))
        return self._status(records)


class NoSql:
    """A connection that must never be used: every attribute access is recorded and fails."""

    def __init__(self):
        self.touched: list[str] = []

    def __getattr__(self, name: str):
        self.touched = [*self.touched, name]
        raise AssertionError(f"the connection was used ({name}) before the arguments were checked")
