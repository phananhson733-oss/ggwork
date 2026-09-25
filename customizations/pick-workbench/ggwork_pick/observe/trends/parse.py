"""Reading Google Trends answers: classification, the )]}' prefix, explore widgets, timelines, related queries
(design 4.1, 4.3, 4.10; plan TR-02).

Pure functions over what one HTTP answer held. Every shape the channel relies on is checked, and anything outside it
raises ParseFailure, which the client reports as parse_error: an unexpected answer is a changed contract, never
something to guess around, and never a zero. The shapes come from the research and are pinned by constructed fixtures
until stage 0 (TR-05) replaces them with real responses; tests/fixtures/trends lists what is still unconfirmed.

Kept verbatim, as the raw row needs it (design 3.5): each point's time (epoch seconds), value, isPartial and hasData,
with None where a key was absent rather than a filled-in default; userType as sent.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from ggwork_pick.observe.trends.source import (
    FetchStatus,
    Line,
    Phase,
    RedirectKind,
    RelatedQuery,
    RelatedResult,
    TimelineResult,
    TrendsQuery,
)

XSSI_PREFIX = ")]}'"
SERIES_WIDGET = "TIMESERIES"
RELATED_WIDGET = "RELATED_QUERIES"  # RELATED_QUERIES, or RELATED_QUERIES_<n> with several terms
BASE_HOST = "trends.google.com"
MAX_POINTS = 2000  # now 7-d is 169 hourly points, today 1-m about 30 days
MAX_VALUE = 100  # values are indexed to the peak of the request
MAX_LABEL_LENGTH = 100
_DIGITS = re.compile(r"^[0-9]{1,12}$")
_LABEL = re.compile(r"^[\x20-\x7e]{1,100}$")
# Google's own hosts: www.google.com, ipv4.google.com, www.google.de, www.google.co.uk and the like.
_GOOGLE_HOST = re.compile(r"^([a-z0-9-]+\.)*google\.[a-z]{2,3}(\.[a-z]{2})?$")
_NAMED_STATUSES = {429: FetchStatus.RATE_LIMITED, 403: FetchStatus.FORBIDDEN}


class ParseFailure(Exception):
    """An answer outside the expected shape. The message names the field, never a value from the answer."""


def strip_xssi(text: str) -> str:
    """Drop the anti-hijacking prefix )]}' (with or without a trailing comma) and the line break after it."""
    if not text.startswith(XSSI_PREFIX):
        return text
    rest = text[len(XSSI_PREFIX) :]
    return rest[1:].lstrip() if rest.startswith(",") else rest.lstrip()


def decode_json(body: bytes) -> object:
    try:
        return json.loads(strip_xssi(body.decode("utf-8")))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ParseFailure("the answer is not JSON after the prefix") from None


# ---- classification ------------------------------------------------------------------------------------------------


def redirect_kind(location: str) -> tuple[RedirectKind, str | None]:
    """Where a redirect points: the sorry (captcha) page, the consent wall, elsewhere on Trends, or anywhere else."""
    try:
        parts = urlsplit(location.strip())
        host = (parts.hostname or "").lower() or (BASE_HOST if location.strip().startswith("/") else "")
    except ValueError:
        return RedirectKind.OTHER, None
    if not host:
        return RedirectKind.OTHER, None
    if host.startswith("consent.") and (_GOOGLE_HOST.match(host) or host == "consent.youtube.com"):
        return RedirectKind.CONSENT, host
    if _GOOGLE_HOST.match(host) and parts.path.startswith("/sorry"):
        return RedirectKind.SORRY, host
    if host == BASE_HOST:
        return RedirectKind.SAME_HOST, host
    return RedirectKind.OTHER, host


def _looks_html(headers: Mapping[str, str], body: bytes) -> bool:
    return "text/html" in headers.get("content-type", "").lower() or body.lstrip()[:1] == b"<"


def classify(phase: Phase, status: int, headers: Mapping[str, str], body: bytes) -> FetchStatus | None:
    """The status an HTTP answer earns before its body is read; None means a 200 from an API path, to be parsed.

    Design 4.3's limit signals: 429, a redirect to the sorry or consent page, HTML from an API path, 403. The warm-up
    asks for the Trends page itself, so HTML is its success, and a redirect within trends.google.com still warms."""
    if status in _NAMED_STATUSES:
        return _NAMED_STATUSES[status]
    if 500 <= status < 600:
        return FetchStatus.SERVER_ERROR
    if 300 <= status < 400:
        kind, _ = redirect_kind(headers.get("location", ""))
        return FetchStatus.OK if phase is Phase.WARMUP and kind is RedirectKind.SAME_HOST else FetchStatus.BLOCKED_REDIRECT
    if status != 200:
        return FetchStatus.PARSE_ERROR  # a 4xx the design does not name: the request contract no longer holds
    if phase is Phase.WARMUP:
        return FetchStatus.OK
    return FetchStatus.HTML_BODY if _looks_html(headers, body) else None


_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,64}$")
_COOKIE_VALUE = re.compile(r"^[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]{0,4096}$")  # RFC 6265 cookie-octet


def _removes(attributes: str) -> bool:
    for attribute in attributes.split(";"):
        key, _, value = attribute.partition("=")
        if key.strip().lower() == "max-age" and re.fullmatch(r"-?[0-9]{1,12}", value.strip()) and int(value) <= 0:
            return True
    return False


def cookie_updates(set_cookies: Sequence[str]) -> tuple[tuple[str, str | None], ...]:
    """Name and value of each Set-Cookie line, None for one that removes the cookie; malformed lines are dropped.

    Attributes other than Max-Age are ignored: the client only ever talks to trends.google.com."""
    updates = []
    for line in set_cookies:
        pair, _, attributes = line.partition(";")
        name, sep, value = pair.partition("=")
        name, value = name.strip(), value.strip()
        if sep and _COOKIE_NAME.match(name) and _COOKIE_VALUE.match(value):
            updates.append((name, None if not value or _removes(attributes) else value))
    return tuple(updates)


# ---- explore -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Widget:
    id: str
    token: str
    request: dict  # sent back as the widget request, verbatim
    keyword: str | None  # a related widget's term


@dataclass(frozen=True)
class Explore:
    timeseries: Widget | None
    related: Widget | None  # None: no related-queries widget for the term (a SCRAPER session may lose them)
    user_type: str | None


def _object(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise ParseFailure(f"{what} is not an object")
    return value


def _list(value: object, what: str) -> list:
    if not isinstance(value, list):
        raise ParseFailure(f"{what} is not a list")
    return value


def _used(raw: object) -> bool:
    """The widgets the client sends back: the series and the related queries. Others (GEO_MAP, RELATED_TOPICS, any
    Google adds) are not checked, so a new or changed widget the channel does not use cannot fail a unit."""
    ident = _object(raw, "widget").get("id")
    return isinstance(ident, str) and (ident == SERIES_WIDGET or ident.startswith(RELATED_WIDGET))


def _widget(raw: dict) -> Widget:
    ident, token, request = raw.get("id"), raw.get("token"), raw.get("request")
    if not isinstance(token, str) or not token or not isinstance(request, dict):
        raise ParseFailure("a series or related widget lacks its token or request")
    keyword = _path(request, "restriction", "complexKeywordsRestriction", "keyword")
    first = keyword[0] if isinstance(keyword, list) and keyword and isinstance(keyword[0], dict) else {}
    value = first.get("value")
    return Widget(id=ident, token=token, request=request, keyword=value if isinstance(value, str) else None)


def _path(value: object, *keys: str) -> object:
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _label(value: object) -> str | None:
    """userType as sent, when it is a short printable string; anything else reads as absent, not as a failure."""
    return value if isinstance(value, str) and _LABEL.match(value) else None


def _related_widget(widgets: Sequence[Widget], keyword: str, single: bool) -> Widget | None:
    candidates = [widget for widget in widgets if widget.id.startswith(RELATED_WIDGET)]
    matched = [widget for widget in candidates if widget.keyword == keyword]
    if matched:
        return matched[0]
    return candidates[0] if single and len(candidates) == 1 else None


def parse_explore(payload: object, query: TrendsQuery, *, timeline: bool, related: bool) -> Explore:
    """The series widget (required when a timeline is asked for), the related-queries widget for the query's related
    keyword (None when explore offers none), and userConfig.userType from the widget the client uses."""
    widgets = [_widget(raw) for raw in _list(_object(payload, "explore answer").get("widgets"), "widgets") if _used(raw)]
    series = next((widget for widget in widgets if widget.id == SERIES_WIDGET), None)
    if timeline and series is None:
        raise ParseFailure("explore has no TIMESERIES widget")
    labelled = series or next(iter(widgets), None)
    user_type = _label(_path(labelled.request, "userConfig", "userType")) if labelled is not None else None
    found = _related_widget(widgets, query.related_keyword, len(query.terms) == 1) if related else None
    return Explore(timeseries=series, related=found, user_type=user_type)


# ---- multiline -----------------------------------------------------------------------------------------------------


def _time(value: object) -> int:
    if isinstance(value, str) and _DIGITS.match(value):
        return int(value)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise ParseFailure("a point's time is not epoch seconds")


def _values(value: object, width: int) -> tuple[int, ...]:
    values = _list(value, "a point's value")
    if len(values) != width or not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_VALUE for v in values):
        raise ParseFailure("a point's value is not one integer 0-100 per term")
    return tuple(values)


def _flags(point: dict, key: str, width: int) -> tuple[bool | None, ...]:
    if key not in point:
        return (None,) * width
    flags = _list(point[key], f"a point's {key}")
    if len(flags) != width or not all(isinstance(flag, bool) for flag in flags):
        raise ParseFailure(f"a point's {key} is not one boolean per term")
    return tuple(flags)


def _partial(point: dict) -> bool | None:
    value = point.get("isPartial")
    if value is not None and not isinstance(value, bool):
        raise ParseFailure("a point's isPartial is not a boolean")
    return value


def _points(payload: object) -> list:
    points = _list(_object(_object(payload, "multiline answer").get("default"), "default").get("timelineData"), "timelineData")
    if len(points) > MAX_POINTS:
        raise ParseFailure("timelineData has more points than any time range gives")
    return points


def parse_timeline(payload: object, terms: Sequence[str]) -> TimelineResult:
    """One line per term, positional: value[i] belongs to the i-th term of the request (design 4.7)."""
    points = [_object(point, "a point") for point in _points(payload)]
    if not points:
        return TimelineResult(FetchStatus.NO_DATA)
    width = len(terms)
    times = tuple(_time(point.get("time")) for point in points)
    if any(later <= earlier for earlier, later in zip(times, times[1:])):
        raise ParseFailure("timelineData is not in time order")
    values = [_values(point.get("value"), width) for point in points]
    has_data = [_flags(point, "hasData", width) for point in points]
    partial = tuple(_partial(point) for point in points)
    lines = tuple(_line(term, index, times, values, has_data, partial) for index, term in enumerate(terms))
    zero = all(line.status is FetchStatus.OK_ZERO for line in lines)
    return TimelineResult(FetchStatus.OK_ZERO if zero else FetchStatus.OK, lines)


def _line(term: str, index: int, times: tuple[int, ...], values: list, has_data: list, partial: tuple) -> Line:
    column = tuple(row[index] for row in values)
    status = FetchStatus.OK if any(column) else FetchStatus.OK_ZERO
    return Line(term=term, status=status, times=times, values=column, partial=partial, has_data=tuple(row[index] for row in has_data))


# ---- relatedsearches -----------------------------------------------------------------------------------------------


def _related_query(raw: object) -> RelatedQuery:
    item = _object(raw, "a related query")
    query, value, shown = item.get("query"), item.get("value"), item.get("formattedValue")
    if not isinstance(query, str) or not query or not isinstance(value, int) or isinstance(value, bool) or value < 0 or not isinstance(shown, str):
        raise ParseFailure("a related query lacks query, value or formattedValue")
    return RelatedQuery(query=query, value=value, formatted_value=shown)


def parse_related(payload: object) -> RelatedResult:
    """rankedList[0] is top, rankedList[1] rising (pytrends' reading; stage 0 confirms)."""
    ranked = _list(_object(_object(payload, "related answer").get("default"), "default").get("rankedList"), "rankedList")
    if len(ranked) < 2:
        raise ParseFailure("rankedList lacks the top and rising lists")
    top, rising = (tuple(_related_query(raw) for raw in _list(_object(group, "ranked list").get("rankedKeyword"), "rankedKeyword")) for group in ranked[:2])
    if not top and not rising:
        return RelatedResult(FetchStatus.NO_DATA)
    return RelatedResult(FetchStatus.OK, top=top, rising=rising)
