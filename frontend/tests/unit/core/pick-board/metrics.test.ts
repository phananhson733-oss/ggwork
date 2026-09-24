// PORTED_FROM: realshort@816ca2e tests/observe.test.ts（入参校验与缺数据表达 :29-112）
// 本地改动：node:test 换成 @rstest/core；只取 metrics.ts 的纯函数用例，另加「分成对账」改名一条。
import assert from "node:assert/strict";

import { test } from "@rstest/core";

import {
  MAX_PAGE,
  SORTS,
  SORT_LABELS,
  TAB_LABELS,
  ageInDays,
  bucketLabel,
  delta,
  lifecycleBucket,
  parseObserveRequest,
} from "@/core/pick-board/metrics";

/* ---------------- 入参校验：这是 SQL 拼接的入口 ---------------- */

test("排序键只认白名单，坏值降级到默认而不是透传", () => {
  // ORDER BY 的位置没有参数化占位符，字符串透传就是注入点
  const evil = parseObserveRequest({ sort: "id; DROP TABLE dramas" });
  assert.equal(evil.sort, "rr");
  for (const s of SORTS) {
    assert.equal(parseObserveRequest({ sort: s }).sort, s);
  }
});

test("坏参数一律降级，不把能用的页面变成错误页", () => {
  const r = parseObserveRequest({
    tab: "nope",
    bucket: "1000-9999",
    page: "abc",
    locale: "'; --",
  });
  assert.equal(r.tab, "all");
  assert.equal(r.bucket, null);
  assert.equal(r.page, 1);
  assert.equal(r.locale, "");
});

test("页码有上限，防止一个巨大的 OFFSET 变成全表扫", () => {
  assert.equal(parseObserveRequest({ page: "999999999" }).page, MAX_PAGE);
  assert.equal(parseObserveRequest({ page: "-5" }).page, 1);
  assert.equal(parseObserveRequest({ page: "3" }).page, 3);
});

test("book_id 与语种只收合法形状", () => {
  assert.equal(
    parseObserveRequest({ drama: "6a5191d8fa9f9f7a09081789" }).dramaId,
    "6a5191d8fa9f9f7a09081789",
  );
  assert.equal(parseObserveRequest({ drama: "../../etc/passwd" }).dramaId, "");
  assert.equal(parseObserveRequest({ locale: "zh-hant" }).locale, "zh-hant");
  assert.equal(parseObserveRequest({ locale: "EN" }).locale, "en");
});

test("URLSearchParams 与普通对象两种入参等价", () => {
  const a = parseObserveRequest(new URLSearchParams("tab=cand&sort=d7&page=2"));
  const b = parseObserveRequest({ tab: "cand", sort: "d7", page: "2" });
  assert.deepEqual(a, b);
});

/* ---------------- 缺数据的表达：0 与「算不出来」必须不同 ---------------- */

test("缺快照的增量是 null，不是 0", () => {
  // 0 = 量过了没变；null = 那天没有快照。混成一个数会让正在涨的新剧看起来是死的
  assert.equal(delta(100, null), null);
  assert.equal(delta(100, undefined), null);
  assert.equal(delta(100, 100), 0);
  assert.equal(delta(100, 80), 20);
  assert.equal(delta(80, 100), -20);
});

test("publish_at 为空时上线天数是 null，绝不当成第 0 天", () => {
  const now = new Date("2026-09-08T00:00:00Z");
  assert.equal(ageInDays(null, now), null);
  assert.equal(lifecycleBucket(null), null);
  assert.equal(bucketLabel(null), "未知");
});

test("上线天数按 UTC 日历算，不受当天时分秒影响", () => {
  const now = new Date("2026-09-08T23:59:00Z");
  assert.equal(ageInDays(new Date("2026-09-08T00:01:00Z"), now), 0);
  assert.equal(ageInDays(new Date("2026-09-07T23:00:00Z"), now), 1);
  assert.equal(ageInDays(new Date("2026-07-14T00:00:00Z"), now), 56);
});

test("分桶边界正好落在 7 / 30 / 90 / 365 上", () => {
  assert.equal(lifecycleBucket(0), "0-7");
  assert.equal(lifecycleBucket(7), "0-7");
  assert.equal(lifecycleBucket(8), "8-30");
  assert.equal(lifecycleBucket(30), "8-30");
  assert.equal(lifecycleBucket(31), "31-90");
  assert.equal(lifecycleBucket(90), "31-90");
  assert.equal(lifecycleBucket(91), "91-365");
  assert.equal(lifecycleBucket(365), "91-365");
  assert.equal(lifecycleBucket(366), "366+");
});

test("对账在工作台叫「订单对账」；「预估分成」这个排序名不改", () => {
  assert.equal(TAB_LABELS.bill, "订单对账");
  assert.ok(!Object.values(TAB_LABELS).includes("分成对账"));
  assert.equal(SORT_LABELS.bill, "预估分成");
});
