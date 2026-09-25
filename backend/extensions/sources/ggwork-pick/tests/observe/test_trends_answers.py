"""TR-02: how the client reads each HTTP answer (plan TR-02; design 4.2, 4.3, 4.10).

Redirects are read, never followed, and only a sorry or consent page is blocked_redirect, the wall TR-03 ends the day
on; any other redirect is a changed contract. The status of an answer decides before its body: a large error page is
still the refusal. Transport failures are timeout, which TR-03 retries once, only when they happened on the way.
Everything runs against httpx.MockTransport; no request leaves the process.
"""

import asyncio

import httpx
import pytest
from trends_fakes import DAY, FakeTrends, Recorder, make_client, query_of, streamed

from ggwork_pick.observe.trends.source import FetchStatus, RedirectKind


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "kind", "host"), [("redirect_sorry", RedirectKind.SORRY, "www.google.com"), ("redirect_consent", RedirectKind.CONSENT, "consent.google.com")]
)
async def test_sorry_redirect_detected_without_follow(answer, kind, host):
    fake = FakeTrends(explore=answer, warmup=answer)
    recorder = Recorder(fake)
    async with make_client(fake, recorder) as client:
        result = await client.fetch(query_of("explore_4lines_us_h"))
        warmed = await client.warm(day=DAY)
    # One request each: the redirect was read, not followed (the fake refuses any host but trends.google.com).
    assert fake.phases() == ["explore", "warmup"]
    assert result.status is FetchStatus.BLOCKED_REDIRECT and result.captcha_or_consent
    assert warmed.status is FetchStatus.BLOCKED_REDIRECT
    assert [(record.redirect_kind, record.redirect_host) for record in recorder.records] == [(kind, host), (kind, host)]


def _redirect(location: str):
    def answer(request):
        return httpx.Response(302, headers={"location": location}, request=request)

    return answer


OTHER_REDIRECTS = {
    "/trends/api/explore2": (RedirectKind.SAME_HOST, "trends.google.com"),
    "https://trends.google.com/trends/explore?geo=US": (RedirectKind.SAME_HOST, "trends.google.com"),
    "https://accounts.google.com/ServiceLogin": (RedirectKind.OTHER, "accounts.google.com"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("location", sorted(OTHER_REDIRECTS))
async def test_other_redirects_are_parse_errors(location):
    """Google moving an API path is a changed contract, not a wall: parse_error, which neither ends the day nor counts
    towards disabled_7d; where it pointed is still on the record."""
    fake = FakeTrends(explore=_redirect(location))
    async with make_client(fake, Recorder(fake)) as client:
        result = await client.fetch(query_of("explore_4lines_us_h"))
    assert result.status is FetchStatus.PARSE_ERROR and not result.captcha_or_consent and len(fake.requests) == 1
    assert (result.requests[0].redirect_kind, result.requests[0].redirect_host) == OTHER_REDIRECTS[location]
    assert result.requests[0].http_status == 302 and result.timeline.lines is None


REDIRECT_TARGETS = {
    "https://www.google.com/sorry/index?continue=x": True,
    "https://consent.google.com/ml?continue=x": True,
    "https://consent.youtube.com/m?continue=x": True,
    "/trends/api/explore2": False,
    "https://trends.google.com/trends/": False,
    "https://accounts.google.com/ServiceLogin": False,
    "https://www.google.com/search?q=x": False,
}


@pytest.mark.asyncio
async def test_blocked_redirect_only_for_sorry_or_consent():
    """The invariant TR-03's breaker relies on (blocked_redirect -> WALL, the day ends at once): a request record says
    blocked_redirect exactly when its redirect went to the sorry or consent page."""
    for location, wall in REDIRECT_TARGETS.items():
        for phase in ("warmup", "explore", "multiline", "related"):
            answers = {"explore": "explore_4lines_us_h", "multiline": "multiline_hourly_ok", phase: _redirect(location)}
            fake = FakeTrends(**answers)
            recorder = Recorder(fake)
            async with make_client(fake, recorder) as client:
                await client.warm(day=DAY) if phase == "warmup" else await client.fetch(query_of("explore_4lines_us_h"), related=True)
            record = recorder.records[-1]
            assert (record.fetch_status is FetchStatus.BLOCKED_REDIRECT) is wall, (location, phase)
            assert (record.redirect_kind in (RedirectKind.SORRY, RedirectKind.CONSENT)) is wall, (location, phase)


@pytest.mark.asyncio
async def test_breaker_reads_only_walls_as_walls():
    """Across the seam with TR-03 (skipped until the batch 1a integration brings trends/breaker.py): a redirect within
    trends.google.com never puts the day out; a sorry page does. TR-03's signal_of takes the result's status and its
    captcha_or_consent flag, as the executor passes them (the flag has no default, like D42)."""
    breaker = pytest.importorskip("ggwork_pick.observe.trends.breaker")
    signals = {}
    for location in REDIRECT_TARGETS:
        fake = FakeTrends(explore=_redirect(location))
        async with make_client(fake, Recorder(fake)) as client:
            result = await client.fetch(query_of("explore_4lines_us_h"))
        signals[location] = breaker.signal_of(result.status.value, captcha_or_consent=result.captcha_or_consent)
        record = result.requests[-1]
        walled = record.redirect_kind in (RedirectKind.SORRY, RedirectKind.CONSENT)
        assert breaker.signal_of(record.fetch_status.value, captcha_or_consent=walled) is signals[location]
    assert {location for location, signal in signals.items() if signal is breaker.Signal.WALL} == {
        location for location, wall in REDIRECT_TARGETS.items() if wall
    }
    assert signals["/trends/api/explore2"] is breaker.Signal.NEUTRAL


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["http_503", "timeout", "http_429", "redirect_sorry"])
async def test_client_never_retries(answer):
    """Retrying is TR-03's policy in the executor: 429 never, 5xx or timeout once after 30-60 seconds."""
    fake = FakeTrends(explore="explore_4lines_us_h", multiline=answer)
    async with make_client(fake, Recorder(fake)) as client:
        await client.fetch(query_of("explore_4lines_us_h"))
    assert fake.phases() == ["explore", "multiline"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (429, [], FetchStatus.RATE_LIMITED),
        (403, [], FetchStatus.FORBIDDEN),
        (503, [], FetchStatus.SERVER_ERROR),
        (302, [("location", "https://www.google.com/sorry/index")], FetchStatus.BLOCKED_REDIRECT),
    ],
)
async def test_body_cap_never_hides_a_status(status, headers, expected):
    """The cap guards what the parser would read: only a 200 over it is a parse_error. A refusal with a large error
    page is still the refusal, so the breaker still sees its limit signal; its body is cut at the cap."""
    fake = FakeTrends(explore=streamed(status, b"<html>" + b"x" * 50_000, headers))
    recorder = Recorder(fake)
    async with make_client(fake, recorder, max_body_bytes=1_000, capture=recorder.capture) as client:
        result = await client.fetch(query_of("explore_4lines_us_h"))
    assert result.status is expected and recorder.records[0].http_status == status
    assert recorder.records[0].bytes == 1_000 and len(recorder.bodies[0]) == 1_000


TRANSPORT_ERRORS = {
    httpx.ReadTimeout: FetchStatus.TIMEOUT,
    httpx.ConnectTimeout: FetchStatus.TIMEOUT,
    httpx.PoolTimeout: FetchStatus.TIMEOUT,
    httpx.ConnectError: FetchStatus.TIMEOUT,
    httpx.ReadError: FetchStatus.TIMEOUT,
    httpx.WriteError: FetchStatus.TIMEOUT,
    httpx.RemoteProtocolError: FetchStatus.TIMEOUT,
    httpx.ProxyError: FetchStatus.TIMEOUT,
    httpx.LocalProtocolError: FetchStatus.PARSE_ERROR,
    httpx.UnsupportedProtocol: FetchStatus.PARSE_ERROR,
    httpx.DecodingError: FetchStatus.PARSE_ERROR,
    httpx.TooManyRedirects: FetchStatus.PARSE_ERROR,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", sorted(TRANSPORT_ERRORS, key=lambda error: error.__name__))
async def test_transport_errors_by_kind(error):
    """Only a failure on the way (time ran out, the connection or the far end broke) is a timeout, which TR-03 retries
    once. A request this side could not send (a bad header from a restored jar, say) will fail the same way again: it
    is parse_error, never retried. The class name is kept either way."""

    def failing(request):
        raise error("synthetic failure", request=request) if issubclass(error, httpx.RequestError) else error("synthetic failure")

    fake = FakeTrends(explore=failing)
    recorder = Recorder(fake)
    async with make_client(fake, recorder) as client:
        result = await client.fetch(query_of("explore_4lines_us_h"))
    assert result.status is TRANSPORT_ERRORS[error]
    assert (recorder.records[0].http_status, recorder.records[0].error_class) == (None, error.__name__)


@pytest.mark.asyncio
async def test_undecodable_body_keeps_the_status():
    """A body httpx cannot decode came with an HTTP status: a 200 is a parse_error, a 429 is still rate_limited."""
    broken = [("content-encoding", "gzip"), ("content-type", "application/json")]
    for status, expected in ((200, FetchStatus.PARSE_ERROR), (429, FetchStatus.RATE_LIMITED)):
        fake = FakeTrends(explore=streamed(status, b"not gzip at all", broken))
        recorder = Recorder(fake)
        async with make_client(fake, recorder) as client:
            result = await client.fetch(query_of("explore_4lines_us_h"))
        assert result.status is expected and recorder.records[0].http_status == status
        assert recorder.records[0].error_class == "DecodingError"


@pytest.mark.asyncio
async def test_body_cap_and_request_deadline():
    fake = FakeTrends(explore="explore_4lines_us_h", multiline="multiline_hourly_ok")
    async with make_client(fake, Recorder(fake), max_body_bytes=20_000) as client:
        capped = await client.fetch(query_of("explore_4lines_us_h"))
    assert capped.status is FetchStatus.PARSE_ERROR and capped.timeline.lines is None

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, request=request)

    fake = FakeTrends(explore=slow)
    recorder = Recorder(fake)
    async with make_client(fake, recorder, request_seconds=0.05) as client:
        late = await client.fetch(query_of("explore_4lines_us_h"))
    assert late.status is FetchStatus.TIMEOUT and recorder.records[0].http_status is None
