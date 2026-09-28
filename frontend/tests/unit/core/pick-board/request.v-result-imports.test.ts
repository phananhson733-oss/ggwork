import assert from "node:assert/strict";

import { test } from "@rstest/core";

import {
  LIST_TABS,
  RS_RANK_LABELS,
  TABS,
  parsePickRequest,
  pickQuery,
} from "@/core/pick-board/request";

const parse = (query: string) => parsePickRequest(new URLSearchParams(query));
const RESULT = "0123456789abcdef0123456789abcdef";

test("v 只收 1–6 位、不以 0 开头的十进制数，其余一律当没传", () => {
  assert.equal(parse("v=1").v, 1);
  assert.equal(parse("v=999999").v, 999_999);
  assert.equal(parse("v=42").v, 42);
  for (const bad of ["0", "1000000", "01", "1a", "-1", "1.0", " 1", "１", ""])
    assert.equal(parse(`v=${encodeURIComponent(bad)}`).v, null, `v=${bad}`);
  assert.equal(parse("").v, null);
  assert.equal(parsePickRequest({ v: ["7", "8"] }).v, 7, "对象形态取第一个");
});

test("result 只收 32 位小写十六进制（uuid4().hex），其余是空串", () => {
  assert.equal(parse(`result=${RESULT}`).result, RESULT);
  for (const bad of [
    RESULT.toUpperCase(),
    RESULT.slice(1),
    `${RESULT}0`,
    "0123456789abcdef-0123456789abcdef",
    `${RESULT.slice(0, 31)}g`,
  ])
    assert.equal(parse(`result=${bad}`).result, "", bad);
  assert.equal(parse("").result, "");
});

test("tab=imports 能解析，但不是证据页的来源 tab", () => {
  assert.ok((TABS as readonly string[]).includes("imports"));
  assert.equal(parse("tab=imports").tab, "imports");
  assert.ok(!(LIST_TABS as readonly string[]).includes("imports"));
  assert.equal(parse("tab=row&row=k-1&from=imports").from, "pick");
  assert.equal(pickQuery(parse(""), { tab: "imports" }), "?tab=imports");
});

test("pickQuery：v 非空时每个 tab 都写，且写在最后", () => {
  const base = { ...parse(""), v: 12 };
  assert.equal(pickQuery(base), "?v=12");
  assert.equal(pickQuery(base, { tab: "imports" }), "?tab=imports&v=12");
  assert.equal(
    pickQuery(base, { tab: "row", rowKey: "k-1", from: "all" }),
    "?tab=row&row=k-1&from=all&v=12",
  );
  assert.equal(
    pickQuery(base, { tab: "rank", rank: "sm", grade: "SS" }),
    "?tab=rank&rk=sm&grade=SS&v=12",
  );
  assert.equal(pickQuery(base, { v: null }), "", "v 为 null 时不写");
});

test("pickQuery：result 保留在选剧与来自选剧的证据页，其他 tab 丢掉", () => {
  const base = { ...parse(""), result: RESULT };
  assert.equal(pickQuery(base), `?result=${RESULT}`);
  assert.equal(pickQuery({ ...base, v: 3 }), `?v=3&result=${RESULT}`);
  assert.match(
    pickQuery(base, { tab: "row", rowKey: "k-1", from: "pick" }),
    /result=/,
  );
  assert.doesNotMatch(
    pickQuery(base, { tab: "row", rowKey: "k-1", from: "all" }),
    /result=/,
  );
  for (const tab of ["all", "rank", "posted", "rules", "imports"] as const)
    assert.doesNotMatch(
      pickQuery(base, { tab, rowKey: "k-1" }),
      /result=/,
      tab,
    );
});

test("v 与 result 来回一致", () => {
  const q = `?tab=pick&sort=title&v=5&result=${RESULT}`;
  const req = parse(q.slice(1));
  assert.equal(req.v, 5);
  assert.equal(req.result, RESULT);
  assert.equal(pickQuery(req), `?sort=title&v=5&result=${RESULT}`);
  assert.deepEqual(parse(pickQuery(req).slice(1)), req);
});

test('旧链接：/admin/pick 的查询串原样解析，只多出 v=null、result=""', () => {
  const legacy =
    "tab=rank&rk=rs_growth&rs=d7&rl=en&bk=0-7&platform=kalos&q=Dragon&size=20&page=2";
  const req = parse(legacy);
  assert.equal(req.v, null);
  assert.equal(req.result, "");
  assert.equal(req.rank, "rs_growth");
  assert.equal(req.rsSort, "d7");
  assert.equal(req.platform, "kalos");
  assert.equal(req.page, 2);
  assert.equal(
    pickQuery(req),
    "?tab=rank&platform=kalos&q=Dragon&size=20&page=2&rk=rs_growth&rs=d7&rl=en&bk=0-7",
  );
});

test("ReelShort 对账在工作台叫「订单对账」", () => {
  assert.equal(RS_RANK_LABELS.rs_ledger, "ReelShort 订单对账");
  assert.ok(!Object.values(RS_RANK_LABELS).some((l) => l.includes("分成对账")));
});
