"""The shape of RealShort's feed responses, feed v2 (`pick-export-v2`) and v1 (`pick-feed-v1`), checked page by page.

Frozen at RealShort 816ca2e (rs = realshort-pick-export-v2). Transport is checked here: envelopes, the cursor chain,
and the manifest's identity (version, asOf, fingerprint), snapshotDays order and window and latestSnapshot. What the
contract says the manifest and the rows contain, and its constants, live in contracts.py alone: parse_manifest hands the
manifest row to contracts.parse_manifest once transport has passed. Drift is an echo that says the source moved: a v2 page with
another fingerprint, a v1 page with another capturedAt, fingerprint or sourceRevision (plan 1520, 1523). A v2 page echoing
another resource or asOf answers a request that was never sent: a contract error, like anything else out of shape
(the brief's P2-2a page checks). Error messages name fields, never values.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType

from ggwork_pick.mirror import contracts
from ggwork_pick.mirror.contracts import COUNTED_RESOURCES, EXPORT_VERSION, SERIES_RESOURCE
from ggwork_pick.mirror.errors import ContractError, DriftError

V1_VERSION = "pick-feed-v1"  # rs:src/lib/pick/feed-map.ts:21; ggwork_pick/sync.py:22
V1_PAGE_LIMIT = 1000  # sync.py:24; RealShort's FEED_MAX_LIMIT (feed-map.ts:22)
V1_MAX_PAGES = 40  # sync.py:25
SERIES_DAY_SPAN = 93  # day runs from the as_of day back 92 days (rs:src/lib/pick/export-v2-page.ts:20, :49-54)
V2_ENVELOPE_KEYS = frozenset({"ok", "version", "resource", "asOf", "fingerprint", "rows", "nextCursor"})  # export-v2-page.ts:125-133
# The fixed words RealShort puts in error bodies (rs:src/lib/pick/feed-http.ts:6-14, export-v2-page.ts:72)
ERROR_WORDS = frozenset({"not_found", "unauthorized", "bad_request", "source_changed", "source_busy", "read_failed", "row_too_large"})
REASON_WORDS = frozenset({"resource", "unknown_param", "duplicate_param", "as_of", "fp", "cursor", "limit", "day"})
AS_OF_LAG = timedelta(minutes=2)  # plan 2.5

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
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
        """One stdout line of the dry-run: the eight metrics and retried (this request repeated a read_failed one),
        plus day and the error word when they say something. The dry-run adds the run as attempt (the brief's P2-2a)."""
        base = {
            "resource": self.resource,
            "page": self.page,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "bytes": self.bytes,
            "wire_bytes": self.wire_bytes,
            "rows": self.rows,
            "retry_after": self.retry_after,
            "retried": self.attempt > 1,
        }
        optional = {"day": self.day, "error": self.error}
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
    """Envelope of any v2 page (rs:src/lib/pick/export-v2.ts:516, :534-538): resource and asOf are contract, fp is drift."""
    label = f"RealShort feed v2 {resource}"
    if not isinstance(body, dict) or body.get("ok") is not True or body.get("version") != EXPORT_VERSION:
        raise ContractError(f"{label} 的版本或格式不符", resource=resource)
    require_keys(body, V2_ENVELOPE_KEYS, f"{label} 的信封")
    if body["resource"] != resource:
        raise ContractError(f"{label} 回显的 resource 不是 {resource}", resource=resource)
    if body["asOf"] != as_of_text:
        raise ContractError(f"{label} 回显的 asOf 与请求的 as_of 不同", resource=resource)
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


def _manifest_identity(row: dict, body: dict, as_of_text: str) -> None:
    """Which manifest this is: read with get(), since whether every key is there is the content check's to say."""
    if row.get("version") != EXPORT_VERSION:
        raise ContractError("manifest.version 不符", resource="manifest")
    if row.get("asOf") != as_of_text:
        raise ContractError("manifest.asOf 与信封不同", resource="manifest")
    fingerprint = row.get("fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT.match(fingerprint) or fingerprint != body["fingerprint"]:
        raise ContractError("manifest.fingerprint 不是 64 位小写十六进制，或与信封不同", resource="manifest")
    revision = row.get("sourceRevision")
    if revision is not None and not isinstance(revision, str):
        raise ContractError("manifest.sourceRevision 不是字符串或 null", resource="manifest")


def _series_day(day: object, as_of: datetime) -> bool:
    try:
        check_series_day(day, as_of)
    except ValueError:
        return False
    return True


def _snapshot_days(value: object, as_of: datetime) -> list[str]:
    """The days of snapshotDays, over the days rs_series_day takes (rs:src/lib/pick/export-v2.ts:440-446) and strictly
    increasing: a day outside that window could never be fetched, so it is a contract error here rather than a
    ValueError from pages() later. Each entry's keys and rows are the content check's."""
    if not isinstance(value, list):
        raise ContractError("manifest.snapshotDays 不是数组", resource="manifest")
    days = []
    for index, item in enumerate(value):
        day = item.get("day") if isinstance(item, dict) else None
        if not _series_day(day, as_of):
            raise ContractError(f"manifest.snapshotDays[{index}] 的 day 不是 as_of 当天往前 {SERIES_DAY_SPAN} 天内的真实日期", resource="manifest")
        days = [*days, day]
    if any(earlier >= later for earlier, later in zip(days, days[1:])):
        raise ContractError("manifest.snapshotDays 不是按日期严格递增", resource="manifest")
    return days


def _manifest_row(body: object, as_of_text: str) -> dict:
    page = check_v2_page(body, resource="manifest", as_of_text=as_of_text, fp=None)
    if len(page["rows"]) != 1 or page["nextCursor"] is not None:
        raise ContractError("manifest 的 rows 必须恰好 1 个元素，nextCursor 为 null", resource="manifest")
    row = page["rows"][0]
    _manifest_identity(row, page, as_of_text)
    return row


def parse_manifest(body: object, *, as_of: datetime, metrics: PageMetrics, busy_sleeps: tuple[int, ...] = ()) -> Manifest:
    """The manifest page (rs:src/lib/pick/export-v2.ts:497-518): transport here, then contracts.parse_manifest for the
    content (every key and nested shape of MANIFEST_SHAPE, forbidden names, NUL); synchronous, so a large page's caller
    runs it in a worker thread."""
    as_of_text = format_as_of(as_of)
    row = _manifest_row(body, as_of_text)
    days = _snapshot_days(row.get("snapshotDays"), as_of)
    if row.get("latestSnapshot") != (days[-1] if days else None):
        raise ContractError("manifest.latestSnapshot 不是 snapshotDays 的最后一天", resource="manifest")
    contracts.parse_manifest(row)
    return Manifest(
        as_of=as_of.astimezone(UTC),
        fingerprint=row["fingerprint"],
        source_revision=row["sourceRevision"],
        counts=MappingProxyType({name: row["counts"][name] for name in COUNTED_RESOURCES}),
        latest_snapshot=row["latestSnapshot"],
        snapshot_days=MappingProxyType({entry["day"]: entry["rows"] for entry in row["snapshotDays"]}),
        meta=MappingProxyType(row["meta"]),
        row=MappingProxyType(row),
        metrics=metrics,
        busy_sleeps=tuple(busy_sleeps),
    )
