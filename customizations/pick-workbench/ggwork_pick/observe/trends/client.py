"""The direct Google Trends client: httpx only, one request at a time, never a retry (design 4.1, 4.3, 4.4; plan TR-02).

The request chain is the web client's: a warm-up for the NID cookie, explore for the widget tokens, then multiline for
the series and relatedsearches for the related queries, all with tz=0 and hl=en-US. Paths and parameters follow the
direct-connection research; stage 0 (TR-05) confirms them against real answers.

The session (TR-14) is wired in through two required callbacks, like D42's required argument, so an executor cannot
forget either: `gate` is awaited before every request, the warm-up included (the pacer, the budget reservation and the
lease check live there, and whatever it raises stops the unit before anything is sent); `on_request` receives the
record of every request that was sent (ggwp_obs_requests). The client itself never retries and never sleeps: the retry
policy and the breaker are TR-03's, applied by the executor.

Redirects are never followed; a sorry or consent page is reported, and the consent wall is never accepted. The jar
(TrendsJar, TR-04's CookieJar) takes cookies only from answers that succeeded, so a refused request keeps the old jar
(design 4.4: Google turns new sessions away first). Cookie values never reach a record, a repr, a log line or an error.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from types import MappingProxyType

import httpx

from ggwork_pick.observe.clock import Clock
from ggwork_pick.observe.instants import stamp
from ggwork_pick.observe.trends.egress import EgressProbe, refuse_all_cookies
from ggwork_pick.observe.trends.parse import (
    Explore,
    ParseFailure,
    classify,
    decode_json,
    parse_explore,
    parse_related,
    parse_timeline,
    redirect_kind,
    set_cookies,
)
from ggwork_pick.observe.trends.source import (
    FAILURES,
    RETRYABLE,
    SOURCE_NAME,
    EgressReading,
    FetchResult,
    FetchStatus,
    Phase,
    RelatedResult,
    RequestRecord,
    RequestStep,
    SetCookie,
    TimelineResult,
    TrendsJar,
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
# Failures on the way, where the same request may well get through a minute later: timeout, which TR-03 retries once.
# Anything else httpx raises (a header this side cannot send, a body it cannot decode) fails the same way again: it is
# parse_error, never retried.
TRANSIENT_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, httpx.ProxyError)
ACCEPT = MappingProxyType({Phase.WARMUP: "text/html,application/xhtml+xml,*/*;q=0.8"})
ACCEPT_API = "application/json, text/plain, */*"
ACCEPT_LANGUAGE = "en-US,en;q=0.9"
# The web client asks explore with GET, pytrends with POST (parameters in the query string either way). Stage 0 (TR-05)
# tries both and settles the default; the widget calls are GET.
EXPLORE_METHODS = ("GET", "POST")
DEFAULT_EXPLORE_METHOD = "GET"
logger = logging.getLogger(__name__)


def retry_after_seconds(value: str | None, *, now: datetime) -> int | None:
    """Safe diagnostic metadata only; never log the header text or change retry policy."""
    if not value or len(value) > 128:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        return min(int(value), 86400)
    try:
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is None:
            return None
        return max(0, min(int((deadline - now).total_seconds()), 86400))
    except (ValueError, TypeError, OverflowError):
        return None


Gate = Callable[[RequestStep], Awaitable[None]]
OnRequest = Callable[[RequestRecord], Awaitable[None]]
Capture = Callable[[RequestRecord, bytes], Awaitable[None]]
Reader = Callable[[object], tuple[FetchStatus, object, str | None]]


@dataclass(frozen=True)
class _Answer:
    status: int | None
    headers: httpx.Headers
    body: bytes
    failed: FetchStatus | None = None  # decided before classify: no answer, or a 200 whose body cannot be read
    error_class: str | None = None


@dataclass(frozen=True)
class _Exchange:
    answer: _Answer
    started_at: datetime
    latency_ms: float
    egress: EgressReading


@dataclass(frozen=True)
class _Outcome:
    record: RequestRecord
    value: object  # what the reader made of the answer; None unless it was read successfully
    cookies: tuple[SetCookie, ...]  # what a usable answer set; always empty for a failure (design 4.4)


async def _read_capped(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    """At most `limit` bytes of the decoded body, and whether there was more; reading stops at the cap."""
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        if size + len(chunk) > limit:
            return b"".join([*chunks, chunk[: limit - size]]), True
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks), False


async def _answer_of(response: httpx.Response, limit: int) -> _Answer:
    """The status decides first: only a 200, whose body the parser reads, fails on a body over the cap or one that
    cannot be decoded. Any other answer keeps its status (a 429's error page can be large); its body is cut at the cap."""
    status, headers = response.status_code, response.headers
    try:
        body, over = await _read_capped(response, limit)
    except httpx.DecodingError:
        return _Answer(status, headers, b"", FetchStatus.PARSE_ERROR if status == 200 else None, "DecodingError")
    if over and status == 200:
        return _Answer(status, headers, b"", FetchStatus.PARSE_ERROR, "BodyTooLarge")
    return _Answer(status, headers, body)


def _compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _explore_params(query: TrendsQuery) -> dict[str, str]:
    items = [{"keyword": term, "geo": query.google_geo, "time": query.timeframe} for term in query.terms]
    return {**FIXED_PARAMS, "req": _compact({"comparisonItem": items, "category": 0, "property": query.google_property})}


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

    jar is any TrendsJar, TR-04's CookieJar in the session. clock stamps each record (started_at from now(), latency
    from monotonic()) and dates the cookies. egress is D20's probe, None or disabled by default; capture receives each
    answer's raw body for stage 0's archive (TR-05) and is off in the cron; explore_method is for stage 0 to compare."""

    source_name = SOURCE_NAME

    def __init__(
        self,
        *,
        jar: TrendsJar,
        clock: Clock,
        gate: Gate,
        on_request: OnRequest,
        transport: httpx.AsyncBaseTransport | None = None,
        egress: EgressProbe | None = None,
        capture: Capture | None = None,
        timeouts: httpx.Timeout = DEFAULT_TIMEOUTS,
        max_body_bytes: int = MAX_BODY_BYTES,
        request_seconds: float = REQUEST_SECONDS,
        explore_method: str = DEFAULT_EXPLORE_METHOD,
    ):
        if explore_method not in EXPLORE_METHODS:
            raise ValueError(f"explore_method is one of {EXPLORE_METHODS}")
        self._jar, self._clock, self._gate, self._on_request = jar, clock, gate, on_request
        self._egress, self._capture = egress, capture
        self._max_body_bytes, self._request_seconds, self._explore_method = max_body_bytes, request_seconds, explore_method
        # Cookies go out only in the header built from the jar; httpx's own jar refuses everything it is handed.
        self._http = httpx.AsyncClient(base_url=BASE_URL, transport=transport, timeout=timeouts, follow_redirects=False, cookies=refuse_all_cookies())

    def __repr__(self) -> str:
        """Built from the jar's names and dates, never from the jar's own repr: no value can slip in through it."""
        egress = "on" if self._egress is not None and self._egress.enabled else "off"
        jar = self._jar
        return f"TrendsClient(user_agent={jar.user_agent!r}, cookies={list(jar.cookie_names)!r}, warmed_on={jar.warmed_on!r}, egress={egress})"

    async def __aenter__(self) -> "TrendsClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def jar(self) -> TrendsJar:
        """The current jar, for the session to persist (encrypted, TR-04 and TR-13)."""
        return self._jar

    async def warm(self, *, day: date) -> WarmResult:
        """At most one warm-up per target date (D23): a jar that cannot warm on `day` sends nothing (status None).

        An answer uses up the day: a good one fills the jar, a refused one keeps the old cookies (design 4.4). A timeout
        or a 5xx does not: nothing usable came back, so the executor's one retry (TR-03, design 4.2) goes out."""
        if not self._jar.can_warm(day):
            return WarmResult(status=None, jar=self._jar, request=None)
        outcome = await self._call(RequestStep(Phase.WARMUP, None, None), WARM_PATH, dict(WARM_PARAMS), None)
        if outcome.record.fetch_status not in RETRYABLE:
            self._jar = self._jar.warmed(outcome.cookies, day=day, now=self._clock.now())
        return WarmResult(status=outcome.record.fetch_status, jar=self._jar, request=outcome.record)

    async def fetch(
        self, query: TrendsQuery, *, timeline: bool = True, related: bool = False, label: str | None = None, explore_method: str | None = None
    ) -> FetchResult:
        """One query unit: explore, then multiline (timeline) and relatedsearches (related). The first failed request
        ends the unit; every part asked for and not yet fetched carries that failure and no values. `explore_method`
        overrides the client's own for this unit (stage 0 asks its repeat by POST on the session's one client)."""
        if not (timeline or related):
            raise ValueError("a unit asks for a timeline, related queries or both")
        if explore_method is not None and explore_method not in EXPLORE_METHODS:
            raise ValueError(f"explore_method is one of {EXPLORE_METHODS}")
        method = explore_method or self._explore_method

        def read_explore(payload: object) -> tuple[FetchStatus, object, str | None]:
            explore = parse_explore(payload, query, timeline=timeline, related=related)
            return FetchStatus.OK, explore, explore.user_type

        step = RequestStep(Phase.EXPLORE, query, label)
        record, explore = await self._fetch_step(step, EXPLORE_PATH, _explore_params(query), read_explore, method)
        records = (record,)
        if explore is None:
            return self._stopped(query, records, None, timeline=timeline, related=related)
        series = None
        if timeline:
            step = RequestStep(Phase.MULTILINE, query, label)
            record, series = await self._fetch_step(
                step, MULTILINE_PATH, _widget_params(explore.timeseries.request, explore.timeseries.token), _read_series(query)
            )
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
        record, found = await self._fetch_step(step, RELATED_PATH, _widget_params(explore.related.request, explore.related.token), _read_related)
        return (found if found is not None else RelatedResult(record.fetch_status)), (record,)

    async def _fetch_step(self, step: RequestStep, path: str, params: dict[str, str], read: Reader, method: str = "GET") -> tuple[RequestRecord, object]:
        """One request of a unit; a usable answer's cookies go into the jar, a refused one's never do (design 4.4)."""
        outcome = await self._call(step, path, params, read, method)
        if outcome.cookies:
            self._jar = self._jar.updated(outcome.cookies, now=self._clock.now())
        return outcome.record, outcome.value

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

    async def _call(self, step: RequestStep, path: str, params: dict[str, str], read: Reader | None, method: str = "GET") -> _Outcome:
        """gate, egress, HTTP, read, record: one request. The caller decides what the jar takes from it."""
        exchange = await self._exchange(step, path, params, method)
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
            retry_after_seconds=retry_after_seconds(answer.headers.get("retry-after"), now=self._clock.now()),
        )
        cookies = set_cookies(answer.headers.get_list("set-cookie"), now=self._clock.now()) if status not in FAILURES else ()
        await self._on_request(record)
        if answer.status in (429, 503):
            # Request identity and HTTP facts remain in obs_requests. This private operational log adds the
            # missing header metadata without a schema change; absent/unparseable stays null, never zero.
            logger.warning(
                "[pick-trends-response] %s",
                _compact(
                    {
                        "phase": record.phase.value,
                        "sent_at": stamp(record.started_at),
                        "http_status": record.http_status,
                        "retry_after_seconds": record.retry_after_seconds,
                    }
                ),
            )
        if self._capture is not None:
            await self._capture(record, answer.body)
        return _Outcome(record, value, cookies)

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

    async def _exchange(self, step: RequestStep, path: str, params: dict[str, str], method: str) -> _Exchange:
        await self._gate(step)
        egress = await self._egress.before_request() if self._egress is not None else EgressReading()
        started_at, started = self._clock.now(), self._clock.monotonic()
        answer = await self._send(step.phase, method, path, params)
        return _Exchange(answer, started_at, round((self._clock.monotonic() - started) * 1000, 1), egress)

    def _headers(self, phase: Phase) -> dict[str, str]:
        """The jar's User-Agent and Cookie (expired cookies left out), then what every request of the phase sends."""
        jar_headers = {name.lower(): value for name, value in self._jar.request_headers(self._clock.now()).items()}
        return {**jar_headers, "accept": ACCEPT.get(phase, ACCEPT_API), "accept-language": ACCEPT_LANGUAGE}

    async def _send(self, phase: Phase, method: str, path: str, params: dict[str, str]) -> _Answer:
        """One request, parameters in the query string. Errors become a status and the error's class name: an
        exception's text can quote the request."""
        try:
            async with asyncio.timeout(self._request_seconds):
                async with self._http.stream(method, path, params=params, headers=self._headers(phase)) as response:
                    return await _answer_of(response, self._max_body_bytes)
        except TimeoutError:
            return _Answer(None, httpx.Headers(), b"", FetchStatus.TIMEOUT, "TimeoutError")
        except TRANSIENT_ERRORS as exc:
            return _Answer(None, httpx.Headers(), b"", FetchStatus.TIMEOUT, type(exc).__name__)
        except httpx.HTTPError as exc:
            return _Answer(None, httpx.Headers(), b"", FetchStatus.PARSE_ERROR, type(exc).__name__)
