import { describe, expect, it } from "@rstest/core";

import {
  hotScopeLine,
  itemFactsLine,
  relaxationLine,
  zeroDiagnosisLines,
} from "@/core/pick/notes";

// Evaluation batch 2 (2026-10-05): the card shows what the model was told
// beside a result, in words, and never turns an uncountable step into 0.
describe("pick result notes", () => {
  it("spells out an item's facts and admits missing ones", () => {
    expect(
      itemFactsLine({
        tags: ["复仇", "豪门"],
        listed_at: "2026-09-01T00:00:00Z",
        channel_rules: {
          youtube: "allowed",
          tiktok: "denied",
          facebook: "unknown",
        },
      }),
    ).toBe(
      "标签 复仇、豪门 · 上架 2026-09-01 · youtube 可发、tiktok 禁发、facebook 待核实",
    );
    expect(
      itemFactsLine({ tags: [], listed_at: null, channel_rules: {} }),
    ).toBe("标签未注明 · 上架日期未知 · 渠道规则未注明");
    expect(itemFactsLine(undefined)).toBeNull();
  });
  it("names each relaxed condition, what went with it, and an uncountable step", () => {
    expect(
      relaxationLine({ condition: "tags", value: ["复仇"], matched_total: 2 }),
    ).toBe("去掉「标签 复仇」：2 部");
    expect(
      relaxationLine({
        condition: "signal_kind",
        value: "kd",
        also_removed: ["sort"],
        matched_total: 0,
      }),
    ).toBe("去掉「榜单 kd」（连同按名次排序）：0 部");
    expect(
      relaxationLine({
        condition: "exclude_posted",
        value: true,
        matched_total: null,
        unavailable: "去掉这一项后会碰到没有发布记录的剧，数不出来",
      }),
    ).toBe(
      "去掉「排除团队已发」：去掉这一项后会碰到没有发布记录的剧，数不出来",
    );
    expect(
      relaxationLine({ condition: "excluded", value: 7, matched_total: 3 }),
    ).toBe("去掉「排除已选与看过的 7 部」：3 部");
  });
  it("cautions only when no single relaxation helps", () => {
    const base = { catalog_rows: 10, delisted_rows: 1 };
    const helped = zeroDiagnosisLines({
      ...base,
      without_each: [{ condition: "language", value: "en", matched_total: 4 }],
    });
    expect(helped.lead).toContain("共 10 部（已下架 1 部不计入）");
    expect(helped.steps).toEqual(["去掉「语种 en」：4 部"]);
    expect(helped.caution).toBeNull();
    const stuck = zeroDiagnosisLines({
      ...base,
      without_each: [{ condition: "language", value: "en", matched_total: 0 }],
    });
    expect(stuck.caution).toContain("同时调整几项");
    expect(zeroDiagnosisLines({ ...base, without_each: [] }).lead).toContain(
      "没有可以放宽的条件",
    );
  });
  it("says what hot_only counted", () => {
    expect(hotScopeLine({ counted: ["kd", "qc"], not_counted: ["clk"] })).toBe(
      "热门依据算了 kd、qc；不算 clk",
    );
    expect(hotScopeLine({ counted: [], not_counted: [] })).toBe(
      "这批剧库里没有能算作热门依据的榜单",
    );
  });
});
