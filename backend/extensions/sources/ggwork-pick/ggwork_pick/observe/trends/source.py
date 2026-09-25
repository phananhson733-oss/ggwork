"""What a Trends source hands the session: the TrendsSource protocol, the ten fetch statuses and the result shapes,
and the cookie-jar seam with TR-04 (design 4.1, 4.4, 4.7, 4.10, 4.11; plan TR-02, D20).

A source turns one query unit (identity x geo x query shape x time range, design 4.5) into at most three HTTP requests:
explore, then multiline for the series and relatedsearches for the related queries. The session (TR-14) owns pacing,
budget, retries and the breaker; a source only reports what each request returned. The rule the rest of the channel
leans on: values exist only where a request succeeded. A failed step leaves every requested part with its status and
no values at all, never a series of zeros (design 4.10, counterexample 1).

Changing the source (a vendor, a proxy, the official API) changes COLLECTOR_VERSION too, and sets from different
sources never confirm one another (design 4.11). The direct client is SOURCE_NAME.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from ggwork_pick.observe.contract import TREND_GEO_PATTERN

# The channel's one cookie jar is TR-04's (plan 5): the parser builds the jar's own cookies, so a Set-Cookie line
# that Cookie's checks refuse is dropped at the parser (trends-client.md, 集成说明).
from ggwork_pick.observe.trends.cookies import Cookie as SetCookie

SOURCE_NAME = "trends-direct"
GEO_WORLDWIDE = "WW"  # the contract's worldwide geo; Google's own spelling of it is the empty string
MAX_TERMS = 5  # comparisonItems in one explore
MAX_TERM_LENGTH = 200
# Granularity (contract TRENDS_WINDOW_KINDS) -> Google's time range: H is hourly over 7 days, D is daily over a month
# (design 4.9 schemes H and D). Stage 0 chooses between them; any other range is refused.
TIMEFRAMES = MappingProxyType({"H": "now 7-d", "D": "today 1-m"})
# Search property (contract DISCOVERY_PROPERTIES) -> Google's gprop: web search is the empty string; YouTube search
# serves the stable-period seeds (design 4.8).
GOOGLE_PROPERTIES = MappingProxyType({"web": "", "youtube": "youtube"})
# A real browser's UA for CookieJar.fresh(): fixed for the jar's life and never rotated (design 4.4).
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
_GEO = re.compile(rf"(?:{TREND_GEO_PATTERN})")
_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f]")


class FetchStatus(StrEnum):
    """The ten outcomes of a request or a series (design 4.10). Only OK and OK_ZERO carry values."""

    OK = "ok"  # a complete series with at least one non-zero point
    OK_ZERO = "ok_zero"  # a complete series of zeros: judged as sparse, never as a failure
    NO_DATA = "no_data"  # HTTP succeeded but there is nothing: no timeline points, no related queries
    RATE_LIMITED = "rate_limited"  # 429
    BLOCKED_REDIRECT = "blocked_redirect"  # a redirect to the sorry (captcha) or consent page: the wall, the day ends
    HTML_BODY = "html_body"  # an API path answered with an HTML page
    FORBIDDEN = "forbidden"  # 403
    SERVER_ERROR = "server_error"  # 5xx
    TIMEOUT = "timeout"  # no HTTP answer: the time limits ran out, or the connection or the far end broke on the way
    # A changed contract: an answer outside the shape the parser relies on, an unnamed 4xx, any other redirect, a 200
    # over the body cap or undecodable, a request httpx could not send. Never retried, never a limit signal.
    PARSE_ERROR = "parse_error"


JUDGEABLE = frozenset({FetchStatus.OK, FetchStatus.OK_ZERO})
# Design 4.3: the signals that trip the breaker. blocked_redirect, a sorry or consent page, also ends the day (TR-03's
# Signal.WALL): no other redirect may produce it.
LIMIT_SIGNALS = frozenset({FetchStatus.RATE_LIMITED, FetchStatus.BLOCKED_REDIRECT, FetchStatus.HTML_BODY, FetchStatus.FORBIDDEN})
RETRYABLE = frozenset({FetchStatus.SERVER_ERROR, FetchStatus.TIMEOUT})  # design 4.2: once, 30-60 seconds later (TR-03)
FAILURES = LIMIT_SIGNALS | RETRYABLE | {FetchStatus.PARSE_ERROR}


class Phase(StrEnum):
    WARMUP = "warmup"
    EXPLORE = "explore"
    MULTILINE = "multiline"
    RELATED = "related"


class RedirectKind(StrEnum):
    SORRY = "sorry"  # google's captcha page
    CONSENT = "consent"  # the EU consent wall; never accepted (design 4.1)
    SAME_HOST = "same_host"  # elsewhere on trends.google.com
    OTHER = "other"


def _check_term(term: object) -> str:
    if not isinstance(term, str) or not term.strip() or len(term) > MAX_TERM_LENGTH or _UNPRINTABLE.search(term):
        raise ValueError(f"a Trends term is 1-{MAX_TERM_LENGTH} printable characters")
    return term


@dataclass(frozen=True)
class TrendsQuery:
    """One query unit's request: up to five terms in one explore, so their lines share one scale.

    bare is the term the judgement reads (design 4.7): the plain title, or None for a generic or contained title, whose
    request carries intent variants only. related_term picks whose related queries to fetch (the bare title by
    default). geo is the contract's: WW or an ISO alpha-2 code."""

    terms: tuple[str, ...]
    bare: str | None
    geo: str
    granularity: str
    search_property: str = "web"  # contract DISCOVERY_PROPERTIES: web or youtube
    related_term: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.terms, tuple) or not 1 <= len(self.terms) <= MAX_TERMS:
            raise ValueError(f"a query has 1 to {MAX_TERMS} terms, as a tuple")
        if len(set(map(_check_term, self.terms))) != len(self.terms):
            raise ValueError("terms repeat")
        for name in ("bare", "related_term"):
            if getattr(self, name) is not None and getattr(self, name) not in self.terms:
                raise ValueError(f"{name} is not one of the terms")
        if not isinstance(self.geo, str) or not _GEO.fullmatch(self.geo):
            raise ValueError("geo is WW or an ISO alpha-2 code")
        if self.granularity not in TIMEFRAMES:
            raise ValueError(f"granularity is one of {sorted(TIMEFRAMES)}")
        if self.search_property not in GOOGLE_PROPERTIES:
            raise ValueError(f"search_property is one of {sorted(GOOGLE_PROPERTIES)}")

    @property
    def timeframe(self) -> str:
        return TIMEFRAMES[self.granularity]

    @property
    def google_geo(self) -> str:
        return "" if self.geo == GEO_WORLDWIDE else self.geo

    @property
    def google_property(self) -> str:
        return GOOGLE_PROPERTIES[self.search_property]

    @property
    def related_keyword(self) -> str:
        return self.related_term or self.bare or self.terms[0]

    def request_params(self) -> dict:
        """The request as the raw row keeps it (design 3.5 ggwp_obs_raw), in the contract's words."""
        return {
            "terms": list(self.terms),
            "bare": self.bare,
            "geo": self.geo,
            "timeframe": self.timeframe,
            "property": self.search_property,
            "category": 0,
            "tz": 0,
            "hl": "en-US",
        }


@runtime_checkable
class TrendsJar(Protocol):
    """The cookie jar as the client uses it; TR-04's trends/cookies.CookieJar is the implementation (plan 5).

    Immutable: updated() and warmed() return a new jar. The UA belongs to the jar and goes out with its cookies in
    request_headers(), which leaves expired cookies out. warmed_on is the target date of the last warm-up (D23), and
    can_warm(day) is False once warmed_on >= day: a jar warms at most once per target date, never for an earlier one.
    The client reads nothing else, and never a repr: cookie values stay inside the jar and the Cookie header."""

    @property
    def user_agent(self) -> str: ...

    @property
    def warmed_on(self) -> date | None: ...

    @property
    def cookie_names(self) -> tuple[str, ...]: ...

    def can_warm(self, day: date) -> bool: ...

    def request_headers(self, now: datetime) -> Mapping[str, str]: ...

    def updated(self, cookies: Iterable[SetCookie], *, now: datetime) -> "TrendsJar": ...

    def warmed(self, cookies: Iterable[SetCookie], *, day: date, now: datetime) -> "TrendsJar": ...


@dataclass(frozen=True)
class EgressReading:
    """The latest egress IP and when it was measured (D20); both None while probing is off or has never succeeded."""

    ip: str | None = None
    measured_at: datetime | None = None


@dataclass(frozen=True)
class RequestStep:
    """What the gate is told before a request goes out (the executor's pacer, budget and lease check, TR-14)."""

    phase: Phase
    query: TrendsQuery | None
    label: str | None


@dataclass(frozen=True)
class RequestRecord:
    """One HTTP request, for ggwp_obs_requests (design 3.5): never a cookie, never a value of the series."""

    phase: Phase
    label: str | None
    started_at: datetime
    latency_ms: float
    http_status: int | None
    fetch_status: FetchStatus
    redirect_kind: RedirectKind | None
    redirect_host: str | None
    user_type: str | None
    bytes: int
    error_class: str | None
    egress: EgressReading


@dataclass(frozen=True)
class Line:
    """One term's series, as sent: each point's time as it came (epoch seconds, a string in the web client's answers),
    values 0-100, isPartial and hasData (None where the key was absent)."""

    term: str
    status: FetchStatus  # OK or OK_ZERO
    raw_times: tuple[str | int, ...]
    values: tuple[int, ...]
    partial: tuple[bool | None, ...]
    has_data: tuple[bool | None, ...]

    @property
    def times(self) -> tuple[int, ...]:
        """Epoch seconds; the parser has checked every raw time is one."""
        return tuple(int(time) for time in self.raw_times)

    def as_raw(self) -> dict:
        """The series for ggwp_obs_raw: the arrays as the answer had them, time included (design 3.5)."""
        return {
            "term": self.term,
            "status": self.status.value,
            "time": list(self.raw_times),
            "value": list(self.values),
            "isPartial": list(self.partial),
            "hasData": list(self.has_data),
        }


@dataclass(frozen=True)
class TimelineResult:
    status: FetchStatus
    lines: tuple[Line, ...] | None = None  # present exactly when status is OK or OK_ZERO

    def __post_init__(self) -> None:
        if (self.lines is not None) != (self.status in JUDGEABLE):
            raise ValueError("a timeline has lines exactly when it is ok or ok_zero")

    def line(self, term: str | None) -> Line | None:
        return next((line for line in self.lines or () if line.term == term), None) if term is not None else None


@dataclass(frozen=True)
class RelatedQuery:
    query: str
    value: int
    formatted_value: str  # verbatim ("100", "+250%", "Breakout"); how Breakout shows is for stage 0 to confirm


@dataclass(frozen=True)
class RelatedResult:
    status: FetchStatus
    top: tuple[RelatedQuery, ...] | None = None
    rising: tuple[RelatedQuery, ...] | None = None
    widget_missing: bool = False  # explore offered no related-queries widget for the term: nothing was requested

    def __post_init__(self) -> None:
        if (self.top is not None) != (self.status is FetchStatus.OK) or (self.rising is None) != (self.top is None):
            raise ValueError("related queries exist exactly when the answer is ok")


@dataclass(frozen=True)
class FetchResult:
    """One query unit. Every part asked for is present: timeline when a series was asked for, related likewise."""

    query: TrendsQuery
    stopped_by: FetchStatus | None  # the failure that ended the unit early, if one did
    user_type: str | None  # explore's userConfig.userType, verbatim
    timeline: TimelineResult | None
    related: RelatedResult | None
    requests: tuple[RequestRecord, ...]
    egress: EgressReading

    @property
    def status(self) -> FetchStatus:
        if self.stopped_by is not None:
            return self.stopped_by
        return self.timeline.status if self.timeline is not None else self.related.status

    def line(self, term: str) -> Line | None:
        return self.timeline.line(term) if self.timeline is not None else None

    @property
    def bare_line(self) -> Line | None:
        return self.line(self.query.bare) if self.query.bare is not None else None

    @property
    def captcha_or_consent(self) -> bool:
        """A sorry or consent page, that is a blocked_redirect: design 4.3 ends the day on the first one."""
        return any(record.redirect_kind in (RedirectKind.SORRY, RedirectKind.CONSENT) for record in self.requests)


@dataclass(frozen=True)
class WarmResult:
    status: FetchStatus | None  # None: the jar cannot warm on that day (warmed_on >= day), nothing was sent
    jar: TrendsJar
    request: RequestRecord | None

    @property
    def captcha_or_consent(self) -> bool:
        return self.request is not None and self.request.redirect_kind in (RedirectKind.SORRY, RedirectKind.CONSENT)


@runtime_checkable
class TrendsSource(Protocol):
    """A Trends source as the session uses it; the direct client is one, a vendor adapter would be another."""

    source_name: str

    @property
    def jar(self) -> TrendsJar: ...

    async def warm(self, *, day: date) -> WarmResult: ...

    async def fetch(self, query: TrendsQuery, *, timeline: bool = True, related: bool = False, label: str | None = None) -> FetchResult: ...
