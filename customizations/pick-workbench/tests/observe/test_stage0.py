"""TR-05: stage 0 of the Trends channel, its control list, the two-day task lists, the metrics and the gates (plan TR-05,
section 8; design 4.9, 4.11).

Pure functions over synthetic inputs (stage0_fakes): nothing reaches the network or the artifacts directory. The runner
and the report have their own test files.
"""

import json
import math
from pathlib import Path

import pytest
from stage0_fakes import controls_document, daily_values, hourly_values, related_missing, related_ok, result_line, seed_line

from ggwork_pick.observe.trends import stage0, stage0_metrics
from ggwork_pick.observe.trends.stage0 import build_plan, parse_controls
from ggwork_pick.observe.trends.stage0_metrics import (
    choose_granularity,
    gate_a,
    gate_b,
    nonzero_days,
    nonzero_hours,
    parse_manual_csv,
    route_for,
    series_of,
    shape_compare,
    spearman,
    visible,
)

CANARY_CONTROLS = Path(stage0.__file__).with_name("canary_controls.json")


def controls():
    return parse_controls(controls_document())


def control(cid: str) -> dict:
    return next(entry for entry in controls_document()["controls"] if entry["id"] == cid)


# ---- the control list ----------------------------------------------------------------------------------------------


def test_controls_parse_groups():
    parsed = controls()
    assert [len(parsed.group(name)) for name in stage0.GROUPS] == [16, 4, 12, 5, 4]
    assert parsed.by_id()["pos-01"].identity is not None
    assert parsed.by_id()["mkt-us"].identity is None


@pytest.mark.parametrize(
    "change, problem",
    [
        (lambda doc: doc["controls"].append(dict(doc["controls"][0])), "重复"),
        (lambda doc: doc["controls"][0].update(geo="usa"), "geo"),
        (lambda doc: doc["controls"][0].update(group="friends"), "group"),
        (lambda doc: doc["controls"][0].update(kind="generic"), "kind"),
        (lambda doc: doc["controls"][0].pop("identity"), "identity"),
        (lambda doc: doc["controls"][-1].update(identity='["realshort-pick","x","en"]'), "identity"),
        (lambda doc: doc["controls"][0].update(term=""), "term"),
        (lambda doc: doc.update(format="v0"), "format"),
        (lambda doc: doc.update(controls=[c for c in doc["controls"] if c["id"] != "pos-16" and c["id"] != "pos-15"]), "15"),
    ],
)
def test_controls_refused(change, problem):
    doc = controls_document()
    change(doc)
    with pytest.raises(ValueError, match=problem):
        parse_controls(doc)


def test_controls_identity_is_a_workbench_key():
    doc = controls_document()
    doc["controls"][0]["identity"] = json.dumps(["other-source", "abc", "en"])
    with pytest.raises(ValueError, match="identity"):
        parse_controls(doc)


def test_controls_keep_their_provenance():
    """The report quotes where the controls came from (the GSC export's age above all)."""
    assert dict(controls().provenance) == {"gsc_age": "synthetic"}
    with pytest.raises(ValueError, match="provenance"):
        parse_controls({**controls_document(), "provenance": {"gsc_age": 3}})


# ---- the two-day plan ----------------------------------------------------------------------------------------------


def test_budget_respected():
    """Two days of at most 90 HTTP requests each, 180 in all: warm-up, explore, multiline, related and repeats counted."""
    day1, day2 = build_plan(controls())
    for day in (day1, day2):
        assert day.http <= stage0.MAX_HTTP_PER_DAY
        assert day.http == stage0.WARMUPS_PER_DAY + sum(1 + unit.timeline + unit.related for unit in day.units)
    assert day1.http + day2.http <= stage0.MAX_HTTP_TOTAL
    assert (day1.http, day2.http) == (88, 84)


def test_budget_refuses_an_oversized_list():
    doc = controls_document(positives=20, regional_per_geo=5, related_positives=20)
    with pytest.raises(ValueError, match="90"):
        build_plan(parse_controls(doc))


def test_plan_keeps_each_control_on_one_day():
    parsed = controls()
    day1, day2 = build_plan(parsed)
    seen = {}
    for day in (day1, day2):
        for unit in day.units:
            if unit.repeat_of is None:
                seen.setdefault(unit.control, set()).add((day.day, unit.granularity, unit.timeline))
    for entry in parsed.controls:
        days = {day for day, _, _ in seen[entry.id]}
        assert len(days) == 1, entry.id
        expected = {(True, "H"), (True, "D")} if entry.group != "seed" else {(False, "H")}
        assert {(timeline, granularity) for _, granularity, timeline in seen[entry.id]} == expected
    for group in ("positive", "regional", "negative", "seed", "market"):
        per_day = [sum(1 for c in parsed.group(group) if next(iter(seen[c.id]))[0] == d) for d in (1, 2)]
        assert abs(per_day[0] - per_day[1]) <= 1, group


def test_plan_related_only_where_asked():
    parsed = controls()
    units = [(unit, parsed.by_id()[unit.control]) for day in build_plan(parsed) for unit in day.units]
    for unit, entry in units:
        if unit.related:
            assert entry.related and (unit.granularity == "D" or entry.group == "seed")
        elif entry.related and unit.repeat_of is None:
            assert unit.granularity == "H" and entry.group != "seed"


def test_plan_repeats_after_their_original():
    """One repeat a day, a few units after the unit it repeats; day 1 asks explore by GET, day 2 by POST."""
    for day in build_plan(controls()):
        keys = [unit.key for unit in day.units]
        repeats = [unit for unit in day.units if unit.repeat_of is not None]
        assert len(repeats) == 1
        repeat = repeats[0]
        assert keys.index(repeat.key) - keys.index(repeat.repeat_of) >= stage0.REPEAT_GAP_UNITS + 1
        original = day.units[keys.index(repeat.repeat_of)]
        assert (original.control, original.granularity, original.timeline, original.related) == (repeat.control, "H", True, False)
        assert repeat.method == ("GET" if day.day == 1 else "POST")
        assert {unit.method for unit in day.units if unit.repeat_of is None} == {"GET"}
        assert len(set(keys)) == len(keys)


def test_plan_market_last():
    """The market series come last: when retries use up the slack, they are what the cap cuts."""
    for day in build_plan(controls()):
        groups = [controls().by_id()[unit.control].group for unit in day.units if unit.repeat_of is None]
        first_market = groups.index("market")
        assert set(groups[first_market:]) == {"market"}


def test_plan_document_round_trip():
    parsed = controls()
    for day in build_plan(parsed):
        document = day.to_document(parsed)
        assert document["format"] == stage0.PLAN_FORMAT
        assert document["http"] == day.http
        assert stage0.DayPlan.from_document(json.loads(json.dumps(document))) == day


def test_plan_document_refuses_a_tampered_count():
    parsed = controls()
    document = build_plan(parsed)[0].to_document(parsed)
    document["units"] = [*document["units"], document["units"][0]]
    with pytest.raises(ValueError):
        stage0.DayPlan.from_document(document)


# ---- visibility ----------------------------------------------------------------------------------------------------


def test_visibility_metrics():
    """「非零小时 ≥12/144」over the last 144 complete hours, the partial point left out; 「非零日 ≥N/30」likewise."""
    pos = control("pos-01")
    twelve = series_of(result_line(pos, "H", hourly_values(12)))
    assert nonzero_hours(twelve) == stage0_metrics.Visibility(nonzero=12, window=144, points=168)
    assert visible(twelve, n=12) is True
    assert visible(series_of(result_line(pos, "H", hourly_values(11))), n=12) is False
    partial_only = series_of(result_line(pos, "H", hourly_values(0, partial_value=80)))
    assert nonzero_hours(partial_only).nonzero == 0
    days = series_of(result_line(pos, "D", daily_values(12)))
    assert nonzero_days(days) == stage0_metrics.Visibility(nonzero=12, window=30, points=30)
    assert visible(days, n=12) is True and visible(days, n=13) is False


def test_visibility_short_series_is_counted_as_is():
    short = series_of(result_line(control("pos-01"), "H", hourly_values(12, points=160)[-100:]))
    assert nonzero_hours(short).window == 99


def test_unobserved_is_not_invisible():
    """A failed unit is unobserved: never zero, never 'not visible' (premise 1)."""
    failed = series_of(result_line(control("pos-01"), "H", None, status="rate_limited"))
    assert failed.status == "rate_limited"
    assert nonzero_hours(failed).nonzero is None
    assert visible(failed, n=12) is None


def test_units_never_sent_are_unobserved():
    """A unit the cap, the breaker or the deadline stopped before it sent is unobserved too; a seed has no series."""
    never = {**result_line(control("pos-01"), "H", None), "timeline": None, "status": None, "reason": "truncated", "requested_at": None}
    series = series_of(never)
    assert series.status == "truncated" and series.requested_at is None
    assert visible(series, n=12) is None
    assert series_of(seed_line(control("seed-01"), day=1, related=related_ok())) is None


# ---- gate A --------------------------------------------------------------------------------------------------------


def _gate_a_inputs(visible_positives: int, visible_regional: int, *, failed_positives: int = 0):
    lines = []
    for i in range(16):
        entry = control(f"pos-{i + 1:02d}")
        if i < failed_positives:
            lines.append(result_line(entry, "H", None, status="server_error"))
        else:
            lines.append(result_line(entry, "H", hourly_values(12 if i < failed_positives + visible_positives else 3)))
    regional = [c for c in controls_document()["controls"] if c["group"] == "regional"]
    lines += [result_line(entry, "H", hourly_values(20 if k < visible_regional else 0)) for k, entry in enumerate(regional)]
    lines += [result_line(control("neg-01"), "H", hourly_values(140))]  # a generic title: visible, never counted
    return [series_of(line) for line in lines]


def test_gate_verdicts():
    """Gate A: positive visibility >= 50% and an estimated >= 5 non-generic judgements a day."""
    passing = gate_a(_gate_a_inputs(8, 1), granularity="H", n=12)
    assert (passing.positive.observed, passing.positive.visible) == (16, 8)
    assert passing.estimate_daily == 5 and passing.passed is True
    assert gate_a(_gate_a_inputs(7, 1), granularity="H", n=12).passed is False
    starved = gate_a(_gate_a_inputs(8, 0), granularity="H", n=12)
    assert starved.estimate_daily == 0 and starved.passed is False
    halved = gate_a(_gate_a_inputs(8, 1), granularity="H", n=12, a_tier_dramas=30)
    assert halved.estimate_daily == 2 and halved.passed is False


def test_gate_a_leaves_failures_out_of_the_denominator():
    result = gate_a(_gate_a_inputs(8, 1, failed_positives=1), granularity="H", n=12)
    assert (result.positive.observed, result.positive.visible, result.positive.unobserved) == (15, 8, 1)
    assert result.passed is True


def test_gate_a_needs_enough_observations():
    few = [s for s in _gate_a_inputs(8, 1) if s.group != "positive" or s.control in ("pos-01", "pos-02", "pos-03")]
    result = gate_a(few, granularity="H", n=12)
    assert result.passed is None and any("正对照" in reason for reason in result.reasons)


def test_gate_a_reads_only_its_granularity():
    lines = [series_of(result_line(control(f"pos-{i + 1:02d}"), "D", daily_values(20))) for i in range(16)]
    assert gate_a(lines, granularity="H", n=12).positive.observed == 0
    assert gate_a(lines, granularity="D", n=12).positive.visible == 16


@pytest.mark.parametrize(
    "h_rate, d_rate, expected",
    [(0.75, 0.75, "H"), (0.3, 0.75, "H+D"), (0.1, 0.75, "D"), (0.1, 0.2, None)],
)
def test_choose_granularity(h_rate, d_rate, expected):
    def gate(granularity, rate):
        visible_count = round(16 * rate)
        return stage0_metrics.GateA(
            granularity=granularity,
            positive=stage0_metrics.GroupRate(observed=16, visible=visible_count, unobserved=0),
            regional=stage0_metrics.GroupRate(observed=12, visible=12, unobserved=0),
            estimate_daily=60,
            estimate_basis="regional",
            passed=rate >= 0.5,
            reasons=(),
        )

    assert choose_granularity(gate("H", h_rate), gate("D", d_rate)) == expected


# ---- gate B --------------------------------------------------------------------------------------------------------


def _gate_b_lines(*, days=(1, 2), usable=True, user_type="USER_TYPE_LEGIT_USER"):
    lines = []
    for day in days:
        for cid in ("pos-01", "pos-02", "pos-03", "pos-04"):
            related = related_ok() if usable else related_missing()
            lines.append(result_line(control(cid), "D", daily_values(20), day=day, related=related, user_type=user_type))
        lines.append(seed_line(control("seed-01"), day=day, related=related_ok(breakout=True), user_type=user_type))
    return lines


def test_gate_b_passes_on_two_stable_days():
    result = gate_b(_gate_b_lines())
    assert result.passed is True and result.final is True
    assert result.user_types == {"USER_TYPE_LEGIT_USER": 10}
    assert result.breakout_seen is True


def test_gate_b_fails_without_related_queries():
    result = gate_b(_gate_b_lines(usable=False))
    assert result.passed is False
    assert any("相关查询" in reason for reason in result.reasons)


def test_gate_b_needs_user_type():
    assert gate_b(_gate_b_lines(user_type=None)).passed is False


def test_gate_b_is_provisional_on_day_one():
    result = gate_b(_gate_b_lines(days=(1,)))
    assert result.passed is True and result.final is False
    assert any("第二天" in item for item in result.pending)


# ---- the four routes (section 8) -----------------------------------------------------------------------------------


def test_four_routes():
    """Each A/B outcome maps to its section 8 row, with the task changes and the switch settings."""
    combos = {(True, True): "both", (True, False): "a_only", (False, True): "b_only", (False, False): "neither"}
    routes = {combo: route_for(*combo) for combo in combos}
    assert {combo: route.key for combo, route in routes.items()} == combos
    for route in routes.values():
        assert route.trends and route.gsc and route.switches and route.person_days
    assert any("TR-19 取消" in line for line in routes[(True, False)].trends)
    assert any("TR-17 取消" in line for line in routes[(False, True)].trends)
    assert any("S12b 不执行" in line for line in routes[(False, False)].switches)
    assert routes[(True, True)].person_days == "0"


def test_route_needs_both_verdicts():
    assert route_for(True, None) is None and route_for(None, False) is None


# ---- the manual comparison (U5) ------------------------------------------------------------------------------------

MANUAL_HOURLY = "Category: All categories\n\nTime,moonlit vow 1: (United States)\n" + "\n".join(
    f"2026-09-26T{k:02d},{value}" for k, value in enumerate(["12", "<1", "30", "7", "0", "55"])
)
MANUAL_DAILY = "Category: All categories\n\nDay,moonlit vow 1: (Worldwide)\n2026-09-20,5\n2026-09-21,<1\n2026-09-22,9\n"


def test_parse_manual_csv():
    hourly = parse_manual_csv(MANUAL_HOURLY)
    assert (hourly.term, hourly.granularity, len(hourly.points)) == ("moonlit vow 1", "H", 6)
    assert hourly.points[1] == ("2026-09-26T01", 0.5)  # "<1" sits between 0 and 1
    daily = parse_manual_csv(MANUAL_DAILY)
    assert (daily.granularity, daily.points[0]) == ("D", ("2026-09-20", 5.0))
    with pytest.raises(ValueError):
        parse_manual_csv("Time,x\nnot a row")


def test_parse_manual_csv_from_a_chinese_browser():
    """U5 may come from a browser set to Chinese: column and region labels in Chinese, full-width punctuation."""
    manual = parse_manual_csv("类别：所有类别\n\n天,moonlit vow 1：（美国）\n2026-09-25,5\n2026-09-24,<1\n")
    assert (manual.term, manual.geo_label, manual.granularity) == ("moonlit vow 1", "美国", "D")
    assert manual.points[1] == ("2026-09-24", 0.5)
    assert parse_manual_csv("时间,moonlit vow 1: (美国)\n2026-09-25T08,3\n").granularity == "H"


def test_spearman():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 2, 2, 3], [1, 3, 2, 4]) == pytest.approx(4.5 / math.sqrt(22.5), abs=1e-12)  # average ranks for the tie
    assert spearman([5, 5, 5], [1, 2, 3]) is None
    assert spearman([1], [1]) is None


def test_manual_shape_compare():
    """The browser export (UTC+8 labels) against the scraped series (tz=0), aligned by time, is deterministic."""
    values = hourly_values(12)
    values[-9:-3] = [12, 0, 30, 7, 0, 55]  # 16:00-21:00 UTC on 09-25, that is 00:00-05:00 on 09-26 at UTC+8
    scraped = series_of(result_line(control("pos-01"), "H", values))
    manual = parse_manual_csv(MANUAL_HOURLY)
    first = shape_compare(manual, scraped, utc_offset_minutes=8 * 60)
    again = shape_compare(manual, scraped, utc_offset_minutes=8 * 60)
    assert first == again
    assert first.n == 6 and first.rho == pytest.approx(spearman([12, 0.5, 30, 7, 0, 55], [12, 0, 30, 7, 0, 55]))
    assert first.verdict == "一致"
    unaligned = shape_compare(manual, scraped, utc_offset_minutes=-600)
    assert unaligned.n == 0 and unaligned.verdict == "无法比较"


# ---- the canary control list ---------------------------------------------------------------------------------------


def test_canary_controls_file():
    """TR-14's input: identity keys and geo only; titles come from the shared batch at run time."""
    document = json.loads(CANARY_CONTROLS.read_text(encoding="utf-8"))
    assert document["format"] == "trends-canary-controls-v1"
    groups = {entry["group"] for entry in document["controls"]}
    assert groups == {"positive", "negative", "regional"}
    for entry in document["controls"]:
        assert set(entry) == {"identity", "geo", "group"}
        source, source_id, language = json.loads(entry["identity"])
        assert source == "realshort-pick" and source_id and language
        assert stage0.GEO.fullmatch(entry["geo"])
    assert len({entry["identity"] + entry["geo"] for entry in document["controls"]}) == len(document["controls"])
    assert {entry["geo"] for entry in document["market"]} >= {"US", "BG", "DE", "FR", "IT"}
    assert all(set(entry) == {"geo", "term"} for entry in document["market"])  # a market phrase, never a drama title
