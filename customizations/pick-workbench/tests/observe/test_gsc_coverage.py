"""TR-09: window sums, the per-identity consistency check and the site layer of admission (design 5.6; plan section 2).

The written-down rule (premise 3): |X_flt - X_det| <= max(10% x max(X_flt, X_det), 3), in integers, the larger value as
denominator; a side with no row counts as 0 in the difference but stays "no row" in the record. Agreement of two lower
bounds is an admission rule only (premise 2): the result says admitted and why not, never "verified" or "complete".
"""

from dataclasses import fields
from datetime import timedelta

import pytest
from gsc_builders import D0, D1, H_C, PREVIOUS_ROUND, ROUND, W0, W1, compare, states_of, value

from ggwork_pick.observe.gsc import coverage, cutoff, pageset

CANON = "650a1b2c3d4e5f6a7b8c9d0e"
PAGE = f"https://dramashortstv.com/en/drama/the-alphas-bride-{CANON}"
MEMBERS = pageset.page_set(canonical_id=CANON, locale="en", sources=pageset.PageSources(host="dramashortstv.com", rs_ids={}, legacy=()))
MID_W0 = H_C - timedelta(hours=12)


def _row(country: str, impressions: int, *, clicks: int = 0, hour=MID_W0, page=PAGE, position=None) -> coverage.GscRow:
    return coverage.GscRow(country=country, impressions=impressions, clicks=clicks, page=page, hour=hour, position=position)


def test_window_value_no_rows_is_none():
    assert (coverage.WindowValue.of(()).value, coverage.WindowValue.of(()).row_count) == (None, 0)
    assert (coverage.WindowValue.of((0,)).value, coverage.WindowValue.of((0,)).row_count) == (0, 1)
    assert coverage.WindowValue.of((3, 4)) == coverage.WindowValue(7, 2)
    for bad in ((0, 0), (None, 1), (5, 0), (-1, 1)):  # (0, 0) is sum([]) = 0 posing as an observation
        with pytest.raises(ValueError):
            coverage.WindowValue(*bad)


def test_unobserved_stored_null():
    """Premise 1: a window with no row sums to None with row_count 0; sum([]) = 0 is never what gets stored."""
    got = coverage.detail_value((_row("GBR", 9),), MEMBERS, W0, "USA")
    assert (got.value, got.row_count) == (None, 0)
    flt = coverage.filter_value((coverage.GscRow(country="USA", impressions=5, clicks=0, hour=W1.start),), W0, "USA")
    assert (flt.value, flt.row_count) == (None, 0)
    both_empty = compare(W1, None, None)
    assert both_empty.admitted and both_empty.value is None
    assert (both_empty.x_det.value, both_empty.x_flt.value, both_empty.x_det.row_count, both_empty.x_flt.row_count) == (None, None, 0, 0)


def test_window_sums_scope_and_metric():
    rows = (_row("USA", 10, clicks=2), _row("GBR", 20, clicks=3), _row("ZZZ", 5, clicks=1), _row("USA", 7, hour=H_C), _row("USA", 1, hour=W1.start))
    assert coverage.detail_value(rows, MEMBERS, W0, "USA") == coverage.WindowValue(10, 1)
    assert coverage.detail_value(rows, MEMBERS, W0, "ALL") == coverage.WindowValue(35, 3)  # unknown country included
    assert coverage.detail_value(rows, MEMBERS, W0, "ALL", "clicks") == coverage.WindowValue(6, 3)
    assert coverage.detail_value(rows, MEMBERS, W1, "USA") == coverage.WindowValue(1, 1)
    daily = (coverage.GscRow(country="USA", impressions=4, clicks=0, page=PAGE, pt_date=D0.days[0]),)
    assert coverage.detail_value(daily, MEMBERS, D0, "USA").value == 4 and coverage.detail_value(daily, MEMBERS, D1, "USA").value is None
    with pytest.raises(ValueError):
        coverage.detail_value(rows, MEMBERS, W0, "USA", "ctr")


def test_detail_position_is_impression_weighted():
    rows = (_row("USA", 100, position=4.0), _row("USA", 300, position=8.0), _row("USA", 0, position=50.0), _row("GBR", 50, position=1.0))
    assert coverage.detail_position(rows, MEMBERS, W0, "USA") == pytest.approx(7.0)
    assert coverage.detail_position(rows, MEMBERS, W1, "USA") is None
    assert coverage.detail_position((_row("USA", 10),), MEMBERS, W0, "USA") is None


def test_consistency_denominator_is_max():
    cases = {(100, 91): True, (100, 89): False, (2, 5): True, (5, 2): True, (90, 100): True, (100, 90): True, (0, 3): True, (0, 4): False}
    for (flt, det), expected in cases.items():
        assert coverage.consistent(value(flt), value(det)) is expected, (flt, det)
        assert coverage.consistent(value(det), value(flt)) is expected, (det, flt)
    # A side with no row counts as 0 in the difference; the record keeps "no row".
    assert coverage.consistent(value(None), value(3)) and not coverage.consistent(value(None), value(4))
    assert coverage.consistent(value(None), value(None))
    got = compare(W0, 90, 100)
    assert (got.admitted, got.value, got.reason) == (True, 100, None)
    one_sided = compare(W1, None, 3)
    assert (one_sided.admitted, one_sided.value, one_sided.x_det.value, one_sided.x_det.row_count) == (True, 3, None, 0)


def test_consistency_country_scoped():
    det_rows = (_row("USA", 100), _row("GBR", 100))
    flt_rows = tuple(coverage.GscRow(country=c, impressions=n, clicks=0, hour=MID_W0) for c, n in (("USA", 95), ("GBR", 60)))
    admitted = {}
    for country in ("USA", "GBR"):
        detail = coverage.DetailSide(W0, states_of(W0), coverage.detail_value(det_rows, MEMBERS, W0, country))
        filtered = coverage.FilterSide("vh", W0, states_of(W0), "fetched", True, coverage.filter_value(flt_rows, W0, country))
        admitted[country] = coverage.per_identity_consistency(detail, filtered).admitted
    assert admitted == {"USA": True, "GBR": False}


def test_consistency_datastate_mismatch():
    got = compare(D0, 100, 100, flt_states=("all",) * 7)
    assert (got.admitted, got.reason) == (False, "datastate_mismatch")
    mixed = ("final",) * 6 + ("all",)
    assert compare(D0, 100, 100, det_states=mixed, flt_states=mixed).admitted
    assert compare(D0, 100, 100, det_states=mixed, flt_states=("final",) * 7).reason == "datastate_mismatch"


def test_comparison_reasons():
    shifted = cutoff.Window("w0", "24h", W0.start - timedelta(hours=1), W0.end - timedelta(hours=1), ())
    cases = {
        "filter_missing": compare(W0, 100),
        "filter_failed": compare(W0, 100, None, status="failed"),
        "regex_overflow": compare(W0, 100, None, status="regex_overflow"),
        "filter_truncated": compare(W0, 100, 100, status="truncated"),
        "vh_not_this_round": compare(W0, 100, 100, current=False),
        "vd_invalid": compare(D0, 100, 100, current=False),
        "window_mismatch": compare(W0, 100, 100, flt_window=shifted),
        "detail_gap": compare(W0, 100, 80),
    }
    for reason, got in cases.items():
        assert (got.admitted, got.value, got.reason) == (False, None, reason), reason
    with pytest.raises(ValueError):
        coverage.FilterSide("vh", D0, states_of(D0), "fetched", True, value(1))


def test_vh_never_reused():
    assert coverage.vh_current(ROUND, ROUND) and not coverage.vh_current(PREVIOUS_ROUND, ROUND)
    previous = compare(W0, 100, 100, current=coverage.vh_current(PREVIOUS_ROUND, ROUND))
    assert (previous.admitted, previous.reason) == (False, "vh_not_this_round")


def test_vd_valid_rules():
    days = cutoff.window_days(D0, D1)
    basis = tuple(coverage.VdDay(day, 1000 + n, "final" if n < 10 else "all") for n, day in enumerate(days))
    assert coverage.vd_valid(basis, basis) and coverage.vd_validity(basis, tuple(reversed(basis))) is None
    later = cutoff.window_days(*cutoff.daily_windows(D0.days[-1] + timedelta(days=1)))
    moved = tuple(coverage.VdDay(day, 1000 + n, "final" if n < 10 else "all") for n, day in enumerate(later))
    new_version = basis[:3] + (coverage.VdDay(basis[3].pt_date, 9999, basis[3].data_state),) + basis[4:]
    now_final = basis[:12] + (coverage.VdDay(basis[12].pt_date, basis[12].version_id, "final"),) + basis[13:]
    assert coverage.vd_validity(basis, moved) == "window_changed"
    assert coverage.vd_validity(basis, basis[:13]) == "window_changed"
    assert coverage.vd_validity(basis, new_version) == "slice_version_changed"
    assert coverage.vd_validity(basis, now_final) == "datastate_changed"
    assert not any(coverage.vd_valid(basis, other) for other in (moved, new_version, now_final))


def test_combine_chunk_statuses():
    combine = coverage.combine_chunk_statuses
    assert combine(("fetched", "fetched")) == "fetched"
    assert combine(("fetched", "truncated")) == "truncated"
    assert combine(("truncated", "failed", "fetched")) == "failed"
    assert combine(("failed", "regex_overflow")) == "regex_overflow"
    assert combine(()) == "failed"


def test_admission_wording():
    """Premise 2: the result carries admitted and a reason, and says only 两份下界一致（准入）."""
    assert [field.name for field in fields(coverage.Comparison)] == ["window", "x_det", "x_flt", "admitted", "value", "reason"]
    assert coverage.ADMISSION_TEXT == "两份下界一致（准入）"
    words = [field.name for field in fields(coverage.Comparison)] + list(coverage.COMPARISON_REASONS) + [coverage.ADMISSION_TEXT]
    for forbidden in ("完整", "已核实", "核实", "独立", "verified", "complete", "independent", "proof"):
        assert not any(forbidden in word for word in words), forbidden


def _hourly(total_per_hour: int) -> coverage.HourlyTotals:
    hours = tuple(W1.start + timedelta(hours=n) for n in range(48))
    return coverage.HourlyTotals("fetched", {hour: total_per_hour for hour in hours})


def _c_by_hour(detail_per_hour: int) -> dict:
    return {W1.start + timedelta(hours=n): detail_per_hour for n in range(48)}


def _daily(status: str, per_day: int | None) -> coverage.DailyTotals:
    days = cutoff.window_days(W0, W1)
    return coverage.DailyTotals(status, {} if per_day is None else {day: per_day for day in days})


def test_site_admission():
    """Counterexamples 5 and 17: every request answered 200 and none was full, yet A' and C disagree; or neither total."""
    windows = (W0, W1)
    usable = coverage.site_admission_24h(windows, a_prime=_hourly(100), a2_all=None, c_by_hour=_c_by_hour(97), c_by_day={})
    assert (usable.admission, usable.source) == ("usable", "a_prime")
    exceeded = coverage.site_admission_24h(windows, a_prime=_hourly(100), a2_all=None, c_by_hour=_c_by_hour(94), c_by_day={})
    assert (exceeded.admission, exceeded.source) == ("gap_exceeded", "a_prime")
    assert {unit.key for unit in exceeded.exceeded} == {W0.start, W1.start}

    days = cutoff.window_days(W0, W1)
    by_day = coverage.site_admission_24h(
        windows, a_prime=coverage.HourlyTotals("unsupported", {}), a2_all=_daily("fetched", 2400), c_by_hour={}, c_by_day={d: 2350 for d in days}
    )
    assert (by_day.admission, by_day.source) == ("usable", "a2_all")
    neither = coverage.site_admission_24h(
        windows, a_prime=coverage.HourlyTotals("unsupported", {}), a2_all=_daily("failed", None), c_by_hour={}, c_by_day={d: 2350 for d in days}
    )
    assert (neither.admission, neither.source) == ("unverifiable", "none")
    assert coverage.site_admission_24h(windows, a_prime=None, a2_all=None, c_by_hour={}, c_by_day={}).admission == "unverifiable"

    one_hour_missing = coverage.HourlyTotals("fetched", {hour: 100 for hour in list(_hourly(100).by_hour)[1:]})
    assert coverage.site_admission_24h(windows, a_prime=one_hour_missing, a2_all=None, c_by_hour=_c_by_hour(100), c_by_day={}).admission == "unverifiable"


def _day_totals(day, state, *, detail=1000, a2_all=1000, a2_final=1000):
    return coverage.DayTotals(day, state, detail, a2_all, a2_final)


def test_site_admission_7d_datastate_per_day():
    days = cutoff.window_days(D0, D1)
    states = {day: ("final" if n < 10 else "all") for n, day in enumerate(days)}
    base = tuple(_day_totals(day, states[day]) for day in days)
    assert coverage.site_admission_7d((D0, D1), base).admission == "usable"

    # An E day is compared with A''f, a D day with A''a, whatever the other total says.
    e_day, d_day = days[2], days[12]
    final_gap = tuple(_day_totals(d, states[d], a2_final=1100) if d == e_day else t for d, t in zip(days, base))
    assert coverage.site_admission_7d((D0, D1), final_gap).admission == "gap_exceeded"
    all_gap_on_e_day = tuple(_day_totals(d, states[d], a2_all=5000) if d == e_day else t for d, t in zip(days, base))
    assert coverage.site_admission_7d((D0, D1), all_gap_on_e_day).admission == "usable"
    all_gap_on_d_day = tuple(_day_totals(d, states[d], a2_all=5000) if d == d_day else t for d, t in zip(days, base))
    assert coverage.site_admission_7d((D0, D1), all_gap_on_d_day).admission == "gap_exceeded"

    # The matching total missing on any one day: the 7-day window is unverifiable, never judged with the other total.
    no_final = tuple(_day_totals(d, states[d], a2_final=None) if d == e_day else t for d, t in zip(days, base))
    assert coverage.site_admission_7d((D0, D1), no_final).admission == "unverifiable"
    no_slice = tuple(_day_totals(d, None) if d == d_day else t for d, t in zip(days, base))
    assert coverage.site_admission_7d((D0, D1), no_slice).admission == "unverifiable"
    assert coverage.site_admission_7d((D0, D1), base[1:]).admission == "unverifiable"
