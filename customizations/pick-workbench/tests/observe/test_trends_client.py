"""TR-02: the direct Google Trends client (plan TR-02; design 4.1, 4.3, 4.4, 4.10, 4.11; D20).

Everything runs against httpx.MockTransport and the constructed fixtures in tests/fixtures/trends: no request ever
leaves the process. A failure never produces a value (counterexample 1): a failed step hands back its status and no
series, never a series of zeros.
"""

import json
import logging
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from trends_fakes import BARE, DAY, START, FakeJar, FakeTrends, Recorder, body_of, jar_of, load, make_client, query_of

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.contract import ENUMS, TRENDS_WINDOW_KINDS
from ggwork_pick.observe.errors import Refused
from ggwork_pick.observe.trends.client import DEFAULT_EXPLORE_METHOD, EXPLORE_METHODS, FIXED_PARAMS, TrendsClient
from ggwork_pick.observe.trends.egress import ECHO_ENV, EgressProbe, egress_from_env
from ggwork_pick.observe.trends.source import (
    FAILURES,
    GOOGLE_PROPERTIES,
    JUDGEABLE,
    LIMIT_SIGNALS,
    RETRYABLE,
    SOURCE_NAME,
    TIMEFRAMES,
    EgressReading,
    FetchStatus,
    Phase,
    SetCookie,
    TrendsJar,
    TrendsQuery,
    TrendsSource,
)

TEN_KINDS = {
    "ok": ("explore_4lines_us_h", "multiline_hourly_ok"),
    "ok_zero": ("explore_1line_ww_d", "multiline_daily_zero"),
    "no_data": ("explore_1line_ww_d", "multiline_empty"),
    "rate_limited": ("explore_4lines_us_h", "http_429"),
    "blocked_redirect": ("explore_4lines_us_h", "redirect_sorry"),
    "html_body": ("explore_4lines_us_h", "html_200"),
    "forbidden": ("explore_4lines_us_h", "http_403"),
    "server_error": ("explore_4lines_us_h", "http_503"),
    "timeout": ("explore_4lines_us_h", "timeout"),
    "parse_error": ("explore_4lines_us_h", "multiline_bad_shape"),
}
FAILING_ANSWERS = {
    "http_429": FetchStatus.RATE_LIMITED,
    "redirect_sorry": FetchStatus.BLOCKED_REDIRECT,
    "redirect_consent": FetchStatus.BLOCKED_REDIRECT,
    "html_200": FetchStatus.HTML_BODY,
    "http_403": FetchStatus.FORBIDDEN,
    "http_503": FetchStatus.SERVER_ERROR,
    "timeout": FetchStatus.TIMEOUT,
    "json_truncated": FetchStatus.PARSE_ERROR,
}


def test_status_sets_partition_the_ten_kinds():
    assert {status.value for status in FetchStatus} == set(TEN_KINDS)
    assert JUDGEABLE == {FetchStatus.OK, FetchStatus.OK_ZERO}
    assert LIMIT_SIGNALS == {FetchStatus.RATE_LIMITED, FetchStatus.BLOCKED_REDIRECT, FetchStatus.HTML_BODY, FetchStatus.FORBIDDEN}
    assert RETRYABLE == {FetchStatus.SERVER_ERROR, FetchStatus.TIMEOUT}
    assert FAILURES == LIMIT_SIGNALS | RETRYABLE | {FetchStatus.PARSE_ERROR}
    assert FAILURES.isdisjoint(JUDGEABLE | {FetchStatus.NO_DATA})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(TEN_KINDS))
async def test_fetch_status_ten_kinds(kind):
    explore, multiline = TEN_KINDS[kind]
    fake = FakeTrends(explore=explore, multiline=multiline)
    recorder = Recorder(fake)
    async with make_client(fake, recorder) as client:
        result = await client.fetch(query_of(explore))
    assert result.status == kind and result.timeline.status == kind
    assert fake.phases() == ["explore", "multiline"]
    assert [record.fetch_status for record in recorder.records] == [FetchStatus.OK, FetchStatus(kind)]
    assert recorder.records == list(result.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", sorted(FAILING_ANSWERS))
async def test_failed_explore_stops_the_chain(answer):
    fake = FakeTrends(explore=answer)
    recorder = Recorder(fake)
    async with make_client(fake, recorder) as client:
        result = await client.fetch(query_of("explore_4lines_us_h"), related=True)
    assert fake.phases() == ["explore"]
    assert result.status is FAILING_ANSWERS[answer] and result.stopped_by is FAILING_ANSWERS[answer]
    assert result.timeline.status is result.status and result.related.status is result.status


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["explore", "multiline"])
@pytest.mark.parametrize("answer", sorted(FAILING_ANSWERS))
async def test_failure_has_no_values(step, answer):
    """[counterexample 1] A failed step leaves every requested part without values: None, never a series of zeros."""
    answers = {"explore": "explore_4lines_us_h", "multiline": "multiline_hourly_ok", "related": "related_ok", step: answer}
    fake = FakeTrends(**answers)
    async with make_client(fake, Recorder(fake)) as client:
        query = query_of("explore_4lines_us_h")
        result = await client.fetch(query, related=True)
    assert result.status is FAILING_ANSWERS[answer] and result.status in FAILURES
    assert result.timeline.lines is None and result.bare_line is None
    assert all(result.line(term) is None for term in query.terms)
    assert result.related.top is None and result.related.rising is None
    assert "related" not in fake.phases()


@pytest.mark.asyncio
async def test_no_data_has_no_values_either():
    fake = FakeTrends(explore="explore_1line_ww_d", multiline="multiline_empty")
    async with make_client(fake, Recorder(fake)) as client:
        result = await client.fetch(query_of("explore_1line_ww_d"))
    assert result.status is FetchStatus.NO_DATA and result.timeline.lines is None and result.bare_line is None


@pytest.mark.asyncio
async def test_related_failure_keeps_the_fetched_timeline():
    """The series came back whole before relatedsearches was refused: it keeps its values, and the unit reports the
    limit signal so the breaker still trips."""
    fake = FakeTrends(explore="explore_4lines_us_h", multiline="multiline_hourly_ok", related="http_429")
    async with make_client(fake, Recorder(fake)) as client:
        result = await client.fetch(query_of("explore_4lines_us_h"), related=True)
    assert result.status is FetchStatus.RATE_LIMITED and result.stopped_by is FetchStatus.RATE_LIMITED
    assert result.timeline.status is FetchStatus.OK and result.bare_line is not None
    assert result.related.status is FetchStatus.RATE_LIMITED and result.related.top is None


@pytest.mark.asyncio
async def test_params_fixed():
    fake = FakeTrends(warmup="warmup_ok", explore="explore_4lines_us_h", multiline="multiline_hourly_ok", related="related_ok")
    jar = FakeJar()
    async with make_client(fake, Recorder(fake), jar=jar) as client:
        await client.warm(day=DAY)
        await client.fetch(query_of("explore_4lines_us_h"), related=True)
    warm, explore, multiline, related = fake.requests
    assert FIXED_PARAMS == {"hl": "en-US", "tz": "0"}
    for request in (warm, explore, multiline, related):
        assert request.method == "GET" and request.headers.get_list("user-agent") == [jar.user_agent]
    for request in (explore, multiline, related):
        assert request.url.params["hl"] == "en-US" and request.url.params["tz"] == "0"
        assert "NID=525=synthetic-nid-first" in request.headers["cookie"]
    terms = query_of("explore_4lines_us_h").terms
    assert json.loads(explore.url.params["req"]) == {
        "comparisonItem": [{"keyword": term, "geo": "US", "time": "now 7-d"} for term in terms],
        "category": 0,
        "property": "",
    }
    widgets = {widget["id"]: widget for widget in load("explore_4lines_us_h")["response"]["json"]["widgets"]}
    for request, widget in ((multiline, widgets["TIMESERIES"]), (related, widgets["RELATED_QUERIES_0"])):
        assert json.loads(request.url.params["req"]) == widget["request"] and request.url.params["token"] == widget["token"]
    assert dict(warm.url.params) == {"geo": "US"} and "cookie" not in warm.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_explore_method_is_a_setting(method):
    """The web client asks explore with GET, pytrends with POST; stage 0 compares both without a code change. Only
    the method changes: the parameters stay in the query string, the body stays empty, the widget calls stay GET."""
    fake = FakeTrends(explore="explore_4lines_us_h", multiline="multiline_hourly_ok")
    async with make_client(fake, Recorder(fake), explore_method=method) as client:
        result = await client.fetch(query_of("explore_4lines_us_h"))
    explore, multiline = fake.requests
    assert result.status is FetchStatus.OK and DEFAULT_EXPLORE_METHOD == "GET" and EXPLORE_METHODS == ("GET", "POST")
    assert explore.method == method and multiline.method == "GET"
    assert explore.content == b"" and json.loads(explore.url.params["req"])["comparisonItem"][0]["keyword"] == BARE
    for bad in ("PUT", "get", ""):
        with pytest.raises(ValueError):
            make_client(fake, Recorder(), explore_method=bad)


@pytest.mark.asyncio
async def test_explore_method_per_fetch():
    """Stage 0 asks its repeat by POST on the session's one client (TR-05): a fetch may name its explore method, and
    without one the client's own setting holds."""
    fake = FakeTrends(explore="explore_4lines_us_h", multiline="multiline_hourly_ok")
    async with make_client(fake, Recorder(fake)) as client:
        await client.fetch(query_of("explore_4lines_us_h"), explore_method="POST")
        await client.fetch(query_of("explore_4lines_us_h"))
        with pytest.raises(ValueError):
            await client.fetch(query_of("explore_4lines_us_h"), explore_method="PUT")
    assert [request.method for request in fake.requests] == ["POST", "GET", "GET", "GET"]


@pytest.mark.asyncio
async def test_worldwide_daily_query_params():
    fake = FakeTrends(explore="explore_1line_ww_d", multiline="multiline_daily_zero")
    async with make_client(fake, Recorder(fake)) as client:
        await client.fetch(query_of("explore_1line_ww_d"))
    sent = json.loads(fake.requests[0].url.params["req"])
    assert sent == {"comparisonItem": [{"keyword": BARE, "geo": "", "time": "today 1-m"}], "category": 0, "property": ""}


@pytest.mark.asyncio
async def test_query_speaks_the_contract():
    """The query and the raw row use the contract's words: granularity H or D (TRENDS_WINDOW_KINDS), property web or
    youtube (DISCOVERY_PROPERTIES); only the request to Google uses Google's spelling (web search is "")."""
    assert set(TIMEFRAMES) == set(TRENDS_WINDOW_KINDS)
    assert set(GOOGLE_PROPERTIES) == set(ENUMS["DISCOVERY_PROPERTIES"]) and GOOGLE_PROPERTIES["web"] == ""
    web = query_of("explore_1line_ww_d")
    youtube = query_of("explore_1line_ww_d", search_property="youtube")
    assert web.search_property == "web" and web.request_params()["property"] == "web"
    assert youtube.request_params()["property"] == "youtube" and "gprop" not in youtube.request_params()
    fake = FakeTrends(explore="explore_1line_ww_d", multiline="multiline_daily_zero")
    async with make_client(fake, Recorder(fake)) as client:
        await client.fetch(youtube)
    assert json.loads(fake.requests[0].url.params["req"])["property"] == "youtube"


@pytest.mark.asyncio
async def test_timeline_keeps_partial_and_usertype():
    points = load("multiline_hourly_ok")["response"]["json"]["default"]["timelineData"]
    fake = FakeTrends(explore="explore_4lines_us_h", multiline="multiline_hourly_ok")
    recorder = Recorder(fake)
    async with make_client(fake, recorder) as client:
        result = await client.fetch(query_of("explore_4lines_us_h"))
    bare = result.bare_line
    assert bare.times == tuple(int(point["time"]) for point in points) and len(bare.times) == 169
    # As sent: True on the trailing point, absent (None) elsewhere, never filled in as False.
    assert bare.partial == tuple(point.get("isPartial") for point in points)
    assert bare.partial[-1] is True and bare.partial.count(None) == 168
    assert bare.as_raw()["isPartial"] == list(bare.partial)
    assert result.user_type == "USER_TYPE_LEGIT_USER" and recorder.records[0].user_type == "USER_TYPE_LEGIT_USER"
    fake = FakeTrends(explore="explore_scraper_no_related", multiline="multiline_daily_zero")
    async with make_client(fake, Recorder(fake)) as client:
        assert (await client.fetch(query_of("explore_scraper_no_related"))).user_type == "USER_TYPE_SCRAPER"


@pytest.mark.asyncio
async def test_variant_lines_by_request_order():
    points = load("multiline_hourly_ok")["response"]["json"]["default"]["timelineData"]
    fake = FakeTrends(explore="explore_4lines_us_h", multiline="multiline_hourly_ok")
    async with make_client(fake, Recorder(fake)) as client:
        clear = await client.fetch(query_of("explore_4lines_us_h"))
        # A generic title asks for the intent variants only (design 4.7): there is no bare line to judge on.
        generic = await client.fetch(query_of("explore_4lines_us_h", bare=None))
    assert [line.term for line in clear.timeline.lines] == list(query_of("explore_4lines_us_h").terms)
    assert clear.bare_line.term == BARE and clear.bare_line.values == tuple(point["value"][0] for point in points)
    assert clear.line(f"{BARE} episode").values == tuple(point["value"][3] for point in points)
    assert generic.bare_line is None and generic.timeline.lines is not None


@pytest.mark.asyncio
async def test_warm_once_per_day():
    fake = FakeTrends(warmup="warmup_ok")
    recorder = Recorder(fake)
    async with make_client(fake, recorder) as client:
        first = await client.warm(day=DAY)
        again = await client.warm(day=DAY)
        next_day = await client.warm(day=DAY + timedelta(days=1))
    assert first.status is FetchStatus.OK and first.jar.warmed_on == DAY and first.jar.cookie_names == ("NID", "AEC")
    assert (again.status, again.request) == (None, None) and again.jar == first.jar
    assert next_day.status is FetchStatus.OK and next_day.jar.warmed_on == DAY + timedelta(days=1)
    assert fake.phases() == ["warmup", "warmup"] and [record.phase for record in recorder.records] == [Phase.WARMUP, Phase.WARMUP]


@pytest.mark.asyncio
async def test_no_warm_for_an_earlier_day():
    """The jar decides (can_warm): a day before its warmed_on, from a stepped-back clock or a wrong target date, is
    not a new day and sends nothing."""
    fake = FakeTrends(warmup="warmup_ok")
    async with make_client(fake, Recorder(fake), jar=jar_of(("NID", "525=synthetic-nid-old"), warmed_on=DAY)) as client:
        earlier = await client.warm(day=DAY - timedelta(days=1))
    assert (earlier.status, earlier.request) == (None, None) and earlier.jar.warmed_on == DAY and fake.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("transient", ["timeout", "http_503"])
async def test_warm_retries_after_timeout_or_5xx(transient):
    """Design 4.2 retries a 5xx or a timeout once (TR-03 decides, the executor calls again): a warm-up that got no
    usable answer does not use up the day, so the retry goes out and fills the jar."""
    fake = FakeTrends(warmup=[transient, "warmup_ok"])
    recorder = Recorder(fake)
    async with make_client(fake, recorder) as client:
        failed = await client.warm(day=DAY)
        assert failed.status is FAILING_ANSWERS[transient] and failed.request is not None
        assert failed.jar.warmed_on is None and failed.jar.cookie_names == ()
        retried = await client.warm(day=DAY)
        done = await client.warm(day=DAY)
    assert retried.status is FetchStatus.OK and retried.jar.warmed_on == DAY and retried.jar.cookie_names == ("NID", "AEC")
    assert done.request is None and fake.phases() == ["warmup", "warmup"] and len(recorder.records) == 2


@pytest.mark.asyncio
async def test_same_host_redirect_warms():
    fake = FakeTrends(warmup="warmup_redirect_trends")
    async with make_client(fake, Recorder(fake)) as client:
        warmed = await client.warm(day=DAY)
    assert warmed.status is FetchStatus.OK and warmed.jar.cookie_names == ("NID",) and len(fake.requests) == 1


@pytest.mark.asyncio
async def test_rate_limited_keeps_jar():
    """Google turns new sessions away first (design 4.4): a refused answer never replaces the jar's cookies, even
    when it sets new ones; the refused warm-up still counts as the day's one warm-up."""
    old = jar_of(("NID", "525=synthetic-nid-old"))
    fake = FakeTrends(warmup="http_429", explore="http_429")
    async with make_client(fake, Recorder(fake), jar=old) as client:
        warmed = await client.warm(day=DAY)
        await client.fetch(query_of("explore_4lines_us_h"))
        assert (await client.warm(day=DAY)).request is None
        kept = client.jar
    assert warmed.status is FetchStatus.RATE_LIMITED
    assert kept.cookies == old.cookies and kept.user_agent == "UA-fixed" and kept.warmed_on == DAY
    assert fake.phases() == ["warmup", "explore"]


@pytest.mark.asyncio
async def test_success_adopts_cookie_updates():
    body = body_of(load("explore_4lines_us_h")["response"])

    def rotating(request):
        headers = [
            ("content-type", "application/json"),
            ("set-cookie", "NID=525=synthetic-nid-rotated; path=/"),
            ("set-cookie", "AEC=; max-age=0"),
            ("set-cookie", "SHORT=synthetic-short; Max-Age=60"),
        ]
        return httpx.Response(200, headers=headers, content=body, request=request)

    clock = ManualClock(START)
    jar = jar_of(("NID", "525=synthetic-nid-old"), ("AEC", "synthetic-aec-old"))
    fake = FakeTrends(explore=[rotating, "explore_4lines_us_h"], multiline="multiline_hourly_ok")
    async with make_client(fake, Recorder(fake), jar=jar, clock=clock) as client:
        await client.fetch(query_of("explore_4lines_us_h"))
        rotated = client.jar
        clock.advance(120)  # SHORT's Max-Age has run out: the jar leaves it out of the next request
        await client.fetch(query_of("explore_4lines_us_h"))
    assert rotated.cookie_names == ("NID", "SHORT") and rotated.user_agent == "UA-fixed"
    assert rotated.cookies[1].expires == int(START.timestamp()) + 60
    assert fake.requests[1].headers["cookie"] == "NID=525=synthetic-nid-rotated; SHORT=synthetic-short"
    assert fake.requests[2].headers["cookie"] == "NID=525=synthetic-nid-rotated"


@pytest.mark.asyncio
async def test_gate_before_every_http():
    """The executor's pacer, budget and lease check plug in here (TR-14): the gate runs before every request,
    warm-up included, and a gate that refuses stops the unit before anything is sent."""
    fake = FakeTrends(warmup="warmup_ok", explore="explore_4lines_us_h", multiline="multiline_hourly_ok", related="related_ok")
    recorder = Recorder(fake)
    query = query_of("explore_4lines_us_h")
    async with make_client(fake, recorder) as client:
        await client.warm(day=DAY)
        await client.fetch(query, related=True, label="a_tier")
    expected = [f"{kind}:{phase}" for phase in ("warmup", "explore", "multiline", "related") for kind in ("gate", "http", "record")]
    assert recorder.events == expected
    assert [(step.phase, step.query, step.label) for step in recorder.steps[1:]] == [
        (phase, query, "a_tier") for phase in (Phase.EXPLORE, Phase.MULTILINE, Phase.RELATED)
    ]
    assert [record.label for record in recorder.records] == [None, "a_tier", "a_tier", "a_tier"]

    class Stop(Exception):
        pass

    async def refuse_multiline(step):
        if step.phase is Phase.MULTILINE:
            raise Stop

    fake = FakeTrends(explore="explore_4lines_us_h", multiline="multiline_hourly_ok")
    records = []

    async def keep(record):
        records.append(record)

    client = TrendsClient(jar=FakeJar(), clock=ManualClock(START), gate=refuse_multiline, on_request=keep, transport=fake.transport())
    async with client:
        with pytest.raises(Stop):
            await client.fetch(query)
    assert fake.phases() == ["explore"] and [record.phase for record in records] == [Phase.EXPLORE]


def test_gate_and_on_request_are_required():
    """Like D42: an executor that forgets to wire the pacer or the request log fails at construction."""
    with pytest.raises(TypeError):
        TrendsClient(jar=FakeJar(), clock=ManualClock(START), on_request=Recorder().on_request)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        TrendsClient(jar=FakeJar(), clock=ManualClock(START), gate=Recorder().gate)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_related_only_and_missing_widget():
    fake = FakeTrends(explore="explore_1line_ww_d", related=["related_ok", "related_empty"])
    async with make_client(fake, Recorder(fake)) as client:
        seeds = await client.fetch(query_of("explore_1line_ww_d"), timeline=False, related=True)
        empty = await client.fetch(query_of("explore_1line_ww_d"), timeline=False, related=True)
    assert fake.phases() == ["explore", "related", "explore", "related"]
    assert seeds.status is FetchStatus.OK and seeds.timeline is None and seeds.related.rising[0].formatted_value == "Breakout"
    assert empty.status is FetchStatus.NO_DATA and empty.related.top is None
    fake = FakeTrends(explore="explore_scraper_no_related", multiline="multiline_daily_zero")
    async with make_client(fake, Recorder(fake)) as client:
        stripped = await client.fetch(query_of("explore_scraper_no_related"), related=True)
    # No widget, no request: gate B reads this as "related queries unavailable to this session".
    assert fake.phases() == ["explore", "multiline"]
    assert stripped.related.status is FetchStatus.NO_DATA and stripped.related.widget_missing
    assert stripped.status is FetchStatus.OK_ZERO


@pytest.mark.asyncio
async def test_query_refused_before_any_http():
    terms = ("a", "b")
    bad = [
        dict(terms=(), bare=None),
        dict(terms=tuple("abcdef"), bare="a"),
        dict(terms=("a", "a"), bare="a"),
        dict(terms=("a", " "), bare="a"),
        dict(terms=("a\x00",), bare=None),
        dict(terms=terms, bare="c"),
        dict(terms=terms, bare="a", geo="us"),
        dict(terms=terms, bare="a", geo="USA"),
        dict(terms=terms, bare="a", geo="US\n"),
        dict(terms=terms, bare="a", granularity="W"),
        dict(terms=terms, bare="a", search_property="news"),
        dict(terms=terms, bare="a", search_property=""),  # Google's spelling of web search, not the contract's
        dict(terms=terms, bare="a", related_term="c"),
    ]
    for case in bad:
        spec = {"geo": "US", "granularity": "H", **case}
        with pytest.raises(ValueError):
            TrendsQuery(**spec)
    fake = FakeTrends()
    async with make_client(fake, Recorder(fake)) as client:
        with pytest.raises(ValueError):
            await client.fetch(TrendsQuery(terms=terms, bare="a", geo="US", granularity="H"), timeline=False, related=False)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_capture_hands_raw_bodies_to_stage0():
    fake = FakeTrends(explore="explore_4lines_us_h", multiline="multiline_hourly_ok")
    recorder = Recorder(fake)
    async with make_client(fake, recorder, capture=recorder.capture) as client:
        await client.fetch(query_of("explore_4lines_us_h"))
    assert recorder.bodies == [body_of(load("explore_4lines_us_h")["response"]), body_of(load("multiline_hourly_ok")["response"])]


def _leaked_secrets(texts: list[str], secrets: tuple[str, ...]) -> list[str]:
    return [secret for secret in secrets for text in texts if secret in text]


@pytest.mark.asyncio
async def test_no_cookie_in_logs_or_repr(caplog):
    caplog.set_level(logging.DEBUG)
    secrets = ("synthetic-nid-old-SECRET", "synthetic-nid-first", "synthetic-aec-first")

    def quoting_error(request):
        raise httpx.ConnectError(f"refused with {request.headers.get('cookie')}", request=request)

    class LeakyJar(FakeJar):
        """A jar whose own repr shows its values: the client must not rely on the jar to keep them out."""

        def __repr__(self) -> str:
            return f"LeakyJar({[(cookie.name, cookie.value) for cookie in self.cookies]!r})"

    leaky = LeakyJar(user_agent="UA-fixed", cookies=(SetCookie("NID", "525=synthetic-nid-old-SECRET"),))
    jar = jar_of(("NID", "525=synthetic-nid-old-SECRET"))
    fake = FakeTrends(warmup="warmup_ok", explore=["explore_4lines_us_h", "http_429", "timeout"], multiline="multiline_hourly_ok")
    recorder = Recorder(fake)
    client = make_client(fake, recorder, jar=jar)
    texts = [repr(make_client(fake, Recorder(), jar=leaky)), repr(jar.cookies), str(jar.cookies[0])]
    texts += [repr(client), repr(jar), str(jar)]
    async with client:
        warmed = await client.warm(day=DAY)
        results = [await client.fetch(query_of("explore_4lines_us_h")) for _ in range(3)]
        fake.answers["explore"] = quoting_error
        results.append(await client.fetch(query_of("explore_4lines_us_h")))
        texts += [repr(client), repr(client.jar), str(client.jar), repr(warmed)]
    texts += [caplog.text, *(repr(result) for result in results), *(repr(record) for record in recorder.records)]
    assert _leaked_secrets(texts, secrets) == []
    # The connection error's text quoted the cookie; the result keeps its class and nothing else.
    assert results[-1].status is FetchStatus.TIMEOUT and recorder.records[-1].error_class == "ConnectError"
    assert "NID" in repr(client.jar)  # names are fine, values never


@pytest.mark.asyncio
async def test_egress_recorded_per_request():
    """D20, when U13 has approved an echo service: measured at the session's first request, again after every 20
    requests and after a breaker trip; every request row and result carries the latest reading and when it was taken."""
    clock = ManualClock(START)
    answers = iter(["203.0.113.7", '{"ip": "203.0.113.8"}', "203.0.113.9\n"])
    echoed: list[httpx.Request] = []

    def echo(request):
        echoed.append(request)
        return httpx.Response(200, text=next(answers), request=request)

    probe = EgressProbe("https://echo.example.test/ip", clock=clock, transport=httpx.MockTransport(echo))
    fake = FakeTrends(explore="explore_4lines_us_h", multiline="multiline_hourly_ok")
    recorder = Recorder(fake)
    jar = jar_of(("NID", "525=synthetic-nid-old"))
    results = []
    async with make_client(fake, recorder, jar=jar, clock=clock, egress=probe) as client, probe:
        for _ in range(11):
            results.append(await client.fetch(query_of("explore_4lines_us_h")))
            clock.advance(60)
        probe.invalidate()  # the executor's breaker tripped
        results.append(await client.fetch(query_of("explore_4lines_us_h")))
    readings = [record.egress for record in recorder.records]
    assert all(reading.ip is not None and reading.measured_at is not None for reading in readings)
    assert [reading.ip for reading in readings] == ["203.0.113.7"] * 20 + ["203.0.113.8"] * 2 + ["203.0.113.9"] * 2
    assert readings[0].measured_at == START and readings[20].measured_at == START + timedelta(minutes=10)
    assert [result.egress for result in results] == [result.requests[-1].egress for result in results]
    assert len(echoed) == 3 and {request.url.host for request in echoed} == {"echo.example.test"}
    assert all("cookie" not in request.headers and request.headers["user-agent"] != "UA-fixed" for request in echoed)


@pytest.mark.asyncio
async def test_egress_disabled_records_null():
    def refuse(request):
        raise AssertionError("egress probing is off: nothing may be sent to an echo service")

    clock = ManualClock(START)
    disabled = EgressProbe(None, clock=clock, transport=httpx.MockTransport(refuse))
    assert not disabled.enabled and not egress_from_env({}, clock=clock).enabled
    assert not egress_from_env({ECHO_ENV: "  "}, clock=clock).enabled
    for bad in ("http://echo.example.test/ip", "echo.example.test", "https://user:pw@echo.example.test/"):
        with pytest.raises(Refused):
            egress_from_env({ECHO_ENV: bad}, clock=clock)
    for egress in (disabled, None):
        fake = FakeTrends(explore="explore_4lines_us_h", multiline="multiline_hourly_ok")
        recorder = Recorder(fake)
        async with make_client(fake, recorder, clock=clock, egress=egress) as client:
            result = await client.fetch(query_of("explore_4lines_us_h"))
        assert result.egress == EgressReading(None, None)
        assert [record.egress for record in recorder.records] == [EgressReading(None, None)] * 2
    await disabled.aclose()


@pytest.mark.asyncio
async def test_failed_echo_keeps_last_reading_and_waits():
    clock = ManualClock(START)
    answers = iter([httpx.Response(200, text="203.0.113.7"), httpx.Response(503, text="down"), httpx.Response(200, text="not an ip")])
    calls = []

    def echo(request):
        calls.append(request)
        return next(answers)

    probe = EgressProbe("https://echo.example.test/ip", clock=clock, transport=httpx.MockTransport(echo), every=2)
    try:
        readings = [await probe.before_request() for _ in range(6)]
    finally:
        await probe.aclose()
    assert [reading.ip for reading in readings] == ["203.0.113.7"] * 6 and len(calls) == 3


@pytest.mark.asyncio
async def test_echo_failures_keep_last_reading():
    clock = ManualClock(START)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    answers = iter(
        [
            lambda request: httpx.Response(200, json={"ip": "2001:db8::7"}, request=request),
            refused,
            lambda request: httpx.Response(200, text="203.0.113.9" + " " * 2000, request=request),
            lambda request: httpx.Response(200, text="{not json", request=request),
            lambda request: httpx.Response(200, json={"ip": 7}, request=request),
            lambda request: httpx.Response(302, headers={"location": "https://elsewhere.example/"}, request=request),
        ]
    )
    probe = EgressProbe("https://echo.example.test/ip", clock=clock, transport=httpx.MockTransport(lambda request: next(answers)(request)), every=1)
    async with probe:
        readings = []
        for _ in range(6):
            readings.append(await probe.before_request())
            clock.advance(60)
    assert readings == [EgressReading("2001:db8::7", START)] * 6 and probe.reading == readings[-1]
    assert repr(probe) == "EgressProbe(enabled=True)"
    with pytest.raises(ValueError):
        EgressProbe("https://echo.example.test/ip", clock=clock, every=0)


def test_client_is_a_trends_source():
    client = TrendsClient(jar=FakeJar(), clock=ManualClock(START), gate=Recorder().gate, on_request=Recorder().on_request, transport=FakeTrends().transport())
    assert isinstance(client, TrendsSource) and client.source_name == SOURCE_NAME == "trends-direct"
    assert isinstance(FakeJar(), TrendsJar)


def test_set_cookie_is_tr04_cookie():
    """Integration tripwire (skipped until batch 1a merges trends/cookies.py): the channel has one cookie jar. At the
    merge source.SetCookie becomes an import of TR-04's Cookie, so the parser builds the jar's own cookies
    (docs/pick-workbench/observe-runbook/trends-client.md, 集成说明)."""
    cookies = pytest.importorskip("ggwork_pick.observe.trends.cookies")
    assert SetCookie is cookies.Cookie, "集成时把 trends/source.py 的 SetCookie 换成 cookies.Cookie 的导入"
    assert isinstance(cookies.CookieJar.fresh("UA-fixed"), TrendsJar)


@pytest.mark.asyncio
async def test_client_drives_tr04_cookie_jar():
    """The client on TR-04's real CookieJar (skipped until the integration): warm-up, rotation, removal, refusal."""
    cookies = pytest.importorskip("ggwork_pick.observe.trends.cookies")
    jar = cookies.CookieJar.fresh("UA-fixed")
    fake = FakeTrends(warmup="warmup_ok", explore=["explore_4lines_us_h", "http_429"], multiline="multiline_hourly_ok")
    async with make_client(fake, Recorder(fake), jar=jar) as client:
        warmed = await client.warm(day=DAY)
        await client.fetch(query_of("explore_4lines_us_h"))
        kept = client.jar
        await client.fetch(query_of("explore_4lines_us_h"))
    assert isinstance(warmed.jar, cookies.CookieJar) and warmed.jar.warmed_on == DAY and not warmed.jar.can_warm(DAY)
    assert "NID=525=synthetic-nid-first" in fake.requests[1].headers["cookie"]
    assert client.jar == kept and {request.headers["user-agent"] for request in fake.requests} == {"UA-fixed"}


SOURCE = Path(__file__).resolve().parents[2]
ROOT = Path(__file__).resolve().parents[4]
HEAVY = ("deerflow.runtime", "deerflow.config.app_config", "fastapi", "alembic", "langgraph", "dotenv", "sqlalchemy")


@pytest.mark.parametrize("module", ["ggwork_pick.observe.trends.client", "ggwork_pick.observe.trends.egress"])
def test_trends_client_import_is_light(module):
    """The trends cron and stage 0 import the client; it brings httpx and pydantic, not the gateway (plan D7)."""
    paths = [str(SOURCE), str(ROOT / "backend/packages/extension-api"), str(ROOT / "backend/packages/harness")]
    code = f"import importlib, json, sys\nsys.path[:0] = {json.dumps(paths)}\nimportlib.import_module({module!r})\nprint(json.dumps(sorted(sys.modules)))\n"
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    modules = json.loads(done.stdout.strip().splitlines()[-1])
    assert [name for name in modules if any(name == heavy or name.startswith(heavy + ".") for heavy in HEAVY)] == []
