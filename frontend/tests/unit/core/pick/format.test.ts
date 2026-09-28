import { describe, expect, it } from "@rstest/core";

import {
  conditionsLine,
  dataAsOfLine,
  evidenceLine,
  postedLine,
} from "@/core/pick/format";
import { UNOBSERVED_GSC, forbiddenIn } from "@/core/pick/obs-format";

describe("pick display lines", () => {
  it("names shared sync time and source freshness, and admits unknown time", () => {
    const line = dataAsOfLine({
      source_as_of: "2026-09-23T03:00:00.000Z",
      published_at: "2026-09-23T03:00:05Z",
      freshness: { catalogImportedAt: "2026-09-22T03:19:21.327Z" },
      shared: true,
    });
    expect(line).toContain("RealShort 同步于");
    expect(line).toContain("剧单导入");
    expect(line).not.toContain("ReelShort 指标采集");
    expect(dataAsOfLine(null)).toContain("未知");
  });
  it("prefers structured rank over the legacy JSON value", () => {
    expect(
      evidenceLine({
        citation_id: "c",
        kind: "kd",
        source_ref: "r",
        observed_at: null,
        value: null,
        label: "KalosTV 日榜",
        rank: 3,
        grade: "",
        note: "",
      }),
    ).toBe("KalosTV 日榜 · 第3名");
    expect(
      evidenceLine({
        citation_id: "c",
        kind: "rank",
        source_ref: "r",
        observed_at: null,
        value: null,
      }),
    ).toBe("rank · 数值未知");
  });
  it("never turns an unmatched record into 'not posted'", () => {
    expect(
      postedLine({
        matched: false,
        records: [],
        post_count: 0,
        sched_count: 0,
        last_post_on: null,
        accounts: [],
      }),
    ).toContain("不代表从未发布");
    expect(
      postedLine({
        matched: true,
        records: ["SD-1"],
        post_count: 2,
        sched_count: 1,
        last_post_on: "2026-09-10",
        accounts: ["a", "b", "c", "d"],
      }),
    ).toBe(
      "发布记录：已发 2 条 · 待公开 1 条 · 最近 2026-09-10 · 账号 a、b、c 等",
    );
    expect(postedLine(undefined)).toBeNull();
    expect(
      postedLine(
        {
          matched: false,
          records: [],
          post_count: 0,
          sched_count: 0,
          last_post_on: null,
          accounts: [],
        },
        { exclude_posted: false, posted_account: "acc" },
      ),
    ).toBeNull();
  });
  it("names hot_only between the board and the posted filters", () => {
    expect(
      conditionsLine({
        limit: 5,
        exclude_selected: false,
        language: "en",
        hot_only: true,
        exclude_posted: true,
      }),
    ).toBe("全部剧场 · en · 包含我的已选 · 只要热门依据 · 排除团队已发");
  });
  it("spells out posted and rank filters", () => {
    expect(
      conditionsLine({
        limit: 5,
        exclude_selected: true,
        signal_kind: "kd",
        sort: "rank",
        exclude_posted: true,
        posted_account: "acc",
      }),
    ).toBe(
      "全部剧场 · 全部语种 · 排除我的已选 · 按榜单名次 kd · 排除团队已发 · 排除账号 acc 已发",
    );
  });
});

// Plan TR-16: evidenceLine hands obs_* kinds to obs-format; conditionsLine
// spells out the observation conditions and the obs order.
describe("observation lines (TR-16)", () => {
  const gsc = {
    citation_id: "i:4",
    kind: "obs_gsc",
    source_ref: "obs:3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c:907",
    observed_at: "2026-09-24T18:00:00.000000+00:00",
    value: null,
    label: "USA · 前一窗口",
    rank: null,
    grade: "formal",
    note: "gsc-rules-v1；截至 2026-09-24T18:00:00.000000+00:00",
  };
  it("says an obs value was not observed instead of 'value unknown'", () => {
    const line = evidenceLine(gsc);
    expect(line).toContain(UNOBSERVED_GSC);
    expect(line).not.toContain("数值未知");
    expect(line).not.toContain("评级");
  });
  it("never prints a zero count for an obs kind", () => {
    const line = evidenceLine({ ...gsc, value: 0 });
    expect(line).not.toMatch(/ 0( |$)/);
    expect(forbiddenIn(line)).toEqual([]);
  });
  it("leaves the other kinds as they were", () => {
    expect(
      evidenceLine({ ...gsc, kind: "gsc", label: "GSC", grade: "", note: "" }),
    ).toBe("GSC · 数值未知");
    expect(evidenceLine({ ...gsc, kind: "gsc", label: "GSC", note: "" })).toBe(
      "GSC · 评级 formal",
    );
  });
  it("adds the observation conditions and the obs order to the conditions line", () => {
    expect(
      conditionsLine({
        limit: 5,
        exclude_selected: true,
        sort: "obs",
        trend_state: "rising",
        trend_geos: ["US"],
        gsc_countries: ["USA"],
      }),
    ).toBe(
      "全部剧场 · 全部语种 · 排除我的已选 · Google Trends 上升观察 · Trends 地区 US · GSC 国家 USA · 按观测状态排序",
    );
  });
});
