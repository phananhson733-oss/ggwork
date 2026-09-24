// PORTED_FROM: realshort@816ca2e tests/observe.test.ts（入参校验与缺数据表达 :29-112，每页行数 :284-305，切档位 :338-347，上线分桶 :445-452）
// 本地改动：node:test 换成 @rstest/core；只取 metrics.ts 的纯函数用例，另加「分成对账」改名一条。
// 「每页行数是白名单」只搬纯函数部分，读 observe/queries.ts 的 `LIMIT ${limit + 1} OFFSET` 断言交给 P3-3 的查询测试；
// 「切每页行数」「上线分桶」两条读组件源码，改读工作台路径，P3-4 组件落地后运行。
import assert from "node:assert/strict";

import { describe, test } from "@rstest/core";

import {
  MAX_PAGE,
  PAGE_SIZE,
  PAGE_SIZES,
  SORTS,
  SORT_LABELS,
  TAB_LABELS,
  ageInDays,
  bucketLabel,
  delta,
  lifecycleBucket,
  parseObserveRequest,
} from "@/core/pick-board/metrics";

import { COMPONENTS_DIR, LANDED, readSource } from "./ported-source";

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

/* ---------------- 每页行数：LIMIT 的入口 ---------------- */

test("每页行数是白名单，不一次取回全库", () => {
  // 默认 10：这张表一行 14 列、剧名两行，200 行要滚十几屏
  assert.equal(PAGE_SIZE, 10);
  assert.ok(
    (PAGE_SIZES as readonly number[]).includes(PAGE_SIZE),
    "默认值必须是档位之一，否则页脚上没有一个按钮是高亮的",
  );
  assert.ok(Math.max(...PAGE_SIZES) <= 200, "上界 200，再大就是把全库搬回来");
});

test("size 必须过白名单，任意整数不能透传进 LIMIT", () => {
  // ?size=100000 就是一次把全库搬回来
  assert.equal(parseObserveRequest({ size: "100000" }).size, PAGE_SIZE);
  assert.equal(parseObserveRequest({ size: "-1" }).size, PAGE_SIZE);
  assert.equal(parseObserveRequest({ size: "15" }).size, PAGE_SIZE);
  assert.equal(parseObserveRequest({ size: "abc" }).size, PAGE_SIZE);
  for (const n of PAGE_SIZES) {
    assert.equal(parseObserveRequest({ size: String(n) }).size, n);
  }
});

describe.skipIf(!LANDED.components)("组件文案与链接（P3-4 落地后）", () => {
  test("切每页行数回到第 1 页", () => {
    // 10 行的第 40 页在 200 行下根本不存在：pickHref 在 patch 没给 page 时回第 1 页
    const source = readSource(`${COMPONENTS_DIR}/toolbar.tsx`);
    assert.match(source, /page: patch\.page \?\? 1/);
    assert.match(
      source,
      /pickHref\(req, \{ size: n \}\)/,
      "档位链接不许带 page",
    );
  });

  test("上线分桶只描述已知日期样本，不用固定期限或可靠性承诺", () => {
    const source = readSource(`${COMPONENTS_DIR}/rank-table.tsx`);
    assert.match(source, /只筛选已知日期的样本，不代表全部新剧/);
    assert.doesNotMatch(source, /53 天|样本偏旧|全部有大盘与推广人数/);
    assert.match(
      readSource(`${COMPONENTS_DIR}/reelshort-table.tsx`),
      /空的是还没轮到，不是上游没有/,
      "上线日期列要说清空值是「还没拉到」",
    );
  });
});
