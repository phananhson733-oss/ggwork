"""A small Search Console for the TR-07 probe and export tests: searchAnalytics.query computed from facts.

Nothing here reaches Google. The token endpoint is gsc_fake.FakeGoogle's, which verifies every assertion's signature;
queries are answered from a list of facts by the rules the probe measures, each one a switch a test sets: whether hourly
data and daily data honour byPage, whether [hour,page,country] is accepted, how includingRegex matches (RE2's
PartialMatch by default, on the raw URL, with ^ and $ honoured) and how long it may be, how the metadata fields are
spelled, how far final data lags, which pages the detail leaves out (a request filtered on the page still returns them,
as GSC does for rows it drops from a large unfiltered answer), and whether a country-filtered answer counts differently.
Rows are sorted by clicks, then impressions, then keys, and cut at the request's rowLimit, so a test that lowers
query.ROW_LIMIT sees truncation without 25,000 rows.
"""

import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx
from gsc_fake import TOKEN_URL, FakeGoogle, google_error

PT = ZoneInfo("America/Los_Angeles")
_NOT_RE2 = re.compile(r"\(\?[=!<]|\\[1-9]")  # lookaround and backreferences: Perl, not RE2


@dataclass(frozen=True)
class Fact:
    hour: datetime  # timezone-aware start of the hour
    page: str
    country: str  # lowercase alpha-3, as GSC returns it
    query: str
    clicks: int
    impressions: int

    @property
    def day(self) -> date:
        return self.hour.astimezone(PT).date()


@dataclass(frozen=True)
class SimRules:
    now: datetime
    watermark: datetime  # the first incomplete hour
    final_lag_days: int = 3  # final has rows for PT days up to today - lag
    final_extra: int = 0  # impressions final adds to every fact (a late revision)
    hourly_by_page: Literal["honored", "ignored", "rejected"] = "honored"
    daily_by_page: Literal["honored", "ignored"] = "honored"  # [date] with byPage answered byProperty when ignored
    c_shape: Literal["ok", "rejected"] = "ok"
    vh_shape: Literal["ok", "rejected"] = "ok"
    vd_final: Literal["ok", "rejected"] = "ok"  # [date,country] with a page filter and dataState final
    server_error_when: Callable[[dict], bool] | None = None  # the requests answered 503 (a transient failure)
    regex_limit: int = 4096
    regex_mode: Literal["partial", "full"] = "partial"
    regex_subject: Literal["raw", "decoded"] = "raw"  # decoded: includingRegex sees the percent-decoded URL
    anchors: Literal["honored", "ignored"] = "honored"  # ignored: a leading ^ and a trailing $ are dropped
    spelling: Literal["snake", "camel"] = "snake"
    c_spanning_watermark: bool = True  # a [hour,page,...] answer for the watermark's day carries the field
    detail_hidden_pages: frozenset[str] = frozenset()  # left out of every answer with the page dimension and no page filter
    country_filter_extra: int = 0  # impressions a country-filtered answer adds to every fact (a split that disagrees)
    quota_after: int | None = None  # the query after this many is answered 429

    @property
    def today(self) -> date:
        return self.now.astimezone(PT).date()


def _key(fact: Fact, dimension: str) -> str:
    local = fact.hour.astimezone(PT)
    return {
        "date": local.date().isoformat(),
        "hour": local.isoformat(),
        "page": fact.page,
        "country": fact.country,
        "query": fact.query,
    }[dimension]


class SimGoogle(FakeGoogle):
    """FakeGoogle's token endpoint, with searchAnalytics.query answered from facts under rules."""

    def __init__(self, public_key, facts: Iterable[Fact], rules: SimRules):
        super().__init__(public_key)
        self.rules = rules
        by_day: dict[date, list[Fact]] = defaultdict(list)
        for fact in facts:
            by_day[fact.day].append(fact)
        self._by_day = {day: tuple(items) for day, items in by_day.items()}
        self.bodies: tuple[dict, ...] = ()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return super().handle(request)
        self.calls = (*self.calls, request)
        body = json.loads(request.content)
        self.bodies = (*self.bodies, body)
        if self.rules.quota_after is not None and len(self.bodies) > self.rules.quota_after:
            return google_error(429, "RESOURCE_EXHAUSTED", "Search Analytics load quota exceeded (short-term).", reason="rateLimitExceeded")
        if self.rules.server_error_when is not None and self.rules.server_error_when(body):
            return google_error(503, "UNAVAILABLE", "The service is currently unavailable.", reason="backendError")
        problem = self._rejection(body)
        if problem is not None:
            return google_error(400, "INVALID_ARGUMENT", problem, reason="badRequest")
        return httpx.Response(200, json=self._answer(body))

    # ---- what GSC refuses -------------------------------------------------------------------------------------------

    def _rejection(self, body: dict) -> str | None:
        dimensions, rules = body["dimensions"], self.rules
        filters = [item for group in body.get("dimensionFilterGroups", []) for item in group["filters"]]
        if "hour" in dimensions and body.get("aggregationType") == "byPage" and rules.hourly_by_page == "rejected":
            return "byPage aggregation is not supported for hourly data."
        if dimensions == ["hour", "page", "country"] and rules.c_shape == "rejected":
            return "Too many dimensions for hourly data."
        if dimensions == ["hour", "country"] and filters and rules.vh_shape == "rejected":
            return "Page filters are not supported for this request."
        if dimensions == ["date", "country"] and filters and body["dataState"] == "final" and rules.vd_final == "rejected":
            return "dataState final is not supported with this filter."
        for item in filters:
            if item.get("operator") in ("includingRegex", "excludingRegex"):
                if len(item["expression"]) > rules.regex_limit:
                    return "Regular expression is too long."
                if _NOT_RE2.search(item["expression"]):
                    return "Invalid regular expression (RE2 syntax)."
        return None

    # ---- the answer ----------------------------------------------------------------------------------------------------

    def _visible(self, fact: Fact, data_state: str) -> bool:
        if data_state == "final":
            return fact.day <= self.rules.today - timedelta(days=self.rules.final_lag_days)
        return fact.hour < self.rules.now

    def _passes(self, fact: Fact, item: dict) -> bool:
        value, operator, expression = _key(fact, item["dimension"]), item.get("operator", "equals"), item["expression"]
        if operator == "equals":
            return value == expression
        if operator == "includingRegex":
            match = re.search if self.rules.regex_mode == "partial" else re.fullmatch
            subject = unquote(value) if self.rules.regex_subject == "decoded" and item["dimension"] == "page" else value
            return match(self._anchored(expression), subject) is not None
        raise AssertionError(f"the simulator does not know operator {operator}")

    def _anchored(self, expression: str) -> str:
        if self.rules.anchors == "honored":
            return expression
        return expression.removeprefix("^").removesuffix("$")

    def _selected(self, body: dict) -> list[Fact]:
        start, end = date.fromisoformat(body["startDate"]), date.fromisoformat(body["endDate"])
        filters = [item for group in body.get("dimensionFilterGroups", []) for item in group["filters"]]
        page_filtered = any(item["dimension"] == "page" for item in filters)
        hidden = self.rules.detail_hidden_pages if "page" in body["dimensions"] and not page_filtered else frozenset()
        days = (start + timedelta(days=offset) for offset in range((end - start).days + 1))
        return [
            fact
            for day in days
            for fact in self._by_day.get(day, ())
            if self._visible(fact, body["dataState"]) and fact.page not in hidden and all(self._passes(fact, item) for item in filters)
        ]

    def _answer(self, body: dict) -> dict:
        filters = [item for group in body.get("dimensionFilterGroups", []) for item in group["filters"]]
        by_country = any(item["dimension"] == "country" for item in filters)
        extra = (self.rules.final_extra if body["dataState"] == "final" else 0) + (self.rules.country_filter_extra if by_country else 0)
        sums: dict[tuple[str, ...], list[int]] = defaultdict(lambda: [0, 0])
        for fact in self._selected(body):
            cell = sums[tuple(_key(fact, dimension) for dimension in body["dimensions"])]
            cell[0] += fact.clicks
            cell[1] += fact.impressions + extra
        rows = sorted(sums.items(), key=lambda item: (-item[1][0], -item[1][1], item[0]))[: body["rowLimit"]]
        listed = [
            {"keys": list(keys), "clicks": clicks, "impressions": shown, "ctr": clicks / shown if shown else 0.0, "position": 5.0}
            for keys, (clicks, shown) in rows
        ]
        # GSC leaves the rows field out when nothing was observed.
        payload = {**({"rows": listed} if listed else {}), "responseAggregationType": self._aggregation(body)}
        metadata = self._metadata(body)
        return {**payload, **({"metadata": metadata} if metadata else {})}

    def _aggregation(self, body: dict) -> str:
        wanted = body.get("aggregationType")
        if "page" in body["dimensions"]:
            return "byPage"
        hourly = "hour" in body["dimensions"]
        ignored = self.rules.hourly_by_page == "ignored" if hourly else self.rules.daily_by_page == "ignored"
        return "byPage" if wanted == "byPage" and not ignored else "byProperty"

    def _metadata(self, body: dict) -> dict:
        rules, end = self.rules, date.fromisoformat(body["endDate"])
        watermark_day = rules.watermark.astimezone(PT).date()
        fields: dict[str, str] = {}
        if body["dataState"] == "hourly_all" and end >= watermark_day:
            if rules.c_spanning_watermark or "page" not in body["dimensions"]:
                fields["first_incomplete_hour"] = rules.watermark.astimezone(PT).isoformat()
        elif body["dataState"] == "all" and end >= watermark_day:
            fields["first_incomplete_date"] = watermark_day.isoformat()
        elif body["dataState"] == "final" and end > rules.today - timedelta(days=rules.final_lag_days):
            fields["first_incomplete_date"] = (rules.today - timedelta(days=rules.final_lag_days - 1)).isoformat()
        if rules.spelling == "camel":
            return {"firstIncompleteHour" if name.endswith("hour") else "firstIncompleteDate": value for name, value in fields.items()}
        return fields


# ---- a site to answer from --------------------------------------------------------------------------------------------

HOST = "dramashortstv.com"
NOW = datetime(2026, 9, 25, 19, 25, tzinfo=ZoneInfo("UTC"))  # 12:25 PDT on the 25th
WATERMARK = datetime(2026, 9, 25, 3, tzinfo=PT)  # 10:00 UTC: 9.4 hours behind NOW
TODAY = date(2026, 9, 25)
ID_EN, ID_BG, ID_ES, ID_QB, ID_OLD = (
    "6a86c823feaf9677a7003453", "65f0a1b2c3d4e5f607182930", "64b2c3d4e5f60718293a4b5c", "67d4e5f60718293a4b5c6d7e", "66c3d4e5f60718293a4b5c6d",
)  # fmt: skip
BG_SLUG = "%D0%B2%D0%B5%D0%BB%D0%B8%D0%BA%D0%B8%D1%8F%D1%82-%D0%B8-%D0%BC%D0%BE%D0%B3%D1%8A%D1%89-%D0%B4%D0%B6%D0%B8%D0%BD"  # великият-и-могъщ-джин
EN = f"https://{HOST}/en/drama/the-billionaires-secret-wife-{ID_EN}"
BG = f"https://{HOST}/bg/drama/{BG_SLUG}-{ID_BG}"
ES = f"https://{HOST}/es/drama/amor-en-la-oficina-{ID_ES}"
QB = f"https://{HOST}/en/drama/romance-lessons-with-my-quarterback-{ID_QB}"
HOME = f"https://{HOST}/"
BLOG = f"https://{HOST}/blog/mighty-and-great-genie"
DETAIL = f"https://{HOST}/detail/38000"
ID_38000 = f"https://{HOST}/en?id=38000"
ID_49020 = f"https://{HOST}/en?id=49020"
WWW_PLAY = "https://www.dramashortstv.com/bg/video-play/38000/%D0%B2%D0%B5%D0%BB%D0%B8%D0%BA%D0%B8%D1%8F%D1%82/episode-2"
RECENT_HOURS = (1, 5, 9, 13, 17, 21)  # PT hours of the last few days; older days carry one hour at noon


@dataclass(frozen=True)
class Spec:
    """One page x country x query, repeated every sampled hour from `first` days before today to `last` days before."""

    page: str
    country: str
    query: str
    clicks: int
    impressions: int
    first: int = 95
    last: int = 0


SPECS = (
    Spec(EN, "usa", "the billionaire's secret wife", 3, 40),
    Spec(EN, "usa", "billionaire secret wife drama", 1, 12),
    Spec(EN, "phl", "the billionaire's secret wife", 2, 30),
    Spec(EN, "gbr", "the billionaire's secret wife", 0, 6),
    Spec(BG, "bgr", "великият и могъщ джин", 4, 25),
    Spec(ES, "esp", "amor en la oficina", 1, 9),
    Spec(ES, "mex", "amor en la oficina", 1, 14),
    Spec(QB, "usa", "quarterback drama", 0, 3, first=60, last=60),
    Spec(HOME, "usa", "romance lessons with my quarterback", 2, 20),
    Spec(HOME, "phl", "dramashorts", 1, 8),
    Spec(BLOG, "usa", "mighty and great genie", 0, 5),
    Spec(DETAIL, "usa", "genie drama", 0, 4),
    Spec(ID_38000, "bgr", "genie", 1, 7),
    Spec(ID_49020, "usa", "genie blog", 0, 2),
    Spec(WWW_PLAY, "bgr", "великият", 1, 3),
)


def facts_from(specs: Iterable[Spec], *, today: date = TODAY, recent_days: int = 4) -> tuple[Fact, ...]:
    facts = []
    for spec in specs:
        for back in range(spec.last, spec.first + 1):
            day = today - timedelta(days=back)
            hours = RECENT_HOURS if back < recent_days else (12,)
            for hour in hours:
                moment = datetime(day.year, day.month, day.day, hour, tzinfo=PT)
                facts.append(Fact(moment, spec.page, spec.country, spec.query, spec.clicks, spec.impressions))
    return tuple(facts)


def rules(**changes) -> SimRules:
    return SimRules(now=NOW, watermark=WATERMARK, **changes)
