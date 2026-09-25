"""The lease on a channel's runtime row, the one way a collector writes, and the Trends state in the database
(plan TR-13, D2, D17, D18, D23, D34; design 3.3, 4.3, 4.4; critique A-7; counterexample 10).

The lease. ggwp_obs_runtime holds one row per channel (0007 seeds both, D34) with lease_owner, lease_until and
lease_generation. Taking the lease locks that row (FOR UPDATE; BEGIN IMMEDIATE on SQLite), refuses while another
holder's lease still runs, and otherwise takes it for five minutes one generation up: a takeover and a fresh start are
both a new generation. A holder renews every 60 seconds (ensure_fresh before every request, and on every wake of a
breaker pause through clock.sleep_in_chunks), so a live process never loses it. Nothing else guards the channel: not
the mirror's lock, not ggwp_sync_runs (D2).

Every collector write happens in a step: LeasedWriter.step() opens a transaction, locks the runtime row, checks that
owner, generation and lease_until still hold, and only then hands out the LeasedStep to write through; all of it commits
together or not at all. That covers design 3.3's three boundaries (reserving request budget, writing a response,
publishing a set) and everything else a collector writes. A takeover locks the same row, so it waits for a step in
flight and the old process can never commit after it; a renewal in flight likewise, so the takeover reads the renewed
lease_until. The check compares the generation as well as the owner: an owner name that repeats across runs (a fixed
replica id) still cannot let the old run write, renew or release after the new one took over. A failed check is
LeaseLost (exit 1): nothing of that step is written, and the writer refuses every later step without asking the
database. LeasedWriter is the collectors' only way to the database (test_write_paths lists what a collector may take
from this module); reading() is a read-only transaction. A heavy step passes step(limits=StepLimits(...)); every other
step keeps db.STEP_LIMITS. Times are the injected Clock's (D10) and lease_until is compared in Python, never in SQL.

Start-up order (collector_session): the self-check (exit 2 before anything else, D5), then the lease (exit 3 when the
runtime row is gone or unreadable, D34; exit 1 while another process holds it, or holds the row past the lock wait),
then the state, under the lease, so the state read is the state this process goes on to write. status_reader() reads
without the lease, as ggwp-obs-admin, so counting a channel's connections still counts its collector alone.

DbStateStore is TR-04's StateStore on the Trends runtime row (D17). The pacing and breaker sections go into state_json;
paused_until and breaker_level are their plain copies for status; the cookie jar is sealed (TR-04's seal_jar, D18)
into the Text column cookie_jar beside its user agent. The budget section is the budget row of its target date (D23):
the 22:30 and 00:10 halves of a session share one row, whose request count only ever grows (reserved before sending,
never given back). disabled_7d is cleared by the reset-disable command alone: a state that drops it is refused.
cookie_warmed_at is a stamp like every _at column: the step that first kept the jar's current warm-up (a new warmed_on),
kept as it is by every later save of the same warm-up.
"""

import logging
import os
import socket
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.exc import DBAPIError

from ggwork_pick.models import obs_budget, obs_runtime
from ggwork_pick.observe.clock import Clock
from ggwork_pick.observe.crypto import StateCipher
from ggwork_pick.observe.db import ADMIN_APPLICATION_NAME, APPLICATION_NAMES, ObsDatabase, StepLimits, database_url, open_database
from ggwork_pick.observe.errors import ExitCode, ObserveFailure, StateUnavailable, describe_error
from ggwork_pick.observe.instants import stamp
from ggwork_pick.observe.selfcheck import SelfCheckReport, expectations_from, run_selfcheck
from ggwork_pick.observe.state import RuntimeState, thaw_json
from ggwork_pick.observe.trends import breaker, budget, pacing
from ggwork_pick.observe.trends import state_codec as codec
from ggwork_pick.observe.trends.cookies import CookieJar, open_jar, seal_jar

logger = logging.getLogger(__name__)

LEASE_SECONDS = 300  # design 3.3
RENEW_SECONDS = 60
LEASE = timedelta(seconds=LEASE_SECONDS)
MAX_OWNER = 128  # the lease_owner column
CHANNELS = tuple(APPLICATION_NAMES)
TRENDS = "trends"
DATABASE_FAILURES = (DBAPIError, OSError, TimeoutError)
STATE_JSON_FORMAT = 1
_STATE_JSON_KEYS = frozenset({"format", "pacing", "breaker"})


class LeaseLost(ObserveFailure):
    """The lease is no longer this process's (taken over, or run out): nothing more is written; the next trigger
    resumes from the persisted state."""

    exit_code = ExitCode.FAILED


class LeaseHeld(ObserveFailure):
    """Another process's lease on the channel still runs: this run does nothing; the next trigger tries again."""

    exit_code = ExitCode.FAILED


def new_owner() -> str:
    """This process's name on the lease: host, pid and a random part, so a reused pid is still someone else."""
    host = "".join(char for char in socket.gethostname() if char.isascii() and char.isprintable())[:64] or "host"
    return f"{host}:{os.getpid()}:{uuid4().hex[:12]}"


def _checked_owner(owner: str) -> str:
    if not isinstance(owner, str) or not 0 < len(owner) <= MAX_OWNER or not owner.isprintable():
        raise ValueError(f"租约持有者须是 1–{MAX_OWNER} 个可打印字符")
    return owner


def runtime_update(channel: str):
    return update(obs_runtime).where(obs_runtime.c.channel == channel)


async def locked_runtime(conn, channel: str) -> Mapping[str, object]:
    """The channel's runtime row, locked until the transaction ends; StateUnavailable when it is gone (D34)."""
    statement = select(obs_runtime).where(obs_runtime.c.channel == channel).with_for_update()
    row = (await conn.execute(statement)).mappings().first()
    if row is None:
        raise StateUnavailable(f"ggwp_obs_runtime 没有 {channel} 这一行：不当作新开始，当天不跑（D34）")
    return MappingProxyType(dict(row))


def _lease_until(row: Mapping[str, object]) -> datetime | None:
    try:
        return codec.decode_instant(row["lease_until"], "lease_until")
    except ValueError:
        raise StateUnavailable("运行时行的 lease_until 读不回来：不知道租约还在不在，当天不跑") from None


def lease_live(row: Mapping[str, object], now: datetime) -> bool:
    """Whether someone's lease on the row still runs at `now`."""
    until = _lease_until(row)
    return row["lease_owner"] is not None and until is not None and until > now


@dataclass(frozen=True)
class LeaseToken:
    channel: str
    owner: str
    generation: int
    until: datetime

    def held_in(self, row: Mapping[str, object], now: datetime) -> bool:
        until = _lease_until(row)
        return row["lease_owner"] == self.owner and row["lease_generation"] == self.generation and until is not None and until > now


class ReadStep:
    """A transaction's connection, handed out for its block only: once the block ends, execute() refuses."""

    __slots__ = ("_conn", "_open")

    def __init__(self, conn):
        self._conn = conn
        self._open = True

    @property
    def dialect(self) -> str:
        return self._conn.dialect.name

    async def execute(self, statement, parameters=None):
        if not self._open:
            raise RuntimeError("这一步已经结束：连接已关，不能再用它读写")
        return await self._conn.execute(statement, parameters)

    def _close(self) -> None:
        self._open = False


class LeasedStep(ReadStep):
    """A leased transaction: the lease was checked under the runtime row's lock at `now`; what is executed here commits
    together when the block ends, or not at all. token.generation is what rows recording it (lease_generation) take;
    runtime is the locked runtime row as the step found it."""

    __slots__ = ("token", "now", "runtime")

    def __init__(self, conn, token: LeaseToken, now: datetime, runtime: Mapping[str, object]):
        super().__init__(conn)
        self.token = token
        self.now = now
        self.runtime = runtime


@asynccontextmanager
async def _handed_out(step: ReadStep) -> AsyncIterator[ReadStep]:
    try:
        yield step
    finally:
        step._close()


class LeasedWriter:
    """A channel's lease, held by this process, and the only way its collector reaches the database (design 3.3).

    Take it with acquire(); `async with` releases it at the end."""

    def __init__(self, db: ObsDatabase, token: LeaseToken, clock: Clock):
        self._db = db
        self._token = token
        self._clock = clock
        self._renewed_at = clock.monotonic()
        self._lost = False
        self._released = False

    @classmethod
    async def acquire(cls, db: ObsDatabase, channel: str, *, clock: Clock, owner: str | None = None) -> "LeasedWriter":
        """Take the channel's lease: LeaseHeld while another holder's runs; StateUnavailable (exit 3) when the runtime
        row is gone or cannot be read or written."""
        if channel not in CHANNELS:
            raise ValueError(f"没有 {channel} 这个通道；通道是 {', '.join(CHANNELS)}")
        holder = new_owner() if owner is None else _checked_owner(owner)
        try:
            async with db.transaction() as conn:
                row = await locked_runtime(conn, channel)
                now = clock.now()
                if lease_live(row, now):
                    raise LeaseHeld(f"{channel} 的租约在另一个进程手里，还没到期：本次不跑，下一次触发再试")
                token = LeaseToken(channel, holder, row["lease_generation"] + 1, now + LEASE)
                values = {"lease_owner": holder, "lease_until": stamp(token.until), "lease_generation": token.generation, "updated_at": stamp(now)}
                await conn.execute(runtime_update(channel).values(**values))
        except DATABASE_FAILURES as exc:
            raise StateUnavailable(f"{channel} 的运行时行读写不了，取不到租约，当天不跑") from exc
        logger.info("[pick-obs] %s lease taken, generation %d", channel, token.generation)
        return cls(db, token, clock)

    @property
    def token(self) -> LeaseToken:
        return self._token

    @property
    def channel(self) -> str:
        return self._token.channel

    @property
    def generation(self) -> int:
        return self._token.generation

    @property
    def lost(self) -> bool:
        return self._lost

    def __repr__(self) -> str:
        return f"LeasedWriter(channel={self.channel!r}, generation={self.generation}, lost={self._lost})"

    def _require_held(self) -> None:
        if self._released:
            raise RuntimeError("租约已释放，不能再用这个写入口")
        if self._lost:
            raise LeaseLost(f"{self.channel} 的租约早已不在本进程：本进程停止")

    def _check(self, row: Mapping[str, object], now: datetime) -> None:
        if not self._token.held_in(row, now):
            self._lost = True
            raise LeaseLost(f"{self.channel} 的租约已不在本进程（被接管，或已过期）：这一步什么都没写，本进程停止")

    @asynccontextmanager
    async def step(self, *, limits: StepLimits | None = None) -> AsyncIterator[LeasedStep]:
        """One leased transaction: the runtime row locked and the lease checked before anything is handed out. `limits`
        relaxes this step's waits (a large prune, the link materialization); the others keep the defaults."""
        self._require_held()
        async with self._db.transaction(limits=limits) as conn:
            row = await locked_runtime(conn, self.channel)
            now = self._clock.now()
            self._check(row, now)
            async with _handed_out(LeasedStep(conn, self._token, now, row)) as step:
                yield step

    @asynccontextmanager
    async def reading(self) -> AsyncIterator[ReadStep]:
        """A read-only transaction; reads need no lease."""
        async with self._db.transaction(read_only=True) as conn:
            async with _handed_out(ReadStep(conn)) as reader:
                yield reader

    async def renew(self) -> None:
        self._require_held()
        async with self._db.transaction() as conn:
            row = await locked_runtime(conn, self.channel)
            now = self._clock.now()
            self._check(row, now)
            until = now + LEASE
            await conn.execute(runtime_update(self.channel).values(lease_until=stamp(until), updated_at=stamp(now)))
        self._token = replace(self._token, until=until)
        self._renewed_at = self._clock.monotonic()

    async def ensure_fresh(self) -> None:
        """Before every request and on every wake of a pause: renew once 60 seconds have passed, else nothing."""
        self._require_held()
        if self._clock.monotonic() - self._renewed_at >= RENEW_SECONDS:
            await self.renew()

    async def release(self) -> None:
        """Let the lease go, so the next run need not wait for it to run out: no owner, lease_until = now (whatever
        another process's clock says, an ownerless lease is free). A lease no longer this process's is left alone."""
        if self._released:
            return
        self._released = True
        if self._lost:
            return
        async with self._db.transaction() as conn:
            row = await locked_runtime(conn, self.channel)
            now = self._clock.now()
            if self._token.held_in(row, now):
                await conn.execute(runtime_update(self.channel).values(lease_owner=None, lease_until=stamp(now), updated_at=stamp(now)))

    async def __aenter__(self) -> "LeasedWriter":
        return self

    async def __aexit__(self, *exc_info) -> None:
        try:
            await self.release()
        except Exception as exc:  # the lease runs out by itself; the run's own outcome stands. A cancellation still rises.
            logger.warning("[pick-obs] %s lease not released (it runs out within %d s): %s", self.channel, LEASE_SECONDS, describe_error(exc))


@dataclass(frozen=True)
class CollectorSession:
    report: SelfCheckReport
    writer: LeasedWriter


@asynccontextmanager
async def collector_session(
    channel: str, *, clock: Clock, environ: Mapping[str, str] | None = None, owner: str | None = None
) -> AsyncIterator[CollectorSession]:
    """How every collector starts, before its first HTTP request: the self-check, then the lease. At the end the lease
    is released and the connection closed."""
    expected = expectations_from(environ)
    db = open_database(channel, environ)
    try:
        report = await run_selfcheck(db, expected)
        async with await LeasedWriter.acquire(db, channel, clock=clock, owner=owner) as writer:
            yield CollectorSession(report, writer)
    finally:
        await db.dispose()


@asynccontextmanager
async def status_reader(channel: str, *, environ: Mapping[str, str] | None = None) -> AsyncIterator[ReadStep]:
    """A read-only look at the channel's rows without taking the lease (a `status` command, TR-14): one read-only
    transaction, nothing written, the running collector undisturbed. It connects as ggwp-obs-admin, never under the
    collector's application_name, so pg_stat_activity still shows each channel's collector alone."""
    if channel not in CHANNELS:
        raise ValueError(f"没有 {channel} 这个通道；通道是 {', '.join(CHANNELS)}")
    db = ObsDatabase(database_url(environ), application_name=ADMIN_APPLICATION_NAME)
    try:
        async with db.transaction(read_only=True) as conn:
            async with _handed_out(ReadStep(conn)) as reader:
                yield reader
    finally:
        await db.dispose()


# ---- the Trends state on the runtime row (D17, D18, D23) -------------------------------------------------------------


def state_document(pacing_section: Mapping | None, breaker_section: Mapping | None) -> dict:
    """state_json: the pacing and breaker machines' sections, versioned."""
    return {"format": STATE_JSON_FORMAT, "pacing": thaw_json(pacing_section), "breaker": thaw_json(breaker_section)}


def state_sections(document: object) -> dict[str, object]:
    """state_json read back: None (never written) holds no sections; any other shape or version is a ValueError."""
    if document is None:
        return {"pacing": None, "breaker": None}
    if not isinstance(document, Mapping) or set(document) != _STATE_JSON_KEYS:
        raise ValueError("state_json 的字段不对")
    if type(document["format"]) is not int or document["format"] != STATE_JSON_FORMAT:
        raise ValueError("state_json 的格式版本不认识")
    return {"pacing": document["pacing"], "breaker": document["breaker"]}


def stored_breaker(runtime: Mapping[str, object]) -> breaker.BreakerState | None:
    """The breaker the runtime row holds; ValueError when it cannot be read back."""
    section = state_sections(runtime["state_json"])["breaker"]
    return None if section is None else breaker.BreakerState.from_dict(section)


def _jar_of(row: Mapping[str, object], cipher: StateCipher) -> CookieJar | None:
    token, agent = row["cookie_jar"], row["user_agent"]
    if token is None and agent is None:
        return None
    if token is None or agent is None:
        raise StateUnavailable("运行时行的 cookie 罐与 UA 只存了一半：罐与 UA 必须成对（D18）")
    return open_jar(cipher, token, user_agent=agent)


def _budget_section(row: Mapping[str, object] | None) -> dict | None:
    if row is None:
        return None
    stored = {
        "target_date": row["budget_day"],
        "reserved": row["requests"],
        "first_limit_at": row["first_limited_at"],
        "before_first_limit": row["requests_before_first_limit"],
    }
    return budget.BudgetDay.from_dict(stored).to_dict()


def _state_of(row: Mapping[str, object], budget_row: Mapping[str, object] | None, cipher: StateCipher) -> RuntimeState:
    try:
        sections = state_sections(row["state_json"])
        state = RuntimeState(
            paused_until=codec.decode_instant(row["paused_until"], "paused_until"),
            pacing=sections["pacing"],
            breaker=sections["breaker"],
            budget=_budget_section(budget_row),
            cookie_jar=_jar_of(row, cipher),
        )
    except (ValueError, TypeError):
        raise StateUnavailable("运行时行或预算行里的状态读不回来：内容不合格式（已损坏，或由另一个版本写成）") from None
    # Each machine reads its section back now, before any HTTP, not half-way through the night.
    state.section("pacing", pacing.PacingState.from_dict)
    state.section("breaker", breaker.BreakerState.from_dict)
    return state


def _refuse_cleared_disable(runtime: Mapping[str, object], machine: breaker.BreakerState | None) -> None:
    try:
        before = stored_breaker(runtime)
    except (ValueError, TypeError):
        raise StateUnavailable("运行时行里原有的熔断状态读不回来，不覆盖它") from None
    was_disabled = runtime["disabled_at"] is not None or (before is not None and before.disabled_on is not None)
    if was_disabled and (machine is None or machine.disabled_on is None):
        raise StateUnavailable("disabled 只能由 reset-disable 清除：要保存的状态里它没了（状态已过时），本进程停止")


def _warmed_at(step: LeasedStep, jar: CookieJar | None, cipher: StateCipher) -> str | None:
    """When the jar's warm-up was first kept: the stamp already on the row while the row's jar is this one (same user
    agent) warmed for the same target date, else this step's. A row jar that cannot be read is not overwritten."""
    if jar is None or jar.warmed_on is None:
        return None
    kept = step.runtime["cookie_warmed_at"]
    before = _jar_of(step.runtime, cipher)
    same = before is not None and (before.user_agent, before.warmed_on) == (jar.user_agent, jar.warmed_on)
    return kept if kept is not None and same else stamp(step.now)


def _runtime_values(step: LeasedStep, state: RuntimeState, machine: breaker.BreakerState | None, cipher: StateCipher) -> dict:
    jar = state.cookie_jar
    disabled = machine is not None and machine.disabled_on is not None
    return {
        "breaker_level": machine.day.pauses if machine is not None else 0,
        "paused_until": codec.encode_instant(state.paused_until),
        "disabled_at": (step.runtime["disabled_at"] or stamp(step.now)) if disabled else None,
        "user_agent": jar.user_agent if jar is not None else None,
        "cookie_jar": seal_jar(cipher, jar) if jar is not None else None,
        "cookie_warmed_at": _warmed_at(step, jar, cipher),
        "state_json": state_document(state.pacing, state.breaker),
        "updated_at": stamp(step.now),
    }


def _breaker_counts(day: breaker.BreakerDay, existing, now: datetime) -> dict:
    """The day's breaker figures on its budget row, for the canary summary in SQL (plan section 9)."""
    first_out = existing.extinguished_at if existing is not None else None
    return {
        "breaker_trips": day.trips,
        "http_429": day.rate_limited,
        "probe_failures": day.probe_failures,  # failed probes in a row, as the breaker counts them
        "extinguish_reason": day.extinguished,
        "extinguished_at": (first_out or stamp(now)) if day.extinguished is not None else None,
    }


async def _write_budget(step: LeasedStep, day: budget.BudgetDay, machine: breaker.BreakerState | None, limits: budget.ModeLimits | None) -> None:
    key = codec.encode_day(day.target_date)
    where = (obs_budget.c.channel == TRENDS) & (obs_budget.c.budget_day == key)
    existing = (await step.execute(select(obs_budget.c.requests, obs_budget.c.extinguished_at).where(where))).first()
    if existing is not None and day.reserved < existing.requests:
        raise StateUnavailable("预算只增不减：要写的已预留次数少于库里这个目标日的记录（状态已过时），本进程停止")
    values = {
        "requests": day.reserved,
        "requests_before_first_limit": day.before_first_limit,
        "first_limited_at": codec.encode_instant(day.first_limit_at),
        "updated_at": stamp(step.now),
    }
    if limits is not None:
        values |= {"collect_mode": limits.name, "cap": limits.cap}
    if machine is not None and machine.day.target_date == day.target_date:
        values |= _breaker_counts(machine.day, existing, step.now)
    if existing is None:
        await step.execute(insert(obs_budget).values(channel=TRENDS, budget_day=key, **values))
    else:
        await step.execute(update(obs_budget).where(where).values(**values))


async def _latest_budget(step: LeasedStep) -> Mapping[str, object] | None:
    statement = select(obs_budget).where(obs_budget.c.channel == TRENDS).order_by(obs_budget.c.budget_day.desc()).limit(1)
    return (await step.execute(statement)).mappings().first()


class DbStateStore:
    """TR-04's StateStore for the canary and production (D17): the Trends runtime row and its budget rows, read and
    written in leased steps. `limits`, when given, is recorded on the budget row (collect_mode and cap)."""

    def __init__(self, writer: LeasedWriter, cipher: StateCipher, *, limits: budget.ModeLimits | None = None):
        if writer.channel != TRENDS:
            raise ValueError("DbStateStore 只存 trends 通道的状态")
        self._writer = writer
        self._cipher = cipher
        self._limits = limits

    def __repr__(self) -> str:
        return f"DbStateStore(channel={TRENDS!r}, cipher={self._cipher!r}, limits={self._limits.name if self._limits else None!r})"

    async def load(self) -> RuntimeState:
        """The persisted state, the latest target date's budget included; StateUnavailable (exit 3) when any of it
        cannot be read. Never a fresh state in its place; the seeded, never written row is the first run's state."""
        try:
            async with self._writer.step() as step:
                return _state_of(step.runtime, await _latest_budget(step), self._cipher)
        except DATABASE_FAILURES as exc:
            raise StateUnavailable("状态读不了：运行时行或预算行查询失败，当天不跑") from exc

    async def save(self, state: RuntimeState) -> None:
        async with self._writer.step() as step:
            await self.write(step, state)

    async def write(self, step: LeasedStep, state: RuntimeState) -> None:
        """`state` written inside the caller's step, so it commits together with that step's other rows."""
        if step.token.channel != TRENDS:
            raise ValueError("这一步不是 trends 通道的租约")
        machine = state.section("breaker", breaker.BreakerState.from_dict)
        day = state.section("budget", budget.BudgetDay.from_dict)
        _refuse_cleared_disable(step.runtime, machine)
        await step.execute(runtime_update(TRENDS).values(**_runtime_values(step, state, machine, self._cipher)))
        if day is not None:
            await _write_budget(step, day, machine, self._limits)
