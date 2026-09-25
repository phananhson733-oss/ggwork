"""Running one day of stage 0 on this machine (plan TR-05; design 4.2, 4.3, 4.4, 4.11).

The runner does what TR-14's executor will do, on TR-04's file state instead of the runtime row:
- every HTTP request, the warm-up included, waits for TR-03's pacer and breaker, then reserves the budget, and the
  state is saved before it leaves; its outcome goes through signal_of and the breaker, and the state is saved again;
- a unit stopped by the breaker, the budget or the 01:45 deadline leaves every later unit uncovered with the same
  reason (truncation keeps the plan's order); a first 5xx or timeout reruns the unit once, 30-60 s later;
- the target date is D23's: the day whose 02:00 UTC cutoff comes next, so the daily cap is per target date.

What it writes, under the artifacts root (directories 700, files 600; nothing here goes into git):
- runs/day<N>/results.jsonl: one line per unit and attempt, the latest line per unit counts; units skipped without
  sending carry `reason` and run again on a rerun;
- runs/day<N>/requests.jsonl: one line per HTTP request (never a cookie, never a value of a series);
- runs/day<N>/meta.json: one entry per session: target date, times, counts, breaker events, and whether any proxy
  variable was set (names only: this is the plan's local egress note, and no request is made to find out more);
- raw/day<N>/: the raw body of every API answer and an index; the warm-up page is only hashed.
"""

import hashlib
import json
import os
import random
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import TextIO

import httpx

from ggwork_pick.observe.clock import Clock, sleep_in_chunks
from ggwork_pick.observe.errors import Refused
from ggwork_pick.observe.state import RuntimeState, StateStore, open_state
from ggwork_pick.observe.trends import breaker, budget, pacing
from ggwork_pick.observe.trends.client import TrendsClient
from ggwork_pick.observe.trends.cookies import CookieJar
from ggwork_pick.observe.trends.parse import WALLS
from ggwork_pick.observe.trends.source import DEFAULT_USER_AGENT, FetchResult, Phase, RequestRecord, RequestStep
from ggwork_pick.observe.trends.stage0 import MAX_HTTP_PER_DAY, Control, Controls, DayPlan, Unit
from ggwork_pick.observe.versions import COLLECTOR_VERSION

ARTIFACTS_ROOT = Path.home() / ".gstack" / "projects" / "ggwork-deerflow" / "artifacts" / "trends-stage0"
STATE_FILE_NAME = "trends-state.json"
DIR_MODE, FILE_MODE = 0o700, 0o600
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")
# The stage 0 day: any time of the target date before its 01:45 deadline; the cap is the task list's ceiling.
STAGE0_LIMITS = budget.ModeLimits("stage0", start=time(2, 0), plan=MAX_HTTP_PER_DAY, cap=MAX_HTTP_PER_DAY)
STOPS_THE_REST = frozenset({budget.TRUNCATED, budget.SKIPPED_BREAKER, budget.DEADLINE_REASON})


@dataclass(frozen=True)
class Stage0Paths:
    root: Path

    @property
    def controls_file(self) -> Path:
        return self.root / "controls.json"

    @property
    def plan_dir(self) -> Path:
        return self.root / "plan"

    def plan_file(self, day: int) -> Path:
        return self.plan_dir / f"day{day}.json"

    @property
    def state_file(self) -> Path:
        return self.root / "state" / STATE_FILE_NAME

    def run_dir(self, day: int) -> Path:
        return self.root / "runs" / f"day{day}"

    def raw_dir(self, day: int) -> Path:
        return self.root / "raw" / f"day{day}"

    def report_file(self, day: date, *, interim: bool) -> Path:
        return self.root / f"trends-stage0-{'interim-' if interim else ''}report-{day}.md"


# ---- private files -------------------------------------------------------------------------------------------------


def stamp(moment: datetime) -> str:
    """repository.stamp()'s form, the one clock format the extension writes: UTC, always six fractional digits."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def private_dir(path: Path) -> Path:
    path.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    os.chmod(path, DIR_MODE)
    return path


def write_private(path: Path, text: str) -> None:
    """Replace `path` atomically with `text`, mode 600."""
    private_dir(path.parent)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(temp, FILE_MODE)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_private_bytes(path: Path, data: bytes) -> None:
    private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, FILE_MODE)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
    os.chmod(path, FILE_MODE)


def append_private(path: Path, entry: Mapping) -> None:
    private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, FILE_MODE)
    with os.fdopen(fd, "a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.chmod(path, FILE_MODE)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_results(run_dir: Path) -> dict[str, dict]:
    """The latest line of each unit, in the order the units first appear."""
    latest: dict[str, dict] = {}
    for line in read_jsonl(run_dir / "results.jsonl"):
        latest = {**latest, line["unit"]: line}
    return latest


def load_meta(run_dir: Path) -> dict | None:
    path = run_dir / "meta.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def proxy_environment(environ: Mapping[str, str]) -> dict[str, bool]:
    """Whether each proxy variable is set (non-empty). Never its value: it can carry an account."""
    return {name: bool(environ.get(name)) for name in PROXY_VARIABLES}


# ---- the machines --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Machines:
    pacing: pacing.PacingState
    breaker: breaker.BreakerState
    budget: budget.BudgetDay
    jar: CookieJar


def restore_machines(state: RuntimeState, *, now: datetime) -> Machines:
    """The persisted machines rolled over to `now`'s target date: a new date starts its counters, the same one goes on."""
    target = budget.target_date_of(now)
    paced = state.section("pacing", pacing.PacingState.from_dict) or pacing.initial_state()
    broken = state.section("breaker", breaker.BreakerState.from_dict) or breaker.initial_state(target)
    spent = state.section("budget", budget.BudgetDay.from_dict) or budget.BudgetDay(target)
    jar = state.cookie_jar or CookieJar.fresh(DEFAULT_USER_AGENT)
    return Machines(paced, breaker.for_target_date(broken, target, now=now), budget.for_target_date(spent, target), jar)


def state_of(machines: Machines) -> RuntimeState:
    return RuntimeState(
        paused_until=machines.breaker.day.paused_until,
        pacing=machines.pacing.to_dict(),
        breaker=machines.breaker.to_dict(),
        budget=machines.budget.to_dict(),
        cookie_jar=machines.jar,
    )


# ---- result lines --------------------------------------------------------------------------------------------------


def _related_document(result: FetchResult) -> dict | None:
    related = result.related
    if related is None:
        return None

    def items(found):
        return None if found is None else [{"query": q.query, "value": q.value, "formattedValue": q.formatted_value} for q in found]

    return {"status": related.status.value, "widget_missing": related.widget_missing, "top": items(related.top), "rising": items(related.rising)}


def _timeline_document(result: FetchResult) -> dict | None:
    timeline = result.timeline
    if timeline is None:
        return None
    return {"status": timeline.status.value, "lines": [line.as_raw() for line in timeline.lines] if timeline.lines is not None else None}


def request_document(record: RequestRecord) -> dict:
    return {
        "phase": record.phase.value,
        "label": record.label,
        "started_at": stamp(record.started_at),
        "latency_ms": record.latency_ms,
        "http_status": record.http_status,
        "fetch_status": record.fetch_status.value,
        "redirect_kind": record.redirect_kind.value if record.redirect_kind else None,
        "redirect_host": record.redirect_host,
        "user_type": record.user_type,
        "bytes": record.bytes,
        "error_class": record.error_class,
    }


def _base_line(unit: Unit, control: Control, day: int, target: date) -> dict:
    return {
        "unit": unit.key,
        "control": control.id,
        "group": control.group,
        "kind": control.kind,
        "granularity": unit.granularity,
        "term": control.term,
        "geo": control.geo,
        "method": unit.method,
        "repeat_of": unit.repeat_of,
        "day": day,
        "target_date": str(target),
    }


def result_line(unit: Unit, control: Control, *, day: int, target: date, result: FetchResult, attempts: int) -> dict:
    return {
        **_base_line(unit, control, day, target),
        "requested_at": stamp(result.requests[0].started_at) if result.requests else None,
        "attempts": attempts,
        "status": result.status.value,
        "stopped_by": result.stopped_by.value if result.stopped_by else None,
        "reason": None,
        "user_type": result.user_type,
        "timeline": _timeline_document(result),
        "related": _related_document(result),
        "requests": [request_document(record) for record in result.requests],
    }


def skipped_line(unit: Unit, control: Control, *, day: int, target: date, reason: str) -> dict:
    return {**_base_line(unit, control, day, target), "requested_at": None, "attempts": 0, "status": None, "reason": reason, "timeline": None, "related": None}


# ---- the runner ----------------------------------------------------------------------------------------------------


class _Stop(Exception):
    """A unit cannot go on: truncated, skipped_breaker or deadline."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DayOutcome:
    day: int
    target_date: date
    covered: tuple[str, ...]  # units that ran this session (a result is recorded, a failed one included)
    uncovered: tuple[tuple[str, str], ...]  # units left for a rerun, with the reason
    requests: int
    extinguished: str | None
    status_codes: tuple[str, ...]


async def _no_renewal() -> None:
    """Nothing to renew locally: the file lock lasts as long as the process."""


class DayRunner:
    """One session of one stage 0 day. The state store is held (locked) from load to the end of run()."""

    def __init__(
        self,
        *,
        plan: DayPlan,
        controls: Controls,
        store: StateStore,
        paths: Stage0Paths,
        clock: Clock,
        rng: random.Random,
        transport: httpx.AsyncBaseTransport | None = None,
        limits: budget.ModeLimits = STAGE0_LIMITS,
        pacer: pacing.Pacer | None = None,
        environ: Mapping[str, str] | None = None,
        log: TextIO | None = None,
    ):
        self._plan, self._controls, self._store, self._paths = plan, controls, store, paths
        self._clock, self._rng, self._transport, self._limits = clock, rng, transport, limits
        self._pacer = pacer or pacing.EnvelopePacer()
        self._environ = os.environ if environ is None else environ
        self._log = log or sys.stderr
        self._machines: Machines | None = None
        self._client: TrendsClient | None = None
        self._decision: breaker.Decision | None = None
        self._unit_http, self._half, self._sent, self._unit_key = 1, False, 0, None

    async def run(self, *, init_state: bool = False) -> DayOutcome:
        target = budget.target_date_of(self._clock.now())
        self._check_order(target)
        state = await open_state(self._store, init_state=init_state)
        try:
            self._machines = restore_machines(state, now=self._clock.now())
            pending = self._pending()
            started = self._clock.now()
            warm = await self._warm(target) if pending else None
            covered, uncovered = await self._run_units(pending, target)
            await self._save()
            outcome = self._outcome(target, covered, uncovered)
            self._write_meta(target, started, warm, outcome)
            return outcome
        finally:
            close = getattr(self._store, "close", None)
            if close is not None:
                close()

    # ---- order and progress ----------------------------------------------------------------------------------------

    def _check_order(self, target: date) -> None:
        """Day 2 runs after day 1 and on a later target date: two sessions, two days (design 4.11)."""
        if self._plan.day == 1:
            return
        meta = load_meta(self._paths.run_dir(1))
        if not meta or not meta.get("sessions"):
            raise Refused("第二天须在第一天跑过之后运行：先跑 --day 1")
        first = date.fromisoformat(meta["sessions"][0]["target_date"])
        if target <= first:
            raise Refused(f"第二天须在比第一天晚的目标日运行（D23：02:00 UTC 截止的那天）；第一天的目标日是 {first}")

    def _pending(self) -> tuple[Unit, ...]:
        done = {key for key, line in load_results(self._paths.run_dir(self._plan.day)).items() if line.get("status") is not None}
        return tuple(unit for unit in self._plan.units if unit.key not in done)

    async def _run_units(self, pending: Sequence[Unit], target: date) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
        covered, uncovered, stopped = (), (), None
        by_id = self._controls.by_id()
        for unit in pending:
            control = by_id[unit.control]
            line = skipped_line(unit, control, day=self._plan.day, target=target, reason=stopped) if stopped else await self._run_unit(unit, control, target)
            append_private(self._paths.run_dir(self._plan.day) / "results.jsonl", line)
            if line["status"] is None:
                uncovered = (*uncovered, (unit.key, line["reason"]))
                stopped = line["reason"] if line["reason"] in STOPS_THE_REST else stopped
            else:
                covered = (*covered, unit.key)
            print(f"[stage0] day {self._plan.day} {unit.key}: {line['status'] or line['reason']}", file=self._log)
        return covered, uncovered

    async def _run_unit(self, unit: Unit, control: Control, target: date) -> dict:
        for attempt in (1, 2):
            reason = self._stop_reason(need=unit.http)
            if reason is not None:
                return skipped_line(unit, control, day=self._plan.day, target=target, reason=reason)
            self._decision, self._unit_http, self._unit_key = None, unit.http, unit.key
            try:
                result = await self._fetch(unit, control)
            except _Stop as stop:
                return skipped_line(unit, control, day=self._plan.day, target=target, reason=stop.reason)
            if attempt == 1 and self._decision is not None and self._decision.action is breaker.Action.RETRY:
                continue
            return result_line(unit, control, day=self._plan.day, target=target, result=result, attempts=attempt)
        raise AssertionError("unreachable: the second attempt always returns")

    async def _fetch(self, unit: Unit, control: Control) -> FetchResult:
        async with self._new_client(unit.method) as client:
            self._client = client
            try:
                return await client.fetch(control.query(unit.granularity), timeline=unit.timeline, related=unit.related, label=unit.key)
            finally:
                self._machines, self._client = replace(self._machines, jar=client.jar), None

    async def _warm_once(self, target: date):
        self._decision, self._unit_http, self._unit_key = None, 1, "warmup"
        async with self._new_client("GET") as client:
            self._client = client
            try:
                return await client.warm(day=target)
            finally:
                self._machines, self._client = replace(self._machines, jar=client.jar), None

    async def _warm(self, target: date) -> str | None:
        """At most one warm-up per target date; a 5xx or timeout gets the one retry."""
        for attempt in (1, 2):
            try:
                warmed = await self._warm_once(target)
            except _Stop as stop:
                return f"skipped:{stop.reason}"
            if attempt == 1 and self._decision is not None and self._decision.action is breaker.Action.RETRY:
                continue
            return warmed.status.value if warmed.status is not None else "already_warmed"
        return None

    def _new_client(self, method: str) -> TrendsClient:
        return TrendsClient(
            jar=self._machines.jar,
            clock=self._clock,
            gate=self._gate,
            on_request=self._on_request,
            transport=self._transport,
            capture=self._capture,
            explore_method=method,
        )

    # ---- the gate and the outcome of each request ------------------------------------------------------------------

    def _stop_reason(self, *, need: int) -> str | None:
        machines = self._machines
        return budget.stop_reason(breaker_state=machines.breaker, day=machines.budget, limits=self._limits, now=self._clock.now(), need=need)

    async def _gate(self, step: RequestStep) -> None:
        """Wait for the pacer and the breaker, then reserve the request and save, all before it is sent."""
        first = step.phase in (Phase.WARMUP, Phase.EXPLORE)
        while True:
            reason = self._stop_reason(need=1)
            if reason is not None:
                raise _Stop(reason)
            machines, now = self._machines, self._clock.now()
            paced = self._pacer.ready_at(
                machines.pacing, now=now, first_in_unit=first, half_speed=machines.breaker.day.half_speed, unit_requests=self._unit_http
            )
            ready = max(paced, breaker.ready_at(machines.breaker, now=now))
            if ready <= now:
                break
            await sleep_in_chunks(self._clock, (ready - now).total_seconds(), on_wake=_no_renewal)
        try:
            spent = budget.reserve(self._machines.budget, self._limits)
        except budget.BudgetExhausted:
            raise _Stop(budget.TRUNCATED) from None
        self._half = self._machines.breaker.day.half_speed
        self._machines = replace(self._machines, budget=spent)
        await self._save()

    async def _on_request(self, record: RequestRecord) -> None:
        now, machines = self._clock.now(), self._machines
        paced = self._pacer.record(machines.pacing, sent_at=record.started_at, done_at=now, rng=self._rng, half_speed=self._half)
        signal = breaker.signal_of(record.fetch_status.value, captcha_or_consent=record.redirect_kind in WALLS)
        broken, decision = breaker.observe(machines.breaker, signal, now=now, rng=self._rng)
        spent = machines.budget
        if decision.action in (breaker.Action.PAUSE, breaker.Action.EXTINGUISH):
            spent = budget.note_limit(spent, ordinal=spent.reserved, at=record.started_at)
        self._machines, self._decision, self._sent = replace(machines, pacing=paced, breaker=broken, budget=spent), decision, self._sent + 1
        append_private(self._paths.run_dir(self._plan.day) / "requests.jsonl", {**request_document(record), "decision": decision.action.value})
        await self._save()

    async def _capture(self, record: RequestRecord, body: bytes) -> None:
        """The raw answer for the report and the fixtures: API bodies kept whole, the warm-up page only hashed."""
        raw_dir = private_dir(self._paths.raw_dir(self._plan.day))
        index = raw_dir / "index.jsonl"
        seq = len(read_jsonl(index)) + 1
        body_file = None if record.phase is Phase.WARMUP else f"{seq:04d}-{record.phase.value}.body"
        if body_file is not None:
            write_private_bytes(raw_dir / body_file, body)
        entry = {"seq": seq, "unit": self._unit_key, **request_document(record), "sha256": hashlib.sha256(body).hexdigest(), "body_file": body_file}
        append_private(index, entry)

    async def _save(self) -> None:
        machines = self._machines if self._client is None else replace(self._machines, jar=self._client.jar)
        await self._store.save(state_of(machines))

    # ---- the session record ----------------------------------------------------------------------------------------

    def _outcome(self, target: date, covered: tuple[str, ...], uncovered: tuple[tuple[str, str], ...]) -> DayOutcome:
        broken = self._machines.breaker
        return DayOutcome(self._plan.day, target, covered, uncovered, self._sent, broken.day.extinguished, breaker.status_codes(broken))

    def _write_meta(self, target: date, started: datetime, warm: str | None, outcome: DayOutcome) -> None:
        day, broken = self._machines.budget, self._machines.breaker.day
        session = {
            "target_date": str(target),
            "started_at": stamp(started),
            "finished_at": stamp(self._clock.now()),
            "proxy_env": proxy_environment(self._environ),
            "explore_methods": sorted({unit.method for unit in self._plan.units}),
            "warmup": warm,
            "requests": outcome.requests,
            "covered": len(outcome.covered),
            "uncovered": [list(pair) for pair in outcome.uncovered],
            "budget": {
                "reserved": day.reserved,
                "cap": self._limits.cap,
                "first_limit_at": stamp(day.first_limit_at) if day.first_limit_at else None,
                "before_first_limit": day.before_first_limit,
            },
            "breaker": {
                "trips": broken.trips,
                "pauses": broken.pauses,
                "rate_limited": broken.rate_limited,
                "half_speed": broken.half_speed,
                "extinguished": broken.extinguished,
            },
            "status_codes": list(outcome.status_codes),
        }
        meta = load_meta(self._paths.run_dir(self._plan.day)) or {
            "day": self._plan.day,
            "plan_http": self._plan.http,
            "collector_version": COLLECTOR_VERSION,
            "egress": "off (D20)",
            "sessions": [],
        }
        write_private(
            self._paths.run_dir(self._plan.day) / "meta.json", json.dumps({**meta, "sessions": [*meta["sessions"], session]}, ensure_ascii=False, indent=1)
        )


def dry_run_lines(plan: DayPlan, controls: Controls, done: Callable[[str], bool]) -> list[str]:
    """What `run --dry-run` prints: each unit still to run, its HTTP count and its geo; no request is made."""
    by_id = controls.by_id()
    rows = [f"{unit.key}\t{unit.http}\t{by_id[unit.control].geo}\t{unit.method}" for unit in plan.units if not done(unit.key)]
    return [f"第 {plan.day} 天：待跑 {len(rows)} 个单元，至多 {plan.warmups + sum(u.http for u in plan.units if not done(u.key))} 次请求", *rows]
