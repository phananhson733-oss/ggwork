/**
 * The simplified radar's table rules (simplified scope 2026-09-30, sections 2 and 4): the two means over complete
 * days, the change, the label, the short-title hint, the Trends link and the two sort orders. Pure functions; the
 * thresholds live in TREND_RULES and nowhere else.
 */
import { describe, expect, it } from "@rstest/core";

import {
  TREND_LABEL_TEXT,
  TREND_RULES,
  formatChange,
  formatMean,
  isShortTerm,
  lastCompleteDay,
  sortRows,
  sparkPoints,
  trendStats,
  trendsExploreUrl,
  type TrendPoint,
  type TrendStats,
} from "@/core/pick/trends-table";

const LAST = "2026-09-24";

function day(offset: number): string {
  const moment = Date.UTC(2026, 8, 24) + offset * 86_400_000;
  return new Date(moment).toISOString().slice(0, 10);
}

/**
 * A series ending on LAST: `prior` for the 7 days before the recent 7, `recent` for the 7 days up to LAST; a
 * null is a day Google marked without data.
 */
function series(
  prior: readonly (number | null)[],
  recent: readonly (number | null)[],
  extra: readonly TrendPoint[] = [],
): TrendPoint[] {
  const points = [...prior, ...recent].map((value, index) => ({
    date: day(index - 13),
    value,
    partial: false,
  }));
  return [...points, ...extra];
}

const flat = (value: number): number[] =>
  Array.from({ length: 7 }, () => value);

function stats(
  prior: readonly (number | null)[],
  recent: readonly (number | null)[],
): TrendStats {
  return trendStats(series(prior, recent), LAST);
}

describe("the thresholds", () => {
  it("sit in one place, as the scope sets them", () => {
    expect(TREND_RULES).toEqual({
      windowDays: 7,
      changePercent: 25,
      minNonZeroDays: 3,
      sparkDays: 30,
      shortTermMaxWords: 2,
      shortTermMaxChars: 4,
    });
    expect(Object.isFrozen(TREND_RULES)).toBe(true);
  });

  it("name the five labels as the scope does", () => {
    expect(TREND_LABEL_TEXT).toEqual({
      too_little: "数据太少",
      new: "新出现",
      rising: "上升",
      falling: "回落",
      flat: "持平",
    });
  });
});

describe("lastCompleteDay", () => {
  it("is the UTC day before the batch's window_end", () => {
    expect(lastCompleteDay("2026-09-25T00:00:00.000000+00:00")).toBe(LAST);
    expect(lastCompleteDay("2026-09-25T00:00:00+00:00")).toBe(LAST);
    expect(lastCompleteDay("2026-01-01T00:00:00Z")).toBe("2025-12-31");
  });

  it("refuses what is not an aware moment", () => {
    expect(() => lastCompleteDay("2026-09-25")).toThrow();
  });
});

describe("trendStats", () => {
  it("averages the recent and the prior 7 calendar days up to the last complete day", () => {
    const s = stats(flat(10), flat(20));
    expect(s.recentMean).toBe(20);
    expect(s.priorMean).toBe(10);
    expect(s.recentDays).toBe(7);
    expect(s.priorDays).toBe(7);
    expect(s.nonZeroRecent).toBe(7);
    expect(s.change).toBe(100);
    expect(s.label).toBe("rising");
  });

  it("leaves out days Google returned without data, and days that are not complete", () => {
    const partial = { date: day(1), value: 100, partial: true };
    const alsoPartial = { date: day(0), value: 100, partial: true };
    const points = series(
      [10, null, 10, 10, 10, 10, 10],
      [20, 20, 20, 20, 20, 20, null],
      [partial],
    );
    const replaced = points.map((p) =>
      p.date === day(-1) ? { ...alsoPartial, date: day(-1) } : p,
    );
    const s = trendStats(replaced, LAST);
    expect(s.priorDays).toBe(6);
    expect(s.priorMean).toBe(10);
    expect(s.recentDays).toBe(5);
    expect(s.recentMean).toBe(20);
  });

  it("does not count a day missing from the series as zero", () => {
    const points = series(flat(10), [30, 30, 30, 30, 30, 30, 30]).filter(
      (p) => p.date !== day(-3) && p.date !== day(-10),
    );
    const s = trendStats(points, LAST);
    expect(s.recentDays).toBe(6);
    expect(s.priorDays).toBe(6);
    expect(s.recentMean).toBe(30);
    expect(s.priorMean).toBe(10);
  });

  it("ignores points outside the two windows", () => {
    const older = { date: day(-14), value: 100, partial: false };
    const s = trendStats([older, ...series(flat(10), flat(10))], LAST);
    expect(s.priorMean).toBe(10);
  });
});

describe("the label, top to bottom, the first that holds", () => {
  it("too little: fewer than 3 non-zero days in the recent 7, before anything else", () => {
    const s = stats(flat(0), [0, 0, 0, 0, 0, 40, 60]);
    expect(s.nonZeroRecent).toBe(2);
    expect(s.label).toBe("too_little");
    expect(stats(flat(10), [0, 0, 0, 0, 0, 0, 0]).label).toBe("too_little");
  });

  it("new: the prior mean is 0 and the recent mean above 0 (the scope's example)", () => {
    const s = stats(flat(0), [0, 0, 0, 0, 10, 20, 30]);
    expect(s.label).toBe("new");
    expect(s.change).toBeNull();
  });

  it("too little: no prior day with data, so there is nothing to compare", () => {
    const s = stats([null, null, null, null, null, null, null], flat(20));
    expect(s.priorMean).toBeNull();
    expect(s.change).toBeNull();
    expect(s.label).toBe("too_little");
  });

  it("too little: no recent day with data", () => {
    const s = stats(flat(20), [null, null, null, null, null, null, null]);
    expect(s.recentMean).toBeNull();
    expect(s.label).toBe("too_little");
  });

  it("rising from +25% exactly, falling from -25% exactly, flat in between", () => {
    expect(stats(flat(4), flat(5)).label).toBe("rising");
    expect(stats(flat(4), flat(3)).label).toBe("falling");
    expect(stats(flat(40), [49, 49, 49, 49, 49, 49, 49]).label).toBe("flat");
    expect(stats(flat(40), [31, 31, 31, 31, 31, 31, 31]).label).toBe("flat");
  });

  it("compares exactly, not in floating point", () => {
    // 2 against 8/3 is -25% exactly; in floating point it is -24.999...
    const thirds = stats(
      [2, 3, 3, null, null, null, null],
      [2, 2, 2, null, null, null, null],
    );
    expect(thirds.priorMean).toBeCloseTo(8 / 3);
    expect(thirds.recentMean).toBe(2);
    expect(((2 - 8 / 3) / (8 / 3)) * 100).toBeGreaterThan(-25);
    expect(thirds.label).toBe("falling");
    const up = stats(
      [1, 2, 5, null, null, null, null],
      [3, 3, 4, null, null, null, null],
    );
    expect(up.label).toBe("rising");
  });
});

describe("formatting", () => {
  it("writes a mean with one decimal, and none as a dash", () => {
    expect(formatMean(20)).toBe("20.0");
    expect(formatMean(8 / 3)).toBe("2.7");
    expect(formatMean(null)).toBe("—");
  });

  it("writes the change as a signed whole percent, and none as a dash", () => {
    expect(formatChange(100)).toBe("+100%");
    expect(formatChange(-25)).toBe("−25%");
    expect(formatChange(0)).toBe("0%");
    expect(formatChange(24.6)).toBe("+25%");
    expect(formatChange(null)).toBe("—");
  });
});

describe("isShortTerm", () => {
  it("flags one or two words, and up to four characters in scripts written without spaces", () => {
    for (const term of [
      "Stay",
      "Mafia Queen",
      "女王",
      "灰姑娘",
      "花嫁",
      "여왕",
      "สามี",
    ])
      expect([term, isShortTerm(term)]).toEqual([term, true]);
  });

  it("leaves longer titles alone, and knows it misses some", () => {
    for (const term of [
      "The Billionaire's Secret Wife",
      "Is It Just Me",
      "霸道总裁爱上我吧",
      "CEO的替身新娘",
    ])
      expect([term, isShortTerm(term)]).toEqual([term, false]);
  });
});

describe("trendsExploreUrl", () => {
  it("opens the term on Trends with the row's time range; worldwide has no geo", () => {
    expect(trendsExploreUrl("The Bride's Revenge", "WW", "today 1-m")).toBe(
      "https://trends.google.com/trends/explore?date=today%201-m&q=The%20Bride's%20Revenge",
    );
    expect(trendsExploreUrl("女王 & 我", "US", "today 1-m")).toBe(
      "https://trends.google.com/trends/explore?date=today%201-m&geo=US&q=%E5%A5%B3%E7%8E%8B%20%26%20%E6%88%91",
    );
  });
});

describe("sparkPoints", () => {
  it("lays the complete days on the calendar ending at the last complete day, gaps as null", () => {
    const points = sparkPoints(
      [
        { date: day(-2), value: 10, partial: false },
        { date: day(-1), value: null, partial: false },
        { date: day(0), value: 30, partial: false },
        { date: day(1), value: 90, partial: true },
      ],
      LAST,
    );
    expect(points).toHaveLength(30);
    expect(points.slice(-3)).toEqual([
      { day: day(-2), value: 10 },
      null,
      { day: day(0), value: 30 },
    ]);
    expect(points[0]).toBeNull();
  });
});

describe("sortRows", () => {
  type Row = Readonly<{ order: number; stats: TrendStats | null }>;
  const row = (order: number, s: TrendStats | null): Row => ({
    order,
    stats: s,
  });
  const rows: Row[] = [
    row(1, stats(flat(10), flat(10))),
    row(2, null),
    row(3, stats(flat(10), flat(20))),
    row(4, stats(flat(0), [0, 0, 0, 0, 10, 20, 30])),
    row(5, stats(flat(10), [0, 0, 0, 0, 0, 40, 60])),
    row(6, stats(flat(20), flat(10))),
    row(7, stats(flat(10), flat(20))),
  ];

  it("by change: new first, then the change high to low, then too little, then rows without data; ties by order", () => {
    expect(sortRows(rows, "change").map((r) => r.order)).toEqual([
      4, 3, 7, 1, 6, 5, 2,
    ]);
  });

  it("by order: our own pick order", () => {
    expect(sortRows([...rows].reverse(), "order").map((r) => r.order)).toEqual([
      1, 2, 3, 4, 5, 6, 7,
    ]);
  });

  it("returns a new array", () => {
    const sorted = sortRows(rows, "order");
    expect(sorted).not.toBe(rows);
  });
});
