"""The Trends breaker and retry policy (design 4.2 "重试", 4.3; plan TR-03, D23).

A pure state machine. The executor turns each HTTP outcome into a Signal (signal_of maps TR-02's ten fetch statuses
and its captcha_or_consent flag) and calls observe(), which returns the next state and a Decision:

- CONTINUE: go on with the unit.
- RETRY: a first 5xx or timeout; send the same request again at resume_at (30-60 s later). Two in a row, with nothing
  else in between, are handled as a limit signal. A 429 or any other limit signal is never retried.
- PAUSE: a limit signal (429, 403, an HTML body, two transients in a row) or a failed probe. The rest of the unit is
  abandoned (skipped_breaker); nothing goes out until resume_at, then exactly one probe. A good probe resumes at half
  speed for the rest of the target date; a failed one pauses again. A probe gets one try: anything but a usable answer,
  a 5xx, timeout or unparsable body included, is a failed probe, never a retry.
- EXTINGUISH: the target date is over; every unit left is skipped_breaker.

Pauses within a target date climb one rung each, never back down: 30, 60, 120, 240, 240... minutes (the persisted
"熔断级别" is the count of pauses taken). Failed probes alone never reach 240, since the third ends the day; a new trip
after a good probe carries on up the ladder (design 4.3 leaves this open; see test_probe_ladder).

A day is put out by any one of: three failed probes in a row, a third trip, a fifth 429, or a single captcha, consent or
sorry wall (a redirect to those pages; one is enough). Any other redirect is a limit signal like a 403: a pause, never
the day. Everything counts per target date, the day of the 02:00 UTC publication the session feeds (D23), so crossing
midnight changes nothing; a new target date starts every counter afresh and keeps only the history.

Across days: two extinguished target dates in a row halve the next day's cap (budget.day_limits); a third extinguished
day within seven target dates disables direct access (status code disabled_7d) until an operator clears it
(clear_disabled, through TR-13's reset-disable command). A cleared history is kept, so one more extinguished day inside
the window disables it again.
"""

import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, get_args

from ggwork_pick.observe.trends import state_codec as codec


class Signal(StrEnum):
    SUCCESS = "success"  # a usable answer, empty or all-zero included
    TRANSIENT = "transient"  # 5xx or a timeout: retried once
    RATE_LIMITED = "rate_limited"  # HTTP 429
    LIMITED = "limited"  # 403, or HTML where JSON was expected
    WALL = "wall"  # a captcha, consent or sorry page: the day is over at once
    NEUTRAL = "neutral"  # 200 but unparsable: no retry and no trip, and never a good probe


# TR-02's fetch statuses (design 4.10). A status missing here is refused rather than guessed. A wall is not a status:
# TR-02 files every redirect on an API path as blocked_redirect and reports beside it whether the redirect went to the
# sorry or consent page (captcha_or_consent), which is what signal_of turns into WALL.
FETCH_STATUS_SIGNALS = MappingProxyType(
    {
        "ok": Signal.SUCCESS,
        "ok_zero": Signal.SUCCESS,
        "no_data": Signal.SUCCESS,
        "rate_limited": Signal.RATE_LIMITED,
        "forbidden": Signal.LIMITED,
        "html_body": Signal.LIMITED,
        "blocked_redirect": Signal.LIMITED,  # any other redirect: same host, accounts.google.com, ...
        "server_error": Signal.TRANSIENT,
        "timeout": Signal.TRANSIENT,
        "parse_error": Signal.NEUTRAL,
    }
)

PAUSE_LADDER_MINUTES = (30, 60, 120, 240)
MAX_PROBE_FAILURES = 3  # in a row
MAX_TRIPS_PER_DAY = 3
MAX_RATE_LIMITED_PER_DAY = 5
RETRY_DELAY_SECONDS = (30.0, 60.0)
HALVING_STREAK_DAYS = 2  # this many extinguished target dates in a row halve the next cap
DISABLE_WINDOW_DAYS = 7
DISABLE_AFTER = 3  # extinguished target dates within the window

ExtinguishReason = Literal["wall", "rate_limited", "trips", "probe_failures"]
EXTINGUISH_REASONS = get_args(ExtinguishReason)

EXTINGUISHED_TODAY = "extinguished_today"
DISABLED_7D = "disabled_7d"
STATUS_CODES = (EXTINGUISHED_TODAY, DISABLED_7D)  # contract.STATUS_CODES members, in its order


def signal_of(fetch_status: str, *, captcha_or_consent: bool) -> Signal:
    """The signal of one HTTP request: TR-02's RequestRecord gives its fetch_status, and captcha_or_consent is whether
    its redirect_kind is the sorry or consent page. The flag has no default, so no call site can leave it out (as D42
    does for check_answer). A captcha seen ends the day whatever status came with it."""
    if type(captcha_or_consent) is not bool:
        raise ValueError("captcha_or_consent must be a bool")
    try:
        signal = FETCH_STATUS_SIGNALS[fetch_status]
    except KeyError:
        raise ValueError(f"unknown fetch status; expected one of {sorted(FETCH_STATUS_SIGNALS)}") from None
    return Signal.WALL if captcha_or_consent else signal


class Action(StrEnum):
    CONTINUE = "continue"
    RETRY = "retry"
    PAUSE = "pause"
    EXTINGUISH = "extinguish"


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action
    resume_at: datetime | None = None  # RETRY: when to resend; PAUSE: when the probe may go

    @property
    def abandon_unit(self) -> bool:
        """The rest of the current unit is not sent: it is skipped_breaker."""
        return self.action in (Action.PAUSE, Action.EXTINGUISH)


_COUNT_FIELDS = ("trips", "pauses", "probe_failures", "rate_limited", "transient_streak")
_DAY_KEYS = frozenset({"target_date", *_COUNT_FIELDS, "paused_until", "probe_due", "retry_at", "half_speed", "extinguished"})
_STATE_KEYS = frozenset({"day", "extinguished_days", "disabled_on"})


@dataclass(frozen=True, slots=True)
class BreakerDay:
    """One target date's counters."""

    target_date: date
    trips: int = 0  # limit signals outside a probe; the set summary's breaker_events
    pauses: int = 0  # pauses taken: the next one lasts PAUSE_LADDER_MINUTES[min(pauses, 3)]
    probe_failures: int = 0  # failed probes in a row
    rate_limited: int = 0  # 429s, probes included
    transient_streak: int = 0  # 5xx or timeouts in a row outside a probe
    paused_until: datetime | None = None
    probe_due: bool = False  # the next request is the probe
    retry_at: datetime | None = None
    half_speed: bool = False
    extinguished: ExtinguishReason | None = None

    def __post_init__(self):
        codec.aware_or_none((self.paused_until, self.retry_at), "breaker day times")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_date": codec.encode_day(self.target_date),
            **{name: getattr(self, name) for name in _COUNT_FIELDS},
            "paused_until": codec.encode_instant(self.paused_until),
            "probe_due": self.probe_due,
            "retry_at": codec.encode_instant(self.retry_at),
            "half_speed": self.half_speed,
            "extinguished": self.extinguished,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "BreakerDay":
        data = codec.exact_keys(data, _DAY_KEYS, "breaker day")
        target = codec.decode_day(data["target_date"], "target_date")
        if target is None:
            raise ValueError("breaker day needs its target_date")
        counts = {name: codec.count(data[name], name) for name in _COUNT_FIELDS}
        return cls(
            target_date=target,
            **counts,
            paused_until=codec.decode_instant(data["paused_until"], "paused_until"),
            probe_due=codec.flag(data["probe_due"], "probe_due"),
            retry_at=codec.decode_instant(data["retry_at"], "retry_at"),
            half_speed=codec.flag(data["half_speed"], "half_speed"),
            extinguished=codec.choice(data["extinguished"], EXTINGUISH_REASONS, "extinguished"),
        )


@dataclass(frozen=True, slots=True)
class BreakerState:
    day: BreakerDay
    extinguished_days: tuple[date, ...] = ()  # within the last DISABLE_WINDOW_DAYS target dates, ascending
    disabled_on: date | None = None  # set by the extinguished day that made it three; cleared only by an operator

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.to_dict(),
            "extinguished_days": [codec.encode_day(day) for day in self.extinguished_days],
            "disabled_on": codec.encode_day(self.disabled_on),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "BreakerState":
        data = codec.exact_keys(data, _STATE_KEYS, "breaker state")
        days = data["extinguished_days"]
        if not isinstance(days, list | tuple) or any(day is None for day in days):
            raise ValueError("extinguished_days must be a list of dates")
        decoded = tuple(codec.decode_day(day, "extinguished_days") for day in days)
        if len(set(decoded)) != len(decoded):
            raise ValueError("extinguished_days must not repeat a date")
        return cls(
            day=BreakerDay.from_dict(data["day"]),
            extinguished_days=codec.ascending(decoded, "extinguished_days"),
            disabled_on=codec.decode_day(data["disabled_on"], "disabled_on"),
        )


def initial_state(target_date: date) -> BreakerState:
    return BreakerState(BreakerDay(target_date))


def halted(state: BreakerState) -> bool:
    """No request may go out: the day is extinguished, or direct access is disabled."""
    return state.disabled_on is not None or state.day.extinguished is not None


def ready_at(state: BreakerState, *, now: datetime) -> datetime:
    """The earliest time, not before `now`, the breaker lets the next request go (a pause or a pending retry)."""
    now = codec.require_aware(now, "now")
    return max(at for at in (now, state.day.paused_until, state.day.retry_at) if at is not None)


def for_target_date(state: BreakerState, target_date: date, *, now: datetime) -> BreakerState:
    """The state for `target_date`: unchanged on the same date (midnight is not a new day, D23) or an earlier one (a
    clock stepped back never reopens a day); fresh counters on a later one, keeping the history and a pause still
    running at `now` (a pause is honoured whichever day it lands in)."""
    if target_date <= state.day.target_date:
        return state
    now = codec.require_aware(now, "now")
    carried = state.day.paused_until if state.day.paused_until is not None and state.day.paused_until > now else None
    day = BreakerDay(target_date, paused_until=carried, probe_due=carried is not None)
    return BreakerState(day, _recent_days(state.extinguished_days, target_date), state.disabled_on)


def cap_halved(state: BreakerState, target_date: date) -> bool:
    """Were the HALVING_STREAK_DAYS target dates just before `target_date` all extinguished?"""
    return all(target_date - timedelta(days=back) in state.extinguished_days for back in range(1, HALVING_STREAK_DAYS + 1))


def clear_disabled(state: BreakerState) -> BreakerState:
    """The operator's reset (TR-13 reset-disable, which records who). The history stays."""
    return replace(state, disabled_on=None)


def status_codes(state: BreakerState) -> tuple[str, ...]:
    flags = ((EXTINGUISHED_TODAY, state.day.extinguished is not None), (DISABLED_7D, state.disabled_on is not None))
    return tuple(code for code, on in flags if on)


def observe(state: BreakerState, signal: Signal, *, now: datetime, rng: random.Random) -> tuple[BreakerState, Decision]:
    """The state and decision after one HTTP outcome at `now` (when the response, or the failure, came back)."""
    now = codec.require_aware(now, "now")
    signal = Signal(signal)
    if halted(state):
        return state, Decision(Action.EXTINGUISH)
    day = replace(state.day, rate_limited=state.day.rate_limited + (signal is Signal.RATE_LIMITED))
    if signal is Signal.WALL:
        return _extinguish(state, day, "wall")
    if day.probe_due:
        return _after_probe(state, day, signal, now)
    return _after_request(state, day, signal, now, rng)


def _after_probe(state: BreakerState, day: BreakerDay, signal: Signal, now: datetime) -> tuple[BreakerState, Decision]:
    if signal is Signal.SUCCESS:
        resumed = replace(day, probe_due=False, paused_until=None, probe_failures=0, transient_streak=0, retry_at=None, half_speed=True)
        return replace(state, day=resumed), Decision(Action.CONTINUE)
    failed = replace(day, probe_failures=day.probe_failures + 1, transient_streak=0, retry_at=None)
    if failed.rate_limited >= MAX_RATE_LIMITED_PER_DAY:
        return _extinguish(state, failed, "rate_limited")
    if failed.probe_failures >= MAX_PROBE_FAILURES:
        return _extinguish(state, failed, "probe_failures")
    return _pause(state, failed, now)


def _after_request(state: BreakerState, day: BreakerDay, signal: Signal, now: datetime, rng: random.Random) -> tuple[BreakerState, Decision]:
    if signal in (Signal.SUCCESS, Signal.NEUTRAL):
        return replace(state, day=replace(day, transient_streak=0, retry_at=None)), Decision(Action.CONTINUE)
    if signal is Signal.TRANSIENT and day.transient_streak == 0:
        retry_at = now + timedelta(seconds=rng.uniform(*RETRY_DELAY_SECONDS))
        return replace(state, day=replace(day, transient_streak=1, retry_at=retry_at)), Decision(Action.RETRY, retry_at)
    tripped = replace(day, trips=day.trips + 1, transient_streak=0, retry_at=None)
    if tripped.rate_limited >= MAX_RATE_LIMITED_PER_DAY:
        return _extinguish(state, tripped, "rate_limited")
    if tripped.trips >= MAX_TRIPS_PER_DAY:
        return _extinguish(state, tripped, "trips")
    return _pause(state, tripped, now)


def _pause(state: BreakerState, day: BreakerDay, now: datetime) -> tuple[BreakerState, Decision]:
    minutes = PAUSE_LADDER_MINUTES[min(day.pauses, len(PAUSE_LADDER_MINUTES) - 1)]
    until = now + timedelta(minutes=minutes)
    paused = replace(day, pauses=day.pauses + 1, paused_until=until, probe_due=True)
    return replace(state, day=paused), Decision(Action.PAUSE, until)


def _extinguish(state: BreakerState, day: BreakerDay, reason: ExtinguishReason) -> tuple[BreakerState, Decision]:
    out = replace(day, extinguished=reason, paused_until=None, probe_due=False, retry_at=None, transient_streak=0)
    days = _recent_days(tuple(sorted({*state.extinguished_days, day.target_date})), day.target_date)
    disabled = day.target_date if len(days) >= DISABLE_AFTER else state.disabled_on
    return BreakerState(out, days, disabled), Decision(Action.EXTINGUISH)


def _recent_days(days: tuple[date, ...], target_date: date) -> tuple[date, ...]:
    """The extinguished target dates inside the DISABLE_WINDOW_DAYS ending at `target_date`."""
    first = target_date - timedelta(days=DISABLE_WINDOW_DAYS - 1)
    return tuple(day for day in days if first <= day <= target_date)
