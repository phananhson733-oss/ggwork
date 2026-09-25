"""The direct Google Trends client: httpx only, one request at a time, never a retry (design 4.1, 4.3, 4.4; plan TR-02).

The request chain is the web client's: a warm-up for the NID cookie, explore for the widget tokens, then multiline for
the series and relatedsearches for the related queries, all with tz=0 and hl=en-US. Paths and parameters follow the
direct-connection research; stage 0 (TR-05) confirms them against real answers.

The session (TR-14) is wired in through two required callbacks, like D42's required argument, so an executor cannot
forget either: `gate` is awaited before every request, the warm-up included (the pacer, the budget reservation and the
lease check live there, and whatever it raises stops the unit before anything is sent); `on_request` receives the
record of every request that was sent (ggwp_obs_requests). The client itself never retries and never sleeps: the retry
policy and the breaker are TR-03's, applied by the executor.

Redirects are never followed; a sorry or consent page is reported, and the consent wall is never accepted. The jar's
cookies are replaced only by answers that succeeded, so a refused request keeps the old jar (design 4.4: Google turns
new sessions away first). Cookie values never reach a record, a repr, a log line or an error.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from types import MappingProxyType

import httpx

from ggwork_pick.observe.clock import Clock
from ggwork_pick.observe.trends.egress import EgressProbe, refuse_all_cookies
from ggwork_pick.observe.trends.parse import (
    Explore,
    ParseFailure,
    classify,
    cookie_updates,
    decode_json,
    parse_explore,
    parse_related,
    parse_timeline,
    redirect_kind,
)
from ggwork_pick.observe.trends.source import (
    FAILURES,
    SOURCE_NAME,
    EgressReading,
    FetchResult,
    FetchStatus,
    Jar,
    Phase,
    RelatedResult,
    RequestRecord,
    RequestStep,
    TimelineResult,
    TrendsQuery,
    WarmResult,
)

BASE_URL = "https://trends.google.com"
WARM_PATH, WARM_PARAMS = "/trends/", MappingProxyType({"geo": "US"})  # pytrends-modern's warm-up page
EXPLORE_PATH = "/trends/api/explore"
MULTILINE_PATH = "/trends/api/widgetdata/multiline"
RELATED_PATH = "/trends/api/widgetdata/relatedsearches"
FIXED_PARAMS = MappingProxyType({"hl": "en-US", "tz": "0"})  # design 4.1: every API call, tz as minutes from UTC
DEFAULT_TIMEOUTS = httpx.Timeout(30.0, connect=10.0)
REQUEST_SECONDS = 60.0  # httpx's read timeout is per socket read; this bounds a whole request
MAX_BODY_BYTES = 4_000_000  # the warm-up page is about 0.8 MB; a multiline answer tens of kilobytes
ACCEPT = MappingProxyType({Phase.WARMUP: "text/html,application/xhtml+xml,*/*;q=0.8"})
ACCEPT_API = "application/json, text/plain, */*"
ACCEPT_LANGUAGE = "en-US,en;q=0.9"

Gate = Callable[[RequestStep], Awaitable[None]]
OnRequest = Callable[[RequestRecord], Awaitable[None]]
Capture = Callable[[RequestRecord, bytes], Awaitable[None]]
Reader = Callable[[object], tuple[FetchStatus, object, str | None]]


@dataclass(frozen=True)
class _Answer:
    status: int | None
    headers: httpx.Headers
    body: bytes
    failed: FetchStatus | None = None  # decided before any body was read: no answer, or a body over the cap
    error_class: str | None = None


@dataclass(frozen=True)
class _Exchange:
    answer: _Answer
    started_at: datetime
    latency_ms: float
    egress: EgressReading


class _BodyTooLarge(Exception):
    pass


async def _read_capped(response: httpx.Response, limit: int) -> bytes:
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise _BodyTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


async def _answer_of(response: httpx.Response, limit: int) -> _Answer:
    try:
        return _Answer(response.status_code, response.headers, await _read_capped(response, limit))
    except _BodyTooLarge:
        return _Answer(response.status_code, response.headers, b"", FetchStatus.PARSE_ERROR, "BodyTooLarge")


def _compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _explore_params(query: TrendsQuery) -> dict[str, str]:
    items = [{"keyword": term, "geo": query.google_geo, "time": query.timeframe} for term in query.terms]
    return {**FIXED_PARAMS, "req": _compact({"comparisonItem": items, "category": 0, "property": query.gprop})}


def _widget_params(request: dict, token: str) -> dict[str, str]:
    return {**FIXED_PARAMS, "req": _compact(request), "token": token}


def _read_series(query: TrendsQuery) -> Reader:
    def read(payload: object) -> tuple[FetchStatus, object, str | None]:
        timeline = parse_timeline(payload, query.terms)
        return timeline.status, timeline, None

    return read


def _read_related(payload: object) -> tuple[FetchStatus, object, str | None]:
    related = parse_related(payload)
    return related.status, related, None


class TrendsClient:
    """One Trends session on one jar; `async with TrendsClient(...) as client`. Implements TrendsSource.

    clock stamps each record (started_at from now(), latency from monotonic()). egress is D20's probe, None or disabled
    by default; capture receives each answer's raw body for stage 0's archive (TR-05) and is off in the cron."""

    source_name = SOURCE_NAME

    def __init__(
        self,
        *,
        jar: Jar,
        clock: Clock,
        gate: Gate,
        on_request: OnRequest,
        transport: httpx.AsyncBaseTransport | None = None,
        egress: EgressProbe | None = None,
        capture: Capture | None = None,
        timeouts: httpx.Timeout = DEFAULT_TIMEOUTS,
        max_body_bytes: int = MAX_BODY_BYTES,
        request_seconds: float = REQUEST_SECONDS,
    ):
        self._jar, self._clock, self._gate, self._on_request = jar, clock, gate, on_request
        self._egress, self._capture = egress, capture
        self._max_body_bytes, self._request_seconds = max_body_bytes, request_seconds
        # Cookies go out only in the header built from the jar; httpx's own jar refuses everything it is handed.
        self._http = httpx.AsyncClient(base_url=BASE_URL, transport=transport, timeout=timeouts, follow_redirects=False, cookies=refuse_all_cookies())

    def __repr__(self) -> str:
        egress = "on" if self._egress is not None and self._egress.enabled else "off"
        return f"TrendsClient(jar={self._jar!r}, egress={egress})"

    async def __aenter__(self) -> "TrendsClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def jar(self) -> Jar:
        """The current jar, for the session to persist (encrypted, TR-04 and TR-13)."""
        return self._jar

    async def warm(self, *, day: date) -> WarmResult:
        """At most one warm-up a day (the session's target date): a jar already warmed that day sends nothing."""
        if self._jar.warmed_on == day:
            return WarmResult(status=None, jar=self._jar, request=None)
        record, _ = await self._call(RequestStep(Phase.WARMUP, None, None), WARM_PATH, dict(WARM_PARAMS), None)
        self._jar = replace(self._jar, warmed_on=day)
        return WarmResult(status=record.fetch_status, jar=self._jar, request=record)

    async def fetch(self, query: TrendsQuery, *, timeline: bool = True, related: bool = False, label: str | None = None) -> FetchResult:
        """One query unit: explore, then multiline (timeline) and relatedsearches (related). The first failed request
        ends the unit; every part asked for and not yet fetched carries that failure and no values."""
        if not (timeline or related):
            raise ValueError("a unit asks for a timeline, related queries or both")

        def read_explore(payload: object) -> tuple[FetchStatus, object, str | None]:
            explore = parse_explore(payload, query, timeline=timeline, related=related)
            return FetchStatus.OK, explore, explore.user_type

        record, explore = await self._call(RequestStep(Phase.EXPLORE, query, label), EXPLORE_PATH, _explore_params(query), read_explore)
        records = (record,)
        if explore is None:
            return self._stopped(query, records, None, timeline=timeline, related=related)
        series = None
        if timeline:
            step = RequestStep(Phase.MULTILINE, query, label)
            record, series = await self._call(step, MULTILINE_PATH, _widget_params(explore.timeseries.request, explore.timeseries.token), _read_series(query))
            records = (*records, record)
            if series is None:
                return self._stopped(query, records, explore.user_type, timeline=True, related=related)
        found, more = await self._related(query, explore, label) if related else (None, ())
        records = (*records, *more)
        stopped = records[-1].fetch_status if records[-1].fetch_status in FAILURES else None
        return FetchResult(query, stopped, explore.user_type, series, found, records, records[-1].egress)

    async def _related(self, query: TrendsQuery, explore: Explore, label: str | None) -> tuple[RelatedResult, tuple[RequestRecord, ...]]:
        if explore.related is None:
            return RelatedResult(FetchStatus.NO_DATA, widget_missing=True), ()
        step = RequestStep(Phase.RELATED, query, label)
        record, found = await self._call(step, RELATED_PATH, _widget_params(explore.related.request, explore.related.token), _read_related)
        return (found if found is not None else RelatedResult(record.fetch_status)), (record,)

    @staticmethod
    def _stopped(query: TrendsQuery, records: tuple[RequestRecord, ...], user_type: str | None, *, timeline: bool, related: bool) -> FetchResult:
        failure = records[-1].fetch_status
        return FetchResult(
            query=query,
            stopped_by=failure,
            user_type=user_type,
            timeline=TimelineResult(failure) if timeline else None,
            related=RelatedResult(failure) if related else None,
            requests=records,
            egress=records[-1].egress,
        )

    async def _call(self, step: RequestStep, path: str, params: dict[str, str], read: Reader | None) -> tuple[RequestRecord, object]:
        """gate, egress, HTTP, read, record: one request. The value is None unless the answer was read successfully."""
        exchange = await self._exchange(step, path, params)
        answer = exchange.answer
        status, value, user_type = self._judge(step.phase, answer, read)
        kind, host = redirect_kind(answer.headers.get("location", "")) if answer.status is not None and 300 <= answer.status < 400 else (None, None)
        record = RequestRecord(
            phase=step.phase,
            label=step.label,
            started_at=exchange.started_at,
            latency_ms=exchange.latency_ms,
            http_status=answer.status,
            fetch_status=status,
            redirect_kind=kind,
            redirect_host=host,
            user_type=user_type,
            bytes=len(answer.body),
            error_class=answer.error_class,
            egress=exchange.egress,
        )
        if status not in FAILURES:
            self._jar = self._jar.updated(cookie_updates(answer.headers.get_list("set-cookie")))
        await self._on_request(record)
        if self._capture is not None:
            await self._capture(record, answer.body)
        return record, value

    @staticmethod
    def _judge(phase: Phase, answer: _Answer, read: Reader | None) -> tuple[FetchStatus, object, str | None]:
        if answer.failed is not None:
            return answer.failed, None, None
        status = classify(phase, answer.status, answer.headers, answer.body)
        if status is not None or read is None:
            return status or FetchStatus.OK, None, None
        try:
            return read(decode_json(answer.body))
        except ParseFailure:
            return FetchStatus.PARSE_ERROR, None, None

    async def _exchange(self, step: RequestStep, path: str, params: dict[str, str]) -> _Exchange:
        await self._gate(step)
        egress = await self._egress.before_request() if self._egress is not None else EgressReading()
        started_at, started = self._clock.now(), self._clock.monotonic()
        answer = await self._send(step.phase, path, params)
        return _Exchange(answer, started_at, round((self._clock.monotonic() - started) * 1000, 1), egress)

    def _headers(self, phase: Phase) -> dict[str, str]:
        cookie = self._jar.header()
        headers = {"user-agent": self._jar.user_agent, "accept": ACCEPT.get(phase, ACCEPT_API), "accept-language": ACCEPT_LANGUAGE}
        return {**headers, "cookie": cookie} if cookie is not None else headers

    async def _send(self, phase: Phase, path: str, params: dict[str, str]) -> _Answer:
        """One GET. Errors become a status and the error's class name: an exception's text can quote the request."""
        try:
            async with asyncio.timeout(self._request_seconds):
                async with self._http.stream("GET", path, params=params, headers=self._headers(phase)) as response:
                    return await _answer_of(response, self._max_body_bytes)
        except TimeoutError:
            return _Answer(None, httpx.Headers(), b"", FetchStatus.TIMEOUT, "TimeoutError")
        except httpx.HTTPError as exc:
            return _Answer(None, httpx.Headers(), b"", FetchStatus.TIMEOUT, type(exc).__name__)
