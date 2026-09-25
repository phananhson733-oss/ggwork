"""TR-10: eval-rules-v1, alert dedupe and the four lead-time metrics (design 6.2; plan D33, D37; counterexamples 13, 20)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ggwork_pick.observe import versions
from ggwork_pick.observe.contract_rows import AlertRow, dedupe_key_of
from ggwork_pick.observe.leadtime import (
    BASELINE_EVENT,
    EVAL_RULES,
    FOLLOW_UP_EVENTS,
    MILESTONE_EVENTS,
    POOL_ENTRY,
    POOL_ENTRY_AMBIGUOUS,
    Discovery,
    FollowUp,
    PoolEntry,
    dedupe,
    discovery_metrics,
    eval_rules,
    leadtime_metrics,
    root_identity,
    verdict,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "obs_contract"
BASE = json.loads((FIXTURES / "alert_rows.json").read_text(encoding="utf-8"))["valid"][0]["value"]
START = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)
V1 = "eval-rules-v1"
DAY_MINUTES = 24 * 60


def _stamp(day: float) -> str:
    return (START + timedelta(days=day)).isoformat(timespec="microseconds")


def _alert(alert_id: int, day: float, root: str = "A", *, identity: str | None = None, mode: str = "live", channel: str = "trends", **fields) -> AlertRow:
    state = fields.pop("state", "rising_first" if channel == "trends" else "from_zero")
    scope = fields.pop("scope", "US" if channel == "trends" else "USA")
    return AlertRow.model_validate(
        {
            **BASE, "id": alert_id, "mode": mode, "channel": channel, "state": state, "scope": scope, "identity": identity or root,
            "root_identity": root, "dedupe_key": dedupe_key_of(root, channel, state, scope, mode), "published_at": _stamp(day),
            "created_at": _stamp(day), **fields,
        }
    )  # fmt: skip


def _follow(root: str, event: str, day: float) -> FollowUp:
    return FollowUp(root_identity=root, event=event, at=_stamp(day))


def _metrics(alerts, follow_ups=(), *, now_day: float = 40, since_day: float = 0, **options):
    return leadtime_metrics(
        tuple(alerts), tuple(follow_ups), now=START + timedelta(days=now_day), since=START + timedelta(days=since_day), version=V1, **options
    )


def _only(groups):
    (group,) = groups
    return group


# ---- the denominator (counterexamples 13 and 20) --------------------------------------------------------------------


def test_leadtime_denominator():
    """Every alert 14 days old is in the denominator, fulfilled or not; younger ones are listed as observing, never as
    unfulfilled; an event already there before the alert counts as a lag (negative, the earliest one)."""
    alerts = [
        _alert(1, 0, "A"),  # nothing follows: unfulfilled
        _alert(2, 1, "B"),  # candidate pool three days later: a lead
        _alert(3, 2, "C"),  # chart evidence three days before, and more after: a lag
        _alert(4, 3, "D"),  # the only event comes 15 days later: outside the window, unfulfilled
        _alert(5, 4, "E"),  # the only event came 20 days before: outside the window, unfulfilled
        _alert(6, 30, "F"),  # ten days old with an event already: still observing
        _alert(7, 35, "G"),  # five days old, nothing yet: observing, not unfulfilled
    ]
    follow_ups = [
        _follow("B", "candidate_pool", 4),
        _follow("C", "chart_first", -1),
        _follow("C", "gsc_rising", 1),
        _follow("C", "candidate_pool", 5),
        _follow("D", "chart_first", 18),
        _follow("E", "chart_first", -16),
        _follow("F", "chart_first", 31),
    ]
    group = _only(_metrics(alerts, follow_ups))
    assert (group.channel, group.state) == ("trends", "rising_first")
    assert (group.alerts, group.matured, group.observing) == (7, 5, 2)
    assert (group.lead, group.lag, group.unfulfilled) == (1, 1, 3)
    assert group.unfulfilled_rate == pytest.approx(3 / 5)
    assert group.median_minutes == 0
    outcomes = {outcome.alert_id: (outcome.kind, outcome.minutes, outcome.event) for outcome in group.outcomes}
    assert outcomes == {
        1: ("unfulfilled", None, None),
        2: ("lead", 3 * DAY_MINUTES, "candidate_pool"),
        3: ("lag", -3 * DAY_MINUTES, "chart_first"),
        4: ("unfulfilled", None, None),
        5: ("unfulfilled", None, None),
        6: ("observing", None, None),
        7: ("observing", None, None),
    }


def test_many_firsts_without_follow_ups_are_all_unfulfilled():
    """Counterexample 13: a flood of first alerts that never lead anywhere shows as a high unfulfilled rate."""
    alerts = [_alert(index, index * 0.1, f"R{index}") for index in range(1, 11)] + [_alert(11, 0.5, "S")]
    group = _only(_metrics(alerts, [_follow("S", "candidate_pool", 1)]))
    assert (group.matured, group.unfulfilled, group.lead) == (11, 10, 1)
    assert group.unfulfilled_rate == pytest.approx(10 / 11)


def test_window_edges_are_inclusive():
    alerts = [_alert(1, 0, "A"), _alert(2, 0, "B"), _alert(3, 0, "C")]
    follow_ups = [_follow("A", "chart_first", 14), _follow("B", "chart_first", -14), _follow("C", "chart_first", 0)]
    outcomes = {o.alert_id: (o.kind, o.minutes) for o in _only(_metrics(alerts, follow_ups)).outcomes}
    assert outcomes == {1: ("lead", 14 * DAY_MINUTES), 2: ("lag", -14 * DAY_MINUTES), 3: ("lead", 0)}
    # Exactly 14 days old is matured ("满 14 天"); a microsecond less is still observing.
    assert _only(_metrics([_alert(1, 0, "A")], now_day=14)).matured == 1
    assert _only(_metrics([_alert(1, 0, "A")], now_day=14 - 1e-9)).observing == 1


# ---- dedupe (counterexample 20) and alias continuity ----------------------------------------------------------------


def test_alert_dedupe_14d():
    """The same key within 14 days of the recorded alert is recorded once; the window restarts from the recorded one."""
    same = [_alert(index, day, "A") for index, day in enumerate((0, 5, 13.99, 14, 20, 28), start=1)]
    other_scope = _alert(9, 5, "A", scope="GB")
    kept = dedupe((*same, other_scope), version=V1)
    assert [alert.id for alert in kept] == [1, 9, 4, 6]
    # Already recorded alerts count too: one three days before blocks day 5, and day 11 is 14 days after it.
    recorded = (_alert(20, -3, "A"),)
    assert [alert.id for alert in dedupe((_alert(21, 5, "A"), _alert(22, 11, "A")), recorded, version=V1)] == [22]
    # The metrics dedupe as well, so a repeated row is never counted twice.
    assert _only(_metrics([_alert(1, 0, "A"), _alert(2, 2, "A")])).alerts == 1


def test_dedupe_survives_alias_change():
    """D37: the key follows the frozen alias version back to the earliest identity, so a renamed identity keeps its window."""
    predecessors = {"Y": "X", "Z": "Y"}
    assert root_identity("Z", predecessors) == "X" and root_identity("Y", predecessors) == "X" and root_identity("Q", predecessors) == "Q"
    before = _alert(1, 0, root_identity("X", predecessors), identity="X")
    after = _alert(2, 3, root_identity("Y", predecessors), identity="Y")
    assert [alert.id for alert in dedupe((before, after), version=V1)] == [1]
    assert _only(_metrics([before, after])).alerts == 1


def test_root_identity_refuses_a_cycle():
    with pytest.raises(ValueError, match="环"):
        root_identity("X", {"X": "Y", "Y": "X"})


def test_shadow_alerts_excluded():
    """D37: a shadow alert neither enters the metrics nor takes a live alert's dedupe window."""
    shadow = _alert(1, 0, "A", mode="shadow")
    live = _alert(2, 2, "A")
    assert [alert.id for alert in dedupe((shadow, live), version=V1)] == [1, 2]
    group = _only(_metrics([shadow, live]))
    assert (group.alerts, group.matured) == (1, 1)
    assert [outcome.alert_id for outcome in group.outcomes] == [2]
    assert _metrics([shadow]) == ()


# ---- conclusions -----------------------------------------------------------------------------------------------------


def test_leadtime_min_30():
    """Design 6.2: fewer than 30 matured alerts, or less than four weeks since the switch opened, is no conclusion."""
    unfulfilled = [_alert(index, index * 0.01, f"R{index}") for index in range(1, 31)]
    group29 = _only(_metrics(unfulfilled[:29]))
    assert verdict(group29, now=START + timedelta(days=40), since=START, unfulfilled_cap=0.5, version=V1) == "observing"
    group30 = _only(_metrics(unfulfilled))
    assert verdict(group30, now=START + timedelta(days=40), since=START, unfulfilled_cap=0.5, version=V1) == "demote"
    assert verdict(group30, now=START + timedelta(days=27), since=START, unfulfilled_cap=0.5, version=V1) == "observing"
    leading = _only(_metrics(unfulfilled, [_follow(f"R{index}", "chart_first", 2) for index in range(1, 31)]))
    assert verdict(leading, now=START + timedelta(days=40), since=START, unfulfilled_cap=0.5, version=V1) == "keep"
    lagging = _only(_metrics(unfulfilled, [_follow(f"R{index}", "chart_first", -1) for index in range(1, 31)]))
    assert lagging.median_minutes < 0
    assert verdict(lagging, now=START + timedelta(days=40), since=START, unfulfilled_cap=0.5, version=V1) == "demote"


def test_leadtime_min_30_counts_matured_alerts_only():
    """The 30 are matured alerts: 35 alerts of which 20 are 14 days old and 15 still observing is no conclusion, even
    though every matured one is unfulfilled; once 30 have matured the same group is judged."""
    matured = [_alert(index, index * 0.01, f"R{index}") for index in range(1, 21)]
    observing = [_alert(index, 30 + index * 0.01, f"R{index}") for index in range(21, 36)]
    group = _only(_metrics(matured + observing))
    assert (group.alerts, group.matured, group.observing, group.unfulfilled_rate) == (35, 20, 15, 1.0)
    assert verdict(group, now=START + timedelta(days=40), since=START, unfulfilled_cap=0.5, version=V1) == "observing"
    later = _only(_metrics(matured + observing, now_day=45))
    assert (later.matured, later.observing) == (35, 0)
    assert verdict(later, now=START + timedelta(days=45), since=START, unfulfilled_cap=0.5, version=V1) == "demote"


def test_first_and_confirmed_counted_apart():
    alerts = [_alert(1, 0, "A"), _alert(2, 1, "A", state="rising_confirmed"), _alert(3, 0, "B", channel="gsc")]
    groups = _metrics(alerts)
    assert [(g.channel, g.state, g.alerts) for g in groups] == [("trends", "rising_first", 1), ("trends", "rising_confirmed", 1), ("gsc", "from_zero", 1)]


def test_own_channel_event_is_not_a_follow_up():
    """A GSC alert is not fulfilled by the GSC crossing it announces; a Trends alert is."""
    gsc_alert = _alert(1, 0, "A", channel="gsc")
    trends_alert = _alert(2, 0, "A")
    follow_ups = [_follow("A", "gsc_from_zero", 0.5)]
    (trends, gsc) = _metrics([gsc_alert, trends_alert], follow_ups)
    assert (trends.lead, gsc.unfulfilled) == (1, 1)
    assert _only(_metrics([gsc_alert], [*follow_ups, _follow("A", "candidate_pool", 2)])).lead == 1


def test_only_listed_events_count():
    """The baseline row written on launch day and anything eval-rules-v1 does not list never fulfil an alert."""
    follow_ups = [_follow("A", BASELINE_EVENT, 1), _follow("A", POOL_ENTRY, 1), _follow("A", "promoters_up", 1), _follow("B", "chart_first", 1)]
    assert _only(_metrics([_alert(1, 0, "A")], follow_ups)).unfulfilled == 1
    assert BASELINE_EVENT not in FOLLOW_UP_EVENTS and POOL_ENTRY not in FOLLOW_UP_EVENTS


def test_eval_rules_v1_events():
    """Design 6.2's list, with D33's editorial event; changing it means a new eval-rules version."""
    rules = eval_rules(V1)
    assert [kind.code for kind in rules.events] == list(FOLLOW_UP_EVENTS)
    assert FOLLOW_UP_EVENTS == ("candidate_pool", "chart_first", "gsc_from_zero", "gsc_rising", "promoters_rise", "editorial_add")
    assert {kind.code: kind.channel for kind in rules.events if kind.channel} == {"gsc_from_zero": "gsc", "gsc_rising": "gsc"}
    assert (rules.window, rules.dedupe_window, rules.min_alerts, rules.review_after) == (
        timedelta(days=14), timedelta(days=14), 30, timedelta(weeks=4),
    )  # fmt: skip
    assert versions.EVAL_RULES_VERSION in EVAL_RULES
    # The milestone writers (TR-20, TR-23b) import every code from here: the follow-ups, the baseline, the entry events.
    assert MILESTONE_EVENTS == (*FOLLOW_UP_EVENTS, BASELINE_EVENT, POOL_ENTRY, POOL_ENTRY_AMBIGUOUS)
    assert len(set(MILESTONE_EVENTS)) == len(MILESTONE_EVENTS)
    with pytest.raises(LookupError, match="eval-rules-v9"):
        eval_rules("eval-rules-v9")


def test_metrics_refuse_another_version():
    alert = _alert(1, 0, "A", eval_rules_version="eval-rules-v2")
    with pytest.raises(ValueError, match="eval-rules-v2"):
        _metrics([alert])


def test_since_and_ignored():
    """Alerts before the switch opened are left out; the ignored rate is over every alert of the group."""
    alerts = [_alert(1, -2, "A"), _alert(2, 0, "B"), _alert(3, 1, "C"), _alert(4, 30, "D")]
    group = _only(_metrics(alerts, irrelevant_ids=frozenset({3, 4, 99})))
    assert (group.alerts, group.ignored) == (3, 2)
    assert group.ignored_rate == pytest.approx(2 / 3)
    empty = _only(_metrics([_alert(1, 35, "A")]))
    assert (empty.matured, empty.unfulfilled_rate, empty.median_minutes) == (0, None, None)


def test_naive_moments_refused():
    with pytest.raises(ValueError, match="时区"):
        leadtime_metrics((), (), now=datetime(2026, 11, 1), since=START, version=V1)


# ---- out-of-pool discoveries (D37) ----------------------------------------------------------------------------------


def test_pool_entry_ambiguous_counted_separately():
    """An out-of-pool title matching several pool rows is listed apart and never counts as entered."""
    discoveries = [
        Discovery("en", "stay", _stamp(0)),
        Discovery("en", "stay", _stamp(1)),  # the same key seen again later: the first sighting counts
        Discovery("en", "the lost heiress", _stamp(1)),
        Discovery("es", "la heredera", _stamp(2)),
        Discovery("en", "moon bride", _stamp(3)),
    ]
    entries = [
        PoolEntry("en", "stay", POOL_ENTRY, _stamp(3)),
        PoolEntry("en", "the lost heiress", POOL_ENTRY_AMBIGUOUS, _stamp(2)),
        PoolEntry("es", "la heredera", POOL_ENTRY_AMBIGUOUS, _stamp(3)),
        PoolEntry("es", "la heredera", POOL_ENTRY, _stamp(6)),
        PoolEntry("en", "moon bride", POOL_ENTRY, _stamp(2)),  # before it was discovered: not an entry after discovery
    ]
    result = discovery_metrics(tuple(discoveries), tuple(entries))
    assert (result.total, result.entered, result.ambiguous, result.pending) == (4, 2, 1, 1)
    assert result.entered_rate == pytest.approx(2 / 4)
    assert result.delays_minutes == (3 * DAY_MINUTES, 4 * DAY_MINUTES)
    assert result.median_minutes == pytest.approx(3.5 * DAY_MINUTES)
    assert discovery_metrics((), ()).entered_rate is None


def test_inputs_checked():
    group = _only(_metrics([_alert(1, 0, "A")]))
    for cap in (-0.1, 1.5):
        with pytest.raises(ValueError, match="unfulfilled_cap"):
            verdict(group, now=START + timedelta(days=40), since=START, unfulfilled_cap=cap, version=V1)
    with pytest.raises(ValueError, match="入池事件"):
        PoolEntry("en", "stay", "entered", _stamp(0))
