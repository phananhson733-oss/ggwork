"""A fake Google Trends for the TR-02 tests: canned answers from tests/fixtures/trends, served by httpx.MockTransport.

Nothing here reaches the network. Each fixture is one HTTP answer: status, header pairs (Set-Cookie may repeat) and a
body built from `prefix` + the JSON in `json`, or the raw `text`; a fixture with `raise` makes the transport raise that
httpx exception instead. The fixtures are constructed from design 4.1 and the direct-connection research, and list
under `pending_stage0` the shapes stage 0 (TR-05) still has to confirm against real responses.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "trends"
START = datetime(2026, 9, 25, 22, 10, tzinfo=UTC)
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
    """Answers each phase with the fixture named for it; a list of names answers successive calls in turn.

    Every request is recorded; a request to any host but trends.google.com fails the test (no redirect is followed,
    no third party is called through this transport)."""

    def __init__(self, **answers: str | list[str] | Callable[[httpx.Request], httpx.Response]):
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
        if callable(answer):
            return answer(request)
        name = answer.pop(0) if isinstance(answer, list) else answer
        return respond(name, request)

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
