"""TR-09: usable ranges, the common cutoff and the comparison windows (design 5.3, 5.4).

Every response keeps a usable range anchored on this round's A watermark. The common cutoff H_c takes the earliest of
A's watermark, A''s when A' is available, and the usable end of the latest C slice; the day ends of older slices never
take part (counterexample 23). A formal 24-hour window needs [H_c-48h, H_c) covered without a break by C slices that are
not stale; hours at or after H_c are provisional and never compared.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from gsc_builders import H_C, W0, W1

from ggwork_pick.observe.gsc import coverage, cutoff, pageset

PREVIOUS_CUTOFF = datetime(2026, 9, 24, 16, 0, tzinfo=UTC)


def _day(day: int) -> date:
    return date(2026, 9, day)


def _slice(day: int, watermark: datetime | None = None, *, stale: bool = False) -> cutoff.CSlice:
    return cutoff.CSlice(_day(day), watermark, stale)


def _cut(*slices, a=H_C, a_prime=None, previous=None) -> cutoff.Cutoff:
    return cutoff.common_cutoff(a_watermark=a, a_prime_watermark=a_prime, c_slices=slices, previous_cutoff=previous)


def test_pt_days_follow_los_angeles():
    assert cutoff.pt_day_bounds(_day(24)) == (datetime(2026, 9, 24, 7, tzinfo=UTC), datetime(2026, 9, 25, 7, tzinfo=UTC))
    start, end = cutoff.pt_day_bounds(date(2026, 11, 1))  # clocks go back: a 25-hour PT day
    assert end - start == timedelta(hours=25)
    assert cutoff.pt_date_of(datetime(2026, 9, 25, 6, 59, tzinfo=UTC)) == _day(24)
    assert cutoff.pt_date_of(datetime(2026, 9, 25, 7, 0, tzinfo=UTC)) == _day(25)
    with pytest.raises(ValueError):
        cutoff.pt_date_of(datetime(2026, 9, 25, 7, 0))


def test_watermarks_parse_from_gsc_metadata():
    assert cutoff.parse_hour("2026-09-24T12:00:00-07:00") == H_C
    assert cutoff.parse_hour(None) is None
    assert cutoff.parse_day("2026-09-24") == datetime(2026, 9, 24, 7, tzinfo=UTC)
    assert cutoff.parse_day(None) is None
    for bad in ("2026-09-24T12:00:00", "yesterday"):
        with pytest.raises(ValueError):
            cutoff.parse_hour(bad)


def test_usable_range_three_cases():
    # A itself has no first_incomplete_hour: no formal 24-hour window this round, the previous cutoff carries over.
    carried = _cut(_slice(22), _slice(23), _slice(24, H_C), a=None, previous=PREVIOUS_CUTOFF)
    assert (carried.h_c, carried.carried, carried.formal_24h, carried.reasons) == (PREVIOUS_CUTOFF, True, False, ("a_watermark_absent",))
    assert cutoff.day_usable_range(_day(23), watermark=None, anchor=None).reason == "no_anchor"

    # A PT day that ends before A's watermark: the whole day, though its response legitimately has no field.
    whole = cutoff.day_usable_range(_day(23), watermark=None, anchor=H_C)
    assert (whole.start, whole.end, whole.reason) == (*cutoff.pt_day_bounds(_day(23)), "whole_day")
    assert whole.usable and not whole.watermark_absent

    # A PT day that spans A's watermark must carry the field; without it: watermark_absent, unusable this round.
    absent = cutoff.day_usable_range(_day(24), watermark=None, anchor=H_C)
    assert (absent.end, absent.reason, absent.usable, absent.watermark_absent) == (None, "watermark_absent", False, True)
    until = cutoff.day_usable_range(_day(24), watermark=H_C - timedelta(hours=2), anchor=H_C)
    assert (until.end, until.reason) == (H_C - timedelta(hours=2), "until_watermark")

    # A PT day that starts at or after the watermark has nothing usable yet, whether or not its response has the field.
    later = cutoff.day_usable_range(_day(25), watermark=None, anchor=H_C)
    assert (later.usable, later.start == later.end, later.reason) == (True, True, "after_watermark")


def test_watermark_absent_latest_slice_leaves_no_fresh_cutoff():
    got = _cut(_slice(22), _slice(23), _slice(24), previous=PREVIOUS_CUTOFF)
    assert (got.h_c, got.carried, got.formal_24h, got.reasons) == (PREVIOUS_CUTOFF, True, False, ("latest_slice_unusable",))
    assert dict(got.ranges)[_day(24)].watermark_absent
    none_yet = _cut(previous=None)
    assert (none_yet.h_c, none_yet.formal_24h, none_yet.reasons) == (None, False, ("no_slices",))


def test_cutoff_ignores_history_day_ends():
    """Counterexample 23: the 22nd's slice is complete and A's watermark is noon on the 24th."""
    got = _cut(_slice(22), _slice(23), _slice(24, H_C))
    assert got.h_c == H_C and cutoff.pt_date_of(got.h_c) == _day(24)
    assert (got.formal_24h, got.carried, got.reasons, got.gaps) == (True, False, (), ())
    # The day ends of the 22nd and 23rd (00:00 PT on the 23rd and 24th) would each have pulled H_c back a day.
    assert all(got.h_c > end for end in (cutoff.pt_day_bounds(_day(22))[1], cutoff.pt_day_bounds(_day(23))[1]))


def test_cutoff_takes_the_earliest_bound_on_the_hour():
    a = datetime(2026, 9, 24, 19, 40, tzinfo=UTC)
    assert _cut(_slice(22), _slice(23), _slice(24, a), a=a).h_c == H_C  # the hour H_c falls in counts as incomplete
    assert _cut(_slice(22), _slice(23), _slice(24, a), a=a, a_prime=datetime(2026, 9, 24, 18, 20, tzinfo=UTC)).h_c == H_C - timedelta(hours=1)
    assert _cut(_slice(22), _slice(23), _slice(24, H_C - timedelta(hours=3)), a=a).h_c == H_C - timedelta(hours=3)
    # A slice for a PT day after the watermark ends its (empty) range at its own start and changes nothing.
    assert _cut(_slice(22), _slice(23), _slice(24, H_C), _slice(25)).h_c == H_C


def test_cutoff_48h_gap():
    missing_day = _cut(_slice(22), _slice(24, H_C))
    assert (missing_day.formal_24h, missing_day.reasons) == (False, ("coverage_gap",))
    assert missing_day.gaps == (cutoff.pt_day_bounds(_day(23)),)
    assert missing_day.h_c == H_C and not missing_day.carried

    short_start = _cut(_slice(23), _slice(24, H_C))
    assert short_start.gaps == ((H_C - timedelta(hours=48), cutoff.pt_day_bounds(_day(23))[0]),)
    assert not short_start.formal_24h

    # The 24th's slice never arrived, the 25th's is empty so far: the break sits at the end of the 48 hours.
    missing_today = _cut(_slice(22), _slice(23), _slice(25))
    assert (missing_today.h_c, missing_today.formal_24h) == (H_C, False)
    assert missing_today.gaps == ((cutoff.pt_day_bounds(_day(24))[0], H_C),)


def test_uncovered_intervals():
    hour = timedelta(hours=1)
    span = (H_C - 10 * hour, H_C)
    assert cutoff.uncovered(span, ()) == (span,)
    assert cutoff.uncovered(span, ((H_C - 20 * hour, H_C + hour),)) == ()
    pieces = ((H_C - 8 * hour, H_C - 6 * hour), (H_C - 7 * hour, H_C - 5 * hour), (H_C - 3 * hour, H_C - 2 * hour))
    assert cutoff.uncovered(span, pieces) == ((H_C - 10 * hour, H_C - 8 * hour), (H_C - 5 * hour, H_C - 3 * hour), (H_C - 2 * hour, H_C))


def test_stale_c_slice_blocks_formal_window():
    """Counterexample 28 at the cutoff: a stale slice inside [H_c-48h, H_c) leaves no formal 24-hour window."""
    got = _cut(_slice(22), _slice(23, stale=True), _slice(24, H_C))
    assert (got.formal_24h, got.reasons, got.stale_dates, got.gaps) == (False, ("stale_slice",), (_day(23),), ())
    # A stale slice wholly before the 48 hours touches nothing.
    early = _cut(_slice(21, stale=True), _slice(22), _slice(23), _slice(24, H_C))
    assert (early.formal_24h, early.stale_dates) == (True, ())


def test_provisional_hours_excluded():
    hours = tuple(H_C + timedelta(hours=offset) for offset in (-2, -1, 0, 1))
    comparable, provisional = cutoff.split_provisional(hours, H_C)
    assert comparable == hours[:2] and provisional == hours[2:]
    assert [cutoff.is_provisional(hour, H_C) for hour in hours] == [False, False, True, True]

    page = "https://dramashortstv.com/en/drama/the-alphas-bride-650a1b2c3d4e5f6a7b8c9d0e"
    sources = pageset.PageSources(host="dramashortstv.com", rs_ids={}, legacy=())
    members = pageset.page_set(canonical_id="650a1b2c3d4e5f6a7b8c9d0e", locale="en", sources=sources)
    rows = tuple(coverage.GscRow(country="USA", impressions=10, clicks=1, page=page, hour=hour) for hour in hours)
    assert coverage.detail_value(rows, members, W0, "USA").value == 20


def test_windows():
    assert (W0.label, W0.kind, W0.start, W0.end, W0.days) == ("w0", "24h", H_C - timedelta(hours=24), H_C, ())
    assert (W1.label, W1.start, W1.end) == ("w_minus_1", H_C - timedelta(hours=48), H_C - timedelta(hours=24))
    assert W0.pt_days == (_day(23), _day(24))
    assert W0.covers_hour(H_C - timedelta(hours=1)) and not W0.covers_hour(H_C) and not W0.covers_hour(W1.start)
    assert cutoff.hourly_windows(H_C) == (W0, W1)

    d0, d1 = cutoff.daily_windows(_day(23))
    assert d0.days == tuple(_day(n) for n in range(17, 24)) and d1.days == tuple(_day(n) for n in range(10, 17))
    assert (d0.kind, d0.start, d0.end) == ("7d", cutoff.pt_day_bounds(_day(17))[0], cutoff.pt_day_bounds(_day(23))[1])
    assert d0.covers_day(_day(17)) and not d0.covers_day(_day(16)) and d1.covers_day(_day(16))
    assert cutoff.window_days(d0, d1) == tuple(_day(n) for n in range(10, 24))

    assert cutoff.latest_complete_day(H_C) == _day(23)
    assert cutoff.latest_complete_day(cutoff.pt_day_bounds(_day(25))[0]) == _day(24)
    with pytest.raises(ValueError):
        cutoff.hourly_windows(datetime(2026, 9, 24, 19, 30, tzinfo=UTC))
