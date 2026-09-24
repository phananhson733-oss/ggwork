// PORTED_FROM: realshort@816ca2e tests/pick-sources.test.ts
// 本地改动：只移植纯函数那几条（:108-143）；Sources 组件那条（:95-106）归 P3-4，迁移 SQL 与集成测试不移植。
// now 在工作台是必填参数（冻结时间漏传要在编译期报错），这里多一条钉住它。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

import { test } from "@rstest/core";

import {
  OBSERVE_SOURCES,
  SOURCE_LABELS,
  STALE_RUNNING_MS,
  catalogImportStatus,
  sourceRowStatus,
  sourceStatus,
  type ObserveSource,
  type SourceState,
} from "@/core/pick-board/source-types";

const NOW = new Date("2026-09-23T12:00:00Z");
const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

const state = (
  source: ObserveSource,
  over: Partial<SourceState> = {},
): SourceState => ({
  source,
  status: "success",
  attemptedAt: ago(3_600_000),
  completedAt: ago(3_000_000),
  details: {},
  ...over,
});

test("五个来源各有一行标签，顺序即页底行序", () => {
  assert.deepEqual(
    [...OBSERVE_SOURCES],
    ["catalog", "snapshot", "bill", "gsc", "pick_catalog"],
  );
  assert.deepEqual(Object.keys(SOURCE_LABELS), [...OBSERVE_SOURCES]);
  assert.equal(SOURCE_LABELS.pick_catalog, "剧单导入（pick_catalog）");
});

test("剧单导入失败，或 running 超过 45 分钟（与导出判僵死同一阈值）：提示剧单可能只写了一半、整次重跑 pnpm catalog-import", () => {
  const failed = state("pick_catalog", {
    status: "failed",
    attemptedAt: "2026-09-23T02:45:00.000Z",
    completedAt: ago(86_400_000),
  });
  const stale = state("pick_catalog", {
    status: "running",
    attemptedAt: ago(STALE_RUNNING_MS),
    completedAt: null,
  });
  for (const s of [failed, stale]) {
    const text = catalogImportStatus(s, NOW);
    assert.match(text, /剧单导入失败或中断/);
    assert.match(text, /只写了一半/);
    assert.match(text, /重跑 pnpm catalog-import/);
  }
  assert.match(catalogImportStatus(failed, NOW), /开始于 2026-09-23 02:45 UTC/);
  assert.equal(STALE_RUNNING_MS, 45 * 60_000);
});

test("剧单导入正在跑（45 分钟内）：写正在导入，不说失败；成功之后与其它来源同一套判断", () => {
  const running = state("pick_catalog", {
    status: "running",
    attemptedAt: ago(STALE_RUNNING_MS - 60_000),
    completedAt: null,
  });
  assert.match(catalogImportStatus(running, NOW), /^正在导入（开始于 .* UTC）/);
  assert.doesNotMatch(catalogImportStatus(running, NOW), /失败|中断/);
  /* 开始时间读不出来的 running 按「正在写」算，与导出一致 */
  assert.match(
    catalogImportStatus({ ...running, attemptedAt: "garbage" }, NOW),
    /^正在导入/,
  );
  const ok = state("pick_catalog");
  assert.equal(catalogImportStatus(ok, NOW), sourceStatus(ok, NOW));
  assert.equal(catalogImportStatus(ok, NOW), "最近采集完成");
  const old = state("pick_catalog", { completedAt: ago(37 * 3_600_000) });
  assert.equal(
    catalogImportStatus(old, NOW),
    "采集记录已超过 36 小时，可能过期",
  );
});

test("sourceRowStatus：只有 pick_catalog 换成导入的文案，其它来源原样用 sourceStatus", () => {
  const failedBill = state("bill", { status: "failed", completedAt: null });
  assert.equal(
    sourceRowStatus("bill", failedBill, NOW),
    sourceStatus(failedBill, NOW),
  );
  assert.equal(
    sourceRowStatus("gsc", undefined, NOW),
    sourceStatus(undefined, NOW),
  );
  assert.equal(
    sourceRowStatus("pick_catalog", undefined, NOW),
    "尚无带标记的导入记录",
  );
});

test("过期判断按传入的冻结时间算，不读墙上时钟：as_of 那一刻不过期的，换个更晚的 now 才过期", () => {
  const done = state("snapshot", { completedAt: "2026-09-20T00:00:00.000Z" });
  assert.equal(
    sourceStatus(done, new Date("2026-09-20T12:00:00Z")),
    "最近采集完成",
  );
  assert.equal(
    sourceStatus(done, new Date("2026-09-22T00:00:00Z")),
    "采集记录已超过 36 小时，可能过期",
  );
});

test("now 是必填参数：三个状态函数的签名里没有 new Date() 默认值", () => {
  const source = readFileSync(
    path.resolve(__dirname, "../../../../src/core/pick-board/source-types.ts"),
    "utf8",
  );
  assert.doesNotMatch(source, /\bnow(?:\s*:\s*Date)?\s*=/);
  assert.doesNotMatch(source, /new Date\(\)/, "模块里不读墙上时钟");
});
