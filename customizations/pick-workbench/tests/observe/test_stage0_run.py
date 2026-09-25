"""TR-05: running one day of stage 0 (plan TR-05; design 4.2, 4.3, 4.4, 4.11).

The runner drives TR-02's client through TR-03's pacer, breaker and budget and TR-04's file state, like the cron will:
every HTTP request is paced and reserved before it leaves, a limit signal pauses or ends the day, and the state file
carries the day across restarts. Everything runs on a ManualClock against httpx.MockTransport: nothing leaves the
process, and the artifacts directory is a temporary one.
"""

import asyncio
import json
import os
import stat
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet
from stage0_fakes import controls_document
from trends_fakes import respond

from ggwork_pick.observe.clock import ManualClock, random_source
from ggwork_pick.observe.crypto import StateCipher
from ggwork_pick.observe.errors import Refused, StateUnavailable
from ggwork_pick.observe.state import FileStateStore
from ggwork_pick.observe.trends import budget
from ggwork_pick.observe.trends.stage0 import DayPlan, Unit, parse_controls
from ggwork_pick.observe.trends.stage0_run import (
    PROXY_VARIABLES,
    DayRunner,
    Stage0Paths,
    load_results,
    proxy_environment,
)

START = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)  # target date 2026-09-27 (D23)
NID = "synthetic-nid-first"
LEGIT = "USER_TYPE_LEGIT_USER"


class FakeGoogle:
    """Answers the four Trends paths from the request itself: explore offers a series and a related widget for its one
    term, multiline returns 169 hourly or 31 daily points (the last partial), relatedsearches returns the related_ok
    fixture. `fail` maps a request's ordinal (1-based, over every request) to a fixture to answer with instead."""

    def __init__(self, *, fail: dict[int, str] | None = None, values: int = 9):
        self.fail = fail or {}
        self.values = values
        self.requests: list[httpx.Request] = []
        self.times: list[datetime] = []
        self.clock: ManualClock | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "trends.google.com"
        self.requests.append(request)
        if self.clock is not None:
            self.times.append(self.clock.now())
            self.clock.advance(0.4)
        name = self.fail.get(len(self.requests))
        if name is not None:
            return respond(name, request)
        path = request.url.path
        if path == "/trends/":
            return respond("warmup_ok", request)
        if path.endswith("/relatedsearches"):
            return respond("related_ok", request)
        req = json.loads(parse_qs(request.url.query.decode())["req"][0])
        body = self._explore(req) if path.endswith("/explore") else self._multiline(req)
        return httpx.Response(200, headers=[("content-type", "application/json")], content=(")]}'\n" + json.dumps(body)).encode(), request=request)

    @staticmethod
    def _explore(req: dict) -> dict:
        item = req["comparisonItem"][0]
        keyword = [{"type": "BROAD", "value": item["keyword"]}]
        series = {"id": "TIMESERIES", "token": "synthetic-series", "request": {"time": item["time"], "userConfig": {"userType": LEGIT}}}
        related = {
            "id": "RELATED_QUERIES",
            "token": "synthetic-related",
            "request": {"restriction": {"complexKeywordsRestriction": {"keyword": keyword}}, "userConfig": {"userType": LEGIT}},
        }
        return {"widgets": [series, related]}

    def _multiline(self, req: dict) -> dict:
        hourly = req["time"] == "now 7-d"
        count, step = (169, 3600) if hourly else (31, 86400)
        last = int(datetime(2026, 9, 26, 0, 0, tzinfo=UTC).timestamp())
        points = [{"time": str(last - step * (count - 1 - k)), "value": [self.values if k % 3 == 0 else 0], "hasData": [True]} for k in range(count)]
        points[-1] = {**points[-1], "isPartial": True}
        return {"default": {"timelineData": points}}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def paths(self) -> list[str]:
        return [request.url.path.rsplit("/", 1)[-1] or "warmup" for request in self.requests]


def small_plan(day: int = 1) -> DayPlan:
    return DayPlan(
        day=day,
        units=(
            Unit("pos-01-h", "pos-01", "H", True, False),
            Unit("pos-01-d", "pos-01", "D", True, True),
            Unit("seed-01-r", "seed-01", "H", False, True),
        ),
    )


def make_paths(tmp_path: Path) -> Stage0Paths:
    root = tmp_path / "trends-stage0"
    root.mkdir(mode=0o700)
    return Stage0Paths(root)


def runner(paths: Stage0Paths, key: str, fake: FakeGoogle, clock: ManualClock, *, plan: DayPlan | None = None, environ=None, limits=None) -> DayRunner:
    fake.clock = clock
    store = FileStateStore(paths.state_file, StateCipher([key]))
    options = {"limits": limits} if limits is not None else {}
    return DayRunner(
        plan=plan or small_plan(),
        controls=parse_controls(controls_document()),
        store=store,
        paths=paths,
        clock=clock,
        rng=random_source(7),
        transport=fake.transport(),
        environ=environ or {},
        **options,
    )


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.mark.asyncio
async def test_run_day_writes_results_state_and_raw(tmp_path, key):
    paths, fake, clock = make_paths(tmp_path), FakeGoogle(), ManualClock(START)
    outcome = await runner(paths, key, fake, clock).run(init_state=True)
    assert fake.paths() == ["warmup", "explore", "multiline", "explore", "multiline", "relatedsearches", "explore", "relatedsearches"]
    assert outcome.covered == ("pos-01-h", "pos-01-d", "seed-01-r") and outcome.uncovered == ()
    results = load_results(paths.run_dir(1))
    assert [line["status"] for line in results.values()] == ["ok", "ok", "ok"]
    assert results["pos-01-d"]["related"]["status"] == "ok" and len(results["pos-01-h"]["timeline"]["lines"][0]["value"]) == 169
    assert results["pos-01-h"]["target_date"] == "2026-09-27"
    for path in (paths.state_file, paths.run_dir(1) / "results.jsonl", paths.run_dir(1) / "meta.json", paths.raw_dir(1) / "index.jsonl"):
        assert mode(path) == 0o600, path
    for path in (paths.state_file.parent, paths.run_dir(1), paths.raw_dir(1)):
        assert mode(path) == 0o700, path
    index = [json.loads(line) for line in (paths.raw_dir(1) / "index.jsonl").read_text().splitlines()]
    assert [entry["phase"] for entry in index][:2] == ["warmup", "explore"]
    assert index[0]["body_file"] is None and all(entry["body_file"] for entry in index[1:])
    assert (paths.raw_dir(1) / index[1]["body_file"]).read_bytes().startswith(b")]}'")


@pytest.mark.asyncio
async def test_every_request_reserved_before_it_leaves(tmp_path, key):
    """Budget reserved = requests sent = request lines, the warm-up included (design 4.2)."""
    paths, fake, clock = make_paths(tmp_path), FakeGoogle(), ManualClock(START)
    await runner(paths, key, fake, clock).run(init_state=True)
    state = await FileStateStore(paths.state_file, StateCipher([key])).load()
    day = state.section("budget", budget.BudgetDay.from_dict)
    requests = (paths.run_dir(1) / "requests.jsonl").read_text().splitlines()
    assert day.reserved == len(fake.requests) == len(requests) == 8
    assert state.cookie_jar.warmed_on.isoformat() == "2026-09-27"


@pytest.mark.asyncio
async def test_requests_are_paced(tmp_path, key):
    """At least 1.5 s between two requests of a unit and 25 s between units: TR-03's pacer is wired in."""
    paths, fake, clock = make_paths(tmp_path), FakeGoogle(), ManualClock(START)
    await runner(paths, key, fake, clock).run(init_state=True)
    gaps = [(later - earlier).total_seconds() for earlier, later in zip(fake.times, fake.times[1:])]
    assert all(gap >= 1.5 for gap in gaps)
    unit_starts = [k for k, path in enumerate(fake.paths()) if path == "explore"]
    assert all(gaps[k - 1] >= 25 for k in unit_starts[1:])


@pytest.mark.asyncio
async def test_missing_state_runs_nothing(tmp_path, key):
    paths, fake, clock = make_paths(tmp_path), FakeGoogle(), ManualClock(START)
    with pytest.raises(StateUnavailable):
        await runner(paths, key, fake, clock).run(init_state=False)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_rerun_resumes_without_repeating(tmp_path, key):
    paths, fake, clock = make_paths(tmp_path), FakeGoogle(), ManualClock(START)
    await runner(paths, key, fake, clock).run(init_state=True)
    again = FakeGoogle()
    clock.advance(600)
    outcome = await runner(paths, key, again, clock).run()
    assert again.requests == [] and outcome.covered == ()
    assert len(json.loads((paths.run_dir(1) / "meta.json").read_text())["sessions"]) == 2


@pytest.mark.asyncio
async def test_rate_limit_pauses_then_probes(tmp_path, key):
    """A 429 on the second unit's explore: nothing for 30 minutes, then the next unit's explore is the probe (design 4.3)."""
    paths, clock = make_paths(tmp_path), ManualClock(START)
    fake = FakeGoogle(fail={4: "http_429"})
    outcome = await runner(paths, key, fake, clock).run(init_state=True)
    results = load_results(paths.run_dir(1))
    assert results["pos-01-d"]["status"] == "rate_limited" and results["pos-01-d"]["timeline"]["lines"] is None
    assert results["seed-01-r"]["status"] == "ok"
    assert (fake.times[4] - fake.times[3]) >= timedelta(minutes=30)
    assert outcome.covered == ("pos-01-h", "pos-01-d", "seed-01-r")
    state = await FileStateStore(paths.state_file, StateCipher([key])).load()
    day = state.section("budget", budget.BudgetDay.from_dict)
    assert day.before_first_limit == 3


@pytest.mark.asyncio
async def test_transient_failure_reruns_the_unit_once(tmp_path, key):
    paths, clock = make_paths(tmp_path), ManualClock(START)
    fake = FakeGoogle(fail={3: "http_503"})
    await runner(paths, key, fake, clock).run(init_state=True)
    results = load_results(paths.run_dir(1))
    assert results["pos-01-h"]["status"] == "ok" and results["pos-01-h"]["attempts"] == 2
    assert fake.paths()[:5] == ["warmup", "explore", "multiline", "explore", "multiline"]
    assert (fake.times[3] - fake.times[2]) >= timedelta(seconds=30)


@pytest.mark.asyncio
async def test_captcha_ends_the_day(tmp_path, key):
    paths, clock = make_paths(tmp_path), ManualClock(START)
    fake = FakeGoogle(fail={2: "redirect_sorry"})
    outcome = await runner(paths, key, fake, clock).run(init_state=True)
    assert len(fake.requests) == 2
    assert outcome.extinguished == "wall"
    assert dict(outcome.uncovered) == {"pos-01-d": "skipped_breaker", "seed-01-r": "skipped_breaker"}
    results = load_results(paths.run_dir(1))
    assert results["pos-01-h"]["status"] == "blocked_redirect" and results["seed-01-r"]["reason"] == "skipped_breaker"


@pytest.mark.asyncio
async def test_cap_truncates_and_a_later_day_resumes(tmp_path, key):
    paths, clock = make_paths(tmp_path), ManualClock(START)
    tight = budget.ModeLimits("stage0", start=time(2, 0), plan=5, cap=5)
    outcome = await runner(paths, key, FakeGoogle(), clock, limits=tight).run(init_state=True)
    assert outcome.covered == ("pos-01-h",) and dict(outcome.uncovered) == {"pos-01-d": "truncated", "seed-01-r": "truncated"}
    clock.advance(24 * 3600)
    later = FakeGoogle()
    resumed = await runner(paths, key, later, clock).run()
    assert resumed.covered == ("pos-01-d", "seed-01-r")
    assert later.paths()[0] == "warmup"  # a new target date warms once more


@pytest.mark.asyncio
async def test_day_two_needs_day_one_on_an_earlier_target_date(tmp_path, key):
    paths, clock = make_paths(tmp_path), ManualClock(START)
    fake = FakeGoogle()
    with pytest.raises(Refused, match="第一天"):
        await runner(paths, key, fake, clock, plan=small_plan(2)).run(init_state=True)
    await runner(paths, key, fake, clock).run(init_state=True)
    sent = len(fake.requests)
    clock.advance(3600)
    with pytest.raises(Refused, match="目标日"):
        await runner(paths, key, fake, clock, plan=small_plan(2)).run()
    assert len(fake.requests) == sent
    clock.advance(24 * 3600)
    outcome = await runner(paths, key, fake, clock, plan=small_plan(2)).run()
    assert outcome.day == 2 and len(outcome.covered) == 3


@pytest.mark.asyncio
async def test_proxy_variables_recorded_by_name_only(tmp_path, key):
    paths, fake, clock = make_paths(tmp_path), FakeGoogle(), ManualClock(START)
    secret = "http://someone:hunter2@proxy.example:3128"
    await runner(paths, key, fake, clock, environ={"HTTPS_PROXY": secret}).run(init_state=True)
    session = json.loads((paths.run_dir(1) / "meta.json").read_text())["sessions"][0]
    assert session["proxy_env"] == {name: name == "HTTPS_PROXY" for name in PROXY_VARIABLES}
    everything = b"".join(path.read_bytes() for path in paths.root.rglob("*") if path.is_file())
    assert b"hunter2" not in everything and NID.encode() not in everything


def test_proxy_environment_names_only():
    assert proxy_environment({"http_proxy": "x", "NO_PROXY": ""}) == {name: name == "http_proxy" for name in PROXY_VARIABLES}


@pytest.mark.asyncio
async def test_deadline_window_refuses_before_sending(tmp_path, key):
    """01:45-02:00 UTC is past every target date's deadline (D23): nothing is sent, every unit is left for later."""
    paths, fake = make_paths(tmp_path), FakeGoogle()
    clock = ManualClock(datetime(2026, 9, 27, 1, 50, tzinfo=UTC))
    outcome = await runner(paths, key, fake, clock).run(init_state=True)
    assert fake.requests == [] and {reason for _, reason in outcome.uncovered} == {"deadline"}


def test_concurrent_runs_cannot_share_the_state(tmp_path, key):
    paths = make_paths(tmp_path)

    async def both():
        clock = ManualClock(START)
        await runner(paths, key, FakeGoogle(), clock).run(init_state=True)
        holder = FileStateStore(paths.state_file, StateCipher([key]))
        await holder.load()
        try:
            with pytest.raises(StateUnavailable):
                await runner(paths, key, FakeGoogle(), clock).run()
        finally:
            holder.close()

    asyncio.run(both())
