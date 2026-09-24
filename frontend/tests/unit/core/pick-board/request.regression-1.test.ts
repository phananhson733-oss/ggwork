// PORTED_FROM: realshort@816ca2e tests/pick-request.regression-1.test.ts
// 本地改动：node:test 换成 @rstest/core；源码形状那条改读 src/components/workspace/pick-board（P3-4 落地后才跑）。
import assert from "node:assert/strict";

import { describe, test } from "@rstest/core";

import {
  LIST_TABS,
  parsePickRequest,
  pickQuery,
} from "@/core/pick-board/request";

import {
  COMPONENTS_DIR,
  LANDED,
  readSource,
  stripComments,
  tsxIn,
} from "./ported-source";

// Regression: ISSUE-001 — 证据页丢掉来源 tab，「返回列表」从全部剧库 / 榜单 / 发布记录进来一律落回选剧 tab
// Found by /qa on 2026-09-11
// Report: .gstack/qa-reports/qa-report-localhost-admin-pick-2026-09-11.md

test("证据页 URL 带来源 tab（from=），坏值回落选剧，别的 tab 不写它", () => {
  const base = parsePickRequest(new URLSearchParams(""));
  assert.equal(base.from, "pick");
  assert.deepEqual(
    [...LIST_TABS],
    ["pick", "all", "rank", "posted"],
    "row 自己不能当来源",
  );
  assert.equal(
    parsePickRequest(new URLSearchParams("tab=row&row=k-1&from=row")).from,
    "pick",
  );
  assert.equal(
    parsePickRequest(new URLSearchParams("tab=row&row=k-1&from=;drop")).from,
    "pick",
  );
  assert.equal(
    parsePickRequest(new URLSearchParams("tab=row&row=k-1&from=all")).from,
    "all",
  );
  // 默认来源不写；非默认来源写进去；不是证据页时不写（from 只对证据页有意义）
  assert.equal(
    pickQuery(base, { tab: "row", rowKey: "k-1", from: "pick" }),
    "?tab=row&row=k-1",
  );
  assert.equal(
    pickQuery(base, { tab: "row", rowKey: "k-1", from: "all" }),
    "?tab=row&row=k-1&from=all",
  );
  assert.equal(pickQuery(base, { tab: "all", from: "rank" }), "?tab=all");
});

test("从榜单 / 发布记录进证据页，那两个 tab 自己的参数跟着走，返回时回得到同一张表", () => {
  const base = parsePickRequest(new URLSearchParams(""));
  // 来源是榜单：rk / day / week / grade 都保留；pst / sd 不属于它
  const fromRank = pickQuery(base, {
    tab: "row",
    rowKey: "kalos-x",
    from: "rank",
    rank: "kw",
    week: "8.10–8.16",
    grade: "SS",
    postedState: "pub",
    sd: "SD-1",
  });
  assert.equal(
    fromRank,
    "?tab=row&row=kalos-x&from=rank&rk=kw&week=8.10%E2%80%938.16&grade=SS",
  );
  const backFromRank = parsePickRequest(new URLSearchParams(fromRank));
  assert.equal(
    pickQuery(backFromRank, { tab: backFromRank.from, rowKey: "" }),
    "?tab=rank&rk=kw&week=8.10%E2%80%938.16&grade=SS",
  );
  // 来源是发布记录：pst 与 sd 保留（从单条记录页进来就回到那条记录）；rk 不属于它
  const fromPosted = pickQuery(base, {
    tab: "row",
    rowKey: "mobo-x",
    from: "posted",
    postedState: "pub",
    sd: "SD-000044",
    rank: "kw",
    page: 2,
  });
  assert.equal(
    fromPosted,
    "?tab=row&page=2&row=mobo-x&from=posted&pst=pub&sd=SD-000044",
  );
  const backFromPosted = parsePickRequest(new URLSearchParams(fromPosted));
  assert.equal(
    pickQuery(backFromPosted, {
      tab: backFromPosted.from,
      rowKey: "",
      page: backFromPosted.page,
    }),
    "?tab=posted&page=2&pst=pub&sd=SD-000044",
  );
  // 来源是选剧 / 全部剧库：列表参数本来就带着，tab 现在也带着
  const fromAll = pickQuery(base, {
    tab: "row",
    rowKey: "flare-1",
    from: "all",
    q: "Dragon",
    size: 20,
    page: 2,
    sort: "title",
  });
  assert.equal(
    fromAll,
    "?tab=row&sort=title&q=Dragon&size=20&page=2&row=flare-1&from=all",
  );
  const backFromAll = parsePickRequest(new URLSearchParams(fromAll));
  assert.equal(
    pickQuery(backFromAll, {
      tab: backFromAll.from,
      rowKey: "",
      page: backFromAll.page,
    }),
    "?tab=all&sort=title&q=Dragon&size=20&page=2",
  );
});

// 行链接必须统一走 rowHref()：直接 pickHref(req, { tab: "row" ... }) 会漏掉 from，页面照样 200，只有返回时才发现回错了表
describe.skipIf(!LANDED.components)("证据页链接（P3-4 组件落地后）", () => {
  test("选剧台组件里的证据页链接一律经 rowHref()，返回链接按 from 回去", () => {
    for (const f of tsxIn(COMPONENTS_DIR)) {
      const source = stripComments(readSource(f));
      if (f.endsWith("/toolbar.tsx")) {
        assert.match(source, /export function rowHref\(/);
        assert.match(source, /from: originTab\(req\)/);
        continue;
      }
      assert.doesNotMatch(
        source,
        /tab:\s*"row"/,
        `${f} 应该用 rowHref()，不要自己拼 tab: "row"`,
      );
    }
    const detail = readSource(`${COMPONENTS_DIR}/row-detail.tsx`);
    assert.match(
      detail,
      /pickHref\(req, \{ tab: req\.from,/,
      "返回链接按来源 tab 回去",
    );
    assert.doesNotMatch(
      detail,
      /tab: req\.tab === "row" \? "pick"/,
      "旧写法：来源一律当成选剧",
    );
  });
});
