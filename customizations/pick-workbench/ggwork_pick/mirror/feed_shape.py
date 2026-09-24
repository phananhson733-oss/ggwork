"""The shape of RealShort's feed responses, feed v2 (`pick-export-v2`) and v1 (`pick-feed-v1`), checked page by page.

Frozen at RealShort 816ca2e (rs = realshort-pick-export-v2). Only envelopes, the manifest and the cursor chain are
checked here; the rows themselves are P2-2b's strict models. What RealShort echoes back (as_of, fp, and on v1 the
manifest's fingerprint and build SHA) differing is drift; anything else out of shape is a contract error.
Error messages name fields, never values.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType

from ggwork_pick.mirror.errors import ContractError, DriftError

EXPORT_VERSION = "pick-export-v2"  # rs:src/lib/pick/export-v2-map.ts:25
V1_VERSION = "pick-feed-v1"  # rs:src/lib/pick/feed-map.ts:21; ggwork_pick/sync.py:22
V1_PAGE_LIMIT = 1000  # sync.py:24; RealShort's FEED_MAX_LIMIT (feed-map.ts:22)
V1_MAX_PAGES = 40  # sync.py:25
# rs:src/lib/pick/export-v2-map.ts:61-71; manifest.counts has every one but rs_series_day (export-v2.ts:420)
COUNTED_RESOURCES = ("catalog_rows", "catalog_signals", "catalog_posted", "catalog_accounts", "rs_rows", "rs_ids", "rs_clicks14", "rs_bill_orders")
SERIES_RESOURCE = "rs_series_day"
ROW_RESOURCES = (*COUNTED_RESOURCES, SERIES_RESOURCE)
# maxLimit, also the default limit (rs:src/lib/pick/export-v2-map.ts:130-191, export-v2-page.ts:56-61)
MAX_LIMITS: Mapping[str, int] = MappingProxyType(
    {
        "catalog_rows": 5000,
        "catalog_signals": 5000,
        "catalog_posted": 1000,
        "catalog_accounts": 1000,
        "rs_rows": 2000,
        "rs_ids": 10000,
        "rs_clicks14": 20000,
        "rs_bill_orders": 5000,
        "rs_series_day": 40000,
    }
)
SERIES_DAY_SPAN = 93  # day runs from the as_of day back 92 days (rs:src/lib/pick/export-v2-page.ts:20, :49-54)
V2_ENVELOPE_KEYS = frozenset({"ok", "version", "resource", "asOf", "fingerprint", "rows", "nextCursor"})  # export-v2-page.ts:125-133
MANIFEST_KEYS = frozenset({"version", "asOf", "fingerprint", "sourceRevision", "counts", "latestSnapshot", "snapshotDays", "meta"})  # map.ts:900-909
META_KEYS = frozenset({"freshness", "rsCounts", "growthBaseline", "sources", "rules", "control", "scrub", "warnings"})  # map.ts:878-897
SNAPSHOT_DAY_KEYS = frozenset({"day", "rows"})  # export-v2-map.ts:907
# The fixed words RealShort puts in error bodies (rs:src/lib/pick/feed-http.ts:6-14, export-v2-page.ts:72)
ERROR_WORDS = frozenset({"not_found", "unauthorized", "bad_request", "source_changed", "source_busy", "read_failed", "row_too_large"})
REASON_WORDS = frozenset({"resource", "unknown_param", "duplicate_param", "as_of", "fp", "cursor", "limit", "day"})
AS_OF_LAG = timedelta(minutes=2)  # plan 2.5

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# meta.scrub keys are field paths such as catalog_signals.payload.h[*][*] (rs:src/lib/pick/export-v2-map.ts:641-672)
_SCRUB_PATH = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+|\[\*\]){0,16}$")
_WARNING_CODE = re.compile(r"^[a-z_]{1,64}$")
_KEY_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def select_as_of(now: datetime) -> datetime:
    """Plan 2.5: the current minute less two minutes; RealShort reads every table as of this moment."""
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    return now.astimezone(UTC).replace(second=0, microsecond=0) - AS_OF_LAG


def format_as_of(as_of: datetime) -> str:
    """asOf.toISOString() of a whole minute: what v2 pages echo and v1 sends as capturedAt (rs:src/lib/pick/export-v2.ts:508)."""
    if as_of.tzinfo is None:
        raise ValueError("as_of 必须带时区")
    moment = as_of.astimezone(UTC)
    if moment.second or moment.microsecond:
        raise ValueError("as_of 必须是整分钟")
    return moment.strftime("%Y-%m-%dT%H:%M:00.000Z")


def _real_day(value: object) -> bool:
    if not isinstance(value, str) or not _DAY.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def check_series_day(day: object, as_of: datetime) -> str:
    """The day rs_series_day accepts: a real date from the as_of day (UTC) back SERIES_DAY_SPAN - 1 days."""
    if not _real_day(day):
        raise ValueError("rs_series_day 的 day 必须是 YYYY-MM-DD 的真实日期")
    last = as_of.astimezone(UTC).date()
    if not last - timedelta(days=SERIES_DAY_SPAN - 1) <= date.fromisoformat(day) <= last:
        raise ValueError(f"rs_series_day 的 day 必须在 as_of 当天往前 {SERIES_DAY_SPAN} 天之内")
    return day


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class PageMetrics:
    """What one HTTP response cost. Never a field value; `started` is the timer reading when the request went out."""

    resource: str
    page: int
    status: int
    elapsed_ms: float
    bytes: int
    wire_bytes: int
    rows: int | None
    retry_after: int | None
    attempt: int = 1
    error: str | None = None
    day: str | None = None
    started: float = 0.0

    def line(self) -> dict:
        """One stdout line of the dry-run: the eight metrics, plus day, attempt and error word when they say something."""
        base = {
            "resource": self.resource,
            "page": self.page,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "bytes": self.bytes,
            "wire_bytes": self.wire_bytes,
            "rows": self.rows,
            "retry_after": self.retry_after,
        }
        optional = {"day": self.day, "attempt": self.attempt if self.attempt > 1 else None, "error": self.error}
        return {**base, **{key: value for key, value in optional.items() if value is not None}}


@dataclass(frozen=True)
class Page:
    """One checked page: the parsed JSON as RealShort sent it and what it cost. repr leaves the rows out."""

    body: dict = field(repr=False)
    metrics: PageMetrics

    @property
    def rows(self) -> list:
        return self.body["rows"]


@dataclass(frozen=True)
class Manifest:
    """The manifest row (plan 4.5) with the as_of it was read at; every later request carries its as_of and fp."""

    as_of: datetime
    fingerprint: str
    source_revision: str | None
    counts: Mapping[str, int]
    latest_snapshot: str | None
    snapshot_days: Mapping[str, int]
    meta: Mapping[str, object]
    row: Mapping[str, object]
    metrics: PageMetrics
    busy_sleeps: tuple[int, ...] = ()

    @property
    def as_of_text(self) -> str:
        return format_as_of(self.as_of)

    def row_cap(self, resource: str, day: str | None = None) -> int:
        """Rows RealShort promised: counts, or for rs_series_day the day's snapshotDays entry (0 when not listed)."""
        if resource == SERIES_RESOURCE:
            return self.snapshot_days.get(day, 0) if day is not None else 0
        return self.counts[resource]


def _names(keys) -> str:
    shown = sorted(key if isinstance(key, str) and _KEY_NAME.match(key) else "<非常规键名>" for key in keys)
    return "、".join(shown[:5]) + ("……" if len(shown) > 5 else "")


def require_keys(value: object, expected: frozenset[str], where: str) -> dict:
    """Exactly these keys: a missing or an extra one is a contract error that names keys, not values."""
    if not isinstance(value, dict):
        raise ContractError(f"{where} 不是对象")
    missing, extra = expected - value.keys(), value.keys() - expected
    if missing or extra:
        raise ContractError(f"{where} 的键不符：缺 {_names(missing) or '无'}，多 {_names(extra) or '无'}")
    return value


def check_v2_page(body: object, *, resource: str, as_of_text: str, fp: str | None) -> dict:
    """Envelope of any v2 page (rs:src/lib/pick/export-v2.ts:516, :534-538): resource is contract, asOf and fp are drift."""
    label = f"RealShort feed v2 {resource}"
    if not isinstance(body, dict) or body.get("ok") is not True or body.get("version") != EXPORT_VERSION:
        raise ContractError(f"{label} 的版本或格式不符", resource=resource)
    require_keys(body, V2_ENVELOPE_KEYS, f"{label} 的信封")
    if body["resource"] != resource:
        raise ContractError(f"{label} 回显的 resource 不是 {resource}", resource=resource)
    if body["asOf"] != as_of_text:
        raise DriftError(f"{label} 回显的 asOf 与请求的 as_of 不同", resource=resource)
    if fp is not None and body["fingerprint"] != fp:
        raise DriftError(f"{label} 回显的 fingerprint 与 manifest 不同", resource=resource)
    if not isinstance(body["rows"], list) or not all(isinstance(row, dict) for row in body["rows"]):
        raise ContractError(f"{label} 的 rows 不是对象数组", resource=resource)
    cursor = body["nextCursor"]
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise ContractError(f"{label} 的 nextCursor 不是非空字符串或 null", resource=resource)
    return body


def check_row_page(body: object, *, resource: str, as_of_text: str, fp: str, limit: int) -> str | None:
    """A row page: the envelope, at most limit rows, and a row on every page before the last (export-v2-page.ts:223)."""
    page = check_v2_page(body, resource=resource, as_of_text=as_of_text, fp=fp)
    rows, cursor = page["rows"], page["nextCursor"]
    if len(rows) > limit:
        raise ContractError(f"RealShort feed v2 {resource} 一页 {len(rows)} 行，超过 limit {limit}", resource=resource)
    if cursor is not None and not rows:
        raise ContractError(f"RealShort feed v2 {resource} 的中间页没有行", resource=resource)
    return cursor


def check_v1_page(body: object, *, manifest: Manifest, first: bool) -> str | None:
    """sync.fetch_feed's checks on one page (sync.py:66-81), then the pin to the manifest (plan 5.2 step 5, 761)."""
    if not isinstance(body, dict) or body.get("ok") is not True or body.get("version") != V1_VERSION:
        raise ContractError("RealShort feed 版本或格式不符", resource="v1")
    if not isinstance(body.get("rows"), list):
        raise ContractError("RealShort feed 缺少 rows", resource="v1")
    if first and not _count(body.get("total")):
        raise ContractError("RealShort feed 首页缺少总数", resource="v1")
    cursor = body.get("nextCursor")
    if cursor and not isinstance(cursor, str):
        raise ContractError("RealShort feed 游标无效", resource="v1")
    if body.get("capturedAt") != manifest.as_of_text:
        raise DriftError("RealShort feed 的 capturedAt 不等于 manifest 的 as_of", resource="v1")
    if body.get("fingerprint") != manifest.fingerprint:
        raise DriftError("RealShort feed 的 fingerprint 与 manifest 不同", resource="v1")
    if body.get("sourceRevision") != manifest.source_revision:
        raise DriftError("RealShort feed 的 sourceRevision 与 manifest 不同", resource="v1")
    return cursor or None


def _manifest_identity(row: dict, body: dict, as_of_text: str) -> str:
    if row["version"] != EXPORT_VERSION:
        raise ContractError("manifest.version 不符", resource="manifest")
    if row["asOf"] != as_of_text:
        raise ContractError("manifest.asOf 与信封不同", resource="manifest")
    fingerprint = row["fingerprint"]
    if not isinstance(fingerprint, str) or not _FINGERPRINT.match(fingerprint) or fingerprint != body["fingerprint"]:
        raise ContractError("manifest.fingerprint 不是 64 位小写十六进制，或与信封不同", resource="manifest")
    if row["sourceRevision"] is not None and not isinstance(row["sourceRevision"], str):
        raise ContractError("manifest.sourceRevision 不是字符串或 null", resource="manifest")
    return fingerprint


def _counts(value: object) -> Mapping[str, int]:
    counts = require_keys(value, frozenset(COUNTED_RESOURCES), "manifest.counts")
    bad = [name for name in COUNTED_RESOURCES if not _count(counts[name])]
    if bad:
        raise ContractError(f"manifest.counts.{bad[0]} 不是非负整数", resource="manifest")
    return MappingProxyType({name: counts[name] for name in COUNTED_RESOURCES})


def _series_day(day: object, as_of: datetime) -> bool:
    try:
        check_series_day(day, as_of)
    except ValueError:
        return False
    return True


def _snapshot_days(value: object, as_of: datetime) -> list[tuple[str, int]]:
    """[{day, rows}] over the days rs_series_day takes (rs:src/lib/pick/export-v2.ts:440-446): a day outside that
    window could never be fetched, so it is a contract error here rather than a ValueError from pages() later."""
    if not isinstance(value, list):
        raise ContractError("manifest.snapshotDays 不是数组", resource="manifest")
    days = []
    for index, item in enumerate(value):
        entry = require_keys(item, SNAPSHOT_DAY_KEYS, f"manifest.snapshotDays[{index}]")
        if not _series_day(entry["day"], as_of) or not _count(entry["rows"]):
            message = f"manifest.snapshotDays[{index}] 的 day 不是 as_of 当天往前 {SERIES_DAY_SPAN} 天内的真实日期，或 rows 不是非负整数"
            raise ContractError(message, resource="manifest")
        days = [*days, (entry["day"], entry["rows"])]
    if any(earlier[0] >= later[0] for earlier, later in zip(days, days[1:])):
        raise ContractError("manifest.snapshotDays 不是按日期严格递增", resource="manifest")
    return days


def _meta(value: object) -> Mapping[str, object]:
    meta = require_keys(value, META_KEYS, "manifest.meta")
    scrub, warnings = meta["scrub"], meta["warnings"]
    if not isinstance(scrub, dict) or not all(isinstance(k, str) and len(k) <= 200 and _SCRUB_PATH.match(k) and _count(n) for k, n in scrub.items()):
        raise ContractError("manifest.meta.scrub 不是「字段路径 → 非负整数」", resource="manifest")
    if not isinstance(warnings, list) or not all(isinstance(w, dict) and isinstance(w.get("code"), str) and _WARNING_CODE.match(w["code"]) for w in warnings):
        raise ContractError("manifest.meta.warnings 的某一条没有合规的 code", resource="manifest")
    return MappingProxyType(meta)


def parse_manifest(body: object, *, as_of: datetime, metrics: PageMetrics, busy_sleeps: tuple[int, ...] = ()) -> Manifest:
    """The manifest page (rs:src/lib/pick/export-v2.ts:497-518): one row, exactly its keys, counts and snapshotDays checked."""
    as_of_text = format_as_of(as_of)
    page = check_v2_page(body, resource="manifest", as_of_text=as_of_text, fp=None)
    if len(page["rows"]) != 1 or page["nextCursor"] is not None:
        raise ContractError("manifest 的 rows 必须恰好 1 个元素，nextCursor 为 null", resource="manifest")
    row = require_keys(page["rows"][0], MANIFEST_KEYS, "manifest")
    fingerprint = _manifest_identity(row, page, as_of_text)
    days = _snapshot_days(row["snapshotDays"], as_of)
    if row["latestSnapshot"] != (days[-1][0] if days else None):
        raise ContractError("manifest.latestSnapshot 不是 snapshotDays 的最后一天", resource="manifest")
    return Manifest(
        as_of=as_of.astimezone(UTC),
        fingerprint=fingerprint,
        source_revision=row["sourceRevision"],
        counts=_counts(row["counts"]),
        latest_snapshot=row["latestSnapshot"],
        snapshot_days=MappingProxyType(dict(days)),
        meta=_meta(row["meta"]),
        row=MappingProxyType(row),
        metrics=metrics,
        busy_sleeps=tuple(busy_sleeps),
    )
