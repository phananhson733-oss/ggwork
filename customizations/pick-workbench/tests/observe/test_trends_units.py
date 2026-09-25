"""TR-14: the session's pure parts: settings and modes, the task list and its truncation order, the canary's control
list and units, the window end, the weekly contract check and the summary's codes (plan TR-14, section 9; design 4.5,
4.9, 4.11). The database-backed session is test_trends_run.py; the executor's wiring test_trends_wiring.py."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import pytest

from ggwork_pick.observe.errors import ExitCode, Refused
from ggwork_pick.observe.trends import breaker, budget
from ggwork_pick.observe.trends import session_summary as summary
from ggwork_pick.observe.trends.canary import (
    CONTROLS_FORMAT,
    DEFAULT_CONTROLS_PATH,
    PRIORITY_CONTROL,
    PRIORITY_FIRST_ROUND_A,
    PRIORITY_FIRST_ROUND_B,
    PRIORITY_MARKET,
    CanaryTaskSource,
    euro_american_geos,
    load_controls,
    missing_market_geos,
    parse_controls,
    with_related,
)
from ggwork_pick.observe.trends.contract_check import contract_check_due, contract_check_units, contract_check_verdict
from ggwork_pick.observe.trends.run import day_of, window_end_of
from ggwork_pick.observe.trends.settings import settings_from
from ggwork_pick.observe.trends.units import QueryUnit, SessionPlan, TaskList, cut_to_plan, ordered, plan_budget, rotation, unit_key

TARGET = date(2026, 9, 26)


def _unit(name: str, *, priority: int = 3, listed_at: date | None = None, evidence_on: date | None = None, related: bool = False, item="title"):
    identity = json.dumps(["realshort-pick", name, "en"])
    return QueryUnit(
        key=unit_key("drama", identity, "US", "H"),
        item=item,
        geo="US",
        terms=(name,),
        bare=name,
        granularity="H",
        priority=priority,
        related=related,
        identity=identity,
        listed_at=listed_at,
        evidence_on=evidence_on,
    )


# ---- modes and settings (section 9) -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode, start, plan, cap",
    [("canary1", time(22, 0), 220, 220), ("canary2", time(22, 0), 430, 600), ("stable", time(20, 30), 650, 800)],
)
def test_mode_parameters(mode, start, plan, cap):
    """Section 9's table: every mode stops at 01:45; its plan fits the window with a breaker's margin."""
    settings = settings_from({"PICK_OBS_TRENDS_MODE": mode})
    limits = settings.limits
    assert (limits.start, limits.plan, limits.cap, limits.deadline) == (start, plan, cap, time(1, 45))
    window_start, deadline = limits.window(TARGET)
    assert deadline == datetime(2026, 9, 26, 1, 45, tzinfo=UTC)
    assert window_start == datetime.combine(TARGET - timedelta(days=1), start, UTC)
    assert limits.plan / 2.9 + 40 <= limits.window_minutes()


def test_settings_defaults_and_refusals():
    settings = settings_from({"PICK_OBS_TRENDS_MODE": "canary2"})
    assert (settings.granularity, settings.route, settings.related, settings.contract_check, settings.publish_live) == ("H", "both", True, False, False)
    assert settings.canary and settings.batch_mode == "shadow"
    assert settings_from({"PICK_OBS_TRENDS_MODE": "canary1", "PICK_OBS_PUBLISH": "1"}).batch_mode == "shadow"  # a canary never publishes
    assert settings_from({"PICK_OBS_TRENDS_MODE": "stable", "PICK_OBS_PUBLISH": "1"}).batch_mode == "live"
    assert settings_from({"PICK_OBS_TRENDS_MODE": "stable", "PICK_OBS_TRENDS_ROUTE": "a_only"}).related is False
    assert settings_from({"PICK_OBS_TRENDS_MODE": "stable", "PICK_OBS_TRENDS_GRANULARITY": "HD"}).granularities == ("H", "D")
    assert settings_from({"PICK_OBS_TRENDS_MODE": "canary1", "PICK_OBS_CONTRACT_CHECK": "1"}).contract_check
    for env in (
        {},
        {"PICK_OBS_TRENDS_MODE": "canary3"},
        {"PICK_OBS_TRENDS_MODE": "stable", "PICK_OBS_TRENDS_ROUTE": "neither"},
        {"PICK_OBS_TRENDS_MODE": "stable", "PICK_OBS_TRENDS_GRANULARITY": "W"},
        {"PICK_OBS_TRENDS_MODE": "stable", "PICK_OBS_CANARY_SINCE": "2026-W39-1"},
    ):
        with pytest.raises(Refused) as refused:
            settings_from(env)
        assert refused.value.exit_code == ExitCode.REFUSED
        assert "canary3" not in str(refused.value) and "W39" not in str(refused.value)  # never echoed


@pytest.mark.parametrize(
    "mode, now, idle",
    [
        ("canary1", datetime(2026, 9, 25, 21, 59, tzinfo=UTC), True),
        ("canary1", datetime(2026, 9, 25, 22, 0, tzinfo=UTC), False),
        ("stable", datetime(2026, 9, 25, 20, 0, tzinfo=UTC), True),  # TR-15's 20:00 trigger
        ("stable", datetime(2026, 9, 25, 20, 30, tzinfo=UTC), False),
        ("stable", datetime(2026, 9, 26, 1, 44, tzinfo=UTC), False),
        ("stable", datetime(2026, 9, 26, 1, 45, tzinfo=UTC), True),
        ("stable", datetime(2026, 9, 26, 1, 59, tzinfo=UTC), True),
        ("stable", datetime(2026, 9, 26, 2, 0, tzinfo=UTC), True),  # the next target date, long before its start
    ],
)
def test_trigger_times(mode, now, idle):
    day, reason = day_of(settings_from({"PICK_OBS_TRENDS_MODE": mode}), now)
    assert (reason is not None) == idle
    assert day.target_date == (TARGET if now.hour < 2 or now.day == 25 else TARGET + timedelta(days=1))


def test_window_end_is_the_creation_hour_less_three():
    """Design 4.9 and 6.3: a canary batch created at 22:10 ends its window at 19:00, a stable one at 20:30 at 17:00."""
    assert window_end_of(datetime(2026, 9, 25, 22, 10, tzinfo=UTC)) == datetime(2026, 9, 25, 19, 0, tzinfo=UTC)
    assert window_end_of(datetime(2026, 9, 25, 20, 30, 59, tzinfo=UTC)) == datetime(2026, 9, 25, 17, 0, tzinfo=UTC)
    assert window_end_of(datetime(2026, 9, 26, 0, 20, tzinfo=UTC)) == datetime(2026, 9, 25, 21, 0, tzinfo=UTC)


# ---- the task list (design 4.5) -----------------------------------------------------------------------------------


def test_truncation_order():
    """Rule priority, then listed_at newest first, then the latest evidence date, then the daily identity rotation."""
    market = _unit("market series", priority=1, item="market")
    older = _unit("older", listed_at=date(2026, 9, 20))
    newer = _unit("newer", listed_at=date(2026, 9, 24))
    undated = _unit("undated")
    tie = _unit("tie", listed_at=date(2026, 9, 20))
    fresh_evidence = _unit("fresh evidence", listed_at=date(2026, 9, 20), evidence_on=date(2026, 9, 25))
    stale_evidence = _unit("stale evidence", listed_at=date(2026, 9, 20), evidence_on=date(2026, 9, 1))
    control = _unit("control", priority=2, listed_at=date(2020, 1, 1))
    got = ordered([older, undated, tie, stale_evidence, newer, control, fresh_evidence, market], TARGET)
    names = [unit.terms[0] for unit in got]
    assert names[:5] == ["market series", "control", "newer", "fresh evidence", "stale evidence"]  # any evidence first
    assert names[5:7] == sorted(["tie", "older"], key=lambda name: rotation(_unit(name, listed_at=date(2026, 9, 20)), TARGET))
    assert names[-1] == "undated"  # no listed_at sorts after every date


def test_identity_rotation_changes_daily():
    """The last tie-break rotates the ties from one target date to the next."""
    ties = [_unit(f"tie {index}", listed_at=date(2026, 9, 20)) for index in range(12)]
    orders = {tuple(unit.key for unit in ordered(ties, TARGET + timedelta(days=day))) for day in range(5)}
    assert len(orders) > 1
    assert ordered(ties, TARGET) == ordered(list(reversed(ties)), TARGET)  # the input order does not matter


def test_duplicate_keys_keep_the_first_in_order():
    control = _unit("same", priority=2)
    title = _unit("same", priority=3)
    assert ordered([title, control], TARGET) == (control,)


def test_cut_keeps_a_prefix():
    """What does not fit is truncated in order; a later, smaller unit never jumps the queue."""
    units = (_unit("a"), _unit("b", related=True), _unit("c"), _unit("d"))
    tasks = cut_to_plan(units, 6)
    assert [unit.terms[0] for unit in tasks.planned] == ["a", "b"]
    assert [unit.terms[0] for unit in tasks.truncated] == ["c", "d"]
    assert tasks.planned_http == 5


def test_plan_budget_keeps_design_allowance():
    """Design 4.5: warm-up, probes and retries are planned out of the day's total (about 15 canary, 20 stable)."""
    assert [plan_budget(budget.mode_limits(mode)) for mode in ("canary1", "canary2", "stable")] == [205, 415, 630]


def test_plan_round_trips():
    tasks = TaskList((_unit("a", related=True),), (_unit("b"),))
    plan = SessionPlan("canary", "H", True, tasks, "cat-1", {"missing_controls": []})
    again = SessionPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert again.tasks == tasks and again.catalog_batch_id == "cat-1"
    with pytest.raises(ValueError):
        SessionPlan.from_dict({**plan.to_dict(), "format": "other"})


# ---- the canary (section 9) ---------------------------------------------------------------------------------------


def _controls_document() -> dict:
    return {
        "format": CONTROLS_FORMAT,
        "note": "test",
        "controls": [{"identity": json.dumps(["realshort-pick", "a", "en"]), "geo": "US", "group": "positive"}],
        "market": [{"geo": "US", "term": "short drama"}],
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda doc: doc.update(format="trends-stage0-controls-v1"),
        lambda doc: doc.pop("market"),
        lambda doc: doc["controls"][0].update(geo="usa"),
        lambda doc: doc["controls"][0].update(identity="not json"),
        lambda doc: doc["controls"][0].update(title="a title has no place here"),
        lambda doc: doc["controls"].append(dict(doc["controls"][0])),
        lambda doc: doc["market"][0].update(term=""),
    ],
)
def test_controls_refused(change):
    document = _controls_document()
    change(document)
    with pytest.raises(ValueError):
        parse_controls(document)


def test_missing_controls_file_refuses_the_canary(tmp_path):
    with pytest.raises(Refused) as refused:
        load_controls(tmp_path / "absent.json")
    assert refused.value.exit_code == ExitCode.REFUSED and "拒绝跑金丝雀" in str(refused.value)
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(Refused):
        load_controls(tmp_path / "bad.json")


def test_packaged_controls_file_when_present():
    """TR-05 owns the file; once it is merged, the canary's default reads it as it is, and it has a market series for
    every geo the canary queries (else the canary refuses to run: test_market_series_for_every_queried_geo)."""
    if not DEFAULT_CONTROLS_PATH.is_file():
        pytest.skip("canary_controls.json arrives with TR-05")
    controls = load_controls()
    assert controls.controls and controls.market
    assert missing_market_geos(controls) == (), "canary_controls.json lacks market series: TR-05 adds the phrases"
    CanaryTaskSource(controls, granularities=("H",), related=True)


# TR-05's canary_controls.json as it stands (b933d5a, 3a78e2f): 32 controls, market series for five geos.
TR05_CONTROL_GEOS = ("US",) * 10 + ("BG", "FR", "DE") * 4 + ("MX", "IT") * 3 + ("PL", "RO", "TW", "BR")
TR05_GROUPS = ("positive",) * 16 + ("regional",) * 12 + ("negative",) * 4
TR05_MARKET_GEOS = ("US", "BG", "DE", "FR", "IT")
CANARY_TITLE_GEOS = ("WW", "US", "ES", "MX", "DE", "FR", "IT", "BR")  # market-map-v1's first round, six languages


def _tr05_shaped(market_geos) -> dict:
    controls = [
        {"identity": json.dumps(["realshort-pick", f"sid{index}", "en"]), "geo": geo, "group": group}
        for index, (geo, group) in enumerate(zip(TR05_CONTROL_GEOS, TR05_GROUPS, strict=True))
    ]
    market = [{"geo": geo, "term": f"short drama {geo}"} for geo in market_geos]
    return {"format": CONTROLS_FORMAT, "note": "test copy of TR-05's shape", "controls": controls, "market": market}


def test_market_series_for_every_queried_geo():
    """[design 4.7, 4.5; plan TR-14] One market series per geo the canary queries: every control's geo and every geo
    the recent titles go to. TR-05's list as it stands has US, BG, DE, FR, IT: the canary refuses (exit 2) and names
    what is missing, rather than running a load the design did not plan and TR-17 and TR-30 cannot read per geo."""
    controls = parse_controls(_tr05_shaped(TR05_MARKET_GEOS))
    assert missing_market_geos(controls) == ("BR", "ES", "MX", "PL", "RO", "TW", "WW")
    with pytest.raises(Refused) as refused:
        CanaryTaskSource(controls, granularities=("H",), related=True)
    assert refused.value.exit_code == ExitCode.REFUSED
    assert "BR、ES、MX、PL、RO、TW、WW" in str(refused.value) and "拒绝跑金丝雀" in str(refused.value)
    complete = parse_controls(_tr05_shaped(dict.fromkeys((*TR05_MARKET_GEOS, *CANARY_TITLE_GEOS, "PL", "RO", "TW"))))
    assert missing_market_geos(complete) == ()
    CanaryTaskSource(complete, granularities=("H",), related=True)


def test_euro_american_geos_follow_market_map():
    """The six languages at their first-round geos: A before B, and never GB or BG (no first-round tier)."""
    geos = euro_american_geos()
    assert set(geos) == {"en", "es", "de", "fr", "it", "pt"}
    assert geos["en"] == (("WW", PRIORITY_FIRST_ROUND_A), ("US", PRIORITY_FIRST_ROUND_A))
    assert dict(geos["es"]) == {"ES": PRIORITY_FIRST_ROUND_B, "MX": PRIORITY_FIRST_ROUND_A}
    assert all(geo not in ("GB", "BG") for pairs in geos.values() for geo, _ in pairs)
    assert PRIORITY_MARKET < PRIORITY_CONTROL < PRIORITY_FIRST_ROUND_A < PRIORITY_FIRST_ROUND_B


def test_related_mixed_every_fourth_drama_and_never_on_a_only():
    market = replace(_unit("market", priority=1, item="market"), identity=None)  # a market series names no drama
    dramas = [_unit(f"d{index}") for index in range(8)]
    mixed = with_related((market, *dramas), related=True)
    assert [unit.related for unit in mixed] == [False, True, False, False, False, True, False, False, False]
    assert not any(unit.related for unit in with_related((market, *dramas), related=False))


# ---- the weekly contract check ------------------------------------------------------------------------------------


def test_contract_check_is_monday_and_off_by_default():
    monday = date(2026, 9, 28)
    assert monday.weekday() == 0
    assert not contract_check_due(monday, enabled=False)
    assert contract_check_due(monday, enabled=True) and not contract_check_due(monday + timedelta(days=1), enabled=True)
    units = contract_check_units()
    assert len(units) == 2 and {unit.granularity for unit in units} == {"H", "D"} and all(unit.priority == 0 for unit in units)
    assert contract_check_verdict(units, {}) is None
    assert contract_check_verdict(units, {units[0].key: "ok", units[1].key: "parse_error"}) == "parse_error"


@pytest.mark.parametrize(
    "first, second, verdict",
    [
        ("ok", "ok_zero", "ok"),
        ("no_data", "ok", "ok"),  # parsed: an empty answer still has the shape
        ("rate_limited", "parse_error", "parse_error"),
        ("rate_limited", "server_error", None),  # no usable answer is no pass
        ("ok", "forbidden", None),
        ("html_body", "timeout", None),
        ("ok_zero", None, None),  # the other check never ran: the shape it reads is unconfirmed
    ],
)
def test_contract_check_passes_only_on_usable_answers(first, second, verdict):
    """ok only when every check unit got a usable answer; parse_error when any failed to parse; otherwise no verdict,
    so a parse_error carried from before stays (session_codes)."""
    units = contract_check_units()
    answered = {unit.key: status for unit, status in zip(units, (first, second), strict=True) if status is not None}
    assert contract_check_verdict(units, answered) == verdict
    judged = summary.Judged(False, 0, frozenset({"parse_error"}), verdict, False, False)
    assert ("parse_error" in summary.session_codes(breaker.initial_state(TARGET), judged)) == (verdict != "ok")


# ---- the summary's codes ------------------------------------------------------------------------------------------


def test_all_zero_rate_and_jump():
    units = [_unit(f"d{index}") for index in range(10)]
    progress = {unit.key: {"bare": "ok_zero" if index < 6 else "ok"} for index, unit in enumerate(units)}
    rate = summary.all_zero_rate(units, progress)
    assert (rate.rate, rate.judged) == (0.6, 10)
    assert summary.all_zero_jumped(rate, 0.3) and not summary.all_zero_jumped(rate, 0.45)
    assert not summary.all_zero_jumped(summary.ZeroRate(0.9, 9), 0.1)  # too few series to say
    assert not summary.all_zero_jumped(rate, None)  # nothing to compare with


def test_usertype_change():
    assert summary.usertype_changed(("A", "B"), None)
    assert summary.usertype_changed(("B",), ("A",))
    assert not summary.usertype_changed(("A",), ("A",))
    assert not summary.usertype_changed(("A",), None) and not summary.usertype_changed((), ("A",))
