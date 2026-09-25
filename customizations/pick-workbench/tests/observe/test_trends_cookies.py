"""The one cookie jar of the Trends channel (plan 5: trends/cookies.py belongs to TR-02 and TR-04; design 4.4; D23).

TR-02's client and TR-04's state stores hand the same CookieJar around, so the jar carries every rule the client relies
on: a Set-Cookie with Max-Age <= 0 removes the cookie, a cookie that has expired leaves the jar at the next update, the jar
never holds more than MAX_COOKIES, and a refused warm-up still uses up its target date. A jar that goes through the state
file or the runtime row's sealed column comes back sending exactly the same request headers.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

NOW = datetime(2026, 9, 25, 22, 0, tzinfo=UTC)
TARGET = date(2026, 9, 26)  # the 02:00 UTC deadline this session works towards (D23)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"


def cookie(name: str, value: str = "v", **attributes):
    from ggwork_pick.observe.trends.cookies import Cookie

    return Cookie(name, value, **attributes)


def removal(name: str, **attributes):
    from ggwork_pick.observe.trends.cookies import Cookie

    return Cookie.removal(name, **attributes)


def unix(delta: timedelta) -> int:
    return int((NOW + delta).timestamp())


def fresh():
    from ggwork_pick.observe.trends.cookies import CookieJar

    return CookieJar.fresh(UA)


def test_max_age_zero_removes_the_cookie():
    """TR-02's parser turns Set-Cookie with Max-Age <= 0 into Cookie.removal: the cookie leaves the jar."""
    jar = fresh().warmed([cookie("NID", "525=nid"), cookie("AEC", "aec")], day=TARGET, now=NOW)
    assert jar.cookie_names == ("NID", "AEC")

    removed = jar.updated([removal("AEC")], now=NOW)
    assert removed.cookie_names == ("NID",)
    assert dict(removed.request_headers(NOW)) == {"User-Agent": UA, "Cookie": "NID=525=nid"}
    assert removed.updated([removal("ABSENT")], now=NOW).cookies == removed.cookies  # removing nothing adds nothing

    # Set-Cookie headers apply in order: the later one wins.
    assert jar.updated([cookie("X", "1"), removal("X")], now=NOW).cookie_names == ("NID", "AEC")
    assert jar.updated([removal("X"), cookie("X", "1")], now=NOW).cookie_names == ("NID", "AEC", "X")
    # A removal names one cookie: name, domain and path.
    scoped = jar.updated([cookie("NID", "scoped", domain=".google.com")], now=NOW)
    assert scoped.updated([removal("NID")], now=NOW).cookie_names == ("AEC", "NID")
    assert removal("NID").expired(NOW) and removal("NID").value == ""


def test_expired_cookies_leave_the_jar():
    """Expired cookies were only left out of the header before, so the jar grew for ever; now they leave it."""
    jar = fresh().warmed([cookie("NID"), cookie("AEC", expires=unix(timedelta(hours=1)))], day=TARGET, now=NOW)
    later = NOW + timedelta(hours=2)
    assert "AEC" not in jar.request_headers(later)["Cookie"]
    assert jar.updated([], now=later).cookie_names == ("NID",)  # gone from the jar, not only from the header
    assert jar.warmed((), day=TARGET + timedelta(days=1), now=later).cookie_names == ("NID",)
    arrives_expired = cookie("OLD", expires=unix(-timedelta(seconds=1)))
    assert jar.updated([arrives_expired], now=NOW).cookie_names == ("NID", "AEC")  # never stored
    with pytest.raises(ValueError):
        jar.updated([], now=NOW.replace(tzinfo=None))


def test_jar_never_grows_past_the_cap():
    from ggwork_pick.observe.trends.cookies import MAX_COOKIES, CookieJar

    names = [f"C{index:02d}" for index in range(MAX_COOKIES)]
    full = fresh().warmed([cookie(name) for name in names], day=TARGET, now=NOW)
    assert MAX_COOKIES == 20 and full.cookie_names == tuple(names)

    more = full.updated([cookie("C05", "new"), cookie("EXTRA")], now=NOW)
    assert more.cookie_names == tuple(names)  # the cookies it had come first; one past the cap is dropped
    assert more.request_headers(NOW)["Cookie"].split("; ")[5] == "C05=new"  # a replacement keeps its place
    assert full.updated([removal("C00"), cookie("EXTRA")], now=NOW).cookie_names == (*names[1:], "EXTRA")

    overflowing = fresh().warmed([cookie(f"D{index:02d}") for index in range(MAX_COOKIES + 5)], day=TARGET, now=NOW)
    assert len(overflowing.cookies) == MAX_COOKIES
    too_many = tuple(cookie(f"D{index:02d}") for index in range(MAX_COOKIES + 1))
    with pytest.raises(ValueError):
        CookieJar(UA, too_many)
    document = {"user_agent": UA, "warmed_on": None, "cookies": [one.to_document() for one in too_many]}
    with pytest.raises(ValueError):  # the state store turns this into StateUnavailable
        CookieJar.from_document(document)


def test_refused_warmup_still_uses_up_the_day():
    """TR-02: a warm-up that was refused counts as that target date's; it is not tried again until the next one."""
    refused = fresh().warmed((), day=TARGET, now=NOW)
    assert refused.warmed_on == TARGET and refused.cookies == ()
    assert not refused.can_warm(TARGET)
    with pytest.raises(ValueError):
        refused.warmed([cookie("NID")], day=TARGET, now=NOW)

    yesterday = fresh().warmed([cookie("NID", "old")], day=TARGET - timedelta(days=1), now=NOW)
    kept = yesterday.warmed((), day=TARGET, now=NOW)  # refused today: the old jar is kept (design 4.4)
    assert kept.cookie_names == ("NID",) and kept.warmed_on == TARGET and kept.user_agent == UA


def test_bounds_match_the_parser_and_the_column():
    """TR-02's parser takes names of up to 64 characters and values of up to 4096; the runtime row keeps the UA in a
    VARCHAR(500) column (TR-11). A jar never holds what those would refuse."""
    from ggwork_pick.observe.trends.cookies import MAX_COOKIE_NAME, MAX_COOKIE_VALUE, MAX_USER_AGENT, Cookie, CookieJar

    assert (MAX_COOKIE_NAME, MAX_COOKIE_VALUE, MAX_USER_AGENT) == (64, 4096, 500)
    Cookie("N" * MAX_COOKIE_NAME, "v" * MAX_COOKIE_VALUE)
    CookieJar.fresh("U" * MAX_USER_AGENT)
    for build in (
        lambda: Cookie("N" * (MAX_COOKIE_NAME + 1), "v"),
        lambda: Cookie("N", "v" * (MAX_COOKIE_VALUE + 1)),
        lambda: Cookie("N", "v", domain="d" * 256),
        lambda: Cookie("N", "v", path="/" + "p" * 1024),
        lambda: CookieJar.fresh("U" * (MAX_USER_AGENT + 1)),
    ):
        with pytest.raises(ValueError):
            build()


def built_jar():
    """A jar after a warm-up and an ordinary response: replaced, removed, scoped and expiring cookies."""
    warmed = fresh().warmed(
        [cookie("NID", "525=a+b/c=="), cookie("AEC", "x"), cookie("__Secure-ENID", "y", domain=".google.com", expires=unix(timedelta(days=30)))],
        day=TARGET,
        now=NOW,
    )
    return warmed.updated([removal("AEC"), cookie("SOCS", "z", path="/trends"), cookie("NID", "526=next")], now=NOW)


@pytest.mark.asyncio
async def test_jar_round_trip_keeps_the_request_headers(tmp_path: Path):
    """The seam between the client and the stores: a jar saved and loaded, through the state file or through the sealed
    column of the runtime row, sends exactly what it sent before, and goes on updating the same way."""
    from ggwork_pick.observe.crypto import StateCipher
    from ggwork_pick.observe.state import FileStateStore, RuntimeState
    from ggwork_pick.observe.trends.cookies import open_jar, seal_jar

    jar = built_jar()
    headers = dict(jar.request_headers(NOW))
    assert headers["Cookie"] == "NID=526=next; __Secure-ENID=y; SOCS=z"

    key = Fernet.generate_key().decode()
    path = tmp_path / "obs" / "trends-state.json"
    writer = FileStateStore(path, StateCipher([key]))
    await writer.initialize(RuntimeState(cookie_jar=jar))
    writer.close()  # the run that saved it has ended
    loaded = (await FileStateStore(path, StateCipher([key])).load()).cookie_jar
    sealed = open_jar(StateCipher([key]), seal_jar(StateCipher([key]), jar), user_agent=UA)

    for back in (loaded, sealed):
        assert back == jar
        assert dict(back.request_headers(NOW)) == headers
        assert back.request_headers(NOW)["Cookie"] == headers["Cookie"]  # verbatim, order included
        next_response = [removal("SOCS"), cookie("NEW", "n")]
        assert back.updated(next_response, now=NOW) == jar.updated(next_response, now=NOW)
