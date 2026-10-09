"""A Trends session's progress, summary and status codes (plan TR-14 step 6; design 4.5, 4.10, 4.11; status_rules).

Progress: one entry per unit in the batch's summary_json, written with the unit's raw rows, so a resumed session skips
what is done. An entry has the unit's status, or the reason it did not run (truncated, skipped_breaker, deadline):
those three are terminal for the target date (the cap does not refill, the deadline does not move back, an
extinguished day stays out), so a resume never reruns them.

The summary, written when the session ends: planned and fetched units and the coverage, the uncovered units with their
reasons (the plan's truncated ones first, design 4.5's "未覆盖 N 个单元"), the breaker events, the all-zero rate and the
userType values. The codes (contract STATUS_CODES, in its order):
- extinguished_today, disabled_7d: from the breaker (TR-03);
- canary_terminated: a canary that met one captcha or consent wall, or whose target dates were put out twice for any
  other reason (design 4.11; section 9; G3 seam 3);
- all_zero_jump: the share of all-zero bare series rose by ALL_ZERO_JUMP or more over the previous target date's,
  with at least MIN_JUDGED series today (placeholders, like every threshold here, until the shadow run);
- usertype_changed: the session saw more than one userType, or a set different from the previous target date's;
- parse_error: the weekly contract check failed, or a failure carried from an earlier batch that no check has cleared.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ggwork_pick.observe.contract import STATUS_CODES
from ggwork_pick.observe.trends import breaker
from ggwork_pick.observe.trends.executor import UnitOutcome
from ggwork_pick.observe.trends.source import FetchStatus
from ggwork_pick.observe.trends.units import QueryUnit

FETCHED = frozenset({FetchStatus.OK.value, FetchStatus.OK_ZERO.value, FetchStatus.NO_DATA.value})
JUDGED = frozenset({FetchStatus.OK.value, FetchStatus.OK_ZERO.value})
DRAMA_ITEMS = frozenset({"control", "title"})
ALL_ZERO_JUMP = 0.20  # placeholder, calibrated in the shadow run
MIN_JUDGED = 10
CANARY_TERMINATE_AFTER = 2  # extinguished target dates, for any reason but a wall (design 4.11; section 9)
WALL = breaker.WALL_REASON  # the extinguish reason of a captcha or consent page: one ends the canary
CARRIED_CODES = frozenset({"parse_error"})  # lasting conditions a new batch takes over from the one before it


def progress_entry(outcome: UnitOutcome) -> dict[str, Any]:
    """What summary_json keeps of one unit."""
    result = outcome.result
    if result is None:
        return {"status": None, "reason": outcome.reason, "series": None, "bare": None, "user_type": None, "attempts": outcome.attempts}
    bare = result.bare_line.status.value if result.bare_line is not None else None
    series = result.timeline.status.value if result.timeline is not None else None
    return {
        "status": result.status.value,
        "reason": None,
        "series": series,
        "bare": bare,
        "user_type": result.user_type,
        "attempts": outcome.attempts,
        **(
            {
                "lines": {
                    term: (result.timeline.line(term).status.value if result.timeline and result.timeline.line(term) else series) for term in outcome.unit.terms
                }
            }
            if outcome.unit.members
            else {}
        ),
    }


def fetched(entry: Mapping[str, Any]) -> bool:
    """The unit got a usable answer: its series when it asked for one, else its related queries."""
    return entry["reason"] is None and (entry["series"] if entry["series"] is not None else entry["status"]) in FETCHED


def ordered_codes(codes: Iterable[str]) -> tuple[str, ...]:
    return tuple(code for code in STATUS_CODES if code in set(codes))


@dataclass(frozen=True)
class ZeroRate:
    rate: float | None
    judged: int


def all_zero_rate(units: Sequence[QueryUnit], progress: Mapping[str, Mapping[str, Any]]) -> ZeroRate:
    """The share of the drama units' judgeable bare series (ok or ok_zero) that were all zero."""
    bare = [
        status
        for unit in units
        if unit.item in DRAMA_ITEMS and unit.key in progress
        for status in (list(progress[unit.key].get("lines", {}).values()) if unit.members else [progress[unit.key]["bare"]])
    ]
    judged = [status for status in bare if status in JUDGED]
    if not judged:
        return ZeroRate(None, 0)
    return ZeroRate(sum(status == FetchStatus.OK_ZERO.value for status in judged) / len(judged), len(judged))


def all_zero_jumped(today: ZeroRate, before: float | None) -> bool:
    return today.rate is not None and before is not None and today.judged >= MIN_JUDGED and today.rate - before >= ALL_ZERO_JUMP


def user_types(progress: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted({entry["user_type"] for entry in progress.values() if entry.get("user_type")}))


def usertype_changed(today: Sequence[str], before: Sequence[str] | None) -> bool:
    return len(today) > 1 or (bool(today) and bool(before) and set(today) != set(before))


def uncovered_units(planned: Sequence[QueryUnit], truncated: Sequence[QueryUnit], progress: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    """The units the session did not cover, with why: the plan's truncated units, then the ones stopped on the night."""
    stopped = [(unit, progress[unit.key]["reason"]) for unit in planned if unit.key in progress and progress[unit.key]["reason"] is not None]
    listed = [*((unit, "truncated") for unit in truncated), *stopped]
    return [
        {"key": unit.key, "item": unit.item, "identity": unit.identity, "geo": unit.geo, "granularity": unit.granularity, "reason": reason}
        for unit, reason in listed
    ]


def failed_units(planned: Sequence[QueryUnit], progress: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    return [
        {"key": unit.key, "identity": unit.identity, "geo": unit.geo, "status": progress[unit.key]["status"]}
        for unit in planned
        if unit.key in progress and progress[unit.key]["reason"] is None and not fetched(progress[unit.key])
    ]


@dataclass(frozen=True)
class Judged:
    """What the codes are computed from, besides the breaker."""

    canary: bool
    canary_extinguished: tuple[str | None, ...]  # the reasons of the canary's extinguished target dates
    carried: frozenset[str]
    contract_verdict: str | None
    zero_jump: bool
    usertype_changed: bool


def canary_terminated(reasons: Sequence[str | None]) -> bool:
    """Section 9: "出现验证码或熄火 2 次立即终止". One wall (captcha or consent) ends the canary at once; a day put out
    for any other reason ends it on the second. `reasons` are the persisted ones (session_rows.canary_extinguish_reasons),
    so the refusal at start and the finishing step read the same thing, a crash in between included."""
    return WALL in reasons or len(reasons) >= CANARY_TERMINATE_AFTER


def session_codes(state: breaker.BreakerState, judged: Judged) -> tuple[str, ...]:
    codes = set(breaker.status_codes(state))
    if judged.canary and canary_terminated(judged.canary_extinguished):
        codes.add("canary_terminated")
    if judged.zero_jump:
        codes.add("all_zero_jump")
    if judged.usertype_changed:
        codes.add("usertype_changed")
    parse_error = judged.contract_verdict == "parse_error" or (judged.contract_verdict is None and "parse_error" in judged.carried)
    if parse_error:
        codes.add("parse_error")
    return ordered_codes(codes)
