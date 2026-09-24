// PORTED_FROM: realshort@816ca2e tests/pick-sort-hints.test.ts
// 本地改动：node:test 换成 @rstest/core；源码改读工作台路径，toolbar / rows-table 等 P3-4、queries 等 P3-3 落地后才跑；
// 原 :14、:85-86 读 lib/pick/glossary.ts 的术语表原文，术语表现在随版本数据走，改对 meta.rules 夹具断言。
import assert from "node:assert/strict";

import { describe, test } from "@rstest/core";

import rules from "./fixtures/rules.json";
import {
  COMPONENTS_DIR,
  LANDED,
  QUERIES_FILE,
  readSource,
} from "./ported-source";

/**
 * 排序 chip 的 hover 说明与 `orderBy()` 的键序必须逐键一致（用户 2026-09-12 要求写明规则）。
 * 说明文字是给人看的，SQL 是真正跑的；两边漂了页面就在撒谎，而 typecheck / lint 都不会报。
 * 这里从两个源文件里各自抠出键序，逐条比对，不写死具体字数。
 */

const toolbar = () => readSource(`${COMPONENTS_DIR}/toolbar.tsx`);
const rowsTable = () => readSource(`${COMPONENTS_DIR}/rows-table.tsx`);

function orderByKeys(sort: string): string[] {
  const m = new RegExp(
    `case "${sort}":\\s*(?:default:\\s*)?return sql\`([^\`]+)\``,
  ).exec(readSource(QUERIES_FILE));
  assert.ok(m?.[1], `orderBy() 里找不到 case "${sort}"`);
  return m[1].split(",").map((k) =>
    k
      .trim()
      .replace(/^rows\./, "")
      .replace(/\s+(ASC|DESC).*$/, ""),
  );
}

function hint(sort: string): string {
  const block = /const SORT_HINTS[^=]*=\s*\{([\s\S]*?)\n\};/.exec(
    toolbar(),
  )?.[1];
  assert.ok(block, "toolbar.tsx 里找不到 SORT_HINTS");
  const m = new RegExp(
    `\\n\\s*${sort}:\\s*\\n?([\\s\\S]*?)(?:\\n\\s*(?:listed|title|evidence):|$)`,
  ).exec(block);
  assert.ok(m?.[1] !== undefined, `SORT_HINTS 里找不到 ${sort}`);
  return m[1].replace(/"\s*\+\s*"/g, "").replace(/[\s"]/g, "");
}

const NAME: Record<string, string> = {
  latest_evidence_on: "证据",
  listed_on: "剧单日期",
  title: "剧名",
  platform: "剧场",
  row_key: "行键",
};

describe.skipIf(!(LANDED.components && LANDED.queries))(
  "排序说明与 orderBy（P3-3、P3-4 都落地后）",
  () => {
    for (const sort of ["evidence", "listed", "title"] as const) {
      test(`排序 chip「${sort}」的 hover 与 orderBy() 键序一致`, () => {
        const keys = orderByKeys(sort);
        const text = hint(sort);
        let pos = 0;
        for (const key of keys) {
          const label = NAME[key];
          assert.ok(label, `orderBy() 出现了说明表里没有的键 ${key}`);
          const idx = text.indexOf(label, pos);
          assert.ok(
            idx >= 0,
            `「${sort}」的说明里 ${label}（${key}）没有按 ORDER BY 的顺序出现`,
          );
          pos = idx + label.length;
        }
      });
    }
  },
);

describe.skipIf(!LANDED.components)("排序与列头说明（P3-4 落地后）", () => {
  test("SORT_HINTS 挂到了排序 chip 的 title 上", () => {
    assert.match(toolbar(), /title: SORT_HINTS\[s\]/);
    assert.match(toolbar(), /title=\{item\.title\}/);
  });

  test("证据时间的说明写明了「没有日期排最后」与「不是投放价值排序」", () => {
    const text = hint("evidence");
    assert.ok(
      text.includes("鹊娱"),
      "2026-09-12 起鹊娱两张榜也是证据，说明里要有它的日期口径",
    );
    assert.ok(text.includes("没有日期"), "要说明无日期的行排在哪");
    assert.ok(text.includes("不是投放价值"), "浏览顺序与投放价值必须分开说");
    assert.ok(
      text.includes("不拿剧单日期代填"),
      "证据日期不代填是既有约束，说明里要写",
    );
  });

  test("「类型 · 频道」列头有口径说明，且说清 ReelShort 上游没有这两个字段", () => {
    const m = /text: "类型 · 频道",\s*hint:\s*([\s\S]*?)\n\s*\},/.exec(
      rowsTable(),
    );
    assert.ok(m?.[1], "「类型 · 频道」列头没有 hint");
    assert.ok(m[1].includes("ReelShort 上游没有这两个字段"));
    assert.ok(m[1].includes("上游标签在剧名下面"));
  });

  test("「信号 · 依据」列头说明了 pill 顺序与证据日期的取法", () => {
    const m = /text: "信号 · 依据",\s*hint:\s*([\s\S]*?)\n\s*\},/.exec(
      rowsTable(),
    );
    assert.ok(m?.[1], "「信号 · 依据」列头没有 hint");
    const text = m[1];
    assert.ok(text.includes("pill 的顺序"));
    assert.ok(text.includes("ReelShort 指标在前"));
    assert.ok(text.includes("鹊娱"));
    assert.ok(text.includes("日期未知"));
  });
});

test("术语表（meta.rules.glossary）有「类型 · 频道」词条，排序词条写着三种排序", () => {
  const items = rules.glossary.flatMap((g) => g.items);
  assert.ok(items.some((it) => it.t === "类型 · 频道"));
  const sortEntry = items.find((it) => it.t === "证据时间：最近在前");
  const h = sortEntry && "h" in sortEntry ? sortEntry.h : undefined;
  assert.ok(sortEntry && typeof h === "string", "排序词条缺 h（另两种排序）");
  assert.ok(sortEntry.d.includes("ShortMax / MoboReels 评级"));
  assert.ok(h.includes("剧单日期") && h.includes("剧名"));
});
