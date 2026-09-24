// PORTED_FROM: realshort@816ca2e tests/pick-rank-week.test.ts
// 本地改动：node:test 换成 @rstest/core；「接线」一条拆成查询（P3-3 落地后）与组件（P3-4 落地后）两段、改读工作台路径；
// 原 :61-70 断言问答工具 lib/ask/tools.ts，问答不移植，删掉。
import assert from "node:assert/strict";

import { describe, test } from "@rstest/core";

import {
  parsePickRequest,
  resolveDay,
  resolveWeek,
  weekText,
  type WeekOption,
} from "@/core/pick-board/request";

import {
  COMPONENTS_DIR,
  LANDED,
  QUERIES_RANK_FILE,
  readSource,
} from "./ported-source";

/* 周标签不带年份：「4.20–4.26」在 2025 与 2026 各有一周（问答审计 P1-6 实测的碰撞形态） */
const WEEKS: WeekOption[] = [
  { week: "8.10–8.16", start: "2026-08-10" },
  { week: "4.20–4.26", start: "2026-04-20" },
  { week: "4.20–4.26", start: "2025-04-20" },
  { week: "3.2–3.8", start: "2025-03-02" },
];

test("resolveWeek：周起日期精确对上；旧周标签唯一才认；跨年重名取最近那周并标 ambiguous；对不上回落最近一周并标 missing（问答审计 P1-6）", () => {
  assert.deepEqual(resolveWeek(WEEKS, ""), {
    start: "2026-08-10",
    how: "latest",
  });
  assert.deepEqual(
    resolveWeek(WEEKS, "2025-04-20"),
    { start: "2025-04-20", how: "exact" },
    "去年那周按周起日期能单独选到",
  );
  assert.deepEqual(resolveWeek(WEEKS, "2026-09-07"), {
    start: "2026-08-10",
    how: "missing",
  });
  assert.deepEqual(resolveWeek(WEEKS, "3.2–3.8"), {
    start: "2025-03-02",
    how: "label",
  });
  assert.deepEqual(resolveWeek(WEEKS, "4.20–4.26"), {
    start: "2026-04-20",
    how: "ambiguous",
  });
  assert.deepEqual(resolveWeek(WEEKS, "9.1–9.7"), {
    start: "2026-08-10",
    how: "missing",
  });
  assert.deepEqual(resolveWeek([], "2026-08-10"), {
    start: "",
    how: "missing",
  });
  assert.deepEqual(resolveWeek([], ""), { start: "", how: "latest" });
});

test("resolveDay：没指定取最近一天；不存在的日子回落并标 missing", () => {
  const days = ["2026-09-14", "2026-09-13"];
  assert.deepEqual(resolveDay(days, ""), { day: "2026-09-14", how: "latest" });
  assert.deepEqual(resolveDay(days, "2026-09-13"), {
    day: "2026-09-13",
    how: "exact",
  });
  assert.deepEqual(resolveDay(days, "2026-09-01"), {
    day: "2026-09-14",
    how: "missing",
  });
});

test("weekText：chip 上写周标签，同一个标签对应不止一周时补年份", () => {
  const t = weekText(WEEKS);
  assert.equal(t("2026-08-10"), "8.10–8.16");
  assert.equal(t("2026-04-20"), "4.20–4.26（2026）");
  assert.equal(t("2025-04-20"), "4.20–4.26（2025）");
  assert.equal(t("2099-01-01"), "2099-01-01");
});

test("week 参数收周起日期，也收旧书签里的周标签", () => {
  assert.equal(
    parsePickRequest(new URLSearchParams("tab=rank&rk=kw&week=2025-04-20"))
      .week,
    "2025-04-20",
  );
  assert.equal(
    parsePickRequest(
      new URLSearchParams("tab=rank&rk=kw&week=4.20%E2%80%934.26"),
    ).week,
    "4.20–4.26",
  );
});

const strip = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe.skipIf(!LANDED.queries)("周榜接线：查询（P3-3 落地后）", () => {
  test("周列表按周起分组、周榜按周起过滤，页面拿周起当周的身份", () => {
    const q = strip(readSource(QUERIES_RANK_FILE));
    assert.match(
      q,
      /SELECT x->>0 AS start, max\(x->>1\) AS w FROM catalog_signals s[\s\S]*?GROUP BY start ORDER BY start DESC/,
    );
    assert.match(q, /WHERE x->>0 = \$\{meta\.week\}\)/);
    assert.doesNotMatch(
      q,
      /x->>1 = \$\{meta\.week\}/,
      "按周标签过滤会把两年的行混进同一张榜",
    );
    assert.match(q, /resolveWeek\(weeks, req\.week\)/);
    assert.match(q, /resolveDay\(days, req\.day\)/);
  });
});

describe.skipIf(!LANDED.components)("周榜接线：组件（P3-4 落地后）", () => {
  test("周 chips 与小图按周起取值，不按周标签比对", () => {
    assert.match(
      strip(readSource(`${COMPONENTS_DIR}/rank-filters.tsx`)),
      /values=\{meta\.weeks\.map\(\(w\) => w\.start\)\}/,
    );
    const table = strip(readSource(`${COMPONENTS_DIR}/rank-table.tsx`));
    assert.match(table, /\.map\(\(w\) => w\.start\)/);
    assert.doesNotMatch(table, /x\.week === meta\.week/);
  });
});
