"""A fake Google Trends for the TR-02 tests: canned answers from tests/fixtures/trends, served by httpx.MockTransport.

Nothing here reaches the network. Each fixture is one HTTP answer: status, header pairs (Set-Cookie may repeat) and a
body built from `prefix` + the JSON in `json`, or the raw `text`; a fixture with `raise` makes the transport raise that
httpx exception instead. The fixtures are constructed from design 4.1 and the direct-connection research, and list
under `pending_stage0` the shapes stage 0 (TR-05) still has to confirm against real responses.
"""

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.trends.client import TrendsClient
from ggwork_pick.observe.trends.source import SetCookie, TrendsJar, TrendsQuery

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "trends"
START = datetime(2026, 9, 25, 22, 10, tzinfo=UTC)
DAY = date(2026, 9, 25)  # the session's target date (D23)
BARE = "moonlit vow"
TRENDS_HOST = "trends.google.com"
PATH_PHASES = {
    "/trends/": "warmup",
    "/trends/api/explore": "explore",
    "/trends/api/widgetdata/multiline": "multiline",
    "/trends/api/widgetdata/relatedsearches": "related",
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def fixture_names() -> list[str]:
    return sorted(path.stem for path in FIXTURES.glob("*.json"))


def body_of(response: dict) -> bytes:
    if "json" in response:
        return (response.get("prefix", "") + json.dumps(response["json"], ensure_ascii=False)).encode("utf-8")
    return response.get("text", "").encode("utf-8")


def respond(name: str, request: httpx.Request) -> httpx.Response:
    response = load(name)["response"]
    if "raise" in response:
        raise getattr(httpx, response["raise"])("synthetic transport failure", request=request)
    headers = [(key, value) for key, value in response.get("headers", [])]
    return httpx.Response(response["status"], headers=headers, content=body_of(response), request=request)


class FakeTrends:
    """Answers each phase with the fixture named for it, or a callable; a list answers successive calls in turn.

    Every request is recorded; a request to any host but trends.google.com fails the test (no redirect is followed,
    no third party is called through this transport)."""

    def __init__(self, **answers: str | Callable[[httpx.Request], httpx.Response] | list):
        self.answers = {phase: list(answer) if isinstance(answer, list) else answer for phase, answer in answers.items()}
        self.requests: list[httpx.Request] = []
        self.events: list[str] | None = None

    def phases(self) -> list[str]:
        return [PATH_PHASES[request.url.path] for request in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == TRENDS_HOST, f"request left trends.google.com: {request.url.host}"
        phase = PATH_PHASES[request.url.path]
        self.requests.append(request)
        if self.events is not None:
            self.events.append(f"http:{phase}")
        answer = self.answers[phase]
        answer = answer.pop(0) if isinstance(answer, list) else answer
        return answer(request) if callable(answer) else respond(answer, request)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class Recorder:
    """The gate and on_request callbacks: records the order of gate calls, request records and HTTP sends."""

    def __init__(self, fake: FakeTrends | None = None):
        self.events: list[str] = []
        self.steps: list = []
        self.records: list = []
        self.bodies: list[bytes] = []
        if fake is not None:
            fake.events = self.events

    async def gate(self, step) -> None:
        self.steps.append(step)
        self.events.append(f"gate:{step.phase}")

    async def on_request(self, record) -> None:
        self.records.append(record)
        self.events.append(f"record:{record.phase}")

    async def capture(self, record, body: bytes) -> None:
        self.bodies.append(body)


@dataclass(frozen=True, repr=False)
class FakeJar:
    """The TrendsJar protocol as TR-04's trends/cookies.CookieJar implements it, for the client tests.

    Cookies are keyed by name, domain and path, a later Set-Cookie replaces an earlier one, and an expired cookie
    (a removal included) leaves the jar at the update; the user agent goes out with the cookies; a jar warms once per
    target date. The client tests pin the client's side of the seam; TR-04's own tests pin the jar."""

    user_agent: str = "UA-fixed"
    cookies: tuple = ()
    warmed_on: date | None = None

    @property
    def cookie_names(self) -> tuple[str, ...]:
        return tuple(cookie.name for cookie in self.cookies)

    def can_warm(self, day: date) -> bool:
        return self.warmed_on is None or self.warmed_on < day

    def request_headers(self, now: datetime) -> Mapping[str, str]:
        live = [cookie for cookie in self.cookies if not cookie.expired(now)]
        cookie = {"Cookie": "; ".join(f"{one.name}={one.value}" for one in live)} if live else {}
        return {"User-Agent": self.user_agent, **cookie}

    def updated(self, cookies: Iterable, *, now: datetime) -> "FakeJar":
        merged = {cookie.key: cookie for cookie in self.cookies} | {cookie.key: cookie for cookie in cookies}
        return replace(self, cookies=tuple(cookie for cookie in merged.values() if not cookie.expired(now)))

    def warmed(self, cookies: Iterable, *, day: date, now: datetime) -> "FakeJar":
        if not self.can_warm(day):
            raise ValueError("already warmed on that target date")
        return replace(self.updated(cookies, now=now), warmed_on=day)

    def __repr__(self) -> str:
        return f"FakeJar(user_agent={self.user_agent!r}, cookies={list(self.cookie_names)!r}, warmed_on={self.warmed_on!r})"


def streamed(status: int, body: bytes, headers: list[tuple[str, str]] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    """An answer whose body is read, and decoded, only when the client reads it (as on a real connection)."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers or [], stream=httpx.ByteStream(body), request=request)

    return answer


def query_of(name: str, **changes) -> TrendsQuery:
    """The query a fixture was answered for, with any field changed."""
    spec = load(name)["query"]
    query = TrendsQuery(terms=tuple(spec["terms"]), bare=spec["bare"], geo=spec["geo"], granularity=spec["granularity"])
    return replace(query, **changes) if changes else query


def jar_of(*pairs: tuple[str, str], warmed_on: date | None = None) -> FakeJar:
    return FakeJar(user_agent="UA-fixed", cookies=tuple(SetCookie(name, value) for name, value in pairs), warmed_on=warmed_on)


def make_client(fake: FakeTrends, recorder: Recorder, *, jar: TrendsJar | None = None, clock: ManualClock | None = None, **options) -> TrendsClient:
    return TrendsClient(
        jar=jar if jar is not None else FakeJar(),
        clock=clock or ManualClock(START),
        gate=recorder.gate,
        on_request=recorder.on_request,
        transport=fake.transport(),
        **options,
    )
