import assert from "node:assert/strict";

import { test } from "@rstest/core";

import {
  LIST_TABS,
  OBS_TABS,
  TABS,
  isObsTab,
  parsePickRequest,
  pickQuery,
} from "@/core/pick-board/request";

// 趋势雷达两个 tab（TR-24，设计 3.6）：tab=trends、tab=search；obs 钉住那一通道的集合（32 位小写十六进制，
// 照 cleanVersion 的写法，不像就当没传），oid 是详情页的身份。两个参数只属于这两个 tab。
const parse = (query: string) => parsePickRequest(new URLSearchParams(query));
const SET = "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c";
const IDENTITY = "realshort|64b8c5d2e1f0a9b8c7d6e5f4|en";

test("tab=trends、tab=search 能解析，是观测 tab，不是证据页的来源 tab", () => {
  for (const tab of ["trends", "search"] as const) {
    assert.ok((TABS as readonly string[]).includes(tab));
    assert.ok((OBS_TABS as readonly string[]).includes(tab));
    assert.equal(parse(`tab=${tab}`).tab, tab);
    assert.ok(isObsTab(tab));
    assert.ok(!(LIST_TABS as readonly string[]).includes(tab));
    assert.equal(parse(`tab=row&row=k-1&from=${tab}`).from, "pick");
  }
  for (const tab of [
    "pick",
    "all",
    "row",
    "rank",
    "posted",
    "rules",
    "imports",
  ])
    assert.ok(!isObsTab(tab), tab);
});

test("obs 只收 32 位小写十六进制，其余是空串", () => {
  assert.equal(parse(`tab=trends&obs=${SET}`).obs, SET);
  for (const bad of [
    SET.toUpperCase(),
    SET.slice(1),
    `${SET}0`,
    `${SET.slice(0, 31)}g`,
    "latest",
    "",
  ])
    assert.equal(parse(`tab=trends&obs=${bad}`).obs, "", bad);
  assert.equal(parse("").obs, "");
});

test("oid 原样保留身份，只拒控制字符、空串与超长（512）", () => {
  assert.equal(
    parse(`tab=search&oid=${encodeURIComponent(IDENTITY)}`).oid,
    IDENTITY,
  );
  assert.equal(parse(`tab=search&oid=${"x".repeat(512)}`).oid.length, 512);
  for (const bad of ["x".repeat(513), "a\u0000b", "a\nb", "\u007f"])
    assert.equal(
      parse(`tab=search&oid=${encodeURIComponent(bad)}`).oid,
      "",
      JSON.stringify(bad),
    );
  assert.equal(parse("").oid, "");
});

test("pickQuery：obs、oid 只在两个观测 tab 下写", () => {
  const base = { ...parse(""), obs: SET, oid: IDENTITY };
  assert.equal(
    pickQuery(base, { tab: "trends" }),
    `?tab=trends&obs=${SET}&oid=${encodeURIComponent(IDENTITY)}`,
  );
  assert.equal(
    pickQuery(base, { tab: "search", oid: "" }),
    `?tab=search&obs=${SET}`,
  );
  for (const tab of [
    "pick",
    "all",
    "rank",
    "posted",
    "rules",
    "imports",
  ] as const)
    assert.doesNotMatch(pickQuery(base, { tab }), /obs=|oid=/, tab);
  assert.doesNotMatch(
    pickQuery(base, { tab: "row", rowKey: "k-1", from: "all" }),
    /obs=|oid=/,
  );
});

test("解析与写回来回一致", () => {
  const query = `?tab=search&obs=${SET}&oid=${encodeURIComponent(IDENTITY)}`;
  assert.equal(pickQuery(parse(query.slice(1))), query);
  assert.equal(pickQuery(parse("tab=trends")), "?tab=trends");
});

// 简化版趋势表（2026-09-30）：ts= 只收 change、order，默认按变化；只在 trends tab 写回，别的 tab 丢掉
test("趋势表的排序 ts：白名单、默认按变化、只属于 trends", () => {
  assert.equal(parse("tab=trends").trendsSort, "change");
  assert.equal(parse("tab=trends&ts=order").trendsSort, "order");
  assert.equal(parse("tab=trends&ts=value").trendsSort, "change");
  assert.equal(parse("tab=trends&ts=ORDER").trendsSort, "change");
  assert.equal(pickQuery(parse("tab=trends&ts=order")), "?tab=trends&ts=order");
  assert.equal(pickQuery(parse("tab=trends&ts=change")), "?tab=trends");
  const sorted = parse("tab=trends&ts=order");
  for (const tab of ["pick", "search", "rank", "imports"] as const)
    assert.doesNotMatch(pickQuery(sorted, { tab }), /ts=/, tab);
});
