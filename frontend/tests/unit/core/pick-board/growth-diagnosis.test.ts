// PORTED_FROM: realshort@816ca2e tests/pick-growth-diagnosis.test.ts
// 本地改动：node:test 换成 @rstest/core；原 :67-76「dp1 接线」读 observe/queries.ts 与 queries-rank.ts 的源码，挪到 P3-3 的集成测试。
import assert from "node:assert/strict";

import { test } from "@rstest/core";

import {
  addUtcDays,
  diagnoseGrowthEmpty,
  type GrowthFacts,
} from "@/core/pick-board/growth-diagnosis";
import { SORT_LABELS, SORTS, comparisonValue } from "@/core/pick-board/metrics";
import { GROWTH_SORTS } from "@/core/pick-board/request";

/** 2026-09-14 那天按「推广人数 7 天变化」排：基线日 09-07，保留期里第一个已校验快照是 09-10 */
const base: GrowthFacts = {
  windowDays: 7,
  baselineDay: "2026-09-07",
  baselineSnapshot: "none",
  earliestVerifiedOn: "2026-09-10",
  filtered: false,
  comparableWithoutFilters: false,
};

test("基线日早于第一个已校验快照日：给最早可能有数的日子 = 第一个已校验日 + 窗口天数，一定晚于今天", () => {
  const d = diagnoseGrowthEmpty(base);
  assert.equal(d.reason, "baseline_before_first_verified_snapshot");
  assert.equal(d.earliestPossibleOn, "2026-09-17");
  assert.equal(
    diagnoseGrowthEmpty({ ...base, windowDays: 1, baselineDay: "2026-09-09" })
      .earliestPossibleOn,
    "2026-09-11",
  );
});

test("筛了语种 / 分桶、不加筛选时全库有可比行：filters_empty，不给日期；全库也没有时原因落回快照那一层", () => {
  const d = diagnoseGrowthEmpty({
    ...base,
    baselineDay: "2026-09-13",
    baselineSnapshot: "verified",
    filtered: true,
    comparableWithoutFilters: true,
  });
  assert.equal(d.reason, "filters_empty");
  assert.equal(d.earliestPossibleOn, null);
  assert.equal(
    diagnoseGrowthEmpty({
      ...base,
      filtered: true,
      comparableWithoutFilters: false,
    }).reason,
    "baseline_before_first_verified_snapshot",
    "全库也没有可比行时不是筛选的锅",
  );
  assert.notEqual(
    diagnoseGrowthEmpty({
      ...base,
      filtered: false,
      comparableWithoutFilters: true,
    }).reason,
    "filters_empty",
    "没加筛选就不可能是筛空了",
  );
});

test("基线日已在已校验快照之后：那天没快照 / 未校验 / 快照在仍 0 行三种各说各的，都不给恢复日期", () => {
  const after = { ...base, baselineDay: "2026-09-20" };
  const missing = diagnoseGrowthEmpty({ ...after, baselineSnapshot: "none" });
  assert.equal(missing.reason, "baseline_snapshot_missing");
  assert.equal(
    missing.earliestPossibleOn,
    null,
    "断档之后下一个可用基线日这几件事实推不出来",
  );
  assert.equal(
    diagnoseGrowthEmpty({ ...after, baselineSnapshot: "unverified_only" })
      .reason,
    "baseline_snapshot_unverified",
  );
  assert.equal(
    diagnoseGrowthEmpty({ ...after, baselineSnapshot: "verified" }).reason,
    "no_comparable_rows",
  );
  /* gpt-6-astra 反例：基线日恰好等于第一个已校验日时不是「还没攒到」 */
  assert.equal(
    diagnoseGrowthEmpty({
      ...base,
      baselineDay: "2026-09-10",
      baselineSnapshot: "verified",
    }).reason,
    "no_comparable_rows",
  );
});

test("保留期里一个已校验快照都没有：no_verified_snapshot，不给日期", () => {
  const d = diagnoseGrowthEmpty({ ...base, earliestVerifiedOn: null });
  assert.equal(d.reason, "no_verified_snapshot");
  assert.equal(d.earliestPossibleOn, null);
});

test("addUtcDays 跨月、跨年按 UTC 日历推；不是 YYYY-MM-DD 回 null", () => {
  assert.equal(addUtcDays("2026-09-28", 7), "2026-10-05");
  assert.equal(addUtcDays("2026-12-30", 7), "2027-01-06");
  assert.equal(addUtcDays("09-10", 7), null);
  assert.equal(addUtcDays("2026-13-40", 1), null);
});

/* ---------- 推广人数较昨日（dp1，问答审计 P2-14）：排序键在五个地方，漏任何一处都不报错 ---------- */

test("dp1：白名单、标签、涨幅榜排序、可比值都认它；可比值看昨天那行快照的推广人数", () => {
  assert.ok(
    (SORTS as readonly string[]).includes("dp1"),
    "不在 SORTS 里会被 parse 回 rr",
  );
  assert.equal(SORT_LABELS.dp1, "推广人数较昨日变化");
  assert.ok(
    GROWTH_SORTS.includes("dp1"),
    "不在 GROWTH_SORTS 里 rsSortFor 回落 d7",
  );
  const row = {
    revenueCents1: 1,
    revenueCents7: 7,
    promotersCnt1: null,
    promotersCnt7: 70,
  };
  assert.equal(
    comparisonValue(row, "dp1"),
    null,
    "昨天没快照：页面上这一行不进榜",
  );
  assert.equal(comparisonValue({ ...row, promotersCnt1: 10 }, "dp1"), 10);
  assert.equal(comparisonValue(row, "dp7"), 70);
});
