"""searchAnalytics.query: the request body and the response, as pure functions (design 5.1-5.3; plan TR-06).

The body follows rs:src/lib/gsc-encoding.ts:47-76 (rs = realshort-pick-export-v2 816ca2e): rowLimit and dataState are
always sent, because both defaults fail silently (1,000 rows cut off the long tail; `final` returns nothing for recent
days). dataState has no default here at all. Nothing is paged: fresh data is never read with startRow (design 5.2), a
full answer is reported as truncated and the caller splits the slice by country instead.

The response is read strictly, since a value that is not what GSC promised must fail the request, not become a number:
- no rows field means nothing was observed in what GSC returned, never a zero (design 5.3);
- metadata is kept as returned, with first_incomplete_hour and first_incomplete_date parsed (the watermark, design 5.3);
- responseAggregationType is kept: whether hourly data honours byPage is read from it (TR-07 P2).
Dates are Pacific Time calendar days, as GSC counts them; turning them into windows is gsc/cutoff.py's (TR-09).
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import get_args

from ggwork_pick.observe.contract import DataState, SliceStatus

ROW_LIMIT = 25_000  # the API's maximum (rs gsc-encoding.ts:62-64)
SEARCH_TYPE = "web"
DATA_STATES = get_args(DataState)  # all, final, hourly_all: the contract's names are GSC's own
DIMENSIONS = ("date", "hour", "page", "query", "country", "device", "searchAppearance")
FILTER_DIMENSIONS = ("page", "query", "country", "device", "searchAppearance")
AGGREGATION_TYPES = ("auto", "byPage", "byProperty", "byNewsShowcasePanel")
FILTER_OPERATORS = ("equals", "notEquals", "contains", "notContains", "includingRegex", "excludingRegex")
GROUP_TYPES = ("and",)
_GROUP_KEYS = frozenset({"groupType", "filters"})
_FILTER_KEYS = frozenset({"dimension", "operator", "expression"})
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WORD = re.compile(r"^[A-Za-z_]{1,40}$")
_METADATA_FIELDS = {"first_incomplete_date": "firstIncompleteDate", "first_incomplete_hour": "firstIncompleteHour"}
MAX_WATERMARK_LENGTH = 40  # contract_rows.TotalsRow.watermark


class ResponseShapeError(ValueError):
    """A 200 whose body is not what GSC promises. The message names the field, never a value."""


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _filter_problem(item: object) -> str | None:
    if not isinstance(item, Mapping) or not set(item) <= _FILTER_KEYS or not {"dimension", "expression"} <= set(item):
        return "每个 filter 只有 dimension、operator、expression，前后两项必填"
    if item["dimension"] not in FILTER_DIMENSIONS:
        return f"filter 的 dimension 只能是 {', '.join(FILTER_DIMENSIONS)}"
    if item.get("operator", "equals") not in FILTER_OPERATORS:
        return f"filter 的 operator 只能是 {', '.join(FILTER_OPERATORS)}"
    if not isinstance(item["expression"], str) or not item["expression"]:
        return "filter 的 expression 是非空字符串"
    return None


def _group_problem(group: object) -> str | None:
    if not isinstance(group, Mapping) or not set(group) <= _GROUP_KEYS or "filters" not in group:
        return "每个 dimensionFilterGroup 只有 groupType 与 filters，filters 必填"
    if group.get("groupType", "and") not in GROUP_TYPES:
        return "groupType 只能是 and"
    filters = group["filters"]
    if not isinstance(filters, list | tuple) or not filters:
        return "filters 是非空列表"
    return next((problem for problem in map(_filter_problem, filters) if problem is not None), None)


def _query_problem(query: "GscQuery") -> str | None:
    dimensions, groups = query.dimensions, query.dimension_filter_groups
    checks = (
        (all(isinstance(day, date) and not isinstance(day, datetime) for day in (query.start_date, query.end_date)), "起止日期是 date"),
        (isinstance(dimensions, tuple) and set(dimensions) <= set(DIMENSIONS), f"dimensions 是 {', '.join(DIMENSIONS)} 组成的元组"),
        (len(set(dimensions)) == len(dimensions), "dimensions 不重复"),
        (query.data_state in DATA_STATES, f"dataState 必须写明，只能是 {', '.join(DATA_STATES)}"),
        (("hour" in dimensions) == (query.data_state == "hourly_all"), "hour 维度与 hourly_all 成对出现（设计 5.2 的每个请求都是这样）"),
        (query.aggregation_type is None or query.aggregation_type in AGGREGATION_TYPES, f"aggregationType 只能是 {', '.join(AGGREGATION_TYPES)}"),
        (isinstance(groups, list | tuple), "dimension_filter_groups 是列表"),
    )
    problem = next((message for holds, message in checks if not holds), None)
    if problem is None and query.start_date > query.end_date:
        problem = "起始日期晚于结束日期"
    return problem or next((found for found in map(_group_problem, groups) if found is not None), None)


@dataclass(frozen=True)
class GscQuery:
    """One searchAnalytics.query. dimension_filter_groups is GSC's own shape, checked and then sent verbatim; a copy is
    kept, so changing the caller's list afterwards changes nothing."""

    start_date: date
    end_date: date
    dimensions: tuple[str, ...]
    data_state: DataState
    aggregation_type: str | None = None
    dimension_filter_groups: Sequence[Mapping[str, object]] = ()

    def __post_init__(self):
        problem = _query_problem(self)
        if problem is not None:
            raise ValueError(f"GSC 查询不成立：{problem}")
        object.__setattr__(self, "dimension_filter_groups", _freeze(self.dimension_filter_groups))

    @property
    def label(self) -> str:
        """For a log line or an error: the shape and the dates, never the filter expressions."""
        shape = "/".join(filter(None, (f"[{','.join(self.dimensions)}]", self.data_state, self.aggregation_type)))
        filtered = "（带过滤）" if self.dimension_filter_groups else ""
        return f"searchAnalytics.query {shape} {self.start_date.isoformat()}…{self.end_date.isoformat()}{filtered}"


def query_body(query: GscQuery) -> dict:
    """The JSON body. aggregationType and dimensionFilterGroups go out only when given, exactly as given."""
    optional = {
        **({"aggregationType": query.aggregation_type} if query.aggregation_type is not None else {}),
        **({"dimensionFilterGroups": _thaw(query.dimension_filter_groups)} if query.dimension_filter_groups else {}),
    }
    return {
        "startDate": query.start_date.isoformat(),
        "endDate": query.end_date.isoformat(),
        "dimensions": list(query.dimensions),
        "type": SEARCH_TYPE,
        "dataState": query.data_state,
        **optional,
        "rowLimit": ROW_LIMIT,
    }


@dataclass(frozen=True)
class GscRow:
    keys: tuple[str, ...]  # in the query's dimension order, verbatim (a page is GSC's full URL string)
    clicks: int
    impressions: int
    ctr: float
    position: float


@dataclass(frozen=True)
class GscMetadata:
    """The data-state watermark of one response (design 5.3). raw is the metadata object as returned (its string
    fields), stored with the response; a field GSC left out is None, which is legal for a day before the watermark."""

    first_incomplete_date: date | None = None
    first_incomplete_hour: datetime | None = None
    raw: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class GscResponse:
    query: GscQuery
    rows: tuple[GscRow, ...]
    aggregation: str | None  # responseAggregationType as returned
    metadata: GscMetadata
    fetched_at: datetime

    @property
    def truncated(self) -> bool:
        """A full answer: more rows may exist than were returned (design 5.2); the slice is split, never paged."""
        return len(self.rows) == ROW_LIMIT

    @property
    def request_status(self) -> SliceStatus:
        return "truncated" if self.truncated else "fetched"


def parse_day(text: object) -> date:
    """A GSC date (YYYY-MM-DD, a Pacific Time calendar day)."""
    if not isinstance(text, str) or not _DAY.fullmatch(text):
        raise ValueError("不是 YYYY-MM-DD 日期")
    return date.fromisoformat(text)


def parse_hour(text: object) -> datetime:
    """A GSC hour (ISO-8601 with its UTC offset, such as 2026-09-24T17:00:00-07:00). Without an offset it names no instant."""
    if not isinstance(text, str) or not 0 < len(text) <= MAX_WATERMARK_LENGTH:
        raise ValueError("不是带时区偏移的 ISO-8601 时刻")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("不是带时区偏移的 ISO-8601 时刻") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("不是带时区偏移的 ISO-8601 时刻")
    return parsed


def _metadata_text(metadata: Mapping, name: str) -> object:
    """A watermark field by its REST name, or by the camelCase JSON name until TR-07 sees which one GSC sends."""
    found = [metadata[spelling] for spelling in (name, _METADATA_FIELDS[name]) if metadata.get(spelling) is not None]
    if len(found) == 2 and found[0] != found[1]:
        raise ResponseShapeError(f"metadata 的 {name} 两种拼法的值不同")
    return found[0] if found else None


def _metadata(payload: Mapping) -> GscMetadata:
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ResponseShapeError("metadata 不是对象")
    day, hour = _metadata_text(metadata, "first_incomplete_date"), _metadata_text(metadata, "first_incomplete_hour")
    try:
        parsed_day = None if day is None else parse_day(day)
        parsed_hour = None if hour is None else parse_hour(hour)
    except ValueError as bad:
        raise ResponseShapeError(f"metadata 的水位字段：{bad}") from None
    raw = MappingProxyType({key: value for key, value in metadata.items() if isinstance(key, str) and isinstance(value, str)})
    return GscMetadata(first_incomplete_date=parsed_day, first_incomplete_hour=parsed_hour, raw=raw)


def _count(value: object) -> int | None:
    number = value if isinstance(value, int | float) and not isinstance(value, bool) else None
    return int(number) if number is not None and math.isfinite(number) and number >= 0 and number == int(number) else None


def _measure(value: object) -> float | None:
    number = value if isinstance(value, int | float) and not isinstance(value, bool) else None
    return float(number) if number is not None and math.isfinite(number) and number >= 0 else None


def _row(item: object, width: int, number: int) -> GscRow:
    fields = item if isinstance(item, Mapping) else {}
    keys = fields.get("keys")
    if not isinstance(keys, list) or len(keys) != width or not all(isinstance(key, str) for key in keys):
        raise ResponseShapeError(f"第 {number} 行的 keys 不是与维度一一对应的字符串")
    clicks, impressions = _count(fields.get("clicks")), _count(fields.get("impressions"))
    ctr, position = _measure(fields.get("ctr")), _measure(fields.get("position"))
    if None in (clicks, impressions, ctr, position):
        raise ResponseShapeError(f"第 {number} 行的 clicks、impressions 不是非负整数，或 ctr、position 不是非负有限数")
    return GscRow(keys=tuple(keys), clicks=clicks, impressions=impressions, ctr=ctr, position=position)


def parse_response(query: GscQuery, payload: object, *, fetched_at: datetime) -> GscResponse:
    """A 200's JSON body as a GscResponse, or ResponseShapeError."""
    if not isinstance(payload, Mapping):
        raise ResponseShapeError("正文不是 JSON 对象")
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        raise ResponseShapeError("rows 不是列表")
    if len(rows) > ROW_LIMIT:
        raise ResponseShapeError(f"返回行数超过 rowLimit {ROW_LIMIT}")
    aggregation = payload.get("responseAggregationType")
    if aggregation is not None and not (isinstance(aggregation, str) and _WORD.fullmatch(aggregation)):
        raise ResponseShapeError("responseAggregationType 不是一个词")
    width = len(query.dimensions)
    parsed = tuple(_row(item, width, number) for number, item in enumerate(rows, start=1))
    return GscResponse(query=query, rows=parsed, aggregation=aggregation, metadata=_metadata(payload), fetched_at=fetched_at)
