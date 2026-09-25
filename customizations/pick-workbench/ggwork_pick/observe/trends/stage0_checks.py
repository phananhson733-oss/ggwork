"""Stage 0's interface checks and the lag (plan TR-05; design 4.1, 4.9 rule 1, 4.11 "接口核实").

What the real answers look like, read off the runner's result lines: how many points each time range returns (169
hourly?), where isPartial sits, how time is written, which userType values come back, what an empty answer looks like,
how far a repeat of the same request a few minutes later differs, whether explore answers both GET and POST, and how
related queries are shaped (Breakout included). Pure functions; the report prints them.

The lag (design 4.9 rule 1: window_end = the batch's hour minus 3 hours, "按 isPartial 的位置回标"): for each series,
the hours between the hour it was requested in and the end of its last complete point. The suggested lag is the largest
one seen per granularity, so no window ever reaches past the complete points of any sample. Daily series are read the
same way: under 24 hours means the daily window ends at the UTC day boundary itself.
"""

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from ggwork_pick.observe.trends.stage0_metrics import JUDGEABLE, STEP_SECONDS, Series, series_of

DESIGN_LAG_HOURS = 3  # design 4.9 rule 1, the value stage 0 re-reads
MAX_EXAMPLES = 5


def _frozen_counts(items: Iterable) -> Mapping:
    return MappingProxyType(dict(sorted(Counter(items).items(), key=lambda pair: str(pair[0]))))


# ---- the lag -------------------------------------------------------------------------------------------------------


def lag_hours(series: Series) -> int | None:
    """Hours from the end of the last complete point to the hour the series was requested in; None when unobserved."""
    if not series.observed or not series.complete() or series.requested_at is None:
        return None
    last_time, _ = series.complete()[-1]
    end = datetime.fromtimestamp(last_time, UTC) + timedelta(seconds=STEP_SECONDS[series.granularity])
    requested_hour = series.requested_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return math.floor((requested_hour - end).total_seconds() / 3600)


@dataclass(frozen=True)
class LagSummary:
    samples: tuple[int, ...]
    suggested: int | None  # the largest lag seen; None without a sample

    @property
    def distribution(self) -> Mapping[int, int]:
        return _frozen_counts(self.samples)


def lag_summary(series_list: Iterable[Series], *, granularity: str = "H") -> LagSummary:
    chosen = (series for series in series_list if series.granularity == granularity and series.repeat_of is None)
    samples = tuple(lag for lag in map(lag_hours, chosen) if lag is not None)
    return LagSummary(samples=samples, suggested=max(samples) if samples else None)


# ---- repeats -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RepeatDiff:
    unit: str
    original: str
    method: str
    minutes_apart: float
    shift_points: int  # how many points the repeat's axis moved on (the hour ticked over in between)
    common: int  # complete points both answers share
    differing: int
    max_abs_diff: int | None


def repeat_diff(original: Series, repeat: Series) -> RepeatDiff:
    first, second = dict(original.complete()), dict(repeat.complete())
    common = sorted(set(first) & set(second))
    diffs = [abs(first[time] - second[time]) for time in common]
    step = STEP_SECONDS[original.granularity]
    shift = (repeat.times[-1] - original.times[-1]) // step if original.times and repeat.times else 0
    minutes = (repeat.requested_at - original.requested_at).total_seconds() / 60
    return RepeatDiff(
        repeat.unit, original.unit, repeat.method, round(minutes, 1), shift, len(common), sum(d > 0 for d in diffs), max(diffs) if diffs else None
    )


def repeat_diffs(series_list: Sequence[Series]) -> tuple[RepeatDiff, ...]:
    by_unit = {series.unit: series for series in series_list}
    pairs = [(by_unit.get(series.repeat_of), series) for series in series_list if series.repeat_of is not None]
    return tuple(repeat_diff(original, repeat) for original, repeat in pairs if original is not None and original.observed and repeat.observed)


# ---- the answers' shapes -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RelatedShape:
    asked: int
    ok: int
    empty: int  # a widget was offered, both lists came back empty
    widget_missing: int
    failed: int
    breakout: int  # rising queries whose formattedValue reads "Breakout"
    formatted_examples: tuple[str, ...]  # rising formattedValue spellings other than a plain number


@dataclass(frozen=True)
class InterfaceSummary:
    points: Mapping[str, Mapping[int, int]]  # granularity -> number of points -> series
    partial_positions: Mapping[str, Mapping[str, int]]  # granularity -> positions from the end, e.g. "-1" -> series
    time_types: Mapping[str, int]
    has_data_missing: int
    user_types: Mapping[str, int]
    statuses: Mapping[str, Mapping[str, int]]  # granularity -> series status -> count
    methods: Mapping[str, Mapping[str, int]]  # explore method -> unit status -> count
    repeats: tuple[RepeatDiff, ...]
    related: RelatedShape


def _raw_lines(results: Iterable[Mapping]) -> list[tuple[str, Mapping]]:
    return [(line["granularity"], raw) for line in results for raw in ((line.get("timeline") or {}).get("lines") or ())]


def _partial_positions(raw: Mapping) -> str:
    flags = raw.get("isPartial") or ()
    positions = [str(k - len(flags)) for k, flag in enumerate(flags) if flag is True]
    return ",".join(positions) or "none"


def _related_shape(results: Iterable[Mapping]) -> RelatedShape:
    related = [line["related"] for line in results if line.get("related") is not None]
    rising = [item for entry in related for item in (entry.get("rising") or ())]
    spelled = sorted({item["formattedValue"] for item in rising if not str(item["formattedValue"]).isdigit()})
    return RelatedShape(
        asked=len(related),
        ok=sum(entry["status"] == "ok" for entry in related),
        empty=sum(entry["status"] == "no_data" and not entry.get("widget_missing") for entry in related),
        widget_missing=sum(bool(entry.get("widget_missing")) for entry in related),
        failed=sum(entry["status"] not in ("ok", "no_data") for entry in related),
        breakout=sum(item.get("formattedValue") == "Breakout" for item in rising),
        formatted_examples=tuple(spelled[:MAX_EXAMPLES]),
    )


def interface_summary(results: Sequence[Mapping]) -> InterfaceSummary:
    raws = _raw_lines(results)
    timelines = [line for line in results if line.get("timeline") is not None]
    series_list = [series for series in map(series_of, timelines) if series is not None]
    return InterfaceSummary(
        points=MappingProxyType({g: _frozen_counts(len(raw["value"]) for kind, raw in raws if kind == g) for g in ("H", "D")}),
        partial_positions=MappingProxyType({g: _frozen_counts(_partial_positions(raw) for kind, raw in raws if kind == g) for g in ("H", "D")}),
        time_types=_frozen_counts(type(raw["time"][0]).__name__ for _, raw in raws if raw["time"]),
        has_data_missing=sum(1 for _, raw in raws if any(flag is None for flag in raw.get("hasData") or (None,))),
        user_types=_frozen_counts(line["user_type"] for line in results if line.get("user_type")),
        statuses=MappingProxyType({g: _frozen_counts(line["timeline"]["status"] for line in timelines if line["granularity"] == g) for g in ("H", "D")}),
        methods=MappingProxyType(
            {m: _frozen_counts(line["status"] for line in results if line.get("method") == m and line.get("status")) for m in ("GET", "POST")}
        ),
        repeats=repeat_diffs(series_list),
        related=_related_shape(results),
    )


def judged_statuses() -> frozenset[str]:
    return JUDGEABLE
