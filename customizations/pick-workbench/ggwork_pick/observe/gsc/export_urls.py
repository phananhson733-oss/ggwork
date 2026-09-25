"""The 90-day page URL list RealShort's legacy export reads, and the positive-control candidates of stage 0
(plan TR-07, TR-08, TR-05; design 5.5, 4.11).

The list: every page URL GSC returned in the window, as the raw string it returned (never decoded; design 5.5), one line
each in JSONL, {raw_url, clicks, impressions}, deduplicated only when two strings are identical, their counts summed
over the requests they came from. Requests are split so that every answer used is short of rowLimit: by calendar month
first, a full month by halving its days down to one day, and a day still full by country (each country with an
`equals` filter, the countries from a [country] request for that day). A site's [page] for one day is rarely full, so
halving costs about two requests per piece, where a country split costs one request for each of some 200 countries;
countries are kept for the day that needs them. Fresh data is never paged (design 5.2). A one-day, one-country answer
that is still full cannot be split further: it is kept and named, and the list is marked incomplete. The manifest
records the window, the dataState, every request's slice and the sha256 of the list, which TR-08 records as its input.

The candidates: dramas whose exact title is searched, by matching the [query,country] rows of the last 28 days against
the title keys of the new drama pages in the list (urls.title_key, RealShort's own rule). Their requests are halved by
days like the list's but never split by country: a day still full is used as it is, since its rows are the top ones by
clicks, which is what a positive control is picked from, and the file says which days those were. A title searched
while GSC showed the home page is still found, as long as the drama's page appeared somewhere in the 90 days. Each
candidate carries its top countries, and the Trends geo market-map-v1 pairs with each, for stage 0 to query it where it
is really searched (design 4.11), with the query and the geo to start from (top_query, suggested_geo). Titles longer
than a slug's 60 codepoints never match: their slug is cut.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import groupby
from typing import Protocol

from ggwork_pick.observe.contract import DataState
from ggwork_pick.observe.gsc.cutoff import pt_date_of
from ggwork_pick.observe.gsc.pacer import Pacer
from ggwork_pick.observe.gsc.query import GscQuery, GscResponse, GscRow, format_day
from ggwork_pick.observe.gsc.urls import parse_new_page, slug_title_key, title_key
from ggwork_pick.observe.market_map import MARKET_MAP_V1
from ggwork_pick.observe.versions import COLLECTOR_VERSION

PT_NAME = "America/Los_Angeles"
MAX_DAYS = 480  # GSC keeps 16 months
DEFAULT_DAYS = 90
DEFAULT_CANDIDATE_DAYS = 28
TOP_COUNTRIES = 5
UNLISTED = "*"  # the countries a full [country] answer left out: a leaf that is never complete
MANIFEST_NOTE = (
    "raw_url 是 GSC 返回的原串，未解码；只对完全相同的串去重，计数跨请求求和。"
    "used 为 true 的切片都不满额；complete 为 false 时，incomplete_leaves 里的切片拆到一天一国仍满额"
)
CANDIDATES_USE = (
    "阶段 0 正对照的候选，转成 trends-stage0-controls-v1 的步骤见 observe-runbook/gsc-probe.md：term 取 top_query，geo 取 suggested_geo，"
    "identity 按 book_id 在共享剧库里找到这部剧后抄它的工作台身份键；泛词剧与已下架剧另行剔除。complete 为 false 时，"
    "incomplete_leaves 里的日子只取到了按点击排前的行"
)
MATCH_RULE = "查询词与新页 slug 按 RealShort 的剧名规则（小写、去撇号、NFC、非字母数字折成一个连字符）得到的键完全相同；slug 截到 60 个码点的长剧名匹配不到"


class QueryClient(Protocol):
    """What a split fetch needs of GscClient."""

    async def query(self, query: GscQuery) -> GscResponse: ...


@dataclass(frozen=True, slots=True)
class Leaf:
    """One request's slice. used: its rows are in the result (False: it was full and its children replace it)."""

    start: date
    end: date
    country: str | None
    rows: int
    truncated: bool
    used: bool

    def as_json(self) -> dict:
        return {"start": format_day(self.start), "end": format_day(self.end), "country": self.country, "rows": self.rows}


@dataclass(frozen=True, slots=True)
class SplitFetch:
    rows: tuple[GscRow, ...]
    leaves: tuple[Leaf, ...]
    requests: int

    @property
    def incomplete(self) -> tuple[Leaf, ...]:
        return tuple(leaf for leaf in self.leaves if leaf.used and leaf.truncated)

    @property
    def complete(self) -> bool:
        return not self.incomplete


def _joined(parent: Leaf | None, parts: Iterable[SplitFetch], requests: int) -> SplitFetch:
    parts = tuple(parts)
    return SplitFetch(
        rows=tuple(row for part in parts for row in part.rows),
        leaves=(*((parent,) if parent is not None else ()), *(leaf for part in parts for leaf in part.leaves)),
        requests=requests + sum(part.requests for part in parts),
    )


def month_ranges(start: date, end: date) -> tuple[tuple[date, date], ...]:
    """[start, end] cut at calendar month boundaries."""
    ranges, first = (), start
    while first <= end:
        following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        last = min(end, following - timedelta(days=1))
        ranges, first = (*ranges, (first, last)), following
    return ranges


def halves(start: date, end: date) -> tuple[tuple[date, date], tuple[date, date]]:
    middle = start + timedelta(days=((end - start).days + 1) // 2 - 1)
    return (start, middle), (middle + timedelta(days=1), end)


@dataclass(frozen=True, slots=True)
class _Plan:
    gsc: QueryClient
    pacer: Pacer
    dimensions: tuple[str, ...]
    data_state: DataState
    split_countries: bool

    def query(self, start: date, end: date, country: str | None = None, dimensions: tuple[str, ...] | None = None) -> GscQuery:
        groups = ({"groupType": "and", "filters": [{"dimension": "country", "operator": "equals", "expression": country}]},) if country else ()
        return GscQuery(start, end, dimensions or self.dimensions, self.data_state, dimension_filter_groups=groups)

    async def ask(self, query: GscQuery) -> GscResponse:
        await self.pacer.before_request()
        return await self.gsc.query(query)


def _site_leaf(start: date, end: date, response: GscResponse, *, used: bool) -> Leaf:
    return Leaf(start, end, None, len(response.rows), response.truncated, used)


async def _by_days(plan: _Plan, start: date, end: date) -> SplitFetch:
    """[start, end] for the whole site, halved while full; a single day still full goes by country when the plan says
    so, and is otherwise used as it is (a leaf that is never complete)."""
    response = await plan.ask(plan.query(start, end))
    if not response.truncated or (start == end and not plan.split_countries):
        return SplitFetch(response.rows, (_site_leaf(start, end, response, used=True),), 1)
    parent = _site_leaf(start, end, response, used=False)
    if start == end:
        return _joined(parent, (await _by_country(plan, start),), 1)
    first, second = halves(start, end)
    return _joined(parent, (await _by_days(plan, *first), await _by_days(plan, *second)), 1)


async def _one_country(plan: _Plan, day: date, country: str) -> SplitFetch:
    response = await plan.ask(plan.query(day, day, country))
    return SplitFetch(response.rows, (Leaf(day, day, country, len(response.rows), response.truncated, True),), 1)


async def _by_country(plan: _Plan, day: date) -> SplitFetch:
    """One full day, one request per country of that day's [country] list; a country still full is kept and named."""
    listing = await plan.ask(plan.query(day, day, dimensions=("country",)))
    parts = tuple([await _one_country(plan, day, row.keys[0]) for row in listing.rows])
    unlisted = (SplitFetch((), (Leaf(day, day, UNLISTED, 0, True, True),), 0),) if listing.truncated else ()
    return _joined(None, (*parts, *unlisted), 1)


async def fetch_split(
    gsc: QueryClient, pacer: Pacer, *, dimensions: Sequence[str], data_state: DataState, start: date, end: date, split_countries: bool = True
) -> SplitFetch:
    """Every row of [start, end] for these dimensions, split until no answer used is full (see the module text)."""
    plan = _Plan(gsc, pacer, tuple(dimensions), data_state, split_countries)
    parts = [await _by_days(plan, *month) for month in month_ranges(start, end)]
    return _joined(None, parts, 0)


def default_end(now: datetime) -> date:
    """The last PT day before today: the newest one that can be whole."""
    return pt_date_of(now) - timedelta(days=1)


def window_json(start: date, end: date) -> dict:
    return {"start": format_day(start), "end": format_day(end), "days": (end - start).days + 1, "timezone": PT_NAME}


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- the URL list ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UrlRow:
    raw_url: str
    clicks: int
    impressions: int


@dataclass(frozen=True, slots=True)
class UrlExport:
    start: date
    end: date
    data_state: DataState
    rows: tuple[UrlRow, ...]
    fetch: SplitFetch


def sum_urls(rows: Iterable[GscRow]) -> tuple[UrlRow, ...]:
    """One row per identical raw string, in code point order, counts summed."""
    ordered = sorted(rows, key=lambda row: row.keys[0])
    grouped = ((url, tuple(group)) for url, group in groupby(ordered, key=lambda row: row.keys[0]))
    return tuple(UrlRow(url, sum(row.clicks for row in group), sum(row.impressions for row in group)) for url, group in grouped)


async def export_page_urls(gsc: QueryClient, pacer: Pacer, *, end: date, days: int, data_state: DataState) -> UrlExport:
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days is within 1..{MAX_DAYS}")
    start = end - timedelta(days=days - 1)
    fetch = await fetch_split(gsc, pacer, dimensions=("page",), data_state=data_state, start=start, end=end)
    return UrlExport(start, end, data_state, sum_urls(fetch.rows), fetch)


def jsonl_bytes(rows: Iterable[UrlRow]) -> bytes:
    lines = (
        json.dumps({"raw_url": row.raw_url, "clicks": row.clicks, "impressions": row.impressions}, ensure_ascii=False, separators=(",", ":")) for row in rows
    )
    return "".join(f"{line}\n" for line in lines).encode("utf-8")


def manifest(export: UrlExport, *, site_url: str, generated_at: datetime, file_name: str, digest: str) -> dict:
    fetch = export.fetch
    return {
        "kind": "gsc-page-urls",
        "file": file_name,
        "sha256": digest,
        "site_url": site_url,
        "generated_at": _stamp(generated_at),
        "collector_version": COLLECTOR_VERSION,
        "window": window_json(export.start, export.end),
        "data_state": export.data_state,
        "dimensions": ["page"],
        "search_type": "web",
        "rows": len(export.rows),
        "clicks": sum(row.clicks for row in export.rows),
        "impressions": sum(row.impressions for row in export.rows),
        "requests": fetch.requests,
        "complete": fetch.complete,
        "incomplete_leaves": [leaf.as_json() for leaf in fetch.incomplete],
        "leaves": [{**leaf.as_json(), "truncated": leaf.truncated, "used": leaf.used} for leaf in fetch.leaves],
        "note": MANIFEST_NOTE,
    }


# ---- positive-control candidates ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CountryShare:
    country: str  # as GSC returns it: lowercase alpha-3
    impressions: int
    clicks: int
    trends_geo: str | None  # the geo market-map-v1 pairs with it (None: the country pairs with no geo)


@dataclass(frozen=True, slots=True)
class CandidatePage:
    locale: str
    book_id: str
    raw_url: str
    impressions: int  # over the URL list's window


@dataclass(frozen=True, slots=True)
class Candidate:
    title_key: str
    impressions: int
    clicks: int
    queries: tuple[str, ...]
    top_query: str  # the matched query with the most impressions: stage 0's term to start from
    countries: tuple[CountryShare, ...]
    pages: tuple[CandidatePage, ...]

    @property
    def suggested(self) -> CountryShare | None:
        """The country with the most impressions that market-map-v1 pairs with a Trends geo."""
        return next((share for share in self.countries if share.trends_geo is not None), None)


@dataclass(frozen=True, slots=True)
class CandidateList:
    start: date
    end: date
    data_state: DataState
    candidates: tuple[Candidate, ...]
    fetch: SplitFetch


def title_pages(rows: Iterable[UrlRow]) -> dict[str, tuple[CandidatePage, ...]]:
    """New drama pages by the title key of their slug, the most seen first."""
    found = ((parse_new_page(row.raw_url), row) for row in rows)
    keyed = sorted(
        ((slug_title_key(page.slug), CandidatePage(page.locale, page.book_id, row.raw_url, row.impressions)) for page, row in found if page is not None),
        key=lambda item: (item[0], -item[1].impressions, item[1].raw_url),
    )
    return {key: tuple(page for _, page in group) for key, group in groupby(keyed, key=lambda item: item[0]) if key}


def _countries(rows: Sequence[GscRow]) -> tuple[CountryShare, ...]:
    ordered = sorted(rows, key=lambda row: row.keys[1])
    shares = (
        CountryShare(country, sum(row.impressions for row in group), sum(row.clicks for row in group), MARKET_MAP_V1.geo_of_country(country.upper()))
        for country, group in ((country, tuple(group)) for country, group in groupby(ordered, key=lambda row: row.keys[1]))
    )
    return tuple(sorted(shares, key=lambda share: (-share.impressions, share.country)))[:TOP_COUNTRIES]


def _top_query(rows: Sequence[GscRow]) -> str:
    ordered = sorted(rows, key=lambda row: row.keys[0])
    totals = ((query, sum(row.impressions for row in group)) for query, group in groupby(ordered, key=lambda row: row.keys[0]))
    return min(totals, key=lambda item: (-item[1], item[0]))[0]


def _candidate(key: str, rows: Sequence[GscRow], pages: tuple[CandidatePage, ...]) -> Candidate:
    return Candidate(
        title_key=key,
        impressions=sum(row.impressions for row in rows),
        clicks=sum(row.clicks for row in rows),
        queries=tuple(sorted({row.keys[0] for row in rows})),
        top_query=_top_query(rows),
        countries=_countries(rows),
        pages=pages,
    )


async def positive_control_candidates(gsc: QueryClient, pacer: Pacer, *, end: date, days: int, pages: Iterable[UrlRow]) -> CandidateList:
    """[query,country] over the last `days` PT days, kept where the query's title key is a new page's slug key. A day
    still full after halving is used as it is (its top rows), never split by country."""
    start = end - timedelta(days=days - 1)
    fetch = await fetch_split(gsc, pacer, dimensions=("query", "country"), data_state="all", start=start, end=end, split_countries=False)
    titles = title_pages(pages)
    keyed = ((title_key(row.keys[0]), row) for row in fetch.rows)
    matched = sorted(((key, row) for key, row in keyed if key in titles), key=lambda item: item[0])
    candidates = (_candidate(key, tuple(row for _, row in group), titles[key]) for key, group in groupby(matched, key=lambda item: item[0]))
    ordered = tuple(sorted(candidates, key=lambda candidate: (-candidate.impressions, candidate.title_key)))
    return CandidateList(start, end, "all", ordered, fetch)


def _candidate_json(candidate: Candidate) -> dict:
    suggested = candidate.suggested
    return {
        "title_key": candidate.title_key,
        "impressions": candidate.impressions,
        "clicks": candidate.clicks,
        "queries": list(candidate.queries),
        "top_query": candidate.top_query,
        "suggested_country": suggested.country if suggested else None,
        "suggested_geo": suggested.trends_geo if suggested else None,
        "top_countries": [
            {"country": share.country, "impressions": share.impressions, "clicks": share.clicks, "trends_geo": share.trends_geo}
            for share in candidate.countries
        ],
        "pages": [{"locale": page.locale, "book_id": page.book_id, "raw_url": page.raw_url, "impressions": page.impressions} for page in candidate.pages],
    }


def candidates_json(found: CandidateList, *, site_url: str, generated_at: datetime, limit: int | None = None) -> dict:
    return {
        "kind": "gsc-positive-control-candidates",
        "site_url": site_url,
        "generated_at": _stamp(generated_at),
        "window": window_json(found.start, found.end),
        "data_state": found.data_state,
        "dimensions": ["query", "country"],
        "requests": found.fetch.requests,
        "complete": found.fetch.complete,
        "incomplete_leaves": [leaf.as_json() for leaf in found.fetch.incomplete],
        "match_rule": MATCH_RULE,
        "use": CANDIDATES_USE,
        "total_candidates": len(found.candidates),
        "candidates": [_candidate_json(candidate) for candidate in found.candidates[:limit]],
    }
