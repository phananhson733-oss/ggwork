"""Strict contracts of RealShort feed v2 (pick-export-v2) pages: columns, row models, the manifest (plan 3.3, 4.4, 4.5, 4.6).

This module is the one place the feed v2 contract is written down: the version, the resources, RESOURCE_COLUMNS and the
page limits (copied from RealShort 816ca2e src/lib/pick/export-v2-map.ts:116-192, RESOURCE_SPECS), and the manifest's
content (MANIFEST_SHAPE, :853-909). feed_shape.py imports its constants from here and checks only transport (envelope,
identity, snapshotDays order and window), then hands the manifest to parse_manifest. The row models are made from
RESOURCE_COLUMNS, and so are the mirror's DDL and COPY column order. Every model is strict; objects are closed (an
unknown key, a missing column or a value of another type refuses the whole page). Text is kept verbatim, so nothing here
inherits StrictInput (it strips whitespace).

Before any model sees a page, the whole decoded page is checked for NUL and lone surrogates (unstorable_path) and for key
names at any depth that look like forbidden fields (FORBIDDEN_NAME, :28). Errors are PageContractError, a ContractError of
the feed client (errors.py), and name the resource, the row index and the key path, never a value. All of this is
synchronous and CPU-bound; callers run it in asyncio.to_thread.
"""

import keyword
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError, create_model
from pydantic_core import PydanticCustomError

from ggwork_pick.contracts import unstorable_path
from ggwork_pick.mirror.errors import ContractError

EXPORT_VERSION = "pick-export-v2"  # export-v2-map.ts:25
MANIFEST = "manifest"
SERIES_RESOURCE = "rs_series_day"
ROW_RESOURCES = (
    "catalog_rows",
    "catalog_signals",
    "catalog_posted",
    "catalog_accounts",
    "rs_rows",
    "rs_ids",
    "rs_clicks14",
    "rs_bill_orders",
    SERIES_RESOURCE,
)
EXPORT_RESOURCES = (MANIFEST, *ROW_RESOURCES)
# manifest.counts has every row resource but the per-day curve (export-v2.ts readCounts).
COUNTED_RESOURCES = tuple(resource for resource in ROW_RESOURCES if resource != SERIES_RESOURCE)

# export-v2-map.ts:28, verbatim. JS /i without u folds ASCII only, hence re.ASCII.
FORBIDDEN_NAME = re.compile("pan_|promotion_value|promotion_code|promotion_link|revenue_usd|bill_usd|usd", re.IGNORECASE | re.ASCII)

COLUMN_TYPES = ("text", "day", "int", "float", "bool", "ts", "text[]", "json")
ColumnType = Literal["text", "day", "int", "float", "bool", "ts", "text[]", "json"]


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: ColumnType
    nullable: bool


def _column(definition: str) -> Column:
    """One column from "name:type", nullable written "name:type?": RealShort's cols() (export-v2-map.ts:105-114)."""
    name, _, raw = definition.partition(":")
    nullable = raw.endswith("?")
    kind = raw[:-1] if nullable else raw
    if kind not in COLUMN_TYPES:
        raise ValueError(f"export-v2 列 {name} 的类型 {kind} 不认识")
    return Column(name, kind, nullable)


def _columns(*definitions: str) -> tuple[Column, ...]:
    return tuple(_column(definition) for definition in definitions)


# export-v2-map.ts:117-122: the catalog_rows shape, shared by ReelShort rows.
_CATALOG_SHAPED = (
    "row_key:text", "platform:text", "source_table:text", "title:text", "title_cn:text", "lang:text", "kind:text",
    "origin:text", "tags:text", "listed_on:day?", "episodes:int?", "pay_start:int?", "youtube:bool", "merged_rows:int",
    "off_on:day?", "reoff_note:text", "title_key:text", "in_site_ids:text[]", "legacy_only:bool", "site_other:bool",
    "has_signal:bool", "latest_evidence_on:day?",
)  # fmt: skip

# export-v2-map.ts:130-192, resource by resource: the keyset cursor key and the columns in output order.
RESOURCE_KEYS = MappingProxyType(
    {
        "catalog_rows": ("row_key",),
        "catalog_signals": ("row_key", "kind", "ord"),
        "catalog_posted": ("sd",),
        "catalog_accounts": ("id",),
        "rs_rows": ("drama_id",),
        "rs_ids": ("id",),
        "rs_clicks14": ("drama_id", "day"),
        "rs_bill_orders": ("bill_date", "book_id", "promotion_type"),
        "rs_series_day": ("drama_id",),
    }
)
# export-v2-map.ts:130-191 maxLimit: the most rows a page may ask for, and RealShort's default limit (export-v2-page.ts:107).
MAX_LIMITS = MappingProxyType(
    {
        "catalog_rows": 5000,
        "catalog_signals": 5000,
        "catalog_posted": 1000,
        "catalog_accounts": 1000,
        "rs_rows": 2000,
        "rs_ids": 10000,
        "rs_clicks14": 20000,
        "rs_bill_orders": 5000,
        SERIES_RESOURCE: 40000,
    }
)
# fmt: off
RESOURCE_COLUMNS = MappingProxyType(
    {
        "catalog_rows": _columns(*_CATALOG_SHAPED, "imported_at:ts", "has_pan:bool"),
        "catalog_signals": _columns("row_key:text", "kind:text", "ord:int", "evidence_on:day?", "rank:int?", "grade:text", "note:text", "payload:json"),
        "catalog_posted": _columns(
            "sd:text", "feishu_record:text", "title:text", "title_key:text", "lang:text", "platform:text", "life:text",
            "scheduled:bool", "online_on:day?", "why:text", "note:text", "archived:bool", "post_count:int", "last_post_on:day?",
            "views_total:int", "sources:text[]", "cats:text[]", "who:text[]", "accounts:text[]", "created_on:day?",
            "updated_on:day?", "first_post_on:day?", "metric_at:day?", "sched_count:int", "views_count:int", "posts:json",
            "row_keys:text[]", "drama_ids:text[]", "imported_at:ts",
        ),
        "catalog_accounts": _columns(
            "id:text", "name:text", "url:text", "grp:text", "form:text", "niche:text", "status:text", "fans:int?", "as_of:day?",
            "imported_at:ts",
        ),
        "rs_rows": _columns(
            *_CATALOG_SHAPED,
            "has_pan:bool", "rs_clk:bool", "rs_bill:bool", "rs_gsc:bool", "rs_clk_on:day?", "rs_bill_on:day?", "rs_gsc_on:day?",
            "drama_id:text",
            "locale:text", "slug:text", "publish_at:ts?", "chapter_count:int", "pay_start_raw:int", "rr:float", "promoters_cnt:int",
            "metrics_valid:bool?", "synced_at:ts?", "search_impressions:int", "search_data_at:ts?", "detail_synced_at:ts?",
            "tag_list:text[]", "description:text",
            "baseline1_at:ts?", "baseline7_at:ts?", "baseline15_at:ts?", "rr1:float?", "p1:int?", "rr7:float?", "p7:int?",
            "rr15:float?", "p15:int?", "s1_rr:float?", "s1_p:int?", "s7_rr:float?", "s7_p:int?", "clicks7:int",
            "last_click_on:day?", "bill_orders:int", "last_bill_on:day?", "bill_rank:int?",
        ),
        "rs_ids": _columns(
            "id:text", "canonical_id:text?", "locale:text", "slug:text", "title:text", "chapter_count:int", "pay_start:int",
            "is_public_canonical:bool",
        ),
        "rs_clicks14": _columns("drama_id:text", "day:day", "human:int", "bot:int"),
        "rs_bill_orders": _columns(
            "bill_date:day", "book_id:text", "promotion_type:text", "canonical_id:text?", "book_title:text", "order_cnt:int",
            "source_rows:int", "same_day_clicks:int",
        ),
        "rs_series_day": _columns("drama_id:text", "revenue_cents:float", "promoters_cnt:int"),
    }
)
# fmt: on
# export-v2-map.ts:195 and :197: the keys a signal payload and a posted post may have.
SIGNAL_PAYLOAD_KEYS = ("d", "w", "weeks", "best", "days", "first", "h", "qy", "pid")
POSTED_POST_KEYS = ("d", "acct", "st", "views", "likes", "favs", "cmts", "shares", "md", "url", "note", "how", "pid")

# The manifest's records (MANIFEST_SHAPE's rec(of, keys), export-v2-map.ts:785-793, :853-909) come in two kinds here:
# - keyed by one of RealShort's business enums (PLATFORMS, BASES, RANKS, RS_RANKS, SORTS, EXPORT_SOURCES in request.ts,
#   observe/metrics.ts, observe/source-types.ts) or by any key (rec(of, null)): the enums grow with RealShort releases (a
#   new theater), so any key is taken (_open_record) and only the value's shape is checked; forbidden key names are still
#   refused at any depth (FORBIDDEN_NAME);
# - keyed by a list written into the shape itself (growthBaseline ["1", "7"], youtubeLabels, counts' eight resources):
#   fixed there, so fixed here (_keyed; counts even needs all eight).
# test_manifest_models_take_extra_keys_exactly_where_realshort_does holds this against the generated MANIFEST_SHAPE.
SOURCE_DETAIL_KEYS = (
    "startDate", "endDate", "timezone", "dataState", "rows", "expectedRows", "unresolvedPages", "unmatchedQueries",
    "pageRows", "queryRows", "truncated", "partial", "scope", "ratio", "billPeriod", "termsFetchedAt",
)  # fmt: skip

# Date.toISOString(): what RealShort's ts columns always are (export-v2-map.ts:709-712).
TS_TEXT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
DAY_TEXT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MAX_SAFE_INTEGER = 2**53 - 1
# meta.scrub keys are toExportRow's field paths such as catalog_signals.payload.h[*][*] (export-v2-map.ts:641-672).
SCRUB_PATH = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+|\[\*\]){0,16}")
SCRUB_PATH_MAX = 200
# meta.warnings codes are fixed words (catalog_import_incomplete, source_stale_running; export-v2.ts:183-190).
WARNING_CODE = re.compile(r"[a-z_]{1,64}")
# Longest piece of a key path an error repeats: key names are not values, but nothing bounds their length.
PATH_PART_MAX = 64
# A key that is not a plain name could be text out of a row: an error path names its place, never the key.
PLAIN_PATH_PART = re.compile(r"[A-Za-z0-9_?.*\[\]]+")
ODD_KEY = "<非常规键名>"


def ts_datetime(value: str) -> datetime:
    """A RealShort timestamp as an aware UTC datetime; ValueError, without the value, for anything else."""
    parsed = _parse_ts(value) if isinstance(value, str) and TS_TEXT.fullmatch(value) else None
    if parsed is None:
        raise ValueError("不是 RealShort 的毫秒 UTC 时间（YYYY-MM-DDTHH:MM:SS.sssZ）")
    return parsed


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _timestamp(value: str) -> str:
    if TS_TEXT.fullmatch(value) is None or _parse_ts(value) is None:
        raise PydanticCustomError("realshort_ts", "应是 RealShort 的毫秒 UTC 时间（YYYY-MM-DDTHH:MM:SS.sssZ）")
    return value


def _real_day(value: str) -> str:
    """A calendar day as YYYY-MM-DD in ASCII digits: what PG's to_char writes and rs_series_day's day parameter must be."""
    if DAY_TEXT.fullmatch(value) is None or _parse_day(value) is None:
        raise PydanticCustomError("realshort_day", "应是 YYYY-MM-DD 的真实日期")
    return value


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _scalar(value):
    """RealShort's SCALAR shape: a string, a finite number, a boolean or null."""
    if value is None or isinstance(value, str | bool | int) or (isinstance(value, float) and math.isfinite(value)):
        return value
    raise PydanticCustomError("realshort_scalar", "应是字符串、有限数、布尔或 null")


def _scrub_paths(value):
    """meta.scrub before its values are read: every key a field path. The error is on meta.scrub and names no key."""
    if isinstance(value, dict) and not all(isinstance(key, str) and len(key) <= SCRUB_PATH_MAX and SCRUB_PATH.fullmatch(key) for key in value):
        raise PydanticCustomError("realshort_scrub_path", "键应是 toExportRow 的字段路径（资源.列[.子键]，数组下标写 [*]）")
    return value


def _warning_code(value: str) -> str:
    if WARNING_CODE.fullmatch(value) is None:
        raise PydanticCustomError("realshort_warning_code", "应是小写字母与下划线组成的告警代号")
    return value


def _is_json(value) -> bool:
    if value is None or isinstance(value, str | bool | int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json(item) for item in value)
    return isinstance(value, dict) and all(isinstance(key, str) and _is_json(item) for key, item in value.items())


def _json_value(value):
    """Loose JSON (payload and post values): any shape RealShort's isJsonValue accepts, so no NaN or Infinity."""
    if not _is_json(value):
        raise PydanticCustomError("realshort_json", "应是 JSON 值（数只收有限数）")
    return value


Timestamp = Annotated[StrictStr, AfterValidator(_timestamp)]
RealDay = Annotated[StrictStr, AfterValidator(_real_day)]
Scalar = Annotated[Any, AfterValidator(_scalar)]
JsonValue = Annotated[Any, AfterValidator(_json_value)]
SafeInt = Annotated[StrictInt, Field(ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER)]
Count = Annotated[StrictInt, Field(ge=0, le=MAX_SAFE_INTEGER)]
Fingerprint = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
ScrubCounts = Annotated[dict[StrictStr, Count], BeforeValidator(_scrub_paths)]
WarningCode = Annotated[StrictStr, AfterValidator(_warning_code)]


class StrictContract(BaseModel):
    """Closed and strict; values are never coerced and never echoed into error text."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False, hide_input_in_errors=True)


def _field_name(key: str) -> str:
    """An attribute name for a JSON key; the key itself stays the alias, so input and output use RealShort's names."""
    usable = key.isidentifier() and not keyword.iskeyword(key) and not key.startswith("_") and not hasattr(BaseModel, key)
    return key if usable else f"k_{key}"


def _fields(keys, value_type, *, required: bool) -> dict:
    default = ... if required else None
    return {_field_name(key): (value_type, Field(default=default, alias=key)) for key in keys}


def _flat(name: str, keys, *, partial: bool = False) -> type[StrictContract]:
    """RealShort's flat(): scalars under fixed keys; partial = keys may be missing."""
    return create_model(name, __base__=StrictContract, __module__=__name__, **_fields(keys, Scalar, required=not partial))


def _keyed(name: str, keys, value_type) -> type[StrictContract]:
    """RealShort's rec(of, keys) with keys written into the shape: any of them, each holding value_type; another key refuses the page."""
    return create_model(name, __base__=StrictContract, __module__=__name__, **_fields(keys, value_type, required=False))


def _open_record(value_type):
    """The type of RealShort's rec(of, null), or rec(of, <a business enum>): any key, each value of value_type."""
    return dict[StrictStr, value_type]


SignalPayload = create_model("SignalPayload", __base__=StrictContract, __module__=__name__, **_fields(SIGNAL_PAYLOAD_KEYS, JsonValue, required=False))
PostedPost = create_model("PostedPost", __base__=StrictContract, __module__=__name__, **_fields(POSTED_POST_KEYS, JsonValue, required=False))

_BASE_TYPES = MappingProxyType(
    {"text": StrictStr, "day": StrictStr, "int": SafeInt, "float": StrictFloat, "bool": StrictBool, "ts": Timestamp, "text[]": list[StrictStr]}
)
# A day column is only a string to RealShort (normalizeValue, export-v2-map.ts:741-743), and most are text it never checks
# (every day column is PG text there). The ones its SQL formats are held to a real YYYY-MM-DD: rs_clicks14.day is
# to_char(..., 'YYYY-MM-DD') (export-v2.ts:242).
_REAL_DAY_COLUMNS = frozenset({("rs_clicks14", "day")})
# export-v2-map.ts:200-203: the only json columns, each with its key whitelist.
_JSON_TYPES = MappingProxyType({("catalog_signals", "payload"): SignalPayload, ("catalog_posted", "posts"): list[PostedPost]})


class MirrorRow(StrictContract):
    """One row of a row resource; each resource's model is made from RESOURCE_COLUMNS."""

    resource: ClassVar[str] = ""


def _column_type(resource: str, column: Column):
    if column.type == "json":
        if (resource, column.name) not in _JSON_TYPES:
            raise ValueError(f"export-v2 的 json 列 {resource}.{column.name} 没有登记键白名单")
        base = _JSON_TYPES[(resource, column.name)]
    elif (resource, column.name) in _REAL_DAY_COLUMNS:
        base = RealDay
    else:
        base = _BASE_TYPES[column.type]
    return base | None if column.nullable else base


def _row_model(resource: str) -> type[MirrorRow]:
    fields = {column.name: (_column_type(resource, column), ...) for column in RESOURCE_COLUMNS[resource]}
    name = "".join(part.title() for part in resource.split("_")) + "Row"
    model = create_model(name, __base__=MirrorRow, __module__=__name__, **fields)
    model.resource = resource
    return model


ROW_MODELS = MappingProxyType({resource: _row_model(resource) for resource in ROW_RESOURCES})

# ---------------------------------------------------------------- manifest (export-v2-map.ts:853-909)

Counts = create_model("Counts", __base__=StrictContract, __module__=__name__, **_fields(COUNTED_RESOURCES, Count, required=True))
SnapshotDay = create_model("SnapshotDay", __base__=StrictContract, __module__=__name__, day=(RealDay, ...), rows=(Count, ...))
Freshness = _flat("Freshness", ("importedAt", "rows", "withSignal", "signals", "posted", "rsCanonical", "rsCandidates", "rsSyncedAt"))
RsCounts = _flat("RsCounts", ("all", "cand", "growthD1", "growthD7", "growthDp1", "growthDp7", "pc", "clk", "gsc", "bill", "ledger"))
GrowthBaseline = _flat("GrowthBaseline", ("baselineDay", "baselineSnapshot", "earliestVerifiedOn"))
SourceDetails = _flat("SourceDetails", SOURCE_DETAIL_KEYS, partial=True)
PlatformRule = _flat("PlatformRule", ("key", "name", "doc", "updated", "back", "report", "yt", "ytNote", "tag", "unban", "material", "signals"))
GlossaryItem = _flat("GlossaryItem", ("t", "a", "ask", "d", "h"), partial=True)
Lang = _flat("Lang", ("lang", "n"))
FacetPosted = _flat("FacetPosted", ("pool", "yes", "no"))
PostedStats = _flat("PostedStats", ("total", "pubCount", "postsSum", "viewsSum", "metricAt", "importedAt", "accountCount"))
PostedStates = _flat("PostedStates", ("pub", "sched", "none", "nomatch"))
Ledger = _flat("Ledger", ("rows", "orders"))


class ManifestWarning(StrictContract):
    """flat(["code", "source", "status", "attemptedAt"]), with code held to a fixed word: the dry-run prints codes only."""

    code: WarningCode
    source: Scalar
    status: Scalar
    attemptedAt: Scalar


class Source(StrictContract):
    source: Scalar
    status: Scalar
    attemptedAt: Scalar
    completedAt: Scalar
    details: SourceDetails


class GlossaryGroup(StrictContract):
    g: Scalar
    d: Scalar
    items: list[GlossaryItem]


class Facets(StrictContract):
    platforms: _open_record(Scalar)  # rec(SCALAR, PLATFORMS)
    langs: list[Lang]
    bases: _open_record(Scalar)  # rec(SCALAR, BASES)
    posted: FacetPosted


class Rules(StrictContract):
    platformRules: _open_record(PlatformRule)  # rec(flat(...), PLATFORMS)
    inUse: list[Scalar]
    basisLabels: _open_record(Scalar)  # rec(SCALAR, BASES)
    basisDateLabels: _open_record(Scalar)  # rec(SCALAR, BASES)
    rsRankLabels: _open_record(Scalar)  # rec(SCALAR, RS_RANKS)
    youtubeLabels: _keyed("YoutubeLabels", ("ok", "only", "warn", "no"), Scalar)
    glossary: list[GlossaryGroup]
    ruleHints: _open_record(Scalar)  # rec(SCALAR)
    langLoc: _open_record(Scalar)  # rec(SCALAR)
    postedPoolUrl: Scalar
    sortLabels: _open_record(Scalar)  # rec(SCALAR, RS_SORTS)


class Control(StrictContract):
    facetsPick: Facets
    facetsAll: Facets
    rankCounts: _open_record(Scalar)  # rec(SCALAR, RANKS): only theaters with signals have a key (queries-rank.ts:139-140)
    postedStats: PostedStats
    postedStates: PostedStates
    ledger: Ledger


class Meta(StrictContract):
    freshness: Freshness
    rsCounts: RsCounts
    growthBaseline: _keyed("GrowthBaselines", ("1", "7"), GrowthBaseline)
    sources: _open_record(Source)  # rec(obj(...), EXPORT_SOURCES)
    rules: Rules
    control: Control
    scrub: ScrubCounts
    warnings: list[ManifestWarning]


class ManifestModel(StrictContract):
    """The manifest's content as MANIFEST_SHAPE promises it (not the client's checked feed_shape.Manifest)."""

    version: Literal["pick-export-v2"]
    asOf: Timestamp
    fingerprint: Fingerprint
    sourceRevision: StrictStr | None
    counts: Counts
    latestSnapshot: StrictStr | None
    snapshotDays: list[SnapshotDay]
    meta: Meta


# ---------------------------------------------------------------- parsing


class PageContractError(ContractError):
    """A page is not what feed v2 promises: a ContractError that also carries the row index (None above the rows) and key path."""

    def __init__(self, resource: str, row: int | None, path: str, reason: str):
        where = f"第 {row} 行的 " if row is not None else ""
        super().__init__(f"RealShort feed v2 {resource} {where}{path or '整条记录'} 不符合约定：{reason}", resource=resource)
        self.row = row
        self.path = path


def _path_part(part) -> str:
    text = str(part)[:PATH_PART_MAX]
    return text if PLAIN_PATH_PART.fullmatch(text) else ODD_KEY


def _path(parts) -> str:
    return ".".join(_path_part(part) for part in parts)


def _located(resource: str, parts: list, reason: str) -> PageContractError:
    """An error at parts, a path from the page (or the manifest) root; a row resource's rows[i] becomes the row index."""
    if len(parts) >= 2 and parts[0] == "rows" and isinstance(parts[1], int):
        row = None if resource == MANIFEST else parts[1]
        return PageContractError(resource, row, _path(parts[2:]), reason)
    return PageContractError(resource, None, _path(parts), reason)


def _forbidden_key_at(value) -> list | None:
    """The path to the first key, at any depth, that looks like a forbidden field, or None."""
    if isinstance(value, dict):
        pairs = value.items()
    elif isinstance(value, list):
        pairs = enumerate(value)
    else:
        return None
    for key, item in pairs:
        if isinstance(key, str) and FORBIDDEN_NAME.search(key):
            return [key]
        found = _forbidden_key_at(item)
        if found is not None:
            return [key, *found]
    return None


def _unstorable_parts(value) -> list | None:
    """unstorable_path split back into parts, a row index as a number."""
    where = unstorable_path(value)
    if where is None:
        return None
    parts = where.split(".")
    if len(parts) >= 2 and parts[0] == "rows" and parts[1].isdigit():
        return ["rows", int(parts[1]), *parts[2:]]
    return parts


def _check_text_and_names(resource: str, value) -> None:
    unstorable = _unstorable_parts(value)
    if unstorable is not None:
        raise _located(resource, unstorable, "含 NUL 字符或孤立代理项")
    forbidden = _forbidden_key_at(value)
    if forbidden is not None:
        raise _located(resource, forbidden, "键名像禁止字段（网盘、推广或金额）")


def _validated(model: type[StrictContract], value) -> tuple[StrictContract | None, list]:
    try:
        return model.model_validate(value), []
    except ValidationError as exc:
        return None, exc.errors(include_url=False, include_context=False, include_input=False)


def _contract(resource: str, model: type[StrictContract], value, row: int | None) -> StrictContract:
    parsed, errors = _validated(model, value)
    if parsed is not None:
        return parsed
    # Raised outside the except block: no ValidationError (whose text holds values) rides along as __context__.
    first = errors[0]
    raise PageContractError(resource, row, _path(first["loc"]), f"{first['type']}，共 {len(errors)} 处")


def parse_manifest(manifest) -> ManifestModel:
    """The manifest object (the one row of the manifest page), checked like a page and validated against MANIFEST_SHAPE."""
    _check_text_and_names(MANIFEST, manifest)
    return _contract(MANIFEST, ManifestModel, manifest, row=None)


def parse_page(resource: str, page) -> tuple[StrictContract, ...]:
    """One decoded page body of feed v2 -> its rows as models (a manifest page -> a 1-tuple of ManifestModel).

    Only rows is read here; the envelope (ok, version, resource, asOf, fingerprint, nextCursor) is the client's to check.
    The whole page is refused on the first problem, with a PageContractError that holds no value.
    """
    if resource not in EXPORT_RESOURCES:
        raise ValueError(f"feed v2 没有资源 {str(resource)[:PATH_PART_MAX]}")
    if not isinstance(page, dict) or not isinstance(page.get("rows"), list):
        raise PageContractError(resource, None, "rows", "页面应是带 rows 数组的对象")
    _check_text_and_names(resource, page)
    rows = page["rows"]
    if resource == MANIFEST:
        if len(rows) != 1:
            raise PageContractError(resource, None, "rows", f"manifest 页应正好一行，实际 {len(rows)} 行")
        return (_contract(resource, ManifestModel, rows[0], row=None),)
    model = ROW_MODELS[resource]
    return tuple(_contract(resource, model, value, row=index) for index, value in enumerate(rows))


def row_values(row: MirrorRow) -> tuple:
    """The row's values in RESOURCE_COLUMNS order, JSON columns back as the plain dicts and lists RealShort sent."""
    dumped = row.model_dump(by_alias=True, exclude_unset=True)
    return tuple(dumped[column.name] for column in RESOURCE_COLUMNS[row.resource])
