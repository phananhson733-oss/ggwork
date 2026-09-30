/**
 * The data page's radar banners (plan TR-24, D10): the TS twin of
 * ggwork_pick/observe/status_rules.channel_banners, computed at request time
 * from pick_obs.run_status and the live set. tests/fixtures/obs_status_cases.json
 * is the shared truth; Python's test_obs_status_rules runs the same cases.
 */
import { describe, expect, it } from "@rstest/core";

import {
  channelBanners,
  runMissed,
  trendsDueDate,
  trendsSetStale,
  trendsTableStale,
  type LatestRunInput,
} from "@/components/workspace/pick-board/views/obs-banner-rules";

import { repoText } from "../../../../core/pick/obs-contract-fixtures";

type Case = Readonly<{
  name: string;
  channel: "trends" | "gsc";
  now: string;
  live_published_at: string | null;
  table_through?: string | null;
  latest_run: LatestRunInput | null;
  expected: { code: string; level: string }[];
}>;

const FIXTURE = JSON.parse(
  repoText(
    "customizations/pick-workbench/tests/fixtures/obs_status_cases.json",
  ),
) as { cases: Case[]; params: Record<string, unknown> };

describe("channelBanners", () => {
  it.each(FIXTURE.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(
      channelBanners(c.channel, {
        latestRun: c.latest_run,
        livePublishedAt: c.live_published_at,
        now: c.now,
        tableThrough: c.table_through ?? null,
      }),
    ).toEqual(c.expected);
  });

  it("uses the fixture's parameters", () => {
    expect(FIXTURE.params).toEqual({
      trends_stale_hours: 26,
      trends_due_utc: "02:30",
      gsc_missed_hours: 4,
    });
  });

  it("takes the request's Date as now", () => {
    const run: LatestRunInput = {
      started_at: "2026-09-25T03:25:04.000000+00:00",
      mode: "live",
      target_date: null,
      status_codes: [],
    };
    const banners = (now: Date) =>
      channelBanners("gsc", { latestRun: run, livePublishedAt: null, now });
    expect(banners(new Date("2026-09-25T07:25:04.000Z"))).toEqual([]);
    expect(banners(new Date("2026-09-25T07:25:04.001Z"))).toEqual([
      { code: "run_missed", level: "red" },
    ]);
  });

  it("shows a code a newer collector wrote as red, after the known ones", () => {
    const run: LatestRunInput = {
      started_at: "2026-09-24T20:30:05.000000+00:00",
      mode: "shadow",
      target_date: "2026-09-25",
      status_codes: ["future_code", "usertype_changed", "extinguished_today"],
    };
    expect(
      channelBanners("trends", {
        latestRun: run,
        livePublishedAt: null,
        now: "2026-09-25T04:00:00.000000+00:00",
      }),
    ).toEqual([
      { code: "extinguished_today", level: "red" },
      { code: "future_code", level: "red" },
      { code: "usertype_changed", level: "warn" },
      { code: "shadow_mode", level: "info" },
    ]);
  });

  it("repeats no code", () => {
    const run: LatestRunInput = {
      started_at: "2026-09-24T20:30:05.000000+00:00",
      mode: "live",
      target_date: "2026-09-24",
      status_codes: ["run_missed", "run_missed"],
    };
    expect(
      channelBanners("trends", {
        latestRun: run,
        livePublishedAt: null,
        now: "2026-09-25T04:00:00.000000+00:00",
      }),
    ).toEqual([{ code: "run_missed", level: "red" }]);
  });
});

describe("the three time rules", () => {
  it("a Trends set over 26 hours is stale; exactly 26 hours is not", () => {
    const published = "2026-09-25T01:52:10.000000+00:00";
    expect(trendsSetStale(published, "2026-09-26T03:52:10.000000+00:00")).toBe(
      false,
    );
    expect(trendsSetStale(published, "2026-09-26T03:52:10.000001+00:00")).toBe(
      true,
    );
    expect(trendsSetStale(null, "2030-01-01T00:00:00.000000+00:00")).toBe(
      false,
    );
  });

  it("the due target date turns at 02:30 UTC", () => {
    expect(trendsDueDate("2026-09-26T02:29:59.999999+00:00")).toBe(
      "2026-09-25",
    );
    expect(trendsDueDate("2026-09-26T02:30:00.000000+00:00")).toBe(
      "2026-09-26",
    );
  });

  it("a table batch due today is stale from 02:30 UTC; none yet is not", () => {
    expect(
      trendsTableStale("2026-09-25", "2026-09-26T02:29:59.999999+00:00"),
    ).toBe(false);
    expect(
      trendsTableStale("2026-09-25", "2026-09-26T02:30:00.000000+00:00"),
    ).toBe(true);
    expect(trendsTableStale(null, "2030-01-01T00:00:00.000000+00:00")).toBe(
      false,
    );
  });

  it("tableThrough belongs to Trends only", () => {
    expect(() =>
      channelBanners("gsc", {
        latestRun: null,
        livePublishedAt: null,
        now: "2026-09-26T04:00:00.000000+00:00",
        tableThrough: "2026-09-25",
      }),
    ).toThrow();
  });

  it("a channel that never ran has not missed a run", () => {
    expect(runMissed("trends", null, "2030-01-01T00:00:00.000000+00:00")).toBe(
      false,
    );
    expect(runMissed("gsc", null, "2030-01-01T00:00:00.000000+00:00")).toBe(
      false,
    );
  });
});
