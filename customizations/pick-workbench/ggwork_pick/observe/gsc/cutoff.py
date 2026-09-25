"""Usable ranges, the common cutoff and the comparison windows (plan TR-09; design 5.3, 5.4).

Every response keeps the range of time it may be used for, anchored on this round's A watermark (A's metadata
first_incomplete_hour):

- a PT day that ends by the anchor is usable whole, whether or not its response carries a watermark (the official
  contract leaves the field out once a day is complete);
- a PT day that spans the anchor must carry the field and is usable up to it; without it the response contradicts A
  (watermark_absent) and nothing in it is usable this round;
- a PT day that starts at or after the anchor has nothing usable yet.

The common cutoff H_c is the earliest of A's watermark, A''s when A' is available, and the usable end of the latest C
slice, in UTC and floored to the hour (the hour H_c falls in is incomplete too). Day ends of older slices never take
part: a complete slice of the 22nd must not pull H_c back to the 23rd (counterexample 23). The round has a formal 24-hour
window only when [H_c-48h, H_c) is covered without a break by C's usable ranges and touches no stale slice; hours at or
after H_c are provisional and never compared. The 7-day windows are the latest 7 complete PT days and the 7 before.

All instants are timezone-aware and returned in UTC; PT means America/Los_Angeles, daylight saving included.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
HOUR = timedelta(hours=1)
WINDOW_HOURS = timedelta(hours=24)
CONTINUITY = timedelta(hours=48)  # [H_c-48h, H_c): both 24-hour windows
WINDOW_DAYS = 7

RangeReason = Literal["whole_day", "until_watermark", "after_watermark", "watermark_absent", "no_anchor"]
CutoffReason = Literal["a_watermark_absent", "no_slices", "latest_slice_unusable", "coverage_gap", "stale_slice"]
WindowKind = Literal["24h", "7d"]
WindowLabel = Literal["w0", "w_minus_1"]


def utc(moment: datetime) -> datetime:
    """The same instant in UTC; a naive datetime is refused (every instant here is explicit)."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"a timezone-aware instant is required, got {moment!r}")
    return moment.astimezone(UTC)


def pt_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of a PT day in UTC: 23, 24 or 25 hours long."""
    following = day + timedelta(days=1)
    start = datetime(day.year, day.month, day.day, tzinfo=PT)
    end = datetime(following.year, following.month, following.day, tzinfo=PT)
    return start.astimezone(UTC), end.astimezone(UTC)


def pt_date_of(moment: datetime) -> date:
    return utc(moment).astimezone(PT).date()


def floor_hour(moment: datetime) -> datetime:
    return utc(moment).replace(minute=0, second=0, microsecond=0)


def parse_hour(text: str | None) -> datetime | None:
    """GSC's metadata.first_incomplete_hour (ISO 8601 with an offset) as a UTC instant; None stays None."""
    if text is None:
        return None
    return utc(datetime.fromisoformat(text))


def parse_day(text: str | None) -> datetime | None:
    """GSC's metadata.first_incomplete_date (a PT date) as the instant that day starts; None stays None."""
    if text is None:
        return None
    return pt_day_bounds(date.fromisoformat(text))[0]


# ---- usable ranges (design 5.3) -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UsableRange:
    """What of [start, period end) may be used this round: [start, end), or nothing when end is None."""

    start: datetime
    end: datetime | None
    reason: RangeReason

    @property
    def usable(self) -> bool:
        return self.end is not None

    @property
    def watermark_absent(self) -> bool:
        return self.reason == "watermark_absent"


def usable_range(start: datetime, end: datetime, *, watermark: datetime | None, anchor: datetime | None) -> UsableRange:
    """The usable part of one response covering [start, end), given its own watermark and this round's anchor.

    anchor is A's watermark; with none this round has nothing to anchor on (no_anchor). A daily D or E slice may be
    anchored on the carried cutoff instead: that is the caller's choice, made once per round.
    """
    start, end = utc(start), utc(end)
    if anchor is None:
        return UsableRange(start, None, "no_anchor")
    anchor = utc(anchor)
    if watermark is not None:
        return UsableRange(start, min(max(utc(watermark), start), end), "until_watermark")
    if end <= anchor:
        return UsableRange(start, end, "whole_day")
    if start >= anchor:
        return UsableRange(start, start, "after_watermark")
    return UsableRange(start, None, "watermark_absent")


def day_usable_range(day: date, *, watermark: datetime | None, anchor: datetime | None) -> UsableRange:
    return usable_range(*pt_day_bounds(day), watermark=watermark, anchor=anchor)


# ---- the common cutoff (design 5.4) ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CSlice:
    """The active version of one C slice (hour x page x country for a PT day).

    watermark: its first_incomplete_hour; a slice split by country passes its shards' earliest, and None when a shard
    that spans the anchor has none. stale: a newer version failed or was truncated and this older one is still active.
    """

    pt_date: date
    watermark: datetime | None
    stale: bool = False


@dataclass(frozen=True, slots=True)
class Cutoff:
    """This round's H_c. carried: no fresh cutoff could be set, h_c is the previous set's (or None before the first).

    formal_24h holds only with a fresh H_c whose 48 hours are covered and touch no stale slice; reasons say why not.
    """

    h_c: datetime | None
    carried: bool
    formal_24h: bool
    reasons: tuple[CutoffReason, ...]
    ranges: tuple[tuple[date, UsableRange], ...]
    gaps: tuple[tuple[datetime, datetime], ...]
    stale_dates: tuple[date, ...]


def _carried(previous: datetime | None, reason: CutoffReason, ranges=()) -> Cutoff:
    return Cutoff(None if previous is None else utc(previous), True, False, (reason,), ranges, (), ())


def uncovered(span: tuple[datetime, datetime], covered: Iterable[tuple[datetime, datetime]]) -> tuple[tuple[datetime, datetime], ...]:
    """The parts of [span) that no interval in covered reaches, in order."""
    start, end = span
    gaps: tuple[tuple[datetime, datetime], ...] = ()
    cursor = start
    for lo, hi in sorted(covered):
        if lo > cursor:
            gaps = (*gaps, (cursor, min(lo, end)))
        cursor = max(cursor, hi)
        if cursor >= end:
            break
    if cursor < end:
        gaps = (*gaps, (cursor, end))
    return tuple((lo, hi) for lo, hi in gaps if lo < hi)


def _touches(day: date, span: tuple[datetime, datetime]) -> bool:
    lo, hi = pt_day_bounds(day)
    return lo < span[1] and hi > span[0]


def common_cutoff(
    *,
    a_watermark: datetime | None,
    a_prime_watermark: datetime | None,
    c_slices: Sequence[CSlice],
    previous_cutoff: datetime | None = None,
) -> Cutoff:
    """H_c for this round (design 5.4). a_prime_watermark is None when A' is unsupported or failed: it then takes no part.

    Without A's watermark, or without a usable latest C slice, no fresh H_c exists and the previous one carries over
    (design 5.3: this round has no formal 24-hour window).
    """
    if a_watermark is None:
        return _carried(previous_cutoff, "a_watermark_absent")
    ordered = sorted(c_slices, key=lambda piece: piece.pt_date)
    ranges = tuple((piece.pt_date, day_usable_range(piece.pt_date, watermark=piece.watermark, anchor=a_watermark)) for piece in ordered)
    if not ranges:
        return _carried(previous_cutoff, "no_slices")
    latest = ranges[-1][1]
    if not latest.usable:
        return _carried(previous_cutoff, "latest_slice_unusable", ranges)
    bounds = (utc(a_watermark), latest.end, *(() if a_prime_watermark is None else (utc(a_prime_watermark),)))
    h_c = floor_hour(min(bounds))
    span = (h_c - CONTINUITY, h_c)
    gaps = uncovered(span, ((r.start, r.end) for _, r in ranges if r.usable))
    stale = tuple(piece.pt_date for piece in ordered if piece.stale and _touches(piece.pt_date, span))
    reasons: tuple[CutoffReason, ...] = (*(("coverage_gap",) if gaps else ()), *(("stale_slice",) if stale else ()))
    return Cutoff(h_c, False, not reasons, reasons, ranges, gaps, stale)


# ---- windows ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Window:
    """One comparison window: [start, end) in UTC; a 7-day window also lists its PT days (hourly rows are matched by hour,
    daily rows by PT day)."""

    label: WindowLabel
    kind: WindowKind
    start: datetime
    end: datetime
    days: tuple[date, ...]

    @property
    def span(self) -> timedelta:
        return self.end - self.start

    @property
    def pt_days(self) -> tuple[date, ...]:
        """Every PT day the window touches."""
        if self.days:
            return self.days
        first, last = pt_date_of(self.start), pt_date_of(self.end - timedelta(microseconds=1))
        return tuple(first + timedelta(days=n) for n in range((last - first).days + 1))

    def covers_hour(self, hour: datetime) -> bool:
        return self.start <= utc(hour) < self.end

    def covers_day(self, day: date) -> bool:
        return day in self.days


def hourly_windows(h_c: datetime) -> tuple[Window, Window]:
    """W0 = [H_c-24h, H_c) and W-1 the 24 hours before it."""
    h_c = utc(h_c)
    if h_c != floor_hour(h_c):
        raise ValueError("H_c is always on the hour")
    w0 = Window("w0", "24h", h_c - WINDOW_HOURS, h_c, ())
    return w0, Window("w_minus_1", "24h", w0.start - WINDOW_HOURS, w0.start, ())


def latest_complete_day(anchor: datetime) -> date:
    """The latest PT day that ends by the anchor (pass H_c, so both 24-hour and 7-day windows share one cutoff)."""
    return pt_date_of(anchor) - timedelta(days=1)


def _days_window(label: WindowLabel, last: date) -> Window:
    days = tuple(last - timedelta(days=offset) for offset in range(WINDOW_DAYS - 1, -1, -1))
    return Window(label, "7d", pt_day_bounds(days[0])[0], pt_day_bounds(days[-1])[1], days)


def daily_windows(latest: date) -> tuple[Window, Window]:
    """The 7 complete PT days ending on `latest`, and the 7 before them."""
    w0 = _days_window("w0", latest)
    return w0, _days_window("w_minus_1", w0.days[0] - timedelta(days=1))


def window_days(*windows: Window) -> tuple[date, ...]:
    """Every PT day the windows touch, in order, each once."""
    return tuple(sorted({day for window in windows for day in window.pt_days}))


def is_provisional(hour: datetime, h_c: datetime) -> bool:
    """An hour at or after H_c is provisional: shown as such, never compared."""
    return utc(hour) >= utc(h_c)


def split_provisional(hours: Iterable[datetime], h_c: datetime) -> tuple[tuple[datetime, ...], tuple[datetime, ...]]:
    ordered = tuple(hours)
    return tuple(h for h in ordered if not is_provisional(h, h_c)), tuple(h for h in ordered if is_provisional(h, h_c))
