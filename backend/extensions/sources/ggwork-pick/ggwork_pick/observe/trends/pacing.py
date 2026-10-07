"""Pacing for the Google Trends channel (design 4.2; plan TR-03, counterexample 2).

Every HTTP request is paced, warm-up, probe and retry included, one at a time. Before each one the executor asks
ready_at() and sleeps until then (clock.sleep_in_chunks, renewing the lease); after it, record() returns the next state.
The limits, all of them at once:

- a token bucket of 8 refilled at 4 a minute, so no minute holds more than 12 requests;
- at most 200 requests in any 60 minutes;
- 40 active minutes, then 10 idle: a unit only starts inside its segment's first 40 minutes, and the rest counts from
  the last response. Any idle of 10 minutes or more, such as a breaker pause, opens a fresh segment;
- a unit is never held up half-way (explore's widget token is spent by the very next request): its first request
  waits until the bucket and the hour both have room for the whole unit, so the rest of it only keeps its gaps;
- 1.5-3 s between the requests of one query unit and 25 s plus 0-10 s between units, measured from the previous
  response. The gaps are drawn when a request is recorded, so asking ready_at() again after a wake-up, or after a
  restart, gives the same answer.

Together that averages about 2.9 requests a minute. At half speed (after a successful probe, design 4.3) every gap
doubles and the bucket refills at half the rate. The state is immutable and round-trips through a plain dict; time
and randomness always come from the caller.

Named presets, the one source for stage 0's --pace and the cron's PICK_OBS_TRENDS_PACE (settings.py):
- design: the numbers above, design 4.2's envelope;
- user: the user's own tested rhythm, a bucket of 4 refilled at 2 a minute, everything else as design. Stage 0's day 1
  met a 429 at the 56th request at the design's speed, and none at half of it. The production default (G3, seam 2):
  about 1.7 requests a minute with the rests, 0.9 at half speed. It limits the average rate; it is not a strict
  "four, then a minute's pause".
- conservative: the approved daily recovery's bucket of 2, refill of 1/minute, 60/hour and 100-120s between units.
  The cron accepts it only with an explicit daily recovery epoch and a fitting actual daily plan.
"""

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol

from ggwork_pick.observe.trends import state_codec as codec

HOUR = timedelta(hours=1)
_UNBOUND = datetime.min.replace(tzinfo=UTC)  # a limit that does not hold anything back
HALF_SPEED_FACTOR = 2.0  # half speed: every gap twice as long, the bucket refilled at half the rate


@dataclass(frozen=True, slots=True)
class PacingParams:
    """Design 4.2's numbers. A variant exists for tests (see the hour cap test) and must still make sense."""

    bucket_capacity: float = 8
    refill_per_minute: float = 4
    hour_cap: int = 200
    segment_minutes: float = 40
    rest_minutes: float = 10
    intra_unit_seconds: tuple[float, float] = (1.5, 3.0)
    inter_unit_seconds: tuple[float, float] = (25.0, 35.0)

    def __post_init__(self):
        numbers = (self.bucket_capacity, self.refill_per_minute, self.segment_minutes, self.rest_minutes)
        if not all(math.isfinite(n) and n > 0 for n in numbers) or self.bucket_capacity < 1:
            raise ValueError("pacing needs a bucket of at least 1, a positive refill, segment and rest")
        if type(self.hour_cap) is not int or self.hour_cap < 1:
            raise ValueError("pacing needs an hourly cap of at least 1 request")
        for low, high in (self.intra_unit_seconds, self.inter_unit_seconds):
            if not (math.isfinite(low) and math.isfinite(high) and 0 <= low <= high):
                raise ValueError("a gap range must be finite, non-negative and ordered")


DESIGN_PARAMS = PacingParams()
USER_PARAMS = replace(DESIGN_PARAMS, bucket_capacity=4, refill_per_minute=2)
CONSERVATIVE_PARAMS = replace(USER_PARAMS, bucket_capacity=2, refill_per_minute=1, hour_cap=60, inter_unit_seconds=(100, 120))
PRESETS: Mapping[str, PacingParams] = MappingProxyType({"design": DESIGN_PARAMS, "user": USER_PARAMS, "conservative": CONSERVATIVE_PARAMS})
PRODUCTION_PRESET = "user"  # the cron's default (settings.PICK_OBS_TRENDS_PACE)
DEFAULT_PARAMS = DESIGN_PARAMS  # what a bare EnvelopePacer() paces at: stage 0's default and the TR-03 tests'


def recovery_note() -> dict[str, Any]:
    p = CONSERVATIVE_PARAMS
    return {
        "preset": "conservative",
        "bucket_capacity": p.bucket_capacity,
        "refill_per_minute": p.refill_per_minute,
        "hour_cap": p.hour_cap,
        "segment_minutes": p.segment_minutes,
        "rest_minutes": p.rest_minutes,
        "intra_unit_seconds": list(p.intra_unit_seconds),
        "inter_unit_seconds": list(p.inter_unit_seconds),
    }


_KEYS = frozenset({"tokens", "settled_at", "recent", "segment_started_at", "last_done_at", "intra_gap", "unit_gap"})


@dataclass(frozen=True, slots=True)
class PacingState:
    tokens: float  # the bucket at settled_at, after that request took its token; below 0 only if a caller sent early
    settled_at: datetime | None = None  # the last send; None: nothing sent yet
    recent: tuple[datetime, ...] = ()  # send times within the hour before the last send, oldest first
    segment_started_at: datetime | None = None
    last_done_at: datetime | None = None  # when the last response (or failure) came back
    intra_gap: float = 0.0  # drawn at the last record: seconds before the next request of the same unit
    unit_gap: float = 0.0  # ... before the first request of the next unit

    def __post_init__(self):
        codec.aware_or_none((self.settled_at, self.segment_started_at, self.last_done_at, *self.recent), "pacing times")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens": self.tokens,
            "settled_at": codec.encode_instant(self.settled_at),
            "recent": [codec.encode_instant(at) for at in self.recent],
            "segment_started_at": codec.encode_instant(self.segment_started_at),
            "last_done_at": codec.encode_instant(self.last_done_at),
            "intra_gap": self.intra_gap,
            "unit_gap": self.unit_gap,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "PacingState":
        data = codec.exact_keys(data, _KEYS, "pacing state")
        recent = data["recent"]
        if not isinstance(recent, list | tuple):
            raise ValueError("pacing state recent must be a list")
        return cls(
            tokens=codec.finite(data["tokens"], "tokens"),
            settled_at=codec.decode_instant(data["settled_at"], "settled_at"),
            recent=codec.ascending(tuple(codec.decode_instant(at, "recent") for at in recent), "recent"),
            segment_started_at=codec.decode_instant(data["segment_started_at"], "segment_started_at"),
            last_done_at=codec.decode_instant(data["last_done_at"], "last_done_at"),
            intra_gap=codec.finite(data["intra_gap"], "intra_gap", minimum=0),
            unit_gap=codec.finite(data["unit_gap"], "unit_gap", minimum=0),
        )


def initial_state(params: PacingParams = DEFAULT_PARAMS) -> PacingState:
    """Nothing sent yet: a full bucket and no gap to keep."""
    return PacingState(tokens=float(params.bucket_capacity))


class Pacer(Protocol):
    """What the executor holds; TR-14's wiring test swaps in a no-op and expects its transport-level envelope to fail."""

    def ready_at(self, state: PacingState, *, now: datetime, first_in_unit: bool, half_speed: bool, unit_requests: int = 1) -> datetime: ...

    def record(self, state: PacingState, *, sent_at: datetime, done_at: datetime, rng: random.Random, half_speed: bool) -> PacingState: ...


class EnvelopePacer:
    """Design 4.2's envelope. Holds only its parameters: the state is passed in and a new one comes back."""

    def __init__(self, params: PacingParams = DEFAULT_PARAMS):
        self.params = params

    def ready_at(self, state: PacingState, *, now: datetime, first_in_unit: bool, half_speed: bool, unit_requests: int = 1) -> datetime:
        """The earliest time, not before `now`, the next request may go. A unit's first request passes its unit's size
        in `unit_requests` and waits for room for all of it. Each limit only ever loosens as time passes, so the latest
        of them satisfies them all; the rest is applied last."""
        now = codec.require_aware(now, "now")
        if type(unit_requests) is not int or unit_requests < 1:
            raise ValueError("unit_requests must be a positive integer")
        factor = HALF_SPEED_FACTOR if half_speed else 1.0
        need = unit_requests if first_in_unit else 1
        candidate = max(now, self._gap_ready(state, first_in_unit, factor), self._bucket_ready(state, factor, need))
        candidate = max(candidate, self._window_ready(state, candidate, need))
        return self._after_rest(state, candidate, first_in_unit)

    def record(self, state: PacingState, *, sent_at: datetime, done_at: datetime, rng: random.Random, half_speed: bool) -> PacingState:
        """The state after a request sent at `sent_at` came back (or failed) at `done_at`."""
        sent_at, done_at = codec.require_aware(sent_at, "sent_at"), codec.require_aware(done_at, "done_at")
        if done_at < sent_at:
            raise ValueError("a request cannot come back before it was sent")
        factor = HALF_SPEED_FACTOR if half_speed else 1.0
        rested = state.last_done_at is None or sent_at - state.last_done_at >= timedelta(minutes=self.params.rest_minutes)
        return PacingState(
            tokens=self._tokens_at(state, sent_at, factor) - 1,
            settled_at=sent_at,
            recent=tuple(sorted((*(at for at in state.recent if at > sent_at - HOUR), sent_at))),
            segment_started_at=sent_at if rested or state.segment_started_at is None else state.segment_started_at,
            last_done_at=done_at,
            intra_gap=rng.uniform(*self.params.intra_unit_seconds),
            unit_gap=rng.uniform(*self.params.inter_unit_seconds),
        )

    # ---- the limits -------------------------------------------------------------------------------------------------

    def _rate_per_second(self, factor: float) -> float:
        return self.params.refill_per_minute / 60 / factor

    def _tokens_at(self, state: PacingState, at: datetime, factor: float) -> float:
        if state.settled_at is None:
            return float(self.params.bucket_capacity)
        elapsed = max((at - state.settled_at).total_seconds(), 0.0)  # a wall clock stepped back refills nothing
        return min(float(self.params.bucket_capacity), state.tokens + elapsed * self._rate_per_second(factor))

    def _bucket_ready(self, state: PacingState, factor: float, need: int) -> datetime:
        """When the bucket holds `need` tokens (never more than it can hold)."""
        need = min(need, self.params.bucket_capacity)
        if state.settled_at is None or state.tokens >= need:
            return _UNBOUND
        return state.settled_at + timedelta(seconds=(need - state.tokens) / self._rate_per_second(factor))

    def _gap_ready(self, state: PacingState, first_in_unit: bool, factor: float) -> datetime:
        if state.last_done_at is None:
            return _UNBOUND
        gap = state.unit_gap if first_in_unit else state.intra_gap
        return state.last_done_at + timedelta(seconds=gap * factor)

    def _window_ready(self, state: PacingState, candidate: datetime, need: int) -> datetime:
        """Sending `need` requests from `candidate` on must leave at most hour_cap in any 60 minutes ending among them."""
        room = self.params.hour_cap - min(need, self.params.hour_cap)
        in_window = [at for at in state.recent if at > candidate - HOUR]
        if len(in_window) <= room:
            return candidate
        return in_window[len(in_window) - room - 1] + HOUR

    def _after_rest(self, state: PacingState, candidate: datetime, first_in_unit: bool) -> datetime:
        if not first_in_unit or state.segment_started_at is None or state.last_done_at is None:
            return candidate
        rest_until = state.last_done_at + timedelta(minutes=self.params.rest_minutes)
        if candidate >= rest_until or candidate < state.segment_started_at + timedelta(minutes=self.params.segment_minutes):
            return candidate
        return rest_until
