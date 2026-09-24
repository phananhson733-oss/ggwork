// PORTED_FROM: realshort@816ca2e tests/pick-qa.regression-2.test.ts
// 本地改动：node:test 换成 @rstest/core；这是源码形状测试，按 regression-1 的规则改读工作台路径：
// 组件 src/components/workspace/pick-board（P3-4），查询 src/server/pick-board/queries.ts（P3-3），
// 旧 page.tsx 拆成 app/workspace/pick-data/page.tsx 加 views/*.tsx（P3-5），各自落地后才跑。
import assert from "node:assert/strict";

import { describe, test } from "@rstest/core";

import {
  COMPONENTS_DIR,
  LANDED,
  PAGE_FILE,
  QUERIES_FILE,
  VIEWS_DIR,
  readSource,
  stripComments,
  tsxIn,
} from "./ported-source";

// Regression: ISSUE-003 / 004 / 005 / 006 — /qa 2026-09-11 留下的四个 low
// Found by /qa on 2026-09-11
// Report: .gstack/qa-reports/qa-report-localhost-admin-pick-2026-09-11.md
//
// 四条都是「改回去不报错」的形态：页码越界退回通用空状态、chips 丢计数、th 丢 scope、
// select 丢 required，typecheck / lint 全绿，页面照样 200。所以钉源码文本。

const read = (p: string) => stripComments(readSource(p));
const pageFiles = () => [PAGE_FILE, ...tsxIn(VIEWS_DIR)];

describe.skipIf(!(LANDED.views && LANDED.components))(
  "ISSUE-003（P3-5 落地后）",
  () => {
    test("三个列表 tab 在 rows 空而 total > 0 时走 OutOfRange，不走通用空状态", () => {
      const page = pageFiles().map(read).join("\n");
      assert.match(
        page,
        /import \{[^}]*\bOutOfRange\b[^}]*\} from "@\/components\/workspace\/pick-board\/toolbar"/,
      );
      assert.equal(
        (
          page.match(
            /<OutOfRange req=\{req\} total=\{(total|list\.total)\} \/>/g,
          ) ?? []
        ).length,
        4,
        "选剧 / 全部剧库、剧场榜、ReelShort 榜、发布记录各一处",
      );
      const toolbar = read(`${COMPONENTS_DIR}/toolbar.tsx`);
      assert.match(toolbar, /export function OutOfRange\(/);
      assert.match(toolbar, /pickHref\(req, \{ page: 1 \}\)/, "给第 1 页");
      assert.match(toolbar, /pickHref\(req, \{ page: pages \}\)/, "给最后一页");
    });
  },
);

describe.skipIf(!(LANDED.queries && LANDED.components))(
  "ISSUE-004（P3-3、P3-4 落地后）",
  () => {
    test("发布记录 chips 的计数与筛选共用 POSTED_WHERE，且计数不带自己那一维", () => {
      const q = read(QUERIES_FILE);
      assert.match(
        q,
        /const POSTED_WHERE: Record<Exclude<PostedFilter, "">, SQL>/,
      );
      assert.match(
        q,
        /f\.push\(POSTED_WHERE\[req\.posted\]\)/,
        "筛选走同一份片段",
      );
      for (const k of ["pool", "yes", "no"])
        assert.match(
          q,
          new RegExp(
            `count\\(\\*\\) FILTER \\(WHERE \\$\\{POSTED_WHERE\\.${k}\\}\\)`,
          ),
          `计数 ${k} 走同一份片段`,
        );
      assert.match(
        q,
        /filtersFor\(req, "posted"\)/,
        "计数时跳过当前选中的发布记录条件",
      );
      assert.doesNotMatch(
        q,
        /EXISTS \(SELECT 1 FROM catalog_posted p WHERE catalog_rows\.row_key = ANY\(p\.row_keys\)[^`]*`\s*,?\s*\)\s*;?\s*\n\s*else if/,
        "不许再散写第二份 EXISTS",
      );
      const toolbar = read(`${COMPONENTS_DIR}/toolbar.tsx`);
      assert.match(
        toolbar,
        /count: p \? facets\.posted\[p\] : undefined/,
        "「不限」不计，其余三个带数",
      );
    });
  },
);

describe.skipIf(!(LANDED.views && LANDED.components))(
  "ISSUE-005（P3-4、P3-5 落地后）",
  () => {
    test('选剧台每个 <th> 都有 scope="col"（列组头是 scope="colgroup"）', () => {
      const files = [...tsxIn(COMPONENTS_DIR), ...pageFiles()];
      let seen = 0;
      for (const f of files) {
        const s = read(f);
        const ths = s.match(/<th[\s>]/g) ?? [];
        // 两排表头：第一排的列组头（ThGroup / ThGap）是 colgroup，第二排是 col；两种都算「带 scope」
        const scoped = s.match(/<th\s[^>]*scope="col(group)?"/g) ?? [];
        assert.equal(
          scoped.length,
          ths.length,
          `${f}: ${ths.length} 个 <th>，${scoped.length} 个带 scope`,
        );
        seen += ths.length;
      }
      assert.ok(seen >= 30, `至少 30 个表头（实际 ${seen}）`);
    });
  },
);

describe.skipIf(!LANDED.components)("ISSUE-006（P3-4 落地后）", () => {
  test("「更早…」的 select 是 required，占位项 value 为空串", () => {
    const s = read(`${COMPONENTS_DIR}/rank-filters.tsx`);
    const select = /<select[\s\S]*?<\/select>/.exec(s)?.[0] ?? "";
    assert.match(select, /\n\s*required\n/, "select 带 required");
    assert.match(
      select,
      /<option value="">更早…<\/option>/,
      '第一项是 value="" 的占位，required 才认它是没选',
    );
  });
});
