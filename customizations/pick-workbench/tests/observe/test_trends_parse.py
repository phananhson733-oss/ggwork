"""TR-02: the Trends response parser (plan TR-02; design 4.1, 4.7, 4.10).

Every answer is checked for the shape the parser relies on; anything else is a parse_error, never a guess. The shapes
come from constructed fixtures (tests/fixtures/trends) whose `pending_stage0` lists what stage 0 still has to confirm.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from trends_fakes import body_of, fixture_names, load

from ggwork_pick.observe.trends.parse import (
    ParseFailure,
    classify,
    decode_json,
    parse_explore,
    parse_related,
    parse_timeline,
    redirect_kind,
    set_cookies,
    strip_xssi,
)
from ggwork_pick.observe.trends.source import FetchStatus, Phase, RedirectKind, SetCookie, TrendsQuery

BARE = "moonlit vow"
TERMS4 = (BARE, f"{BARE} full movie", f"{BARE} drama", f"{BARE} episode")


def _payload(name: str) -> object:
    return decode_json(body_of(load(name)["response"]))


def _query(name: str) -> TrendsQuery:
    spec = load(name)["query"]
    return TrendsQuery(terms=tuple(spec["terms"]), bare=spec["bare"], geo=spec["geo"], granularity=spec["granularity"])


def _points(name: str) -> list[dict]:
    return load(name)["response"]["json"]["default"]["timelineData"]


def test_fixtures_are_marked():
    """Each fixture says what it is and, while it is constructed, what stage 0 still has to confirm (plan TR-02, TR-05).

    A redacted real response replacing one sets constructed to false; cookies stay synthetic either way."""
    names = fixture_names()
    assert len(names) >= 10
    for name in names:
        fixture = load(name)
        assert {"about", "constructed", "pending_stage0", "response"} <= set(fixture), name
        assert isinstance(fixture["pending_stage0"], list) and isinstance(fixture["constructed"], bool), name
        response = fixture["response"]
        assert len({"json", "text", "raise"} & set(response)) == 1, name
        cookies = [value for key, value in response.get("headers", []) if key.lower() == "set-cookie"]
        assert all("synthetic" in value for value in cookies), name


def test_strip_xssi_prefix():
    for prefix in (")]}'", ")]}',", ")]}'\n", ")]}',\n", ")]}'\r\n"):
        assert json.loads(strip_xssi(prefix + '{"a": 1}')) == {"a": 1}
    # Only a leading prefix goes; the same characters inside the JSON stay.
    inner = '{"a": ")]}\'"}'
    assert strip_xssi(inner) == inner
    for name in ("explore_4lines_us_h", "explore_1line_ww_d", "multiline_hourly_ok", "multiline_daily_zero", "related_ok"):
        assert isinstance(_payload(name), dict), name


def test_decode_refuses_what_is_not_json():
    for body in (b"", b")]}'\n", b')]}\'\n{"default": [', b"\xff\xfe", b")]}'\n[1, 2"):
        with pytest.raises(ParseFailure):
            decode_json(body)
    with pytest.raises(ParseFailure):
        decode_json(body_of(load("json_truncated")["response"]))


def test_variant_lines_order():
    """One line per term, in the order the terms were requested; the bare title's line can be taken on its own."""
    points = _points("multiline_hourly_ok")
    timeline = parse_timeline(_payload("multiline_hourly_ok"), TERMS4)
    assert timeline.status is FetchStatus.OK
    assert [line.term for line in timeline.lines] == list(TERMS4)
    for index, line in enumerate(timeline.lines):
        assert line.values == tuple(point["value"][index] for point in points)
    assert timeline.line(BARE) is timeline.lines[0]
    assert timeline.line("not requested") is None
    # Lines are positional: the same answer read against the terms in another order moves every line with them.
    reversed_terms = tuple(reversed(TERMS4))
    reread = parse_timeline(_payload("multiline_hourly_ok"), reversed_terms)
    assert reread.line(BARE).values == tuple(point["value"][3] for point in points)


def test_timeline_statuses():
    empty = parse_timeline(_payload("multiline_empty"), (BARE,))
    assert (empty.status, empty.lines) == (FetchStatus.NO_DATA, None)
    zero = parse_timeline(_payload("multiline_daily_zero"), (BARE,))
    assert zero.status is FetchStatus.OK_ZERO
    assert [line.status for line in zero.lines] == [FetchStatus.OK_ZERO]
    assert set(zero.lines[0].values) == {0} and len(zero.lines[0].values) == 30
    mixed = {"default": {"timelineData": [{"time": "1789768800", "value": [0, 3]}, {"time": "1789772400", "value": [0, 0]}]}}
    parsed = parse_timeline(mixed, ("a", "b"))
    assert parsed.status is FetchStatus.OK
    assert [line.status for line in parsed.lines] == [FetchStatus.OK_ZERO, FetchStatus.OK]


def test_timeline_keeps_raw_points():
    points = _points("multiline_hourly_ok")
    line = parse_timeline(_payload("multiline_hourly_ok"), TERMS4).lines[0]
    assert line.times == tuple(int(point["time"]) for point in points)
    assert line.partial == tuple(point.get("isPartial") for point in points)
    assert line.has_data == tuple(point["hasData"][0] for point in points)
    # The raw row keeps time as it was sent, a string of epoch seconds (design 3.5 "原样的 time").
    assert line.raw_times == tuple(point["time"] for point in points) and isinstance(line.raw_times[0], str)
    assert line.as_raw() == {
        "term": BARE,
        "status": "ok",
        "time": [point["time"] for point in points],
        "value": [point["value"][0] for point in points],
        "isPartial": [point.get("isPartial") for point in points],
        "hasData": [point["hasData"][0] for point in points],
    }


def _point(**changes) -> dict:
    return {"time": "1789768800", "value": [1, 2], "hasData": [True, True], **changes}


MALFORMED_TIMELINES = {
    "no default": {"timelineData": []},
    "timelineData not a list": {"default": {"timelineData": {}}},
    "point not an object": {"default": {"timelineData": [[1, 2]]}},
    "value too short": {"default": {"timelineData": [_point(value=[1])]}},
    "value a string": {"default": {"timelineData": [_point(value=["1", 2])]}},
    "value a float": {"default": {"timelineData": [_point(value=[1.5, 2])]}},
    "value a bool": {"default": {"timelineData": [_point(value=[True, 2])]}},
    "value above 100": {"default": {"timelineData": [_point(value=[101, 2])]}},
    "value negative": {"default": {"timelineData": [_point(value=[-1, 2])]}},
    "time not digits": {"default": {"timelineData": [_point(time="Sep 18")]}},
    "time missing": {"default": {"timelineData": [{"value": [1, 2]}]}},
    # [counterexample 1] a point without its values is a changed answer, never a point of zeros (ok_zero, judged sparse)
    "value missing": {"default": {"timelineData": [{"time": "1789768800", "hasData": [True, True]}]}},
    "value null": {"default": {"timelineData": [_point(value=None)]}},
    "value missing on a later point": {"default": {"timelineData": [_point(), {"time": "1789772400", "hasData": [True, True]}]}},
    "times not increasing": {"default": {"timelineData": [_point(), _point()]}},
    "isPartial a string": {"default": {"timelineData": [_point(isPartial="true")]}},
    "hasData too short": {"default": {"timelineData": [_point(hasData=[True])]}},
    "hasData not bools": {"default": {"timelineData": [_point(hasData=[1, 0])]}},
}


@pytest.mark.parametrize("case", sorted(MALFORMED_TIMELINES))
def test_timeline_shape_checked(case):
    with pytest.raises(ParseFailure):
        parse_timeline(MALFORMED_TIMELINES[case], ("a", "b"))


def test_has_data_absent_reads_none():
    """hasData is kept as sent: a point without the key reads None for every line, never a filled-in False."""
    points = [
        _point(value=[0, 5]),
        {"time": "1789772400", "value": [0, 0]},
        _point(time="1789776000", value=[0, 0], hasData=[False, True]),
    ]
    timeline = parse_timeline({"default": {"timelineData": points}}, ("a", "b"))
    assert timeline.status is FetchStatus.OK
    assert [line.has_data for line in timeline.lines] == [(True, None, False), (True, None, True)]
    assert timeline.lines[0].as_raw()["hasData"] == [True, None, False]


def test_explore_widgets():
    widgets = load("explore_4lines_us_h")["response"]["json"]["widgets"]
    explore = parse_explore(_payload("explore_4lines_us_h"), _query("explore_4lines_us_h"), timeline=True, related=True)
    assert explore.timeseries.token == widgets[0]["token"] and explore.timeseries.request == widgets[0]["request"]
    assert explore.user_type == "USER_TYPE_LEGIT_USER"
    assert explore.related.id == "RELATED_QUERIES_0" and explore.related.keyword == BARE
    variant = TrendsQuery(terms=TERMS4, bare=BARE, geo="US", granularity="H", related_term=f"{BARE} drama")
    assert parse_explore(_payload("explore_4lines_us_h"), variant, timeline=True, related=True).related.id == "RELATED_QUERIES_2"
    single = parse_explore(_payload("explore_1line_ww_d"), _query("explore_1line_ww_d"), timeline=True, related=True)
    assert single.related.id == "RELATED_QUERIES"
    scraper = parse_explore(_payload("explore_scraper_no_related"), _query("explore_scraper_no_related"), timeline=True, related=True)
    assert scraper.related is None and scraper.user_type == "USER_TYPE_SCRAPER"
    assert parse_explore(_payload("explore_4lines_us_h"), _query("explore_4lines_us_h"), timeline=True, related=False).related is None


def _explore_with_keywords(*keywords: str) -> dict:
    """explore_4lines_us_h with each RELATED_QUERIES_<i> widget's keyword replaced, as Google might normalise it."""
    payload = _payload("explore_4lines_us_h")
    for widget in payload["widgets"]:
        if widget["id"].startswith("RELATED_QUERIES_"):
            index = int(widget["id"].rsplit("_", 1)[1])
            widget["request"]["restriction"]["complexKeywordsRestriction"]["keyword"][0]["value"] = keywords[index]
    return payload


def _without(payload: dict, *ids: str) -> dict:
    return {**payload, "widgets": [widget for widget in payload["widgets"] if widget["id"] not in ids]}


def test_related_widget_found_despite_keyword_normalisation():
    """A widget whose keyword Google spelled differently is still the term's: by position (RELATED_QUERIES_<i>)."""
    query = TrendsQuery(terms=TERMS4, bare=BARE, geo="US", granularity="H", related_term=TERMS4[2])
    for spelled in ("Moonlit  Vow Drama", "moonlit vow drama "):
        payload = _explore_with_keywords(TERMS4[0], TERMS4[1], spelled, TERMS4[3])
        assert parse_explore(payload, query, timeline=True, related=True).related.id == "RELATED_QUERIES_2"
    renamed = _explore_with_keywords("a", "b", "c", "d")  # nothing matches by keyword: the position decides
    assert parse_explore(renamed, query, timeline=True, related=True).related.id == "RELATED_QUERIES_2"


def test_related_widgets_that_do_not_fit_are_a_parse_failure():
    """Related widgets that exist but none of which is the term's are a changed contract, not gate B's "no related
    queries for this session" (widget_missing, which stays for an explore without any RELATED_QUERIES widget)."""
    query = TrendsQuery(terms=TERMS4, bare=BARE, geo="US", granularity="H", related_term=TERMS4[2])
    missing_own = _without(_explore_with_keywords("a", "b", "c", "d"), "RELATED_QUERIES_2")
    swapped = _explore_with_keywords(TERMS4[0], TERMS4[1], TERMS4[3], "d")  # its slot names another requested term
    for payload in (missing_own, swapped):
        with pytest.raises(ParseFailure):
            parse_explore(payload, query, timeline=True, related=True)
    single = _query("explore_1line_ww_d")
    two_unmatched = _payload("explore_1line_ww_d")
    extra = json.loads(json.dumps(next(widget for widget in two_unmatched["widgets"] if widget["id"] == "RELATED_QUERIES")))
    two_unmatched["widgets"] = [*two_unmatched["widgets"], {**extra, "id": "RELATED_QUERIES_1"}]
    for widget in two_unmatched["widgets"]:
        if widget["id"].startswith("RELATED_QUERIES"):
            widget["request"]["restriction"]["complexKeywordsRestriction"]["keyword"][0]["value"] = "something else"
    with pytest.raises(ParseFailure):
        parse_explore(two_unmatched, single, timeline=True, related=True)
    # No related widget at all: the SCRAPER signal, not a failure.
    none_at_all = _without(_payload("explore_4lines_us_h"), *(f"RELATED_QUERIES_{index}" for index in range(4)))
    assert parse_explore(none_at_all, query, timeline=True, related=True).related is None


def test_explore_shape_checked():
    query = _query("explore_1line_ww_d")
    good = _payload("explore_1line_ww_d")
    no_token = json.loads(json.dumps(good))
    del no_token["widgets"][0]["token"]
    no_series = {"widgets": [widget for widget in good["widgets"] if widget["id"] != "TIMESERIES"]}
    for payload in ({}, {"widgets": {}}, {"widgets": [1]}, no_token, no_series):
        with pytest.raises(ParseFailure):
            parse_explore(payload, query, timeline=True, related=False)
    # A widget the client does not use is not checked: an added or changed one cannot fail the unit.
    extra = json.loads(json.dumps(good))
    extra["widgets"] = [{"id": "SOMETHING_NEW"}, {"note": "no id at all"}, *extra["widgets"]]
    del extra["widgets"][3]["token"]  # GEO_MAP
    assert extra["widgets"][3]["id"] == "GEO_MAP"
    assert parse_explore(extra, query, timeline=True, related=True).related.id == "RELATED_QUERIES"
    # Without a timeline to fetch the series widget is not needed.
    assert parse_explore(no_series, query, timeline=False, related=True).related.id == "RELATED_QUERIES"
    odd_label = json.loads(json.dumps(good))
    odd_label["widgets"][0]["request"]["userConfig"]["userType"] = {"nested": True}
    assert parse_explore(odd_label, query, timeline=True, related=False).user_type is None


def test_related_parse():
    related = parse_related(_payload("related_ok"))
    assert related.status is FetchStatus.OK
    assert [item.query for item in related.top] == ["moonlit vow reelshort", "moonlit vow full episodes"]
    assert [(item.value, item.formatted_value) for item in related.rising] == [(5200, "Breakout"), (250, "+250%")]
    empty = parse_related(_payload("related_empty"))
    assert (empty.status, empty.top, empty.rising) == (FetchStatus.NO_DATA, None, None)
    for payload in ({}, {"default": {"rankedList": []}}, {"default": {"rankedList": [{"rankedKeyword": [{"query": 3}]}, {"rankedKeyword": []}]}}):
        with pytest.raises(ParseFailure):
            parse_related(payload)


REDIRECTS = {
    "https://www.google.com/sorry/index?continue=x": (RedirectKind.SORRY, "www.google.com"),
    "https://ipv4.google.com/sorry/index?continue=x": (RedirectKind.SORRY, "ipv4.google.com"),
    "https://www.google.de/sorry/index": (RedirectKind.SORRY, "www.google.de"),
    "https://consent.google.com/ml?continue=x": (RedirectKind.CONSENT, "consent.google.com"),
    "https://consent.youtube.com/m?continue=x": (RedirectKind.CONSENT, "consent.youtube.com"),
    "https://trends.google.com/trends/explore?geo=US": (RedirectKind.SAME_HOST, "trends.google.com"),
    "/trends/explore?geo=US": (RedirectKind.SAME_HOST, "trends.google.com"),
    "https://accounts.google.com/ServiceLogin": (RedirectKind.OTHER, "accounts.google.com"),
    "https://evil.example/sorry/index": (RedirectKind.OTHER, "evil.example"),
    "": (RedirectKind.OTHER, None),
}


@pytest.mark.parametrize("location", sorted(REDIRECTS))
def test_redirect_kinds(location):
    assert redirect_kind(location) == REDIRECTS[location]


def test_classify_by_phase():
    html = {"content-type": "text/html; charset=UTF-8"}
    as_json = {"content-type": "application/json; charset=utf-8"}
    # An API path answering with HTML is a limit signal; the warm-up page is HTML by design.
    assert classify(Phase.EXPLORE, 200, html, b"<!DOCTYPE html>") is FetchStatus.HTML_BODY
    assert classify(Phase.MULTILINE, 200, as_json, b"  <html>") is FetchStatus.HTML_BODY
    assert classify(Phase.WARMUP, 200, html, b"<!doctype html>") is FetchStatus.OK
    assert classify(Phase.MULTILINE, 200, as_json, b")]}'\n{}") is None
    expected = {429: FetchStatus.RATE_LIMITED, 403: FetchStatus.FORBIDDEN, 500: FetchStatus.SERVER_ERROR, 503: FetchStatus.SERVER_ERROR}
    for status, kind in expected.items():
        for phase in Phase:
            assert classify(phase, status, html, b"") is kind
    # A 4xx the design does not name means the request contract no longer holds.
    for status in (400, 401, 404, 410):
        assert classify(Phase.EXPLORE, status, as_json, b"") is FetchStatus.PARSE_ERROR


WALLS = ("https://www.google.com/sorry/index?continue=x", "https://consent.google.com/ml?continue=x")
ELSEWHERE = ("https://trends.google.com/trends/", "/trends/api/explore2", "https://accounts.google.com/ServiceLogin", "")


def test_only_sorry_or_consent_is_blocked_redirect():
    """blocked_redirect is the wall TR-03 ends the day on (design 4.3 names only sorry and consent redirects). Any
    other redirect on an API path is a changed contract, parse_error, never a limit signal; the warm-up page may move
    within trends.google.com."""
    for phase in Phase:
        for location in WALLS:
            assert classify(phase, 302, {"location": location}, b"") is FetchStatus.BLOCKED_REDIRECT, (phase, location)
    for phase in (Phase.EXPLORE, Phase.MULTILINE, Phase.RELATED):
        for status in (301, 302, 303, 307, 308):
            for location in ELSEWHERE:
                assert classify(phase, status, {"location": location}, b"") is FetchStatus.PARSE_ERROR, (phase, status, location)
    assert classify(Phase.WARMUP, 302, {"location": "https://trends.google.com/trends/explore"}, b"") is FetchStatus.OK
    assert classify(Phase.WARMUP, 302, {"location": "/trends/explore"}, b"") is FetchStatus.OK
    for location in ("https://accounts.google.com/ServiceLogin", ""):
        assert classify(Phase.WARMUP, 302, {"location": location}, b"") is FetchStatus.PARSE_ERROR


NOW = datetime(2026, 9, 25, 22, 0, tzinfo=UTC)


def _unix(moment: datetime) -> int:
    return int(moment.timestamp())


def test_set_cookies_carry_expiry_domain_and_path():
    """Set-Cookie lines become cookies for the jar with what TR-04's Cookie keeps: name, value, domain, path, expires."""
    lines = [
        "NID=525=synthetic-nid; expires=Fri, 26-Mar-2027 22:00:00 GMT; path=/; domain=.google.com; HttpOnly",
        "AEC=synthetic-aec; Max-Age=3600; Expires=Fri, 26-Mar-2027 22:00:00 GMT; Path=/trends; Secure",
        "SOCS=synthetic-socs",
        "EMPTY=; path=/",
    ]
    nid, aec, socs, empty = set_cookies(lines, now=NOW)
    assert all(isinstance(cookie, SetCookie) for cookie in (nid, aec, socs, empty))
    assert (nid.name, nid.value, nid.domain, nid.path, nid.expires) == (
        "NID",
        "525=synthetic-nid",
        "google.com",
        "/",
        _unix(datetime(2027, 3, 26, 22, tzinfo=UTC)),
    )
    # Max-Age wins over Expires (RFC 6265 5.3), counted from the moment the answer was read.
    assert (aec.domain, aec.path, aec.expires) == ("", "/trends", _unix(NOW + timedelta(hours=1)))
    assert (socs.domain, socs.path, socs.expires) == ("", "/", None)  # host-only session cookie
    assert (empty.value, empty.expires) == ("", None)  # an empty value is a value, not a removal
    assert not any(cookie.expired(NOW) for cookie in (nid, aec, socs, empty))


def test_set_cookies_removals():
    """Max-Age <= 0, or an Expires already past, removes the cookie: an expired cookie with no value."""
    lines = ["AEC=gone; max-age=0", "NID=gone; Max-Age=-5; domain=google.com", "OLD=gone; expires=Thu, 01 Jan 1970 00:00:01 GMT; path=/x"]
    removals = set_cookies(lines, now=NOW)
    assert [(cookie.name, cookie.domain, cookie.path) for cookie in removals] == [("AEC", "", "/"), ("NID", "google.com", "/"), ("OLD", "", "/x")]
    assert all(cookie.expired(NOW) and cookie.value == "" for cookie in removals)
    assert all(cookie == SetCookie.removal(cookie.name, domain=cookie.domain, path=cookie.path) for cookie in removals)


def test_set_cookies_drop_what_a_browser_would_refuse():
    refused = [
        "no-equals-sign",
        "=value-without-name",
        "bad name=x",
        'QUOTED="x"',
        "SPACE=a b",
        "N" * 65 + "=x",
        "LONG=" + "v" * 4097,
        "ELSEWHERE=x; domain=youtube.com",  # not a domain trends.google.com belongs to
        "SUFFIX=x; domain=.com",
        "LOOKALIKE=x; domain=evilgoogle.com",
        "WIDE=x; domain=www.trends.google.com",
    ]
    assert set_cookies(refused, now=NOW) == ()
    kept = set_cookies(["OK=1; domain=trends.google.com", "UP=1; domain=GOOGLE.com", "ODD=1; max-age=soon; path=relative"], now=NOW)
    assert [(cookie.name, cookie.domain, cookie.path, cookie.expires) for cookie in kept] == [
        ("OK", "trends.google.com", "/", None),
        ("UP", "google.com", "/", None),
        ("ODD", "", "/", None),  # an unreadable Max-Age and a relative Path are ignored, not fatal
    ]


def test_set_cookie_never_shows_its_value():
    cookie = SetCookie("NID", "525=synthetic-secret-value", "google.com")
    assert "synthetic-secret-value" not in repr(cookie) and "synthetic-secret-value" not in str(cookie) and "NID" in repr(cookie)
    for bad in (
        dict(name="bad name", value="x"),
        dict(name="N", value="a;b"),
        dict(name="N", value="x", path="rel"),
        dict(name="N", value="x", domain="a/b"),
        dict(name="N", value="v\n"),
        dict(name="N\n", value="v"),
    ):
        with pytest.raises(ValueError):
            SetCookie(**bad)
