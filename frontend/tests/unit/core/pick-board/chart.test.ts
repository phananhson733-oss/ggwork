// PORTED_FROM: realshort@816ca2e tests/observe.test.ts（曲线几何 :151-217）
// 本地改动：node:test 换成 @rstest/core；只取 chart.ts 的纯函数用例。
import assert from "node:assert/strict";

import { test } from "@rstest/core";

import { buildChart, fillCalendar } from "@/core/pick-board/chart";

const OPTS = {
  width: 640,
  height: 190,
  padLeft: 58,
  padRight: 14,
  padTop: 12,
  padBottom: 26,
};

test("断档处折线真的断开，并单独给一段虚线跨过去", () => {
  // 缺的那天是"同步没跑成"，不是"数值没变"。连成直线等于替上游编了一个数
  const pts = [
    { day: "2026-09-01", value: 10 },
    null,
    { day: "2026-09-03", value: 30 },
  ];
  const g = buildChart(pts, OPTS);
  assert.equal((g.line.match(/M/g) ?? []).length, 2, "折线要断成两段");
  assert.ok(g.gap.length > 0, "断口要有虚线");
  assert.ok(!g.line.includes("NaN"));
});

test("全序列同值不产生 NaN——那会让整条线静默画不出来", () => {
  const pts = Array.from({ length: 5 }, (_, i) => ({
    day: `2026-09-0${i + 1}`,
    value: 42,
  }));
  const g = buildChart(pts, OPTS);
  assert.ok(!g.line.includes("NaN"));
  assert.ok(!g.area.includes("NaN"));
  for (const t of g.ticks) assert.ok(Number.isFinite(t.y));
});

test("一个点也画得出来，不抛错", () => {
  const g = buildChart([{ day: "2026-09-08", value: 1 }], OPTS);
  assert.ok(g.last);
  assert.ok(!g.line.includes("NaN"));
});

test("全是空洞时不炸，且没有末点", () => {
  const g = buildChart([null, null], OPTS);
  assert.equal(g.last, null);
  assert.equal(g.line, "");
});

test("稀疏快照按日历铺开，缺的天补 null 而不是压缩掉", () => {
  // 不铺开的话，一段两周的平缓期会和一天的暴涨画得一样宽，横轴就不再是时间
  const today = new Date("2026-09-08T00:00:00Z");
  const filled = fillCalendar(
    [
      { day: "2026-09-08", value: 3 },
      { day: "2026-09-05", value: 1 },
    ],
    5,
    today,
  );
  assert.equal(filled.length, 5);
  assert.deepEqual(
    filled.map((p) => p?.value ?? null),
    [null, 1, null, null, 3],
  );
  // 窗口是 [今天-4, 今天]，所以起点是 09-04 而不是最早那条数据的日子
  assert.equal(filled[0]?.day ?? "2026-09-04", "2026-09-04");
  assert.equal(filled.at(-1)?.day, "2026-09-08");
});
