import assert from "node:assert/strict";

import { test } from "@rstest/core";

import { queyuHref, QUEYU_INDEX } from "@/core/pick-board/queyu";
import * as request from "@/core/pick-board/request";
import { ROW_KEY_MAX, isRowKey } from "@/core/pick-board/row-key";

import { readSource } from "./ported-source";

test("isRowKey：只拒空串、全空白、控制字符与超长；首尾空格是键的一部分", () => {
  assert.equal(ROW_KEY_MAX, 120);
  for (const good of [
    "shortmax-856049 ",
    " kalos-1",
    "mqk++n/L+Wf/xDC0G43CRQ==",
    "shortmax-845227（已设置定时）",
    "a".repeat(120),
  ])
    assert.equal(isRowKey(good), true, JSON.stringify(good));
  for (const bad of [
    "",
    " ",
    "\u3000",
    "\t",
    "a\nb",
    "a\u007fb",
    "a".repeat(121),
  ])
    assert.equal(isRowKey(bad), false, JSON.stringify(bad));
});

test("request.ts 转出的是同一个判定，证据页的 row 参数按它收", () => {
  assert.equal(request.isRowKey, isRowKey);
  assert.equal(request.ROW_KEY_MAX, ROW_KEY_MAX);
  assert.equal(request.parsePickRequest({ row: "a\u0001" }).rowKey, "");
});

test("row-key.ts 与 queyu.ts 不 import 任何模块：client 组件引它们时不会把 request.ts 与 metrics.ts 带进包", () => {
  for (const file of ["row-key.ts", "queyu.ts"]) {
    const source = readSource(`src/core/pick-board/${file}`);
    assert.doesNotMatch(source, /^\s*(import|export)\s[^;]*\sfrom\s/m, file);
    assert.doesNotMatch(source, /\bimport\s*\(/, file);
  }
  assert.ok(!("queyuHref" in request), "request.ts 不再转出 queyuHref");
});

test("鹊娱链接只带剧名，剧名要编码", () => {
  assert.equal(
    QUEYU_INDEX,
    "https://cps-distribution.zwnet.cn/promotion/index",
  );
  assert.equal(
    queyuHref("龙 & Co"),
    "https://cps-distribution.zwnet.cn/promotion/index?title=%E9%BE%99%20%26%20Co",
  );
});
