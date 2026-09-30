/**
 * The simplified radar's table rules (simplified scope 2026-09-30, sections 2 and 4), computed on the page: the
 * gateway (GET /api/pick/obs/trends-table, ggwork_pick/observe/trends_table.py) hands over each drama's series as
 * Google answered it, and everything read off it is here, so a threshold changes with a frontend deploy only.
 *
 * - Two means: the 7 calendar days up to the last complete UTC day (the day before the batch's window_end), and the
 *   7 before them. Only complete days count; a day Google returned without data (value null) or a day missing from
 *   the series is left out, never read as 0.
 * - The label, first that holds: too little (fewer than minNonZeroDays non-zero days among the recent 7, or no prior
 *   day with data to compare against), new (prior mean 0, recent mean above 0), rising (change at least
 *   +changePercent), falling (at most -changePercent), flat. The comparisons are exact (integer sums), so a change of
 *   exactly 25% is 25%, not 24.999...
 * - The short-title hint, the Trends link and the two sort orders.
 *
 * Every number the scope asks to tune after the first week sits in TREND_RULES. Pure: no clock, no server module.
 */
import { fillCalendar, type ChartPoint } from "@/core/pick-board/chart";

import { stampMicros, utcDayOf } from "./obs-instants";

export const TREND_RULES = Object.freeze({
  /** days in each of the two windows */
  windowDays: 7,
  /** rising from +changePercent, falling from -changePercent */
  changePercent: 25,
  /** fewer non-zero days than this among the recent window: too little data */
  minNonZeroDays: 3,
  /** days the sparkline covers, ending at the last complete day */
  sparkDays: 30,
  /** a title of at most this many words is short */
  shortTermMaxWords: 2,
  /** in scripts written without spaces, a title of at most this many characters is short */
  shortTermMaxChars: 4,
});

export const TREND_LABELS = [
  "too_little",
  "new",
  "rising",
  "falling",
  "flat",
] as const;
export type TrendLabel = (typeof TREND_LABELS)[number];

export const TREND_LABEL_TEXT: Readonly<Record<TrendLabel, string>> = {
  too_little: "数据太少",
  new: "新出现",
  rising: "上升",
  falling: "回落",
  flat: "持平",
};

export const SHORT_TERM_HINT = "剧名较短，搜索热度可能不属于这部剧";

export type TrendPoint = Readonly<{
  date: string;
  value: number | null;
  partial: boolean;
}>;

export type TrendStats = Readonly<{
  /** null: no recent day with data */
  recentMean: number | null;
  /** null: no prior day with data */
  priorMean: number | null;
  /** days with data in each window */
  recentDays: number;
  priorDays: number;
  nonZeroRecent: number;
  /** percent; null when the prior mean is 0 or missing, or the recent one missing */
  change: number | null;
  label: TrendLabel;
}>;

const DAY_MS = 86_400_000;

function shiftDay(day: string, days: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + days * DAY_MS)
    .toISOString()
    .slice(0, 10);
}

/** The last complete UTC day of a batch: the day before its window_end (an aware ISO moment). */
export function lastCompleteDay(windowEnd: string): string {
  return shiftDay(utcDayOf(stampMicros(windowEnd)), -1);
}

type Window = Readonly<{ sum: number; days: number; nonZero: number }>;

/** The complete days Google returned a value for. */
function completeValues(points: readonly TrendPoint[]): ChartPoint[] {
  return points.flatMap((p) =>
    !p.partial && p.value !== null ? [{ day: p.date, value: p.value }] : [],
  );
}

/** The complete days with data in [from, to], by calendar day; a day listed twice counts once (the last one). */
function windowOf(
  points: readonly TrendPoint[],
  from: string,
  to: string,
): Window {
  const byDay = new Map(
    completeValues(points)
      .filter((p) => p.day >= from && p.day <= to)
      .map((p) => [p.day, p.value]),
  );
  const values = [...byDay.values()];
  return {
    sum: values.reduce((total, value) => total + value, 0),
    days: values.length,
    nonZero: values.filter((value) => value > 0).length,
  };
}

/**
 * The label from the two windows, exactly: with means r = rs/rd and p = ps/pd, the change is at least +c% when
 * 100 * (rs*pd - ps*rd) >= c * ps*rd, all integers.
 */
function labelOf(recent: Window, prior: Window): TrendLabel {
  if (recent.nonZero < TREND_RULES.minNonZeroDays || prior.days === 0)
    return "too_little";
  if (prior.sum === 0) return recent.sum > 0 ? "new" : "flat";
  const delta = 100 * (recent.sum * prior.days - prior.sum * recent.days);
  const bound = TREND_RULES.changePercent * prior.sum * recent.days;
  if (delta >= bound) return "rising";
  if (delta <= -bound) return "falling";
  return "flat";
}

/** The two means, the change and the label of one series, ending at `lastComplete` (YYYY-MM-DD). */
export function trendStats(
  points: readonly TrendPoint[],
  lastComplete: string,
): TrendStats {
  const span = TREND_RULES.windowDays;
  const recent = windowOf(
    points,
    shiftDay(lastComplete, 1 - span),
    lastComplete,
  );
  const prior = windowOf(
    points,
    shiftDay(lastComplete, 1 - 2 * span),
    shiftDay(lastComplete, -span),
  );
  const recentMean = recent.days === 0 ? null : recent.sum / recent.days;
  const priorMean = prior.days === 0 ? null : prior.sum / prior.days;
  const change =
    recentMean === null || priorMean === null || priorMean === 0
      ? null
      : ((recentMean - priorMean) / priorMean) * 100;
  return {
    recentMean,
    priorMean,
    recentDays: recent.days,
    priorDays: prior.days,
    nonZeroRecent: recent.nonZero,
    change,
    label: labelOf(recent, prior),
  };
}

export function formatMean(mean: number | null): string {
  return mean === null ? "—" : mean.toFixed(1);
}

export function formatChange(change: number | null): string {
  if (change === null) return "—";
  const whole = Math.round(change);
  if (whole === 0) return "0%";
  return whole > 0 ? `+${whole}%` : `−${-whole}%`;
}

/** Scripts written without spaces between words: a title there is measured in characters. */
const UNSPACED =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Thai}\p{Script=Lao}\p{Script=Khmer}\p{Script=Myanmar}]/u;

/**
 * A title short enough that its searches may be someone else's: at most two words, or at most four characters in a
 * script written without spaces. Only a hint (scope 7.5): it misses some (Is It Just Me) and proves nothing.
 */
export function isShortTerm(term: string): boolean {
  const text = term.trim();
  if (UNSPACED.test(text))
    return (
      [...text.replace(/[\s\p{M}]/gu, "")].length <=
      TREND_RULES.shortTermMaxChars
    );
  return (
    text.split(/\s+/).filter(Boolean).length <= TREND_RULES.shortTermMaxWords
  );
}

const EXPLORE = "https://trends.google.com/trends/explore";

/** The term on Google Trends with the row's time range; WW is worldwide, which Trends writes as no geo. */
export function trendsExploreUrl(
  term: string,
  geo: string,
  timeRange: string,
): string {
  const params = [
    `date=${encodeURIComponent(timeRange)}`,
    ...(geo === "WW" ? [] : [`geo=${encodeURIComponent(geo)}`]),
    `q=${encodeURIComponent(term)}`,
  ];
  return `${EXPLORE}?${params.join("&")}`;
}

/** The sparkline's days: the calendar up to the last complete day, a day without complete data as null (a gap). */
export function sparkPoints(
  points: readonly TrendPoint[],
  lastComplete: string,
): (ChartPoint | null)[] {
  return fillCalendar(
    completeValues(points),
    TREND_RULES.sparkDays,
    new Date(`${lastComplete}T00:00:00Z`),
  );
}

export const TREND_SORTS = ["change", "order"] as const;
export type TrendSort = (typeof TREND_SORTS)[number];

type Sortable = Readonly<{ order: number; stats: TrendStats | null }>;

/** change: new first, then rising, flat and falling by change, then too little, then rows without data. */
function group(stats: TrendStats | null): number {
  if (stats === null) return 3;
  if (stats.label === "new") return 0;
  return stats.label === "too_little" ? 2 : 1;
}

function byChange(a: Sortable, b: Sortable): number {
  const byGroup = group(a.stats) - group(b.stats);
  if (byGroup !== 0) return byGroup;
  if (group(a.stats) === 1) {
    const diff = (b.stats?.change ?? 0) - (a.stats?.change ?? 0);
    if (diff !== 0) return diff;
  }
  return a.order - b.order;
}

/** The rows in a new array: by change (ties by our order), or by our own pick order. */
export function sortRows<T extends Sortable>(
  rows: readonly T[],
  by: TrendSort,
): T[] {
  return [...rows].sort(
    by === "order" ? (a, b) => a.order - b.order : byChange,
  );
}
