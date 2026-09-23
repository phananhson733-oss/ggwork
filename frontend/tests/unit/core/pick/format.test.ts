import { describe, expect, it } from "@rstest/core";

import {
  conditionsLine,
  dataAsOfLine,
  evidenceLine,
  postedLine,
} from "@/core/pick/format";

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
