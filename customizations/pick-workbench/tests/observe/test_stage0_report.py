"""TR-05: the stage 0 report (plan TR-05, section 8; design 4.9, 4.11; D39).

The report is built from the runner's files only. With day 1 alone it is an interim report that marks every conclusion
still waiting for day 2; with both days it settles the gates, the granularity, N, the lag, the route and the full text of
trend-rules-v1, plus the data contract change sheet when D or H+D is chosen. Inputs are synthetic (stage0_fakes).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest
from stage0_fakes import REQUESTED_AT, controls_document, daily_values, hourly_values, related_ok, result_line, seed_line

from ggwork_pick.observe import contract
from ggwork_pick.observe.trends import stage0_checks, stage0_rules
from ggwork_pick.observe.trends.stage0 import parse_controls
from ggwork_pick.observe.trends.stage0_metrics import series_of
from ggwork_pick.observe.trends.stage0_report import build_report, load_days, render, write_report
from ggwork_pick.observe.trends.stage0_run import Stage0Paths, append_private, write_private
from ggwork_pick.observe.wording import forbidden_in

GENERATED = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)


def entries(group: str | None = None) -> list[dict]:
    return [c for c in controls_document()["controls"] if group is None or c["group"] == group]


def day_lines(day: int, *, hourly_visible: int, daily_visible: int, regional_visible: int = 6, failed: tuple[str, ...] = ()) -> list[dict]:
    """The day's half of every group: odd-numbered controls on day 1, even-numbered on day 2."""
    at = REQUESTED_AT + timedelta(days=day - 1)
    lines = []
    for group in ("positive", "regional", "negative", "market"):
        for k, entry in enumerate(entries(group)[day - 1 :: 2]):
            if entry["id"] in failed:
                lines += [result_line(entry, "H", None, day=day, status="server_error", requested_at=at)]
                continue
            limit = {"positive": (hourly_visible, daily_visible), "regional": (regional_visible, regional_visible)}.get(group, (99, 99))
            h = hourly_values(14 if k < limit[0] else 2)
            d = daily_values(20 if k < limit[1] else 4)
            related = related_ok() if entry.get("related") else None
            lines += [result_line(entry, "H", h, day=day, requested_at=at), result_line(entry, "D", d, day=day, related=related, requested_at=at)]
    for entry in entries("seed")[day - 1 :: 2]:
        lines.append(seed_line(entry, day=day, related=related_ok(breakout=True)))
    first = entries("positive")[day - 1]
    repeat = result_line(
        first,
        "H",
        hourly_values(14),
        day=day,
        unit=f"{first['id']}-h-rep",
        repeat_of=f"{first['id']}-h",
        method="GET" if day == 1 else "POST",
        requested_at=at + timedelta(minutes=4),
    )
    return [*lines, repeat]


NO_PROXY_SEEN = {"environment": [], "no_proxy": [], "system": [], "in_effect": None}


def write_day(paths: Stage0Paths, day: int, lines: list[dict], *, proxy: dict | None = None, **changes) -> None:
    for line in lines:
        append_private(paths.run_dir(day) / "results.jsonl", line)
    session = {
        "target_date": (REQUESTED_AT + timedelta(days=day)).date().isoformat(),
        "started_at": (REQUESTED_AT + timedelta(days=day - 1)).isoformat(),
        "finished_at": (REQUESTED_AT + timedelta(days=day - 1, minutes=40)).isoformat(),
        "interrupted": False,
        "proxy": proxy or NO_PROXY_SEEN,
        "explore_methods": ["GET"] if day == 1 else ["GET", "POST"],
        "warmup": "ok",
        "requests": 86,
        "covered": len(lines),
        "uncovered": [],
        "budget": {"reserved": 86, "cap": 90, "first_limit_at": None, "before_first_limit": None},
        "breaker": {"trips": 0, "pauses": 0, "rate_limited": 0, "half_speed": False, "extinguished": None},
        "status_codes": [],
        **changes,
    }
    meta = {"day": day, "plan_http": 86, "collector_version": "obs-collector-v1", "egress": "off (D20)", "sessions": [session]}
    write_private(paths.run_dir(day) / "meta.json", json.dumps(meta))


@pytest.fixture
def paths(tmp_path) -> Stage0Paths:
    root = tmp_path / "trends-stage0"
    root.mkdir(mode=0o700)
    return Stage0Paths(root)


def report_text(paths: Stage0Paths, **options) -> str:
    controls = parse_controls(controls_document())
    report = build_report(controls, load_days(paths), generated_at=GENERATED, **options)
    return render(report)


def test_interim_report_marks_what_waits_for_day_two(paths):
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7))
    controls = parse_controls(controls_document())
    report = build_report(controls, load_days(paths), generated_at=GENERATED)
    assert report.interim is True
    text = render(report)
    assert "中期报告" in text and "定稿" not in text.split("\n")[0]
    assert "待第二天" in text
    assert "第二天的会话还没跑" in text
    assert report.route is None or "暂定" in text


def test_final_report_settles_gates_route_and_rules(paths):
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7))
    write_day(paths, 2, day_lines(2, hourly_visible=6, daily_visible=7))
    controls = parse_controls(controls_document())
    report = build_report(controls, load_days(paths), generated_at=GENERATED)
    assert report.interim is False
    assert (report.gate_h.positive.visible, report.gate_h.positive.observed) == (12, 16)
    assert report.gate_h.passed is True and report.gate_b.passed is True
    assert report.granularity == "H" and report.route.key == "both"
    text = render(report)
    assert "定稿" in text.split("\n")[0]
    assert "A、B 都过" in text
    assert "trend-rules-v1" in text and "B1–B6" in text
    assert "数据合同变更单" not in text


def test_daily_choice_brings_the_contract_change_sheet(paths):
    for day in (1, 2):
        write_day(paths, day, day_lines(day, hourly_visible=0, daily_visible=7))
    text = report_text(paths)
    assert "W1–W4" in text and "数据合同变更单" in text and "latest_block_end" in text


def test_interim_gate_a_decides_on_half_the_positives(paths):
    """Day 1 holds half the positives: one failure there must not leave the interim report without a tentative verdict."""
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7, failed=("pos-01",)))
    report = build_report(parse_controls(controls_document()), load_days(paths), generated_at=GENERATED)
    assert report.gate_h.positive.observed == 7 and report.gate_h.passed is True
    assert report.granularity == "H"


def test_undecided_gates_are_not_called_failures(paths):
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7, failed=("pos-01", "pos-03", "pos-05", "pos-07", "pos-09")))
    report = build_report(parse_controls(controls_document()), load_days(paths), generated_at=GENERATED)
    assert report.gate_h.passed is None and report.gate_a_passed is None and report.route is None
    text = render(report)
    assert "都不过" not in text and "都没过" not in text


def test_report_notes_the_age_of_the_gsc_export(paths):
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7))
    text = report_text(paths)
    assert "小时级 now 7-d 看不到那时的需求，日级 today 1-m 能覆盖" in text


def test_report_records_proxy_variables_by_name(paths):
    proxy = {"environment": ["HTTPS_PROXY"], "no_proxy": [], "system": [], "in_effect": "environment"}
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7), proxy=proxy)
    text = report_text(paths)
    assert "HTTPS_PROXY" in text and "经过代理" in text


def test_report_names_a_system_proxy(paths):
    """No proxy variable, but the system settings name one (Clash, Surge): httpx uses it, so the report says so and never
    calls the session direct."""
    proxy = {"environment": [], "no_proxy": [], "system": ["http", "https"], "in_effect": "system"}
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7), proxy=proxy)
    text = report_text(paths)
    assert "系统代理" in text and "http、https" in text and "本机直连" not in text


def test_report_does_not_call_no_proxy_a_proxy(paths):
    proxy = {"environment": [], "no_proxy": ["NO_PROXY"], "system": [], "in_effect": None}
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7), proxy=proxy)
    text = report_text(paths)
    assert "NO_PROXY" in text and "经过代理" not in text and "本机直连" not in text
    assert "TUN" in text


def test_report_shows_a_zero_count_before_the_first_limit(paths):
    budget = {"reserved": 3, "cap": 90, "first_limit_at": "2026-09-26T03:20:00.000000+00:00", "before_first_limit": 0}
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7), budget=budget)
    assert "之前成功 0 次" in report_text(paths)


# ---- gate B and the route need both sessions (P1) ------------------------------------------------------------------


def never_sent(line: dict, reason: str) -> dict:
    return {**line, "status": None, "reason": reason, "timeline": None, "related": None, "requested_at": None, "attempts": 0, "stopped_by": None}


def test_day_two_put_out_at_once_settles_nothing(paths):
    """Day 2's first request met a captcha and every unit was skipped: the report is not final, gate B does not pass,
    and no route is given."""
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7))
    skipped = [never_sent(line, "skipped_breaker") for line in day_lines(2, hourly_visible=6, daily_visible=7)]
    broken = {"trips": 0, "pauses": 0, "rate_limited": 0, "half_speed": False, "extinguished": "wall"}
    uncovered = [[line["unit"], "skipped_breaker"] for line in skipped]
    write_day(paths, 2, skipped, requests=1, covered=0, uncovered=uncovered, breaker=broken, status_codes=["extinguished_today"])
    report = build_report(parse_controls(controls_document()), load_days(paths), generated_at=GENERATED)
    assert report.interim is True and report.route is None and report.gate_b.passed is False
    text = render(report)
    assert "定稿" not in text.split("\n")[0] and "第二天" in text.split("\n")[0]
    assert "| 闸门 B | 过" not in text and "A、B 都过" not in text
    assert "熄火" in text


def test_day_two_without_related_queries_gives_no_route(paths):
    """Day 2 saw its series but sent no related query: gate B is undecided, so no route, in a final report."""
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7))
    lines = [{**line, "related": None} for line in day_lines(2, hourly_visible=6, daily_visible=7) if line["group"] != "seed"]
    seeds = [never_sent(line, "truncated") for line in day_lines(2, hourly_visible=6, daily_visible=7) if line["group"] == "seed"]
    write_day(paths, 2, [*lines, *seeds], uncovered=[[line["unit"], "truncated"] for line in seeds])
    report = build_report(parse_controls(controls_document()), load_days(paths), generated_at=GENERATED)
    assert report.interim is False and report.gate_b.passed is None and report.route is None
    text = render(report)
    assert "| 闸门 B | 过" not in text and "A、B 都过" not in text


def test_query_mismatch_positive_is_listed_apart(paths):
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7))
    day2 = [{**line, "kind": "query_mismatch"} if line["control"] == "pos-16" else line for line in day_lines(2, hourly_visible=6, daily_visible=7)]
    write_day(paths, 2, day2)
    report = build_report(parse_controls(controls_document()), load_days(paths), generated_at=GENERATED)
    assert report.gate_h.positive.observed == 15 and report.gate_h.mismatch.observed == 1
    text = render(report)
    assert "查询词与剧名不一致" in text and "偏宽" in text


def test_unobserved_controls_are_not_zero(paths):
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7, failed=("pos-01",)))
    controls = parse_controls(controls_document())
    report = build_report(controls, load_days(paths), generated_at=GENERATED)
    assert report.gate_h.positive.unobserved == 1
    text = render(report)
    row = next(line for line in text.splitlines() if line.startswith("| pos-01 "))
    assert "未观测到" in row and "0/144" not in row
    assert row.count("未观测到") == 3  # hourly failed; the daily unit and its related queries never came back
    assert forbidden_in(text) == ()


def test_manual_comparison_pending_then_shown(paths):
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7))
    assert "U5" in report_text(paths) and "待补" in report_text(paths)
    manual = "Category: All categories\n\nDay,moonlit vow 1: (United States)\n" + "\n".join(
        f"{(datetime(2026, 9, 25, tzinfo=UTC) - timedelta(days=k)).date().isoformat()},{v}" for k, v in enumerate([5, 9, 0, 3, 8, 1])
    )
    text = report_text(paths, manual=[("export-1.csv", manual)])
    assert "export-1.csv" in text and "ρ" in text


def test_n_sensitivity_table(paths):
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7))
    text = report_text(paths)
    for n in (6, 9, 12, 15, 20):
        assert f"| {n} |" in text


def test_write_report_names(paths):
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7))
    controls = parse_controls(controls_document())
    report = build_report(controls, load_days(paths), generated_at=GENERATED)
    path = write_report(paths, report)
    assert path.name == "trends-stage0-interim-report-2026-09-28.md"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


# ---- interface checks and the lag ----------------------------------------------------------------------------------


def test_lag_is_read_off_the_partial_point():
    """Requested at 03:20 with the last complete hour ending at 00:00: the window must end 3 hours before the hour."""
    series = series_of(result_line(entries("positive")[0], "H", hourly_values(14)))
    assert stage0_checks.lag_hours(series) == 3
    assert stage0_checks.lag_summary([series, series]).suggested == 3


def test_daily_lag_is_read_the_same_way():
    """Requested at 03:20 with today's partial day: the last complete day ends at 00:00 UTC, 3 hours before the hour, so
    the daily window ends at the UTC day boundary itself."""
    series = series_of(result_line(entries("positive")[0], "D", daily_values(20)))
    assert stage0_checks.lag_hours(series) == 3
    assert stage0_checks.lag_summary([series], granularity="D").suggested == 3
    assert stage0_checks.lag_summary([series]).suggested is None  # the hourly summary leaves daily series out


def test_repeat_diff():
    entry = entries("positive")[0]
    first = series_of(result_line(entry, "H", hourly_values(14)))
    same = series_of(result_line(entry, "H", hourly_values(14), unit="pos-01-h-rep", repeat_of="pos-01-h"))
    assert stage0_checks.repeat_diff(first, same).differing == 0
    changed = hourly_values(14)
    changed[100] = 55
    other = series_of(result_line(entry, "H", changed, unit="pos-01-h-rep", repeat_of="pos-01-h"))
    diff = stage0_checks.repeat_diff(first, other)
    assert (diff.differing, diff.max_abs_diff, diff.common) == (1, 55, 168)


def test_interface_summary_counts_points_and_partials():
    lines = [result_line(entries("positive")[0], "H", hourly_values(14)), result_line(entries("positive")[0], "D", daily_values(20))]
    summary = stage0_checks.interface_summary(lines)
    assert summary.points["H"] == {169: 1} and summary.points["D"] == {31: 1}
    assert summary.partial_positions["H"] == {"-1": 1}
    assert summary.time_types == {"str": 2}


@pytest.mark.parametrize("granularity, marker", [("H", "B1–B6"), ("D", "W1–W4"), ("H+D", "W1–W4")])
def test_rules_text(granularity, marker):
    text = stage0_rules.trend_rules_text(granularity, n=12, lag_hours=3, lag_days=0)
    assert marker in text and "trend-rules-v1" in text
    assert forbidden_in(text) == ()
    sheet = stage0_rules.contract_change_sheet(granularity)
    assert (sheet is None) == (granularity == "H")


def test_rules_text_carries_the_measured_lags():
    assert "整点减 5 小时" in stage0_rules.trend_rules_text("H", n=12, lag_hours=5, lag_days=0)
    daily = stage0_rules.trend_rules_text("D", n=9, lag_hours=3, lag_days=0)
    assert "N=9" in daily and "再减" not in daily
    assert "再减 1 天" in stage0_rules.trend_rules_text("D", n=12, lag_hours=3, lag_days=1)


@pytest.mark.parametrize("granularity, value", [("D", "D"), ("H+D", "HD")])
def test_change_sheet_names_the_contract_value(granularity, value):
    """The report writes H+D; TR-33's frozen inputs take "HD". The sheet says which value TR-17 writes."""
    assert value in get_args(contract.Granularity)
    sheet = stage0_rules.contract_change_sheet(granularity)
    assert f'`frozen_inputs.granularity` = `"{value}"`' in sheet


def test_rules_text_unknown_granularity():
    with pytest.raises(ValueError):
        stage0_rules.trend_rules_text("W", n=12, lag_hours=3)


def test_report_never_reads_the_real_artifacts(paths, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: Path("/nonexistent-home"))
    write_day(paths, 1, day_lines(1, hourly_visible=6, daily_visible=7))
    assert "中期报告" in report_text(paths)
