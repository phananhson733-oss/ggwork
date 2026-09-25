"""TR-03: the Trends breaker, the retry policy and the per-target-date budget (design 4.3, 4.5, 4.11; plan D23, section 9).

The day a count belongs to is the target date, the day whose 02:00 UTC publication the session feeds (D23): a session
from 20:30 to 01:45 is one day, so midnight neither refills the budget nor relights an extinguished day. Pauses climb
30, 60, 120, 240 minutes within a day; three failed probes in a row, three trips, five 429s or one captcha, consent or
sorry wall put the day out; two extinguished days in a row halve the next cap; three in seven target dates disable
direct access until an operator clears it.
"""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest
import trends_session_sim as sim

from ggwork_pick.observe import contract
from ggwork_pick.observe.clock import ManualClock, random_source
from ggwork_pick.observe.trends import breaker, budget

S = breaker.Signal
A = breaker.Action
EVENING = datetime(2026, 9, 25, 22, 0, tzinfo=UTC)
TARGET = date(2026, 9, 26)
FETCH_STATUSES = ("ok", "ok_zero", "no_data", "rate_limited", "blocked_redirect", "html_body", "forbidden", "server_error", "timeout", "parse_error")


def _feed(state, steps, *, start=EVENING, rng=None):
    """Apply (minutes after start, signal) pairs in order; returns the last state and every decision."""
    rng = rng or random_source(1)
    decisions = ()
    for minutes, signal in steps:
        state, decision = breaker.observe(state, signal, now=start + timedelta(minutes=minutes), rng=rng)
        decisions = (*decisions, decision)
    return state, decisions


def _pause_minutes(decisions, start=EVENING, at=()):
    return [round((d.resume_at - (start + timedelta(minutes=m))).total_seconds() / 60) for d, m in zip(decisions, at, strict=True)]


# ---- signals ---------------------------------------------------------------------------------------------------------


def test_every_fetch_status_has_a_signal():
    """TR-02's ten fetch statuses (design 4.10); an unknown one is refused, never guessed."""
    assert set(breaker.FETCH_STATUS_SIGNALS) == set(FETCH_STATUSES)
    assert breaker.signal_of("rate_limited") is S.RATE_LIMITED
    assert breaker.signal_of("blocked_redirect") is S.WALL  # sorry or consent host
    assert {breaker.signal_of(name) for name in ("forbidden", "html_body")} == {S.LIMITED}
    assert {breaker.signal_of(name) for name in ("server_error", "timeout")} == {S.TRANSIENT}
    assert {breaker.signal_of(name) for name in ("ok", "ok_zero", "no_data")} == {S.SUCCESS}
    with pytest.raises(ValueError):
        breaker.signal_of("captcha_maybe")


# ---- 429 and the probe ladder -----------------------------------------------------------------------------------------


def test_429_zero_requests_30min():
    """After a 429 nothing goes out for 30 minutes; at 30 minutes exactly one probe, which then paces at half speed."""
    clock = ManualClock(EVENING)
    result = sim.run(clock=clock, rng=random_source(3), responder=sim.script({10: S.RATE_LIMITED}), units=[2] * 12)
    limited = result.sent[9]
    probe, after = result.sent[10], result.sent[11:]
    assert limited.signal is S.RATE_LIMITED and not limited.probe
    assert sim._seconds(probe.sent_at, limited.done_at) == pytest.approx(30 * 60)
    assert probe.probe and not after[0].probe
    assert [s for s in result.sent if limited.done_at < s.sent_at < probe.sent_at] == []
    assert all(s.half_speed for s in after)
    assert result.decisions[9].action is A.PAUSE and result.decisions[9].abandon_unit
    assert (limited.unit, budget.SKIPPED_BREAKER) in result.uncovered


def test_probe_ladder():
    """Each pause of the day is one rung higher: 30, 60, 120 through failed probes, and a later trip carries on to 240."""
    state, decisions = _feed(
        breaker.initial_state(TARGET),
        [(0, S.LIMITED), (30, S.LIMITED), (90, S.LIMITED), (210, S.SUCCESS), (215, S.LIMITED), (455, S.SUCCESS), (460, S.LIMITED)],
    )
    actions = [d.action for d in decisions]
    assert actions == [A.PAUSE, A.PAUSE, A.PAUSE, A.CONTINUE, A.PAUSE, A.CONTINUE, A.EXTINGUISH]
    assert _pause_minutes([decisions[i] for i in (0, 1, 2, 4)], at=(0, 30, 90, 215)) == [30, 60, 120, 240]
    assert state.day.extinguished == "trips"  # the third trip of the day


def test_probe_success_resumes_at_half_speed():
    state, decisions = _feed(breaker.initial_state(TARGET), [(0, S.RATE_LIMITED), (30, S.SUCCESS)])
    assert decisions[-1].action is A.CONTINUE
    assert state.day.half_speed and not state.day.probe_due and state.day.paused_until is None
    assert state.day.trips == 1 and state.day.rate_limited == 1


def test_ready_at_holds_until_the_pause_ends():
    state, _ = _feed(breaker.initial_state(TARGET), [(0, S.LIMITED)])
    assert breaker.ready_at(state, now=EVENING + timedelta(minutes=5)) == EVENING + timedelta(minutes=30)
    assert state.day.probe_due and not breaker.halted(state)
    assert breaker.ready_at(state, now=EVENING + timedelta(minutes=45)) == EVENING + timedelta(minutes=45)


# ---- extinguishing ------------------------------------------------------------------------------------------------------

EXTINGUISH_CASES = {
    "probe_failures": ([(0, S.LIMITED), (30, S.LIMITED), (90, S.TRANSIENT), (210, S.NEUTRAL)], "probe_failures"),
    "trips": ([(0, S.LIMITED), (30, S.SUCCESS), (40, S.LIMITED), (100, S.SUCCESS), (110, S.LIMITED)], "trips"),
    "429_x5": (
        [(0, S.RATE_LIMITED), (30, S.RATE_LIMITED), (90, S.RATE_LIMITED), (210, S.SUCCESS), (215, S.RATE_LIMITED), (455, S.RATE_LIMITED)],
        "rate_limited",
    ),
    "captcha_or_consent": ([(0, S.WALL)], "wall"),
    "sorry_page": ([(0, breaker.signal_of("blocked_redirect"))], "wall"),
    "wall_while_probing": ([(0, S.LIMITED), (30, S.WALL)], "wall"),
}


@pytest.mark.parametrize("case", sorted(EXTINGUISH_CASES))
def test_extinguish_conditions(case):
    steps, reason = EXTINGUISH_CASES[case]
    state, decisions = _feed(breaker.initial_state(TARGET), steps)
    assert [d.action for d in decisions][-1] is A.EXTINGUISH
    assert all(d.action is not A.EXTINGUISH for d in decisions[:-1])
    assert state.day.extinguished == reason and breaker.halted(state)
    assert decisions[-1].abandon_unit and decisions[-1].resume_at is None
    assert state.extinguished_days == (TARGET,)
    assert breaker.status_codes(state) == ("extinguished_today",)


def test_two_probe_failures_do_not_extinguish():
    state, decisions = _feed(breaker.initial_state(TARGET), [(0, S.LIMITED), (30, S.LIMITED), (90, S.LIMITED)])
    assert not breaker.halted(state) and state.day.probe_failures == 2 and state.day.probe_due


def test_probe_failures_count_in_a_row():
    """A successful probe starts the next pause's count afresh; the day's trips and 429s keep adding up."""
    state, _ = _feed(breaker.initial_state(TARGET), [(0, S.LIMITED), (30, S.LIMITED), (90, S.SUCCESS), (100, S.LIMITED), (340, S.LIMITED)])
    assert not breaker.halted(state)
    assert state.day.probe_failures == 1 and state.day.trips == 2


def test_extinguished_day_refuses_further_outcomes():
    state, _ = _feed(breaker.initial_state(TARGET), [(0, S.WALL)])
    later, decision = breaker.observe(state, S.SUCCESS, now=EVENING + timedelta(hours=1), rng=random_source(1))
    assert later == state and decision.action is A.EXTINGUISH


# ---- retry ------------------------------------------------------------------------------------------------------------


def test_retry_policy():
    rng = random_source(5)
    fresh = breaker.initial_state(TARGET)
    # 429 and the other limit signals are never retried.
    for signal in (S.RATE_LIMITED, S.LIMITED):
        _, decision = breaker.observe(fresh, signal, now=EVENING, rng=rng)
        assert decision.action is A.PAUSE
    # A 5xx or timeout is retried once, 30-60 s later.
    for signal in (breaker.signal_of("server_error"), breaker.signal_of("timeout")):
        once, decision = breaker.observe(fresh, signal, now=EVENING, rng=rng)
        assert decision.action is A.RETRY and not decision.abandon_unit
        assert 30 <= (decision.resume_at - EVENING).total_seconds() <= 60
        assert breaker.ready_at(once, now=EVENING) == decision.resume_at
        # The retry succeeds: carry on, and the next transient is a first one again.
        ok, decision = breaker.observe(once, S.SUCCESS, now=decision.resume_at, rng=rng)
        assert decision.action is A.CONTINUE and ok.day.transient_streak == 0 and ok.day.retry_at is None
        # The retry fails the same way: twice in a row is a limit signal, handled as one.
        twice, decision = breaker.observe(once, signal, now=EVENING + timedelta(minutes=1), rng=rng)
        assert decision.action is A.PAUSE and twice.day.trips == 1 and twice.day.rate_limited == 0


def test_retry_then_limit_signal_trips():
    once, _ = breaker.observe(breaker.initial_state(TARGET), S.TRANSIENT, now=EVENING, rng=random_source(1))
    tripped, decision = breaker.observe(once, S.RATE_LIMITED, now=EVENING + timedelta(minutes=1), rng=random_source(1))
    assert decision.action is A.PAUSE and tripped.day.rate_limited == 1 and tripped.day.transient_streak == 0


def test_neutral_breaks_a_transient_streak():
    state, decisions = _feed(breaker.initial_state(TARGET), [(0, S.TRANSIENT), (1, S.NEUTRAL), (2, S.TRANSIENT)])
    assert [d.action for d in decisions] == [A.RETRY, A.CONTINUE, A.RETRY]
    assert state.day.trips == 0


def test_transient_on_a_probe_is_a_failed_probe():
    state, decisions = _feed(breaker.initial_state(TARGET), [(0, S.LIMITED), (30, S.TRANSIENT)])
    assert decisions[-1].action is A.PAUSE and state.day.probe_failures == 1


# ---- the target date (D23) --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "target"),
    [
        (datetime(2026, 9, 25, 20, 30, tzinfo=UTC), date(2026, 9, 26)),
        (datetime(2026, 9, 25, 23, 59, 59, tzinfo=UTC), date(2026, 9, 26)),
        (datetime(2026, 9, 26, 0, 10, tzinfo=UTC), date(2026, 9, 26)),
        (datetime(2026, 9, 26, 1, 59, 59, 999999, tzinfo=UTC), date(2026, 9, 26)),
        (datetime(2026, 9, 26, 2, 0, tzinfo=UTC), date(2026, 9, 27)),
        (datetime(2026, 9, 26, 10, 0, tzinfo=timezone(timedelta(hours=8))), date(2026, 9, 27)),  # 02:00 UTC
    ],
)
def test_target_date_of(now, target):
    assert budget.target_date_of(now) == target


def test_target_date_needs_an_aware_time():
    with pytest.raises(ValueError):
        budget.target_date_of(datetime(2026, 9, 25, 22, 0))


def test_cross_midnight_same_budget_day():
    """Put out at 22:30; the 00:10 trigger is the same target date: still out, and the budget is not refilled."""
    at_2230 = datetime(2026, 9, 25, 22, 30, tzinfo=UTC)
    state, _ = _feed(breaker.initial_state(budget.target_date_of(at_2230)), [(0, S.WALL)], start=at_2230)
    day = budget.BudgetDay(budget.target_date_of(at_2230), reserved=37)
    at_0010 = datetime(2026, 9, 26, 0, 10, tzinfo=UTC)
    target = budget.target_date_of(at_0010)
    resumed = breaker.for_target_date(state, target, now=at_0010)
    assert resumed == state and breaker.halted(resumed)
    assert budget.for_target_date(day, target) == day
    limits = budget.mode_limits("canary2")
    assert budget.stop_reason(breaker_state=resumed, day=day, limits=limits, now=at_0010) == budget.SKIPPED_BREAKER
    # The next evening is a new target date: fresh counters, a fresh budget, the history kept.
    next_evening = datetime(2026, 9, 26, 22, 0, tzinfo=UTC)
    new_target = budget.target_date_of(next_evening)
    fresh = breaker.for_target_date(resumed, new_target, now=next_evening)
    assert not breaker.halted(fresh) and fresh.day.trips == 0 and fresh.extinguished_days == (TARGET,)
    assert budget.for_target_date(day, new_target) == budget.BudgetDay(new_target)


def _extinguished_on(days):
    state = breaker.initial_state(days[0])
    for day in days:
        now = datetime.combine(day - timedelta(days=1), time(22, 0), UTC)
        state = breaker.for_target_date(state, day, now=now)
        state, _ = breaker.observe(state, S.WALL, now=now, rng=random_source(1))
    return state


def test_cross_day():
    d = date(2026, 10, 1)
    two = _extinguished_on([d, d + timedelta(days=1)])
    assert breaker.cap_halved(two, d + timedelta(days=2))
    assert not breaker.cap_halved(two, d + timedelta(days=3))  # the streak must end the day before
    halved = budget.day_limits("canary2", d + timedelta(days=2), two)
    assert (halved.cap, halved.plan) == (300, 300)
    assert budget.day_limits("stable", d + timedelta(days=2), two).cap == 400
    assert budget.day_limits("canary2", d + timedelta(days=3), two) == budget.mode_limits("canary2")
    # Not consecutive: no halving.
    apart = _extinguished_on([d, d + timedelta(days=2)])
    assert not breaker.cap_halved(apart, d + timedelta(days=3))
    # Three within seven target dates: disabled, with the alert code, until an operator clears it.
    disabled = _extinguished_on([d, d + timedelta(days=3), d + timedelta(days=6)])
    assert disabled.disabled_on == d + timedelta(days=6)
    assert breaker.status_codes(disabled) == ("extinguished_today", "disabled_7d")
    later = breaker.for_target_date(disabled, d + timedelta(days=9), now=datetime.combine(d + timedelta(days=8), time(22), UTC))
    assert breaker.halted(later) and breaker.status_codes(later) == ("disabled_7d",)
    cleared = breaker.clear_disabled(later)
    assert not breaker.halted(cleared) and cleared.disabled_on is None
    # The eighth day no longer counts the first: three in eight days is not three in seven.
    spread = _extinguished_on([d, d + timedelta(days=3), d + timedelta(days=7)])
    assert spread.disabled_on is None


def test_disabled_day_stops_every_unit():
    d = date(2026, 10, 1)
    disabled = _extinguished_on([d, d + timedelta(days=1), d + timedelta(days=2)])
    nxt = d + timedelta(days=3)
    state = breaker.for_target_date(disabled, nxt, now=datetime.combine(d + timedelta(days=2), time(22), UTC))
    limits = budget.mode_limits("stable")
    now = datetime.combine(d + timedelta(days=2), time(21), UTC)
    assert budget.stop_reason(breaker_state=state, day=budget.BudgetDay(nxt), limits=limits, now=now) == budget.SKIPPED_BREAKER


def test_rollover_keeps_a_pause_that_is_still_running():
    state, _ = _feed(breaker.initial_state(TARGET), [(0, S.LIMITED)], start=datetime(2026, 9, 26, 1, 40, tzinfo=UTC))
    after_cutoff = datetime(2026, 9, 26, 2, 5, tzinfo=UTC)
    rolled = breaker.for_target_date(state, date(2026, 9, 27), now=after_cutoff)
    assert rolled.day.target_date == date(2026, 9, 27) and rolled.day.trips == 0
    assert rolled.day.paused_until == datetime(2026, 9, 26, 2, 10, tzinfo=UTC) and rolled.day.probe_due
    evening = breaker.for_target_date(state, date(2026, 9, 27), now=datetime(2026, 9, 26, 20, 30, tzinfo=UTC))
    assert evening.day == breaker.BreakerDay(date(2026, 9, 27))


def test_rollover_never_goes_back_a_day():
    state, _ = _feed(breaker.initial_state(TARGET), [(0, S.WALL)])
    assert breaker.for_target_date(state, TARGET - timedelta(days=1), now=EVENING) == state


# ---- budget ------------------------------------------------------------------------------------------------------------


def test_budget_reserved_before_send():
    """The count includes a request before it leaves; a timeout or an unknown result is never given back."""
    seen = []

    def responder(ordinal, sent_at, day):
        seen.append((ordinal, day.reserved))
        return {3: S.TRANSIENT, 4: S.TRANSIENT}.get(ordinal, S.SUCCESS)

    clock = ManualClock(EVENING)
    limits = budget.mode_limits("canary1")
    result = sim.run(clock=clock, rng=random_source(2), responder=responder, units=[2] * 4, limits=limits)
    assert all(ordinal == reserved for ordinal, reserved in seen)
    assert result.budget.reserved == len(result.sent)  # the timed-out request and its retry both counted
    # A crash between the reservation and the response: what was stored is what was reserved.
    reserved = budget.reserve(result.budget, limits)
    restored = budget.BudgetDay.from_dict(json.loads(json.dumps(reserved.to_dict())))
    assert restored.reserved == result.budget.reserved + 1
    assert not [name for name in dir(budget) if "refund" in name or "release" in name]


def test_reserve_stops_at_the_cap():
    limits = budget.mode_limits("canary1")
    day = budget.BudgetDay(TARGET, reserved=219)
    last = budget.reserve(day, limits)
    assert last.reserved == 220 and budget.remaining(last, limits) == 0
    with pytest.raises(budget.BudgetExhausted):
        budget.reserve(last, limits)
    assert day.reserved == 219  # nothing is changed in place


def test_first_limit_is_kept():
    day = budget.note_limit(budget.BudgetDay(TARGET, reserved=41), ordinal=41, at=EVENING)
    again = budget.note_limit(replace(day, reserved=50), ordinal=50, at=EVENING + timedelta(hours=1))
    assert (again.before_first_limit, again.first_limit_at) == (40, EVENING)
    assert again.reserved == 50


def test_remaining_marked_skipped_breaker():
    """A wall puts the day out: the unit it hit and every unit after it are skipped_breaker, and none of them spends budget."""
    clock = ManualClock(EVENING)
    limits = budget.mode_limits("canary1")
    result = sim.run(clock=clock, rng=random_source(4), responder=sim.script({7: S.WALL}), units=[2] * 10, limits=limits)
    assert result.covered == (0, 1, 2)
    assert result.uncovered == tuple((unit, budget.SKIPPED_BREAKER) for unit in range(3, 10))
    assert result.budget.reserved == 7 and len(result.sent) == 7
    assert result.budget.before_first_limit == 6


def test_trip_skips_only_its_own_unit():
    """A trip abandons the rest of its unit; after a good probe the session goes on."""
    clock = ManualClock(EVENING)
    limits = budget.mode_limits("canary1")
    result = sim.run(clock=clock, rng=random_source(4), responder=sim.script({4: S.LIMITED}), units=[2] * 5, limits=limits)
    assert result.uncovered == ((1, budget.SKIPPED_BREAKER),)
    assert result.covered == (0, 2, 3, 4)


@pytest.mark.parametrize(
    ("now", "paused", "reserved", "need", "expected"),
    [
        (datetime(2026, 9, 26, 1, 0, tzinfo=UTC), None, 0, 2, None),
        (datetime(2026, 9, 26, 1, 45, tzinfo=UTC), None, 0, 2, budget.DEADLINE_REASON),
        (datetime(2026, 9, 26, 1, 20, tzinfo=UTC), datetime(2026, 9, 26, 1, 50, tzinfo=UTC), 0, 2, budget.SKIPPED_BREAKER),
        (datetime(2026, 9, 26, 1, 0, tzinfo=UTC), datetime(2026, 9, 26, 1, 30, tzinfo=UTC), 0, 2, None),
        (datetime(2026, 9, 26, 1, 0, tzinfo=UTC), None, 219, 2, budget.TRUNCATED),
        (datetime(2026, 9, 26, 1, 0, tzinfo=UTC), None, 218, 2, None),
    ],
)
def test_stop_reason(now, paused, reserved, need, expected):
    state = breaker.initial_state(TARGET)
    if paused is not None:
        state = replace(state, day=replace(state.day, paused_until=paused, probe_due=True, trips=1, pauses=1))
    day = budget.BudgetDay(TARGET, reserved=reserved)
    assert budget.stop_reason(breaker_state=state, day=day, limits=budget.mode_limits("canary1"), now=now, need=need) == expected


def test_stop_reasons_are_contract_values():
    assert {budget.SKIPPED_BREAKER, budget.TRUNCATED, budget.DEADLINE_REASON} == set(contract.ENUMS["UNCOVERED_REASONS"])
    assert set(breaker.STATUS_CODES) <= set(contract.STATUS_CODES)


# ---- modes (section 9) --------------------------------------------------------------------------------------------------


def test_mode_caps():
    table = {name: (m.start, m.plan, m.cap, m.deadline) for name, m in budget.MODES.items()}
    assert table == {
        "canary1": (time(22, 0), 220, 220, time(1, 45)),
        "canary2": (time(22, 0), 430, 600, time(1, 45)),
        "stable": (time(20, 30), 650, 800, time(1, 45)),
    }
    assert budget.mode_limits("canary2").window(TARGET) == (datetime(2026, 9, 25, 22, 0, tzinfo=UTC), datetime(2026, 9, 26, 1, 45, tzinfo=UTC))
    with pytest.raises(ValueError):
        budget.mode_limits("canary3")


@pytest.mark.parametrize(
    ("name", "start", "plan", "cap", "fits"),
    [
        ("canary1", time(22, 0), 220, 220, True),  # 116 of 225 minutes
        ("canary2", time(22, 0), 430, 600, True),  # 188 of 225
        ("canary2", time(22, 0), 600, 600, False),  # planning at the cap: 247 of 225 (critique C-29)
        ("stable", time(20, 30), 650, 800, True),  # 264 of 315
        ("stable", time(20, 30), 800, 800, False),  # 316 of 315: raising the plan to 800 needs an earlier start
        ("stable", time(20, 0), 800, 800, True),  # 316 of 345
        ("stable", time(1, 0), 10, 10, True),  # an after-midnight start is on the target date itself
        ("stable", time(1, 30), 10, 10, False),  # 43 of 15
    ],
)
def test_mode_plan_fits_window(name, start, plan, cap, fits):
    if fits:
        limits = budget.ModeLimits(name, start=start, plan=plan, cap=cap)
        assert plan / budget.AVERAGE_REQUESTS_PER_MINUTE + budget.BREAKER_MARGIN_MINUTES <= limits.window_minutes()
    else:
        with pytest.raises(ValueError):
            budget.ModeLimits(name, start=start, plan=plan, cap=cap)


@pytest.mark.parametrize(("plan", "cap"), [(0, 10), (11, 10), (-1, 5)])
def test_mode_limits_refuse_bad_plan_or_cap(plan, cap):
    with pytest.raises(ValueError):
        budget.ModeLimits("x", start=time(20, 0), plan=plan, cap=cap)


# ---- persistence ----------------------------------------------------------------------------------------------------


def test_breaker_state_roundtrips_through_json():
    d = date(2026, 10, 1)
    state = _extinguished_on([d, d + timedelta(days=1)])
    state = breaker.for_target_date(state, d + timedelta(days=2), now=datetime.combine(d + timedelta(days=1), time(22), UTC))
    state, _ = _feed(state, [(0, S.TRANSIENT), (1, S.TRANSIENT), (31, S.RATE_LIMITED)], start=datetime.combine(d + timedelta(days=1), time(22), UTC))
    for sample in (state, breaker.initial_state(TARGET), _extinguished_on([d, d + timedelta(days=1), d + timedelta(days=2)])):
        assert breaker.BreakerState.from_dict(json.loads(json.dumps(sample.to_dict()))) == sample


def test_budget_day_roundtrips_through_json():
    day = budget.note_limit(budget.BudgetDay(TARGET, reserved=12), ordinal=12, at=EVENING)
    for sample in (day, budget.BudgetDay(TARGET)):
        assert budget.BudgetDay.from_dict(json.loads(json.dumps(sample.to_dict()))) == sample


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: {**d, "extra": 1},
        lambda d: {**d, "extinguished_days": ["2026-09-27", "2026-09-26"]},
        lambda d: {**d, "disabled_on": "26/09/2026"},
        lambda d: {**d, "day": {**d["day"], "trips": -1}},
        lambda d: {**d, "day": {**d["day"], "trips": 1.0}},
        lambda d: {**d, "day": {**d["day"], "half_speed": 1}},
        lambda d: {**d, "day": {**d["day"], "extinguished": "bored"}},
        lambda d: {**d, "day": {**d["day"], "paused_until": "2026-09-25T22:30:00"}},
        lambda d: {k: v for k, v in d.items() if k != "day"},
    ],
)
def test_breaker_from_dict_refuses_bad_input(mutate):
    with pytest.raises(ValueError):
        breaker.BreakerState.from_dict(mutate(breaker.initial_state(TARGET).to_dict()))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: {**d, "reserved": -1},
        lambda d: {**d, "reserved": "3"},
        lambda d: {**d, "target_date": "2026-9-26"},
        lambda d: {**d, "first_limit_at": "2026-09-25T22:00:00.000000+00:00"},  # a time without its count
        lambda d: {k: v for k, v in d.items() if k != "reserved"},
    ],
)
def test_budget_from_dict_refuses_bad_input(mutate):
    with pytest.raises(ValueError):
        budget.BudgetDay.from_dict(mutate(budget.BudgetDay(TARGET, reserved=3).to_dict()))


def test_driver_counts_match():
    """The reference wiring: one reservation per HTTP request, probes and retries included."""
    clock = ManualClock(EVENING)
    limits = budget.mode_limits("canary1")
    signals = {3: S.TRANSIENT, 8: S.RATE_LIMITED, 9: S.LIMITED}
    result = sim.run(clock=clock, rng=random_source(6), responder=sim.script(signals), units=[2] * 30, limits=limits)
    assert result.budget.reserved == len(result.sent)
    assert [s.probe for s in result.sent].count(True) == 2
    assert sum(1 for d in result.decisions if d.action is A.RETRY) == 1
