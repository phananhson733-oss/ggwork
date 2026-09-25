"""The Trends daily budget, the target date and the session modes (design 3.2, 4.2, 4.5, 4.11; plan D23, section 9).

The day every Trends count belongs to is the target date: the day whose 02:00 UTC publication the session feeds (D23).
A session from 20:30 to the 01:45 hard deadline is one day, so midnight neither refills the budget nor relights an
extinguished day. (GSC counts by UTC day, for its quota record only; that is not this module.)

Budget is reserved before a request is sent, every HTTP request (warm-up, probe, retry) included, and never given
back: a timeout, a lost response or a crash between the two keeps its reservation. The mode sets the cap:

| mode    | start | plan | cap |
|---------|-------|------|-----|
| canary1 | 22:00 | 220  | 220 |
| canary2 | 22:00 | 430  | 600 |
| stable  | 20:30 | 650  | 800 |

The plan is what the task list is cut to; the cap is the hard ceiling after breaker pauses and retries. A mode is only
accepted if its plan fits its window with room for one breaker episode: plan / 2.9 + 40 minutes <= deadline - start
(raising stable to 800 therefore needs an earlier start). Two extinguished target dates in a row halve the next cap.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from types import MappingProxyType
from typing import Any, Literal

from ggwork_pick.observe.trends import breaker
from ggwork_pick.observe.trends import state_codec as codec

PUBLISH_CUTOFF = time(2, 0)  # UTC: the target date's publication
DEADLINE = time(1, 45)  # UTC: every session stops here (design 3.1)
AVERAGE_REQUESTS_PER_MINUTE = 2.9  # design 4.2, rests included
BREAKER_MARGIN_MINUTES = 40  # one breaker pause (30 minutes) and its probe

StopReason = Literal["truncated", "skipped_breaker", "deadline"]
TRUNCATED: StopReason = "truncated"  # the budget cannot hold the unit
SKIPPED_BREAKER: StopReason = "skipped_breaker"  # the breaker put the day out, or its pause runs past the deadline
DEADLINE_REASON: StopReason = "deadline"


def target_date_of(now: datetime) -> date:
    """The target date a moment belongs to: from 02:00 UTC one day to 02:00 UTC the next is the next day's."""
    now = codec.require_aware(now, "now")
    cutoff = timedelta(hours=PUBLISH_CUTOFF.hour, minutes=PUBLISH_CUTOFF.minute)
    return (now - cutoff).date() + timedelta(days=1)


@dataclass(frozen=True, slots=True)
class ModeLimits:
    """One session mode. Times are UTC; a start at or after 02:00 is on the day before the target date."""

    name: str
    start: time
    plan: int
    cap: int
    deadline: time = DEADLINE

    def __post_init__(self):
        if type(self.plan) is not int or type(self.cap) is not int or not 0 < self.plan <= self.cap:
            raise ValueError(f"mode {self.name}: need 0 < plan <= cap")
        if not all(isinstance(at, time) and at.tzinfo is None for at in (self.start, self.deadline)):
            raise ValueError(f"mode {self.name}: start and deadline are plain UTC times of day")
        if self.deadline > PUBLISH_CUTOFF:
            raise ValueError(f"mode {self.name}: the deadline must come before the 02:00 publication")
        window = self.window_minutes()
        needed = self.plan / AVERAGE_REQUESTS_PER_MINUTE + BREAKER_MARGIN_MINUTES
        if needed > window:
            raise ValueError(f"mode {self.name}: the plan needs {needed:.0f} minutes, the window has {window:.0f}; start earlier or plan less")

    def window(self, target_date: date) -> tuple[datetime, datetime]:
        """The session's start and hard deadline for `target_date`."""
        start_day = target_date - timedelta(days=1) if self.start >= PUBLISH_CUTOFF else target_date
        return datetime.combine(start_day, self.start, UTC), datetime.combine(target_date, self.deadline, UTC)

    def window_minutes(self) -> float:
        start, deadline = self.window(date(2000, 1, 2))
        return (deadline - start).total_seconds() / 60

    def halved(self) -> "ModeLimits":
        cap = max(1, self.cap // 2)
        return replace(self, cap=cap, plan=min(self.plan, cap))


MODES: Mapping[str, ModeLimits] = MappingProxyType(
    {
        "canary1": ModeLimits("canary1", start=time(22, 0), plan=220, cap=220),
        "canary2": ModeLimits("canary2", start=time(22, 0), plan=430, cap=600),
        "stable": ModeLimits("stable", start=time(20, 30), plan=650, cap=800),
    }
)


def mode_limits(name: str) -> ModeLimits:
    try:
        return MODES[name]
    except KeyError:
        raise ValueError(f"unknown Trends mode; expected one of {sorted(MODES)}") from None


def day_limits(mode: str | ModeLimits, target_date: date, breaker_state: breaker.BreakerState) -> ModeLimits:
    """The mode's limits for `target_date`, halved after two extinguished target dates in a row."""
    limits = mode if isinstance(mode, ModeLimits) else mode_limits(mode)
    return limits.halved() if breaker.cap_halved(breaker_state, target_date) else limits


class BudgetExhausted(Exception):
    """The day's cap is spent: the unit, and every unit after it, is truncated."""


_KEYS = frozenset({"target_date", "reserved", "first_limit_at", "before_first_limit"})


@dataclass(frozen=True, slots=True)
class BudgetDay:
    """One target date's budget row (ggwp_obs_budget, keyed by channel and budget_day)."""

    target_date: date
    reserved: int = 0  # requests reserved; only ever grows
    first_limit_at: datetime | None = None  # when the day's first limit signal was sent
    before_first_limit: int | None = None  # requests the day sent before it (design 4.2: which quota mechanism)

    def __post_init__(self):
        codec.aware_or_none((self.first_limit_at,), "first_limit_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_date": codec.encode_day(self.target_date),
            "reserved": self.reserved,
            "first_limit_at": codec.encode_instant(self.first_limit_at),
            "before_first_limit": self.before_first_limit,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "BudgetDay":
        data = codec.exact_keys(data, _KEYS, "budget day")
        target = codec.decode_day(data["target_date"], "target_date")
        reserved = codec.count(data["reserved"], "reserved")
        first_at = codec.decode_instant(data["first_limit_at"], "first_limit_at")
        before = None if data["before_first_limit"] is None else codec.count(data["before_first_limit"], "before_first_limit")
        if target is None or (first_at is None) != (before is None) or (before is not None and before >= reserved):
            raise ValueError("budget day needs a target date, and the first limit's time and count together, under reserved")
        return cls(target, reserved, first_at, before)


def for_target_date(day: BudgetDay, target_date: date) -> BudgetDay:
    """The budget for `target_date`: the same row on the same date (midnight refills nothing), a fresh one on a later
    date, and the same row if a clock stepped back."""
    return day if target_date <= day.target_date else BudgetDay(target_date)


def remaining(day: BudgetDay, limits: ModeLimits) -> int:
    return max(0, limits.cap - day.reserved)


def reserve(day: BudgetDay, limits: ModeLimits) -> BudgetDay:
    """Reserve one request, before it is sent. There is no way back: nothing here ever lowers `reserved`."""
    if day.reserved >= limits.cap:
        raise BudgetExhausted(f"mode {limits.name}: the cap of {limits.cap} requests is spent")
    return replace(day, reserved=day.reserved + 1)


def note_limit(day: BudgetDay, *, ordinal: int, at: datetime) -> BudgetDay:
    """Record the day's first limit signal: request number `ordinal` of the day, sent at `at`. Later ones change
    nothing."""
    if day.first_limit_at is not None:
        return day
    if type(ordinal) is not int or not 1 <= ordinal <= day.reserved:
        raise ValueError("the limited request must be one the day reserved")
    return replace(day, first_limit_at=codec.require_aware(at, "at"), before_first_limit=ordinal - 1)


def stop_reason(*, breaker_state: breaker.BreakerState, day: BudgetDay, limits: ModeLimits, now: datetime, need: int = 1) -> StopReason | None:
    """Why the next unit, of `need` requests, cannot start at `now`; None when it can. The breaker comes first: a day it
    put out, or a pause or retry that ends only after the deadline, is skipped_breaker, not deadline."""
    now = codec.require_aware(now, "now")
    if breaker_state.day.target_date != day.target_date:
        raise ValueError("the breaker and the budget must be on the same target date")
    if breaker.halted(breaker_state):
        return SKIPPED_BREAKER
    deadline = limits.window(day.target_date)[1]
    if now >= deadline:
        return DEADLINE_REASON
    if breaker.ready_at(breaker_state, now=now) >= deadline:
        return SKIPPED_BREAKER
    if remaining(day, limits) < need:
        return TRUNCATED
    return None
