"""TR-03: the Trends breaker, the retry policy and the per-target-date budget (design 4.3, 4.5, 4.11; plan D23, section 9).

The day a count belongs to is the target date, the day whose 02:00 UTC publication the session feeds (D23): a session
from 20:30 to 01:45 is one day, so midnight neither refills the budget nor relights an extinguished day. Pauses climb
30, 60, 120, 240 minutes within a day; three failed probes in a row, three trips, five 429s or one captcha, consent or
sorry wall put the day out; two extinguished days in a row halve the next cap; three in seven target dates disable
direct access until an operator clears it.
"""

import importlib.util
import inspect
import json
import typing
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest
import trends_session_sim as sim

from ggwork_pick.observe import contract
from ggwork_pick.observe.clock import ManualClock, random_source
from ggwork_pick.observe.trends import breaker, budget
from ggwork_pick.observe.trends import state_codec as codec

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


def _signal(name, captcha_or_consent=False):
    return breaker.signal_of(name, captcha_or_consent=captcha_or_consent)


def test_every_fetch_status_has_a_signal():
    """TR-02's ten fetch statuses (design 4.10); an unknown one is refused, never guessed."""
    assert set(breaker.FETCH_STATUS_SIGNALS) == set(FETCH_STATUSES)
    assert _signal("rate_limited") is S.RATE_LIMITED
    assert {_signal(name) for name in ("forbidden", "html_body")} == {S.LIMITED}
    assert {_signal(name) for name in ("server_error", "timeout")} == {S.TRANSIENT}
    assert {_signal(name) for name in ("ok", "ok_zero", "no_data")} == {S.SUCCESS}
    assert _signal("parse_error") is S.NEUTRAL
    with pytest.raises(ValueError):
        _signal("captcha_maybe")


def test_only_a_captcha_or_consent_redirect_is_a_wall():
    """TR-02 files every redirect on an API path as blocked_redirect and says separately whether it went to the sorry or
    consent page (captcha_or_consent). Only those end the day (design 4.3); any other redirect is a limit signal: a
    pause and a probe, never the day."""
    assert _signal("blocked_redirect", captcha_or_consent=True) is S.WALL
    assert _signal("blocked_redirect", captcha_or_consent=False) is S.LIMITED
    # A captcha seen is a captcha, whatever status came with it (TR-02 never pairs them, so this is only a backstop).
    assert {_signal(name, captcha_or_consent=True) for name in FETCH_STATUSES} == {S.WALL}
    # The flag has no default, so a call site cannot forget it (as D42 does for check_answer), and it is a real bool.
    with pytest.raises(TypeError):
        breaker.signal_of("blocked_redirect")  # type: ignore[call-arg]
    for flag in (None, 1, "yes"):
        with pytest.raises(ValueError):
            breaker.signal_of("blocked_redirect", captcha_or_consent=flag)


def test_other_redirect_pauses_without_putting_the_day_out():
    state, decisions = _feed(breaker.initial_state(TARGET), [(0, _signal("blocked_redirect"))])
    assert decisions[-1].action is A.PAUSE and state.day.trips == 1
    assert not breaker.halted(state) and state.extinguished_days == ()
    # On a probe it is a failed probe, not a wall.
    probed, decisions = _feed(state, [(30, _signal("blocked_redirect"))])
    assert decisions[-1].action is A.PAUSE and probed.day.probe_failures == 1 and not breaker.halted(probed)


def test_signals_agree_with_tr02_fetch_statuses():
    """The seam with TR-02 (batch 1a): the same ten statuses, the same limit and retry classes. It skips until
    trends/source.py is on the branch, and runs from the integration merge on."""
    if importlib.util.find_spec("ggwork_pick.observe.trends.source") is None:
        pytest.skip("TR-02's trends/source.py is not on this branch; the seam is checked after integration")
    source = importlib.import_module("ggwork_pick.observe.trends.source")
    assert set(breaker.FETCH_STATUS_SIGNALS) == {status.value for status in source.FetchStatus}
    by_signal = {signal: {name for name in FETCH_STATUSES if _signal(name) is signal} for signal in S}
    assert by_signal[S.RATE_LIMITED] | by_signal[S.LIMITED] == {status.value for status in source.LIMIT_SIGNALS}
    assert by_signal[S.TRANSIENT] == {status.value for status in source.RETRYABLE}
    assert {status.value for status in source.JUDGEABLE} <= by_signal[S.SUCCESS]
    assert by_signal[S.WALL] == set()  # a wall needs captcha_or_consent, which TR-02 reports beside the status


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
    """Each pause of the day is one rung higher: 30, 60, 120 through failed probes, and a later trip carries on to 240.

    Design 4.3 asks for both "a failed probe pauses 60, 120, then 240 minutes" and "three failed probes put the day out",
    so failed probes alone never reach 240: the third ends the day first. The reading here (to be confirmed by the user
    at G2): the rung never goes down within a target date; failed probes count in a row and a good probe clears the
    count; so 240 comes only from a new trip after a good probe, which carries on from the rung reached instead of
    starting again at 30. Within one night's window (at most 315 minutes) counting failed probes in a row or per day
    makes no difference."""
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
    "429_x5_on_a_trip": (
        [(0, S.RATE_LIMITED), (30, S.RATE_LIMITED), (90, S.RATE_LIMITED), (210, S.SUCCESS), (215, S.RATE_LIMITED), (455, S.SUCCESS), (460, S.RATE_LIMITED)],
        "rate_limited",  # also the third trip: the 429 count is checked first
    ),
    "captcha_or_consent": ([(0, S.WALL)], "wall"),
    "sorry_page": ([(0, breaker.signal_of("blocked_redirect", captcha_or_consent=True))], "wall"),
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
    for signal in (_signal("server_error"), _signal("timeout")):
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
    """The count includes a request before it leaves; a timeout or an unknown result is never given back.

    The order (reserve, then send) is asserted here on the reference driver only; the executor's own wiring is pinned
    by TR-13 test_budget_not_refunded and TR-14's transport-level counts. What this module can promise, and
    test_no_budget_call_lowers_reserved checks by behaviour, is that none of its calls ever gives a reservation back."""
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


_BUDGET_SAMPLES = (
    budget.BudgetDay(TARGET),
    budget.BudgetDay(TARGET, reserved=41),
    budget.note_limit(budget.BudgetDay(TARGET, reserved=41), ordinal=7, at=EVENING),
)
# Every public call that hands back a BudgetDay, with sample calls. A new one (say an "unreserve" or a "settle") fails
# the coverage check below until it is listed here, where the monotonic check then catches a refund by what it does,
# whatever it is called.
_BUDGET_CALLS = {
    "for_target_date": lambda day: [budget.for_target_date(day, day.target_date + timedelta(days=n)) for n in (-1, 0, 1)],
    "reserve": lambda day: [budget.reserve(day, limits) for limits in budget.MODES.values()],
    "note_limit": lambda day: [budget.note_limit(day, ordinal=day.reserved, at=EVENING)] if day.reserved else [],
}
_BUDGET_DECODERS = {"from_dict"}  # builds a day from a stored row; there is no earlier day to compare with


def _returns_budget_day(function):
    return typing.get_type_hints(function).get("return") is budget.BudgetDay


def test_no_budget_call_lowers_reserved():
    public = {name: fn for name, fn in inspect.getmembers(budget, inspect.isfunction) if fn.__module__ == budget.__name__ and not name.startswith("_")}
    assert all("return" in typing.get_type_hints(fn) for fn in public.values()), "every public budget call declares what it returns"
    methods = {name for name, fn in inspect.getmembers(budget.BudgetDay, callable) if not name.startswith("_") and _returns_budget_day(fn)}
    assert {name for name, fn in public.items() if _returns_budget_day(fn)} | methods == set(_BUDGET_CALLS) | _BUDGET_DECODERS
    for name, call in _BUDGET_CALLS.items():
        for day in _BUDGET_SAMPLES:
            for out in call(day):
                assert out.target_date != day.target_date or out.reserved >= day.reserved, f"{name} gave a reservation back"


def test_reserve_stops_at_the_cap():
    limits = budget.mode_limits("canary1")
    day = budget.BudgetDay(TARGET, reserved=219)
    last = budget.reserve(day, limits)
    assert last.reserved == 220 and budget.remaining(last, limits) == 0
    with pytest.raises(budget.BudgetExhausted):
        budget.reserve(last, limits)
    assert day.reserved == 219  # nothing is changed in place


AFTER_MIDNIGHT = datetime(2026, 9, 26, 1, 0, tzinfo=UTC)


def _stop(reserved, limits, need=2):
    day = budget.BudgetDay(TARGET, reserved=reserved)
    return budget.stop_reason(breaker_state=breaker.initial_state(TARGET), day=day, limits=limits, now=AFTER_MIDNIGHT, need=need)


def test_canary2_cap_is_the_ceiling_not_the_plan():
    """Section 9: the cap is the hard ceiling after breaker pauses and retries, not the plan. canary2 plans about 430 and
    stops at 600; stopping at the plan would cut the units a pause or a retry pushed past 430, against its 95 % freshness
    with one trip allowed."""
    limits = budget.mode_limits("canary2")
    at_plan = budget.BudgetDay(TARGET, reserved=limits.plan)
    assert budget.reserve(at_plan, limits).reserved == 431
    assert budget.remaining(at_plan, limits) == 170
    assert _stop(430, limits) is None and _stop(598, limits) is None
    assert _stop(599, limits) == budget.TRUNCATED
    with pytest.raises(budget.BudgetExhausted):
        budget.reserve(budget.BudgetDay(TARGET, reserved=600), limits)
    # Halved after two extinguished days in a row: 300 and 300.
    halved = limits.halved()
    assert (halved.plan, halved.cap) == (300, 300)
    assert budget.reserve(budget.BudgetDay(TARGET, reserved=299), halved).reserved == 300
    assert budget.remaining(budget.BudgetDay(TARGET), halved) == 300 and _stop(299, halved) == budget.TRUNCATED
    with pytest.raises(budget.BudgetExhausted):
        budget.reserve(budget.BudgetDay(TARGET, reserved=300), halved)


def test_canary2_plan_survives_a_trip_and_a_retry():
    """The whole canary2 plan, 215 units of 2, with two 5xx retries and one late 429 (a 30-minute pause, its probe, the
    rest at half speed): more than 430 requests go out, every unit but the tripped one is covered, nothing is truncated.
    (The 429 comes late on purpose: half speed for the rest of the night after an early one runs into the deadline,
    which is the window's question, not the budget's.)"""
    limits = budget.mode_limits("canary2")
    signals = {20: S.TRANSIENT, 60: S.TRANSIENT, 401: S.RATE_LIMITED}
    result = sim.run(clock=ManualClock(EVENING), rng=random_source(12), responder=sim.script(signals), units=[2] * 215, limits=limits)
    tripped = result.sent[400].unit  # ordinal 401
    assert result.uncovered == ((tripped, budget.SKIPPED_BREAKER),)
    assert len(result.covered) == 214
    assert result.budget.reserved == len(result.sent) > limits.plan
    assert result.sent[-1].done_at < limits.window(TARGET)[1]


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


def test_budget_running_out_mid_unit_truncates():
    """A retry (or a probe) can spend the budget's last request half-way through a unit that fitted when it started:
    that unit and every one after it are truncated, and nothing is sent past the cap."""
    limits = budget.mode_limits("canary1")
    result = sim.run(
        clock=ManualClock(EVENING),
        rng=random_source(2),
        responder=sim.script({1: S.TRANSIENT}),
        units=[2, 2, 2],
        budget_day=budget.BudgetDay(TARGET, reserved=218),
        limits=limits,
    )
    assert [s.signal for s in result.sent] == [S.TRANSIENT, S.SUCCESS]  # the first request and its retry
    assert result.budget.reserved == limits.cap
    assert result.covered == ()
    assert result.uncovered == tuple((unit, budget.TRUNCATED) for unit in range(3))


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


@pytest.mark.parametrize(("plan", "cap"), [(0, 10), (11, 10), (-1, 5), (10.0, 10)])
def test_mode_limits_refuse_bad_plan_or_cap(plan, cap):
    with pytest.raises(ValueError):
        budget.ModeLimits("x", start=time(20, 0), plan=plan, cap=cap)


@pytest.mark.parametrize(
    "fields",
    [
        {"start": time(20, 0), "deadline": time(2, 30)},  # past the publication
        {"start": "20:00"},
        {"start": time(20, 0, tzinfo=UTC)},  # times are plain UTC times of day
    ],
)
def test_mode_limits_refuse_bad_times(fields):
    with pytest.raises(ValueError):
        budget.ModeLimits("x", plan=10, cap=10, **{"start": time(20, 0), **fields})


def test_budget_calls_refuse_mismatches():
    day = budget.BudgetDay(TARGET, reserved=3)
    for ordinal in (0, 4, True):
        with pytest.raises(ValueError):
            budget.note_limit(day, ordinal=ordinal, at=EVENING)
    with pytest.raises(ValueError):
        budget.note_limit(day, ordinal=3, at=EVENING.replace(tzinfo=None))
    other = breaker.initial_state(TARGET + timedelta(days=1))
    with pytest.raises(ValueError):
        budget.stop_reason(breaker_state=other, day=day, limits=budget.mode_limits("stable"), now=EVENING)
    assert budget.for_target_date(day, TARGET - timedelta(days=1)) == day  # a clock stepped back reopens nothing
    assert budget.mode_limits("canary1").halved() == budget.ModeLimits("canary1", start=time(22, 0), plan=110, cap=110)


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
        lambda d: {**d, "extinguished_days": ["2026-09-26", "2026-09-26"]},
        lambda d: {**d, "extinguished_days": [None]},
        lambda d: {**d, "extinguished_days": "2026-09-26"},
        lambda d: {**d, "day": {**d["day"], "target_date": None}},
        lambda d: {**d, "day": {**d["day"], "retry_at": "yesterday"}},
        lambda d: {**d, "day": {**d["day"], "retry_at": 1758837600}},
        lambda d: {**d, "day": ["not", "a", "mapping"]},
        lambda d: {**d, "disabled_on": "2026-W39-5"},  # an ISO week date is ten characters too
        lambda d: {**d, "disabled_on": "2026-02-30"},  # the right shape, not a day
        lambda d: {**d, "day": {**d["day"], "target_date": "2026-W39-6"}},
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
        lambda d: {**d, "target_date": "2026-W39-6"},
    ],
)
def test_budget_from_dict_refuses_bad_input(mutate):
    with pytest.raises(ValueError):
        budget.BudgetDay.from_dict(mutate(budget.BudgetDay(TARGET, reserved=3).to_dict()))


NAIVE = datetime(2026, 9, 25, 22, 0)
SHANGHAI = timezone(timedelta(hours=8))


@pytest.mark.parametrize(
    "build",
    [
        lambda: breaker.BreakerDay(TARGET, paused_until=NAIVE),
        lambda: breaker.BreakerDay(TARGET, retry_at=NAIVE),
        lambda: replace(breaker.initial_state(TARGET).day, paused_until=NAIVE),
        lambda: budget.BudgetDay(TARGET, reserved=1, first_limit_at=NAIVE, before_first_limit=0),
        lambda: codec.encode_instant(NAIVE),
    ],
)
def test_naive_times_are_refused_when_state_is_built(build):
    """A naive datetime means whatever the host's zone says: written from a Shanghai laptop, 22:00 would be stored as
    14:00Z. States refuse one when they are built, and the encoder refuses one too (TR-13 and TR-14 build states)."""
    with pytest.raises(ValueError):
        build()


def test_aware_times_in_any_zone_are_stored_as_utc():
    day = breaker.BreakerDay(TARGET, paused_until=datetime(2026, 9, 26, 6, 0, tzinfo=SHANGHAI))
    assert day.to_dict()["paused_until"] == "2026-09-25T22:00:00.000000+00:00"
    assert breaker.BreakerDay.from_dict(json.loads(json.dumps(day.to_dict()))) == day
    assert codec.decode_day("2026-09-26", "d") == TARGET


def test_driver_counts_match():
    """The reference wiring: one reservation per HTTP request, probes and retries included."""
    clock = ManualClock(EVENING)
    limits = budget.mode_limits("canary1")
    signals = {3: S.TRANSIENT, 8: S.RATE_LIMITED, 9: S.LIMITED}
    result = sim.run(clock=clock, rng=random_source(6), responder=sim.script(signals), units=[2] * 30, limits=limits)
    assert result.budget.reserved == len(result.sent)
    assert [s.probe for s in result.sent].count(True) == 2
    assert sum(1 for d in result.decisions if d.action is A.RETRY) == 1
