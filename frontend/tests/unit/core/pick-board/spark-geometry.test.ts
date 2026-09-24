// PORTED_FROM: realshort@816ca2e tests/pick-spark.test.ts
// 本地改动：node:test 换成 @rstest/core；下标取值经 nth()，noUncheckedIndexedAccess 下缺元素直接失败。
import assert from "node:assert/strict";

import { test } from "@rstest/core";

import {
  SPARK_H,
  SPARK_W,
  dayEntries,
  rankSpark,
  weekGrid,
} from "@/core/pick-board/spark-geometry";

function nth<T>(items: readonly T[], index: number): T {
  const item = items[index];
  assert.ok(item !== undefined, `缺第 ${index} 个元素`);
  return item;
}

const days = [
  "2026-09-01",
  "2026-09-02",
  "2026-09-03",
  "2026-09-04",
  "2026-09-05",
];

test("日榜折线：横轴是有榜的日子不是日历，掉榜的间隔画虚线不插值，#1 在上", () => {
  const g = rankSpark(
    [
      ["2026-09-01", 3],
      ["2026-09-02", 1, "新上"],
      ["2026-09-05", 10],
      ["2026-08-20", 2],
    ],
    days,
    "2026-09-05",
  );
  assert.ok(g);
  assert.equal(g.dots.length, 3, "不在这 5 天里的 08-20 不画");
  assert.equal(g.segs.length, 2);
  assert.equal(nth(g.segs, 0).gap, false, "09-01 到 09-02 相邻");
  assert.equal(
    nth(g.segs, 1).gap,
    true,
    "09-02 到 09-05 中间隔了两个榜单日，是掉过榜",
  );
  assert.ok(nth(g.dots, 1).y < nth(g.dots, 0).y, "#1 比 #3 靠上");
  assert.equal(nth(g.dots, 2).y, g.yBottom, "#10 钉在底线");
  assert.equal(nth(g.dots, 1).y, g.yTop, "#1 钉在顶线");
  assert.equal(g.dots.filter((d) => d.cur).length, 1);
  assert.equal(nth(g.dots, 2).cur, true, "当前选中的那天是实心大点");
  for (const d of g.dots) {
    assert.ok(
      d.x >= 0 && d.x <= SPARK_W && d.y >= 0 && d.y <= SPARK_H,
      "点都在画布内",
    );
  }
  assert.match(g.title, /09-02 #1/);
});

test("日榜折线：超过 #10 钉在底线，一次都没上榜或只有一个横轴刻度就没有图", () => {
  const g = rankSpark([["2026-09-03", 25]], days, "");
  assert.ok(g);
  assert.equal(nth(g.dots, 0).y, g.yBottom);
  assert.equal(
    rankSpark([["2026-08-01", 1]], days, ""),
    null,
    "这段日子里没上过榜",
  );
  assert.equal(
    rankSpark([["2026-09-01", 1]], ["2026-09-01"], ""),
    null,
    "只有一天撑不起横轴",
  );
  assert.equal(rankSpark("garbage", days, ""), null);
  assert.deepEqual(
    dayEntries([
      ["2026-09-01", "3"],
      ["2026-09-02", 0],
      ["2026-09-03", 2],
    ]),
    [["2026-09-03", 2]],
    "名次不是正整数的条目丢掉",
  );
});

test("周格：每格一周，实心是上榜，描边是选中，折线是累计", () => {
  const weeks = ["2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25"];
  const g = weekGrid(
    [
      ["2026-08-04", "8/4-8/10"],
      ["2026-08-18", "8/18-8/24"],
    ],
    weeks,
    "2026-08-18",
    7,
  );
  assert.ok(g);
  assert.equal(g.cells.length, 4);
  assert.deepEqual(
    g.cells.map((c) => c.on),
    [true, false, true, false],
  );
  assert.deepEqual(
    g.cells.map((c) => c.cur),
    [false, false, true, false],
  );
  assert.equal(g.hit, 2);
  assert.equal(g.title, "近 4 周上榜 2 周，累计 7 周");
  assert.match(g.path, /^M[\d.]+,[\d.]+(L[\d.]+,[\d.]+){3}$/, "四个点的折线");
  const lastCell = nth(g.cells, 3);
  assert.ok(lastCell.x + lastCell.w <= g.labelX, "格子不压到右边的计数字");
  assert.equal(weekGrid([], [], "", 0), null);
});

test("周格按周起认：同一个周标签在不同年份各有一周，去年那周不点亮今年的格子（问答审计 P1-6）", () => {
  const axis = ["2026-04-13", "2026-04-20"];
  const lastYear = weekGrid(
    [["2025-04-20", "4.20–4.26"]],
    axis,
    "2026-04-20",
    1,
  );
  assert.ok(lastYear);
  assert.deepEqual(
    lastYear.cells.map((c) => c.on),
    [false, false],
  );
  const thisYear = weekGrid(
    [["2026-04-20", "4.20–4.26"]],
    axis,
    "2026-04-20",
    1,
  );
  assert.equal(thisYear?.cells[1]?.on, true);
});
