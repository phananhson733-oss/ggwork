"""A fake Google Trends for the TR-14 session tests: answers any query through httpx.MockTransport, on a ManualClock.

Unlike trends_fakes.FakeTrends (canned fixture per phase), this one reads what each request asks for: explore echoes
the requested terms back as a TIMESERIES widget and one RELATED_QUERIES widget per term, multiline answers one series
per term, relatedsearches a top and a rising list. Every answer takes `latency` seconds of the clock, so the transport
log holds when each request left and came back: the envelope is asserted on that log, below the executor.

`script` answers chosen requests (by ordinal, 1-based over the whole fake's life) with a failure: 429, 503, 403,
html, sorry, consent, timeout, or bad (a 200 the parser refuses); `fail_phases` answers every request of a phase
(warmup, explore, multiline, related) with one, unless `script` names that ordinal. `zero` names terms whose series are all zero;
`user_type` is what explore's widgets carry (a callable of the ordinal, to change it half-way). No request ever leaves
the process: anything but trends.google.com fails the test.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

import httpx

from ggwork_pick.observe.clock import ManualClock

TRENDS_HOST = "trends.google.com"
PHASES = {
    "/trends/": "warmup",
    "/trends/api/explore": "explore",
    "/trends/api/widgetdata/multiline": "multiline",
    "/trends/api/widgetdata/relatedsearches": "related",
}
XSSI = ")]}'\n"
LEGIT = "USER_TYPE_LEGIT_USER"
HOURLY_POINTS, DAILY_POINTS = 169, 30
FAILURES = {
    "429": (429, [("content-type", "text/html")], "<html>Too Many Requests</html>"),
    "503": (503, [("content-type", "text/html")], "<html>unavailable</html>"),
    "403": (403, [("content-type", "text/html")], "<html>forbidden</html>"),
    "html": (200, [("content-type", "text/html")], "<html>not json</html>"),
    "sorry": (302, [("location", "https://www.google.com/sorry/index?continue=x")], ""),
    "consent": (302, [("location", "https://consent.google.com/ml?continue=x")], ""),
    "bad": (200, [("content-type", "application/json")], XSSI + json.dumps({"default": {"timelineData": "nope"}, "widgets": "nope"})),
}


@dataclass(frozen=True)
class Seen:
    ordinal: int
    phase: str
    sent_at: datetime
    done_at: datetime
    terms: tuple[str, ...]
    geo: str | None
    answer: str  # "ok" or the scripted failure


def _items(request: httpx.Request, phase: str) -> list[dict]:
    raw = json.loads(request.url.params.get("req", "{}"))
    return raw.get("comparisonItem", []) if isinstance(raw, dict) else []


def _terms_of(request: httpx.Request, phase: str) -> tuple[str, ...]:
    items = _items(request, phase)
    if phase == "explore":
        return tuple(item["keyword"] for item in items)
    if phase == "multiline":
        return tuple(item["complexKeywordsRestriction"]["keyword"][0]["value"] for item in items)
    if phase == "related":
        raw = json.loads(request.url.params.get("req", "{}"))
        return (raw["restriction"]["complexKeywordsRestriction"]["keyword"][0]["value"],)
    return ()


def _geo_of(request: httpx.Request) -> str | None:
    items = _items(request, "explore")
    return (items[0].get("geo") or "WW") if items else None


def _keyword(term: str) -> dict:
    return {"complexKeywordsRestriction": {"keyword": [{"type": "BROAD", "value": term}]}}


def explore_answer(terms: tuple[str, ...], time: str, user_type: str) -> dict:
    config = {"userType": user_type}
    series = {
        "id": "TIMESERIES",
        "token": "synthetic-timeseries-token",
        "request": {"time": time, "comparisonItem": [_keyword(term) for term in terms], "userConfig": config},
    }
    related = [
        {
            "id": "RELATED_QUERIES" if len(terms) == 1 else f"RELATED_QUERIES_{index}",
            "token": f"synthetic-related-token-{index}",
            "request": {"restriction": _keyword(term), "keywordType": "QUERY", "userConfig": config},
        }
        for index, term in enumerate(terms)
    ]
    return {"widgets": [series, *related]}


def multiline_answer(terms: tuple[str, ...], points: int, zero: frozenset[str], start: int = 1_789_000_000) -> dict:
    step = 3600 if points == HOURLY_POINTS else 86400
    data = []
    for index in range(points):
        values = [0 if term in zero else (index * 7 + len(term)) % 60 + 1 for term in terms]
        point = {"time": str(start + index * step), "value": values, "hasData": [True] * len(terms)}
        data.append({**point, "isPartial": True} if index == points - 1 else point)
    return {"default": {"timelineData": data}}


def related_answer(term: str) -> dict:
    top = [{"query": f"{term} reelshort", "value": 100, "formattedValue": "100"}]
    rising = [{"query": f"{term} episode 1", "value": 250, "formattedValue": "+250%"}]
    return {"default": {"rankedList": [{"rankedKeyword": top}, {"rankedKeyword": rising}]}}


class FakeGoogle:
    def __init__(
        self,
        clock: ManualClock,
        *,
        latency: float = 0.4,
        script: dict[int, str] | None = None,
        zero: Iterable[str] = (),
        user_type: str | Callable[[int], str] = LEGIT,
        on_request: Callable[[int, str], None] | None = None,
        fail_phases: dict[str, str] | None = None,
    ):
        self.clock, self.latency, self.script = clock, latency, dict(script or {})
        self.fail_phases = dict(fail_phases or {})
        self.zero = frozenset(zero)
        self.user_type = user_type if callable(user_type) else (lambda ordinal, fixed=user_type: fixed)
        self.on_request = on_request
        self.seen: list[Seen] = []
        self.explore_times: dict[tuple[str, ...], str] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def phases(self) -> list[str]:
        return [seen.phase for seen in self.seen]

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == TRENDS_HOST, f"request left trends.google.com: {request.url.host}"
        phase = PHASES[request.url.path]
        ordinal, sent_at = len(self.seen) + 1, self.clock.now()
        if self.on_request is not None:
            self.on_request(ordinal, phase)
        self.clock.advance(self.latency)
        answer = self.script.get(ordinal, self.fail_phases.get(phase, "ok"))
        self.seen.append(Seen(ordinal, phase, sent_at, self.clock.now(), _terms_of(request, phase), _geo_of(request), answer))
        if answer == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if answer != "ok":
            status, headers, text = FAILURES[answer]
            return httpx.Response(status, headers=headers, text=text, request=request)
        return self._ok(request, phase, ordinal)

    def _ok(self, request: httpx.Request, phase: str, ordinal: int) -> httpx.Response:
        if phase == "warmup":
            cookie = "NID=525=synthetic-nid; path=/; domain=.google.com; expires=Fri, 26-Mar-2027 22:00:00 GMT"
            return httpx.Response(200, headers=[("content-type", "text/html"), ("set-cookie", cookie)], text="<html></html>", request=request)
        terms = _terms_of(request, phase)
        if phase == "explore":
            time = _items(request, phase)[0]["time"]
            body = explore_answer(terms, time, self.user_type(ordinal))
        elif phase == "multiline":
            raw = json.loads(request.url.params["req"])
            points = HOURLY_POINTS if "7-d" in raw.get("time", "now 7-d") else DAILY_POINTS
            body = multiline_answer(terms, points, self.zero)
        else:
            body = related_answer(terms[0])
        return httpx.Response(200, headers=[("content-type", "application/json")], text=XSSI + json.dumps(body), request=request)
