import "server-only";

import { sql, type SQL } from "drizzle-orm";

import { getDb } from "@/db";
import { publicCanonical } from "@/lib/queries";
import { readSources } from "./source-state";
import type { ObserveSource, SourceState } from "./source-types";
import { BOT_UA_PATTERNS } from "./bot-ua";
import {
  PAGE_SIZE,
  SERIES_DAYS,
  type Bucket,
  type ObserveRequest,
  type Sort,
} from "./metrics";

/**
 * 观测台的读查询。
 *
 * 【2026-09-11 观测台页面并入选剧台】：/admin/observe 只剩一条跳转，这个模块成了选剧台的数据层——
 * 选剧台的 lib（queries-reelshort.ts / queries-rank.ts）单向 import 它，页面层仍只经 @/lib/pick/queries。
 * `tests/admin-contracts.test.ts` 用一条窄白名单钉住方向。论证不变：这一页整页就是数据，
 * 把查询挪进 Server Action 只是换个地方发同样多的往返，还多一次空屏。
 *
 * 【所有查询都必须裁列】。理由同 queries.ts 的 CARD_COLUMNS：
 * dramas 单行 1,432 字节，取整行 × 200 行 = 一次翻页 286 KB。
 *
 * 【时间相关的逻辑都接受可选的 asOf】（feed v2 导出，方案 4.2）：导出与核对脚本要在同一个时点上读这些 loader。
 * 不传 asOf 时 SQL 里仍是字面的 now()、JS 侧仍用 Date.now()，线上选剧台发出的 SQL 与改动前逐字相同
 * （tests/pick-export-v2-sql.test.ts 拿改动前的编译结果比）；传了就换成绑定参数的时点：快照日、账单窗口、上线时段按它的
 * UTC 日算，出站点击只数 (asOf − N 天, asOf]。本文件里 now() / Date.now() / new Date() 只许出现在「asOf 缺省」的那一支，
 * 同一个测试做源码扫描。
 */

/** 页面上的一行。金额一律在 SQL 里 ::float8 转好，不让 numeric 以字符串过来。 */
export interface ObserveRow {
  id: string;
  title: string;
  locale: string;
  slug: string;
  publishAt: Date | null;
  chapterCount: number;
  payStart: number;
  /** 上游 recent_revenue，单位美分 */
  revenueCents: number;
  promotersCnt: number;
  searchImpressions: number;
  searchDataAt: Date | null;
  detailSyncedAt: Date | null;
  tags: string[];
  metricsValid: boolean | null;
  syncedAt: Date | null;
  baseline1At: Date | null;
  baseline7At: Date | null;
  /** 昨天那一行的值，没有快照时是 null */
  revenueCents1: number | null;
  promotersCnt1: number | null;
  /** 7 天前那一行的值 */
  revenueCents7: number | null;
  promotersCnt7: number | null;
  /** 15 天前那一行的值（2026-09-12 加，快照 09-10 起有效，所以 09-25 前一律 null） */
  baseline15At: Date | null;
  revenueCents15: number | null;
  promotersCnt15: number | null;
  /** 我方分成累计，单位美元 */
  billUsd: number;
  billOrders: number;
  /**
   * 我方分成按【上游账单日】切的窗口合计（USD）：昨日 / 近 7 日 / 近 15 日 / 近 30 日。
   * 【都不含今天】：账单一天只拉一次（同步开头、UTC 零点那轮），今天那一行要到明天才拉得全，
   * 算进去就是一个永远偏小、明天又会变的数。累计（billUsd）含全部已落库的行。
   * 与 revenueCents 那套（全平台 30 天滚动、上游原值）是两个口径，页面上分别标注，不许合并。
   */
  billUsd1: number;
  billUsd7: number;
  billUsd15: number;
  billUsd30: number;
  /** 上游简介。只有单剧页取（limit === 1），榜单每行不搬这一段 */
  description: string;
  /** 近 7 天排除爬虫后的出站点击 */
  clicks7: number;
  /** 近 7 天里最近一次过滤后出站的 UTC 日；没有就 null（选剧台证据行的日期口径） */
  lastClickOn: string | null;
  /** 最近一条有订单的账单日（上游 bill_date 原文）；没有就 null */
  lastBillOn: string | null;
}

/**
 * 把一列 timestamptz 归一成 Date。
 *
 * 【两种形态都要认，不要"简化"成其中一种】。2026-09-08 在生产库上实测：
 * 同一列 `created_at` 经 `db.execute` + raw SQL 回来的是**字符串**
 * （`"2026-09-01 06:54:13.838991+00"`），经 drizzle 的 select builder 回来的是
 * **Date**。两条路径都在本仓库里用着，而 `queries.ts` 的 `listForSitemap`
 * 早就写了 `instanceof Date` 的分支——那就是这件事的旁证。
 *
 * 字符串那一支还要补时区：Postgres 给的 `+00` 不是合法 ISO 8601 偏移，
 * 直接喂 `new Date()` 得到 Invalid Date（CLAUDE.md「监控与告警」记的那次
 * 假告警就是这么来的）。
 *
 * 解析不出来一律返回 null——「日期未知」在观测台上有明确的渲染
 * （显示"未知"、不进任何上线时段分桶），而 Invalid Date 会一路漏到
 * `toISOString()` 抛错，把整页打掉。
 */
function toTimestamp(value: unknown): Date | null {
  if (value == null) return null;
  if (value instanceof Date)
    return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value !== "string") return null;
  const iso = value.replace(" ", "T").replace(/([+-]\d{2})$/, "$1:00");
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** 用 UTC 日历往前推 n 天，与快照的 observed_on 同口径；传了 asOf 就从它往前推（feed v2 导出取 s1 / s7 两天的原值也用它） */
export function utcDayOffset(days: number, asOf?: Date): string {
  const base = asOf ? asOf.getTime() : Date.now();
  return new Date(base - days * 86_400_000).toISOString().slice(0, 10);
}

/** SQL 里的「现在」：不传 asOf 是字面的 now()（默认路径的 SQL 文本不变），传了是绑定参数的时点 */
function nowSql(asOf?: Date): SQL {
  return asOf ? sql`${asOf.toISOString()}::timestamptz` : sql`now()`;
}

/**
 * 出站点击的上界。不传 asOf 不加（点击只追加，数到 now 为止就是线上页面的口径，SQL 文本也不变）；
 * 传了就只数 as_of 及之前落库的点击：outbound_clicks 不进 fingerprint（方案 4.3），全靠这一条钉住。
 * 要求外层以全名 outbound_clicks 引用这张表。
 */
function clickedBy(asOf?: Date): SQL {
  return asOf ? sql` AND outbound_clicks.created_at <= ${nowSql(asOf)}` : sql``;
}

/**
 * 爬虫过滤片段（导出给 feed v2 的 rs_clicks14，口径只有这一份）。
 *
 * 【`user_agent IS NOT NULL` 这一条不能省】：`NOT (x LIKE ...)` 在 x 为 NULL 时
 * 结果是 NULL 而不是 true，那一行会被 WHERE 悄悄丢掉——而缺 UA 的请求
 * 恰恰更可能是真人（隐私浏览器会抹掉它）。
 */
export function notBot(): SQL {
  const clauses = BOT_UA_PATTERNS.map(
    (p) => sql`lower(outbound_clicks.user_agent) NOT LIKE ${p}`,
  );
  return sql`outbound_clicks.user_agent IS NULL OR (${sql.join(clauses, sql` AND `)})`;
}

/**
 * 账单按 (group_key, locale) 摊到兄弟行上的子查询，别名 b：book_id / orders / usd / last_on。
 *
 * 【必须按 (group_key, locale) 摊到兄弟行上，不能直接 book_id = dramas.id】。
 * 上游给同一部剧发多个 book_id（实测每个 (group_key, locale) 有 2,779 组 2 条），
 * 而账单挂在哪个 id 上由上游决定，公开列表只显示正典那一行。
 * 直接对等 join 的话：实测 295 个出账 book_id 里有 12 个落在非正典行上，
 * 一旦其中任何一个真的出单，那笔钱在总览里显示成「—」，
 * 而概览 tile 的 billUsd 又把它算进了总额——同一屏上两个数对不上。
 *
 * 【导出给选剧台的 ReelShort 分支用】（2026-09-11 并入）：选剧 tab 里 ReelShort 行「有订单」的口径
 * 与这里的候选清单必须逐字相同，两处各写一份迟早漂开。`last_on` 是 text 的 max（bill_date 是
 * YYYY-MM-DD 文本，见 loadBillTotals 那条「不许 ::date」）。
 * 传了 asOf 时四个窗口从 asOf 的 UTC 日往前数（同样不含那一天）。
 */
export function billBySibling(asOf?: Date): SQL {
  /* 窗口边界是 UTC 日历的 YYYY-MM-DD 文本，与 bill_date（text）直接比：不许 ::date，理由见 loadBillTotals。
     【不许写成 .map(utcDayOffset)】：map 会把下标当第二个参数传进去，被当成 asOf */
  const [d1, d7, d15, d30] = [1, 7, 15, 30].map((n) => utcDayOffset(n, asOf));
  return sql`SELECT sib.id AS book_id,
             sum(bd.order_cnt)::int AS orders,
             sum(bd.revenue_usd)::float8 AS usd,
             sum(bd.revenue_usd) FILTER (WHERE bd.bill_date = ${d1})::float8 AS usd_1,
             sum(bd.revenue_usd) FILTER (WHERE bd.bill_date >= ${d7} AND bd.bill_date <= ${d1})::float8 AS usd_7,
             sum(bd.revenue_usd) FILTER (WHERE bd.bill_date >= ${d15} AND bd.bill_date <= ${d1})::float8 AS usd_15,
             sum(bd.revenue_usd) FILTER (WHERE bd.bill_date >= ${d30} AND bd.bill_date <= ${d1})::float8 AS usd_30,
             max(bd.bill_date) FILTER (WHERE bd.order_cnt > 0) AS last_on
      FROM cps_bill_daily bd
      JOIN dramas src ON src.id = bd.book_id
      JOIN dramas sib ON sib.group_key = src.group_key AND sib.locale = src.locale
      GROUP BY sib.id`;
}

/** 近 7 天排除爬虫后的出站，同样摊到兄弟行：drama_id / n / last_on（最近一次的 UTC 日）；传了 asOf 是 (asOf − 7 天, asOf] */
export function clicks7BySibling(asOf?: Date): SQL {
  return sql`SELECT sib.id AS drama_id, count(*)::int AS n,
             to_char((max(outbound_clicks.created_at) AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD') AS last_on
      FROM outbound_clicks
      JOIN dramas src ON src.id = outbound_clicks.drama_id
      JOIN dramas sib ON sib.group_key = src.group_key AND sib.locale = src.locale
      WHERE outbound_clicks.created_at > ${nowSql(asOf)} - interval '7 days'${clickedBy(asOf)} AND (${notBot()})
      GROUP BY sib.id`;
}

/** 「和我们有过交集」：搜索有展示、近 7 天有真人出站、或者出过账单。要求 b / c 两个别名已 join */
export function candidateFilter(): SQL {
  return sql`(dramas.search_impressions > 0 OR COALESCE(c.n, 0) > 0 OR COALESCE(b.orders, 0) > 0)`;
}

/** 主键数组字面量（drizzle 的模板会把数组展开成 record，直接 `${ids}` 会报 cannot cast type record） */
function idArray(ids: readonly string[]): SQL {
  return sql`ARRAY[${sql.join(ids.map((v) => sql`${v}`), sql`, `)}]::text[]`;
}

/**
 * 快照三天（昨日 / 7 天前 / 15 天前）+ 账单 + 出站五段 CTE，主查询、count、rs 榜计数共用。
 * 【绝对不能抄成两份】两边的过滤条件一旦漂开，症状是「页码说有 40 页，翻到第 7 页却是空的」——
 * 而两条 SQL 各自看都对，这是最难查的那类错。
 *
 * 【b、c 两段必须 MATERIALIZED】（2026-09-14 问答第 4 轮实测）：快照上 `to_jsonb(...)->>'metrics_valid' = 'true'` 这类表达式
 * 规划器估 163 行、实际 3.3 万行，于是整条走 Nested Loop，两段按兄弟行摊的聚合被内联、对外层每一行各重算一遍
 * （loops=32827，Join Filter 丢掉 2,148 万 + 1,292 万行）：涨幅榜不筛语种、有可比行时一次 70–97 秒，问答 Q11 因此没交卷；
 * d7 / dp7 现在快只因为 09-17 前没有可比行，有了基线后存在同类风险。物化后两段各算一次，同一条 d1 查询约 8–9 秒。
 * 同一快照、同一组参数下逻辑结果不变（两段都按 sib.id 分组，每个 id 至多一行；外层排序、LIMIT、hasMore、total 没动），没做逐字段结果对照。
 * 【快照三段不物化】：它们靠 (observed_on, drama_id) 主键做窄查询（loadRowsByIds / 单剧），物化会把当天整段快照先搬出来。
 * 【to_jsonb 那种写法不换成真实列】：列不存在时要能跑（tests/observe-db.test.ts 的迁移前合同），而且单换列实测仍是 71 秒
 * 传了 asOf 时快照日按 asOf 的 UTC 日往前推，b / c 两段同样钉在 asOf 上。
 */
function observeCtes(asOf?: Date): SQL {
  return sql`
    WITH s1 AS (
      SELECT drama_id, recent_revenue_cents, promoters_cnt, to_jsonb(drama_observations)->>'metrics_valid' AS valid, to_jsonb(drama_observations)->>'captured_at' AS captured_at
      FROM drama_observations WHERE observed_on = ${utcDayOffset(1, asOf)}
    ), s7 AS (
      SELECT drama_id, recent_revenue_cents, promoters_cnt, to_jsonb(drama_observations)->>'metrics_valid' AS valid, to_jsonb(drama_observations)->>'captured_at' AS captured_at
      FROM drama_observations WHERE observed_on = ${utcDayOffset(7, asOf)}
    ), s15 AS (
      SELECT drama_id, recent_revenue_cents, promoters_cnt, to_jsonb(drama_observations)->>'metrics_valid' AS valid, to_jsonb(drama_observations)->>'captured_at' AS captured_at
      FROM drama_observations WHERE observed_on = ${utcDayOffset(15, asOf)}
    ), b AS MATERIALIZED (${billBySibling(asOf)}), c AS MATERIALIZED (${clicks7BySibling(asOf)})`;
}

const OBSERVE_JOINS = sql`
    FROM dramas
    LEFT JOIN s1 ON s1.drama_id = dramas.id
    LEFT JOIN s7 ON s7.drama_id = dramas.id
    LEFT JOIN s15 ON s15.drama_id = dramas.id
    LEFT JOIN b ON b.book_id = dramas.id
    LEFT JOIN c ON c.drama_id = dramas.id`;

/** 上线时段过滤。publish_at 为空的行在任何一个桶里都不出现，这是有意的。传了 asOf 时天数按 asOf 的 UTC 日算 */
function bucketFilter(bucket: Bucket, asOf?: Date): SQL {
  const age = sql`(timezone('UTC', ${nowSql(asOf)})::date - dramas.publish_at::date)`;
  switch (bucket) {
    case "0-7":
      return sql`dramas.publish_at IS NOT NULL AND ${age} >= 0 AND ${age} <= 7`;
    case "8-30":
      return sql`dramas.publish_at IS NOT NULL AND ${age} > 7 AND ${age} <= 30`;
    case "31-90":
      return sql`dramas.publish_at IS NOT NULL AND ${age} > 30 AND ${age} <= 90`;
    case "91-365":
      return sql`dramas.publish_at IS NOT NULL AND ${age} > 90 AND ${age} <= 365`;
    case "366+":
      return sql`dramas.publish_at IS NOT NULL AND ${age} > 365`;
  }
}

/**
 * 排序片段。
 *
 * 【必须走这个 switch，不许把 req.sort 拼进 SQL】：ORDER BY 的位置没有参数化
 * 占位符，字符串透传就是注入点（校验在 metrics.ts 的 SORTS 白名单，这里是第二道）。
 *
 * 【每条都以 dramas.id 收尾】：排序键有大量并列值（promoters_cnt 头部实测
 * 一个值并列 8 部），没有稳定的第二键时同一行会在翻页之间来回跳，
 * 表现是"翻到第 2 页看见第 1 页那部剧"。同 sitemap 那条稳定排序的教训。
 */
function orderBy(sort: Sort): SQL {
  const tail = sql` NULLS LAST, dramas.id ASC`;
  switch (sort) {
    case "d1":
      return sql`(dramas.recent_revenue_cents - s1.recent_revenue_cents) DESC${tail}`;
    case "d7":
      return sql`(dramas.recent_revenue_cents - s7.recent_revenue_cents) DESC${tail}`;
    case "dp1":
      return sql`(dramas.promoters_cnt - s1.promoters_cnt) DESC${tail}`;
    case "dp7":
      return sql`(dramas.promoters_cnt - s7.promoters_cnt) DESC${tail}`;
    case "promoters":
      return sql`dramas.promoters_cnt DESC${tail}`;
    case "publish":
      return sql`dramas.publish_at DESC${tail}`;
    case "bill":
      return sql`b.usd DESC${tail}`;
    case "eff":
      return sql`(dramas.recent_revenue_cents / NULLIF(dramas.promoters_cnt, 0)) DESC${tail}`;
    case "gsc":
      return sql`dramas.search_impressions DESC${tail}`;
    case "clicks":
      return sql`c.n DESC${tail}`;
    case "rr":
    default:
      return sql`dramas.recent_revenue_cents DESC${tail}`;
  }
}

interface RawRow extends Record<string, unknown> {
  id: string;
  title: string;
  locale: string;
  slug: string;
  publish_at: string | Date | null;
  chapter_count: number;
  pay_start: number;
  rr: number;
  promoters_cnt: number;
  search_impressions: number;
  search_data_at: string | Date | null;
  detail_synced_at: string | Date | null;
  tags: string[];
  metrics_valid: string | null;
  synced_at: string | Date | null;
  baseline1_at: string | null;
  baseline7_at: string | null;
  rr1: number | null;
  p1: number | null;
  rr7: number | null;
  p7: number | null;
  baseline15_at: string | null;
  rr15: number | null;
  p15: number | null;
  bill_usd: number | null;
  bill_orders: number | null;
  bill_usd_1: number | null;
  bill_usd_7: number | null;
  bill_usd_15: number | null;
  bill_usd_30: number | null;
  description: string | null;
  clicks: number | null;
  last_click_on: string | null;
  last_bill_on: string | null;
}

function toRow(r: RawRow): ObserveRow {
  return {
    id: r.id,
    title: r.title,
    locale: r.locale,
    slug: r.slug,
    publishAt: toTimestamp(r.publish_at),
    chapterCount: r.chapter_count,
    payStart: r.pay_start,
    revenueCents: r.rr,
    promotersCnt: r.promoters_cnt,
    searchImpressions: r.search_impressions,
    searchDataAt: toTimestamp(r.search_data_at),
    detailSyncedAt: toTimestamp(r.detail_synced_at),
    tags: r.tags ?? [],
    metricsValid: r.metrics_valid == null ? null : r.metrics_valid === "true",
    syncedAt: toTimestamp(r.synced_at),
    baseline1At: toTimestamp(r.baseline1_at),
    baseline7At: toTimestamp(r.baseline7_at),
    revenueCents1: r.rr1,
    promotersCnt1: r.p1,
    revenueCents7: r.rr7,
    promotersCnt7: r.p7,
    baseline15At: toTimestamp(r.baseline15_at),
    revenueCents15: r.rr15,
    promotersCnt15: r.p15,
    billUsd: r.bill_usd ?? 0,
    billOrders: r.bill_orders ?? 0,
    billUsd1: r.bill_usd_1 ?? 0,
    billUsd7: r.bill_usd_7 ?? 0,
    billUsd15: r.bill_usd_15 ?? 0,
    billUsd30: r.bill_usd_30 ?? 0,
    description: r.description ?? "",
    clicks7: r.clicks ?? 0,
    lastClickOn: r.last_click_on ?? null,
    lastBillOn: r.last_bill_on ?? null,
  };
}

/**
 * 总览 / 候选 / 涨幅榜共用的主查询。三者只差过滤条件和排序，
 * 拆成三条 SQL 意味着同一份口径要维护三遍。
 */
export async function loadRows(
  req: ObserveRequest,
  opts: {
    candidatesOnly?: boolean;
    limit?: number;
    comparableOnly?: boolean;
    /** 单指标榜（推广人数 / 出站 / 搜索 / 分成）：只留那一项 > 0 的行 */
    only?: "promoters" | "clicks" | "gsc" | "bill";
    /** 只取这几部（选剧台一页里的 ReelShort 行）；口径与榜单逐字相同 */
    ids?: readonly string[];
    /** 钉住的时点（feed v2 导出与核对脚本）；不传就是 now，SQL 文本与改动前相同 */
    asOf?: Date;
    /** 取完整 tags 与 description（导出用）；默认只有单剧页（limit === 1）取全，列表截到 tags[1:8]、description 置空 */
    fullText?: boolean;
  } = {},
): Promise<{ rows: ObserveRow[]; hasMore: boolean; total: number | null }> {
  const db = getDb();
  /* opts.limit 是涨幅榜那种「固定取前 N」，它不分页，也就不需要总数 */
  const paged = opts.limit === undefined;
  const limit = opts.limit ?? req.size ?? PAGE_SIZE;
  const offset = paged ? (req.page - 1) * limit : 0;

  const filters: SQL[] = [publicCanonical()];
  if (req.locale) filters.push(sql`dramas.locale = ${req.locale}`);
  if (req.bucket) filters.push(bucketFilter(req.bucket, opts.asOf));
  if (req.q) {
    /* 剧名模糊 + book_id 精确。ILIKE 没有索引，但这是人工搜索、一天几次 */
    filters.push(
      sql`(dramas.title ILIKE ${`%${req.q}%`} OR dramas.id = ${req.q}
        OR EXISTS (SELECT 1 FROM dramas requested WHERE requested.id = ${req.q}
          AND requested.group_key = dramas.group_key AND requested.locale = dramas.locale))`,
    );
  }
  if (opts.candidatesOnly) filters.push(candidateFilter());
  if (opts.ids) filters.push(sql`dramas.id = ANY(${idArray(opts.ids)})`);
  switch (opts.only) {
    case "promoters":
      filters.push(sql`dramas.promoters_cnt > 0`);
      break;
    case "clicks":
      filters.push(sql`COALESCE(c.n, 0) > 0`);
      break;
    case "gsc":
      filters.push(sql`dramas.search_impressions > 0`);
      break;
    case "bill":
      filters.push(sql`COALESCE(b.orders, 0) > 0`);
      break;
  }

  if (opts.comparableOnly) {
    /* 较昨日的两种（d1 / dp1）看 s1，其余看 s7；度量非空跟着排序走。loadRsCounts 的四个 growth_* 与 growth-diagnosis 的窗口天数是同一张对照 */
    const yesterday = req.sort === "d1" || req.sort === "dp1";
    filters.push(sql`to_jsonb(dramas)->>'metrics_valid' = 'true'`);
    filters.push(yesterday ? sql`s1.valid = 'true'` : sql`s7.valid = 'true'`);
    filters.push(
      req.sort === "d1"
        ? sql`s1.recent_revenue_cents IS NOT NULL`
        : req.sort === "dp1"
          ? sql`s1.promoters_cnt IS NOT NULL`
          : req.sort === "dp7"
            ? sql`s7.promoters_cnt IS NOT NULL`
            : sql`s7.recent_revenue_cents IS NOT NULL`,
    );
  }

  const cte = observeCtes(opts.asOf);
  const full = opts.limit === 1 || opts.fullText === true;
  const joins = sql`${OBSERVE_JOINS}
    WHERE ${sql.join(filters, sql` AND `)}`;

  /*
   * 两条查询并发发。总耗时是 max 不是 sum——实测 count 在 3 万行上
   * 约 300-760ms，串行发就等于每次翻页都白等那一次。
   *
   * count 只在分页时查：默认每页 10 行，人要能直接跳到第 N 页就得知道总共几页，
   * 而光靠「多取一行」只答得出「有没有下一页」。
   */
  const [result, totalRows] = await Promise.all([
    db.execute<RawRow>(sql`
    ${cte}
    SELECT dramas.id, dramas.title, dramas.locale, dramas.slug, dramas.publish_at,
           dramas.chapter_count, dramas.pay_start,
           dramas.recent_revenue_cents::float8 AS rr,
           dramas.promoters_cnt, to_jsonb(dramas)->>'metrics_valid' AS metrics_valid, dramas.synced_at, s1.captured_at AS baseline1_at, s7.captured_at AS baseline7_at, s15.captured_at AS baseline15_at, dramas.search_impressions, dramas.search_data_at, dramas.detail_synced_at,
           ${full ? sql`dramas.tags` : sql`dramas.tags[1:8]`} AS tags,
           ${full ? sql`dramas.description` : sql`''`} AS description,
           CASE WHEN s1.valid = 'true' AND to_jsonb(dramas)->>'metrics_valid' = 'true' THEN s1.recent_revenue_cents::float8 END AS rr1, CASE WHEN s1.valid = 'true' AND to_jsonb(dramas)->>'metrics_valid' = 'true' THEN s1.promoters_cnt END AS p1,
           CASE WHEN s7.valid = 'true' AND to_jsonb(dramas)->>'metrics_valid' = 'true' THEN s7.recent_revenue_cents::float8 END AS rr7, CASE WHEN s7.valid = 'true' AND to_jsonb(dramas)->>'metrics_valid' = 'true' THEN s7.promoters_cnt END AS p7,
           CASE WHEN s15.valid = 'true' AND to_jsonb(dramas)->>'metrics_valid' = 'true' THEN s15.recent_revenue_cents::float8 END AS rr15, CASE WHEN s15.valid = 'true' AND to_jsonb(dramas)->>'metrics_valid' = 'true' THEN s15.promoters_cnt END AS p15,
           b.usd AS bill_usd, b.orders AS bill_orders, b.usd_1 AS bill_usd_1, b.usd_7 AS bill_usd_7, b.usd_15 AS bill_usd_15, b.usd_30 AS bill_usd_30, c.n AS clicks,
           c.last_on AS last_click_on, b.last_on AS last_bill_on
    ${joins}
    ORDER BY ${orderBy(req.sort)}
    LIMIT ${limit + 1} OFFSET ${offset}
  `),
    paged
      ? db.execute<{ n: number }>(sql`${cte} SELECT count(*)::int AS n ${joins}`)
      : Promise.resolve(null),
  ]);

  const raw = result.rows;
  return {
    rows: raw.slice(0, limit).map(toRow),
    /* 多取一行判断有没有下一页。总数已经有了，但这一条留着：
       它和 count 是两次独立查询，中途有同步写入时以行本身为准 */
    hasMore: raw.length > limit,
    total: totalRows ? (totalRows.rows[0]?.n ?? 0) : null,
  };
}

const EMPTY_REQ: ObserveRequest = {
  tab: "all",
  sort: "rr",
  locale: "",
  bucket: null,
  page: 1,
  size: PAGE_SIZE,
  q: "",
  dramaId: "",
};

/**
 * 选剧台一页里的 ReelShort 行取指标：同一条 SELECT、同一套 CTE，只多一个 id 过滤。
 * 不分页、不 count；ids 最多一页（200 个）。asOf / fullText 原样传给 loadRows（导出按页取指标用）。
 */
export async function loadRowsByIds(
  ids: readonly string[],
  opts: { asOf?: Date; fullText?: boolean } = {},
): Promise<Map<string, ObserveRow>> {
  if (ids.length === 0) return new Map();
  const { rows } = await loadRows(EMPTY_REQ, { ids, limit: ids.length, asOf: opts.asOf, fullText: opts.fullText });
  return new Map(rows.map((r) => [r.id, r]));
}

export interface RsCounts {
  all: number;
  cand: number;
  /** 至少一种增量（较昨日 / 较 7 天前 / 人数 7 天变化）有基线快照的行 */
  /** 涨幅榜三种增量各自的可比行数：chip 上的数要跟榜的当前排序走，否则「chip 写 2.9 万、榜是空的」 */
  growthD1: number;
  growthD7: number;
  growthDp1: number;
  growthDp7: number;
  pc: number;
  clk: number;
  gsc: number;
  bill: number;
  /** 分成对账：有订单的账单行数 */
  ledger: number;
}

/**
 * 榜单 tab 里 ReelShort 七张榜 + 对账的 chips 计数，一条 SQL 七个 FILTER（同 loadOverview 的理由：
 * 拆开是七次 Neon 往返）。口径与各榜的过滤逐项相同：候选 = candidateFilter，涨幅 = comparableOnly 按三种增量各一个数
 * （2026-09-11 实测：快照只攒了两天时 d1 可比 29,381、d7 为 0，合成一个并集数会让 chip 与默认按 d7 排的空榜互相矛盾）。
 */
export async function loadRsCounts(asOf?: Date): Promise<RsCounts> {
  const db = getDb();
  const valid = sql`to_jsonb(dramas)->>'metrics_valid' = 'true'`;
  const r = await db.execute<Record<string, number>>(sql`
    ${observeCtes(asOf)}
    SELECT count(*)::int AS "all",
           count(*) FILTER (WHERE ${candidateFilter()})::int AS cand,
           count(*) FILTER (WHERE ${valid} AND s1.valid = 'true' AND s1.recent_revenue_cents IS NOT NULL)::int AS growth_d1,
           count(*) FILTER (WHERE ${valid} AND s7.valid = 'true' AND s7.recent_revenue_cents IS NOT NULL)::int AS growth_d7,
           count(*) FILTER (WHERE ${valid} AND s1.valid = 'true' AND s1.promoters_cnt IS NOT NULL)::int AS growth_dp1,
           count(*) FILTER (WHERE ${valid} AND s7.valid = 'true' AND s7.promoters_cnt IS NOT NULL)::int AS growth_dp7,
           count(*) FILTER (WHERE dramas.promoters_cnt > 0)::int AS pc,
           count(*) FILTER (WHERE COALESCE(c.n, 0) > 0)::int AS clk,
           count(*) FILTER (WHERE dramas.search_impressions > 0)::int AS gsc,
           count(*) FILTER (WHERE COALESCE(b.orders, 0) > 0)::int AS bill,
           (SELECT count(*)::int FROM cps_bill_daily WHERE order_cnt > 0) AS ledger
    ${OBSERVE_JOINS}
    WHERE ${publicCanonical()}
  `);
  const row = r.rows[0] ?? {};
  const n = (k: string) => Number(row[k] ?? 0);
  return {
    all: n("all"),
    cand: n("cand"),
    growthD1: n("growth_d1"),
    growthD7: n("growth_d7"),
    growthDp1: n("growth_dp1"),
    growthDp7: n("growth_dp7"),
    pc: n("pc"),
    clk: n("clk"),
    gsc: n("gsc"),
    bill: n("bill"),
    ledger: n("ledger"),
  };
}

/** 涨幅榜空榜诊断要的三件事，原因在 lib/pick/growth-diagnosis.ts 判（observe 不 import pick，状态枚举在这里照写一份同名字面量） */
export interface GrowthBaseline {
  /** 基线日（UTC）= 今天 − windowDays，与 observeCtes 的 s1 / s7 同一个 utcDayOffset */
  baselineDay: string;
  baselineSnapshot: "none" | "unverified_only" | "verified";
  /** 保留期内最早一个有已校验快照的日子；没有是 null。只是保留数据里的最早日，不是系统第一次采集的日子 */
  earliestVerifiedOn: string | null;
}

/**
 * 只在涨幅榜 0 行时跑。「已校验」与 comparableOnly / observeCtes 同一个判断（to_jsonb(...)->>'metrics_valid' = 'true'）。
 * 最早已校验日按 observed_on 顺序取第一行已校验的：09-08 / 09-09 两天的快照 metrics_valid 是 NULL（那一列那时还没有），
 * 它们还在保留期内时要先扫过这两天约 6 万行，一次一两百毫秒，空榜才付。基线日两条 EXISTS 是联合主键的前缀范围，碰到第一行就停
 */
export async function loadGrowthBaseline(windowDays: number, asOf?: Date): Promise<GrowthBaseline> {
  const db = getDb();
  const day = utcDayOffset(windowDays, asOf);
  const r = await db.execute<{ any_on_day: unknown; verified_on_day: unknown; earliest: unknown }>(sql`
    SELECT EXISTS (SELECT 1 FROM drama_observations WHERE observed_on = ${day}) AS any_on_day,
           EXISTS (SELECT 1 FROM drama_observations WHERE observed_on = ${day} AND to_jsonb(drama_observations)->>'metrics_valid' = 'true') AS verified_on_day,
           (SELECT observed_on FROM drama_observations WHERE to_jsonb(drama_observations)->>'metrics_valid' = 'true' ORDER BY observed_on LIMIT 1) AS earliest
  `);
  const row = r.rows[0];
  const yes = (v: unknown) => v === true || v === "t" || v === "true";
  return {
    baselineDay: day,
    baselineSnapshot: yes(row?.verified_on_day) ? "verified" : yes(row?.any_on_day) ? "unverified_only" : "none",
    earliestVerifiedOn: typeof row?.earliest === "string" ? row.earliest : null,
  };
}

export interface ObserveOverview {
  published: number;
  canonical: number;
  withPublishAt: number;
  locales: number;
  /** 正典行的 30 天大盘销售额合计，单位美元 */
  revenueUsd: number;
  gscDramas: number;
  candidates: number;
  clicks7Human: number;
  clicks7All: number;
  clicks7Dramas: number;
  billUsd: number;
  billOrders: number;
  latestBillDate: string | null;
  latestSnapshot: string | null;
  snapshotDays: number;
  snapshotRows: number;
  invalidEpisodes: number;
  verifiedMetrics: number;
  firstBillDate: string | null;
  billFetchedAt: string | null;
  sources: Partial<Record<ObserveSource, SourceState>>;
}

/**
 * 概览面板。
 *
 * 【一条 SQL，不是十条】：全部写成标量子查询，一次往返。拆开是十次
 * Neon 往返，而 compute 正是账单大头（CLAUDE.md「数据库额度是硬约束」）。
 */
export async function loadOverview(asOf?: Date): Promise<ObserveOverview> {
  const db = getDb();
  const bot = notBot();
  const result = await db.execute<Record<string, number | string | null>>(sql`
    SELECT
      (SELECT count(*)::int FROM dramas WHERE detail_synced_at IS NOT NULL) AS published,
      (SELECT count(*)::int FROM dramas WHERE ${publicCanonical()}) AS canonical,
      (SELECT count(*)::int FROM dramas WHERE publish_at IS NOT NULL AND ${publicCanonical()}) AS with_publish_at,
      (SELECT count(*)::int FROM dramas WHERE ${publicCanonical()} AND (chapter_count <= 0 OR pay_start < 0 OR pay_start > chapter_count + 1)) AS invalid_episodes,
      (SELECT count(*)::int FROM dramas WHERE ${publicCanonical()} AND to_jsonb(dramas)->>'metrics_valid' = 'true') AS verified_metrics,
      (SELECT count(DISTINCT locale)::int FROM dramas WHERE detail_synced_at IS NOT NULL) AS locales,
      (SELECT COALESCE(sum(recent_revenue_cents), 0)::float8 / 100
         FROM dramas WHERE ${publicCanonical()}) AS revenue_usd,
      (SELECT count(*)::int FROM dramas WHERE search_impressions > 0) AS gsc_dramas,
      (SELECT count(*)::int FROM outbound_clicks
        WHERE outbound_clicks.created_at > ${nowSql(asOf)} - interval '7 days'${clickedBy(asOf)}) AS clicks7_all,
      (SELECT count(*)::int FROM outbound_clicks
        WHERE outbound_clicks.created_at > ${nowSql(asOf)} - interval '7 days'${clickedBy(asOf)} AND (${bot})) AS clicks7_human,
      (SELECT count(DISTINCT drama_id)::int FROM outbound_clicks
        WHERE outbound_clicks.created_at > ${nowSql(asOf)} - interval '7 days'${clickedBy(asOf)} AND (${bot})) AS clicks7_dramas,
      (SELECT COALESCE(sum(revenue_usd), 0)::float8 FROM cps_bill_daily) AS bill_usd,
      (SELECT COALESCE(sum(order_cnt), 0)::int FROM cps_bill_daily) AS bill_orders,
      (SELECT max(bill_date) FROM cps_bill_daily) AS latest_bill_date,
      (SELECT min(bill_date) FROM cps_bill_daily) AS first_bill_date,
      (SELECT max(fetched_at) FROM cps_bill_daily) AS bill_fetched_at,
      (SELECT max(observed_on) FROM drama_observations) AS latest_snapshot,
      (SELECT count(DISTINCT observed_on)::int FROM drama_observations) AS snapshot_days,
      (SELECT count(*)::int FROM drama_observations
        WHERE observed_on = (SELECT max(observed_on) FROM drama_observations)) AS snapshot_rows
  `);
  const r = result.rows[0] ?? {};
  const n = (k: string) => Number(r[k] ?? 0);
  const s = (k: string) => (r[k] == null ? null : String(r[k]));

  /*
   * 候选数单独一条：它是三个集合的并集，写成标量子查询要把 outbound_clicks
   * 再扫一遍，而这里可以直接复用上面已经算过的口径。一次额外往返换一条清楚的 SQL。
   */
  const cand = await db.execute<CountRow>(sql`
    SELECT count(*)::int AS n FROM dramas
    WHERE ${publicCanonical()} AND (
      dramas.search_impressions > 0
      /*
       * 【必须带 order_cnt > 0，和候选清单 tab 的口径对齐】。
       * 上游把「这个账号名下有这本书的推广位」也当成一行发下来，
       * 实测 389 行覆盖 295 部剧，其中**只有 3 部真的有订单**。
       * 用 EXISTS(有行) 的话，概览的「今日候选」会写 470，
       * 而点进候选清单 tab 只有一百多部——两个数都在同一屏上，对不上。
       */
      OR EXISTS (
        SELECT 1 FROM cps_bill_daily bd
        JOIN dramas src ON src.id = bd.book_id
        WHERE src.group_key = dramas.group_key AND src.locale = dramas.locale
          AND bd.order_cnt > 0
      )
      OR EXISTS (
        SELECT 1 FROM outbound_clicks
        JOIN dramas clicked ON clicked.id = outbound_clicks.drama_id
        WHERE clicked.group_key = dramas.group_key AND clicked.locale = dramas.locale
          AND outbound_clicks.created_at > ${nowSql(asOf)} - interval '7 days'${clickedBy(asOf)} AND (${bot})
      )
    )
  `);

  return {
    published: n("published"),
    canonical: n("canonical"),
    withPublishAt: n("with_publish_at"),
    locales: n("locales"),
    revenueUsd: n("revenue_usd"),
    gscDramas: n("gsc_dramas"),
    candidates: cand.rows[0]?.n ?? 0,
    clicks7Human: n("clicks7_human"),
    clicks7All: n("clicks7_all"),
    clicks7Dramas: n("clicks7_dramas"),
    billUsd: n("bill_usd"),
    billOrders: n("bill_orders"),
    latestBillDate: s("latest_bill_date"),
    latestSnapshot: s("latest_snapshot"),
    snapshotDays: n("snapshot_days"),
    snapshotRows: n("snapshot_rows"),
    invalidEpisodes: n("invalid_episodes"),
    verifiedMetrics: n("verified_metrics"),
    firstBillDate: s("first_bill_date"),
    billFetchedAt: s("bill_fetched_at"),
    sources: await readSources(),
  };
}

interface CountRow extends Record<string, unknown> {
  n: number;
}

export interface SeriesPoint {
  day: string;
  revenueRaw: number;
  promoters: number;
}

export interface BillRow {
  billDate: string;
  bookId: string;
  title: string;
  locale: string;
  publishAt: Date | null;
  promotionType: string;
  promotionValue: string;
  orderCnt: number;
  revenueUsd: number;
  /** 同一 UTC 日、同一部剧，站内有没有排除爬虫后的出站点击 */
  sameDayClicks: number;
}

/**
 * 分成对账。
 *
 * 【`sameDayClicks` 是启发式，页面上必须说明】：短链和口令是上游发给这个
 * CPS 账号的，贴在网站、群里、社媒回填的都是同一个值，上游本身分不出渠道。
 * 同日站内有点击只是"时间和位置对得上"，不是归因证明。
 *
 * 【日期比较是文本对文本，不许写 `bill.bill_date::date`】。bill_date 是 text 列，
 * 而上游那个字段的 zod 是 looseStr（空值有时 null 有时空串，见 CLAUDE.md
 * 「上游接口的坑」）。一旦有一行落成空串，那个强转会抛
 * invalid input syntax for type date，**整个分成对账 tab 永久 500**——
 * 而那一行本身只是个没有订单的噪声行。入库侧另有一道正则过滤，这里是第二道。
 */
export interface BillTotals {
  /** 全量合计，不受 LIMIT 影响 */
  usd: number;
  orders: number;
  /** 其中「同日站内有排除爬虫后的出站」的那部分金额 */
  matchedUsd: number;
  rows: number;
}

/**
 * 对账页顶部那四个数。
 *
 * 【必须单独查，不能对 loadBillRows 的返回求和】——那是 LIMIT 200 之后的结果集，
 * 而它就印在概览面板的全量 billUsd 旁边。今天只有 3 行有订单所以看不出来，
 * 等账单行多起来，同一屏上会出现两个不一样的「我方分成」。
 * 传了 asOf 时「同日站内有出站」只数 as_of 及之前的点击。
 */
export async function loadBillTotals(asOf?: Date): Promise<BillTotals> {
  const db = getDb();
  const r = await db.execute<CountRow & Record<string, number>>(sql`
    SELECT COALESCE(sum(revenue_usd), 0)::float8 AS usd,
           COALESCE(sum(order_cnt), 0)::int AS orders,
           count(*)::int AS n,
           COALESCE(sum(revenue_usd) FILTER (WHERE EXISTS (
             SELECT 1 FROM outbound_clicks
             WHERE outbound_clicks.drama_id IN (SELECT clicked.id FROM dramas billed JOIN dramas clicked ON clicked.group_key = billed.group_key AND clicked.locale = billed.locale WHERE billed.id = cps_bill_daily.book_id)
               AND to_char((outbound_clicks.created_at AT TIME ZONE 'UTC')::date,
                           'YYYY-MM-DD') = cps_bill_daily.bill_date
               AND (${notBot()})${clickedBy(asOf)}
           )), 0)::float8 AS matched
    FROM cps_bill_daily WHERE order_cnt > 0
  `);
  const row = r.rows[0] ?? {};
  return {
    usd: Number(row.usd ?? 0),
    orders: Number(row.orders ?? 0),
    matchedUsd: Number(row.matched ?? 0),
    rows: Number(row.n ?? 0),
  };
}

/**
 * 账单行当天（bill_date 那个 UTC 日）同组同语种兄弟行的过滤后出站数，一个标量子查询。
 * 【要求外层账单行的别名是 bill】（要有 book_id、bill_date 两列），同 clickedBy 要求全名 outbound_clicks 的做法。
 * 对账明细与 feed v2 的 rs_bill_orders 共用这一份，两处口径不许漂开；传了 asOf 时只数 as_of 及之前的点击。
 * loadBillRows 发出的 SQL 与抽出来之前逐字相同（tests/pick-export-v2-sql.test.ts 比夹具）。
 */
export function sameDayClicks(asOf?: Date): SQL {
  return sql`(SELECT count(*)::int FROM outbound_clicks
             WHERE outbound_clicks.drama_id IN (SELECT clicked.id FROM dramas billed JOIN dramas clicked ON clicked.group_key = billed.group_key AND clicked.locale = billed.locale WHERE billed.id = bill.book_id)
               AND to_char((outbound_clicks.created_at AT TIME ZONE 'UTC')::date,
                           'YYYY-MM-DD') = bill.bill_date
               AND (${notBot()})${clickedBy(asOf)})`;
}

/**
 * 对账明细。same_day 数的是账单日当天的过滤后出站：不传 asOf 时数到 now 为止（当天还没过完就还会涨），
 * 传了就只数 as_of 及之前的点击，导出与核对脚本拿到的是同一个数。
 */
export async function loadBillRows(limit = 200, asOf?: Date): Promise<BillRow[]> {
  const db = getDb();
  const result = await db.execute<BillQueryRow>(sql`
    SELECT bill.bill_date, bill.book_id, d.title, d.locale, d.publish_at,
           bill.promotion_type, bill.promotion_value, bill.book_title,
           bill.order_cnt, bill.revenue_usd::float8 AS usd,
           ${sameDayClicks(asOf)} AS same_day
    FROM cps_bill_daily AS bill
    LEFT JOIN dramas AS d ON d.id = bill.book_id
    WHERE bill.order_cnt > 0
    ORDER BY bill.bill_date DESC, bill.revenue_usd DESC
    LIMIT ${limit}
  `);
  return result.rows.map((r) => ({
    billDate: r.bill_date,
    bookId: r.book_id,
    /* 我方库里查不到时退回上游随账单给的标题——那种情况是这部剧已被上游下架 */
    title: r.title ?? r.book_title,
    locale: r.locale ?? "",
    publishAt: toTimestamp(r.publish_at),
    promotionType: r.promotion_type,
    promotionValue: r.promotion_value,
    orderCnt: r.order_cnt,
    revenueUsd: r.usd,
    sameDayClicks: r.same_day,
  }));
}

interface BillQueryRow extends Record<string, unknown> {
  bill_date: string;
  book_id: string;
  title: string | null;
  locale: string | null;
  publish_at: string | Date | null;
  promotion_type: string;
  promotion_value: string;
  book_title: string;
  order_cnt: number;
  usd: number;
  same_day: number;
}

interface SeriesQueryRow extends Record<string, unknown> {
  day: string;
  usd: number;
  promoters: number;
}

interface ClickQueryRow extends Record<string, unknown> {
  day: string;
  human: number;
  bot: number;
}

interface DramaBillQueryRow extends Record<string, unknown> {
  book_id: string;
  bill_date: string;
  promotion_type: string;
  promotion_value: string;
  order_cnt: number;
  usd: number;
}

export interface DramaDetail {
  row: ObserveRow;
  series: SeriesPoint[];
  bill: BillRow[];
  /** bill 取回条数等于 DETAIL_BILL_LIMIT：更早的成交日可能没取到（问答的未取全提示读它） */
  billTruncated: boolean;
  /** 近 14 天出站，按天拆成真人与爬虫 */
  clicks: { day: string; human: number; bot: number }[];
}

/**
 * 单剧页分成明细最多取几条（最近的在前）。取回条数等于它就当被截断了：loadDramaDetail 返回 billTruncated 标记，
 * 问答的投影与未取全提示读标记，不在 ask 层另写一份上限（gpt-6-astra 2026-09-13 审计 N4b）
 */
const DETAIL_BILL_LIMIT = 50;

/**
 * 单剧页。四条查询并发，都很小。
 * 传了 asOf 时 14 天出站是 (asOf − 14 天, asOf]，90 天曲线取到 asOf 的 UTC 日为止（按日期截：as_of 当天、
 * 但在 as_of 之后才写的那一天快照截不掉，由 fingerprint 的「最新快照日与它的行数」核出来，方案 4.3）。
 */
export async function loadDramaDetail(id: string, asOf?: Date): Promise<DramaDetail | null> {
  const db = getDb();
  const canonical = await db.execute(sql`SELECT dramas.id FROM dramas
    JOIN dramas src ON src.group_key = dramas.group_key AND src.locale = dramas.locale
    WHERE src.id = ${id} AND ${publicCanonical()} LIMIT 1`);
  if (!canonical.rows[0]) return null;
  id = String(canonical.rows[0].id);
  const [rows, seriesResult, clicksResult, bill] = await Promise.all([
    loadRows(
      {
        tab: "drama",
        sort: "rr",
        locale: "",
        bucket: null,
        page: 1,
        size: 1,
        q: id,
        dramaId: id,
      },
      { limit: 1, asOf },
    ),
    db.execute<SeriesQueryRow>(sql`
      SELECT observed_on AS day, recent_revenue_cents::float8 AS usd, promoters_cnt AS promoters
      FROM drama_observations
      WHERE drama_id = ${id} AND observed_on >= ${utcDayOffset(SERIES_DAYS, asOf)}${asOf ? sql` AND observed_on <= ${utcDayOffset(0, asOf)}` : sql``}
        AND to_jsonb(drama_observations)->>'metrics_valid' = 'true'
      ORDER BY observed_on ASC
    `),
    db.execute<ClickQueryRow>(sql`
      SELECT to_char((created_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD') AS day,
             count(*) FILTER (WHERE ${notBot()})::int AS human,
             count(*) FILTER (WHERE NOT (${notBot()}))::int AS bot
      FROM outbound_clicks
      WHERE drama_id IN (SELECT src.id FROM dramas src JOIN dramas me ON me.group_key = src.group_key AND me.locale = src.locale WHERE me.id = ${id}) AND outbound_clicks.created_at > ${nowSql(asOf)} - interval '14 days'${clickedBy(asOf)}
      GROUP BY 1 ORDER BY 1 ASC
    `),
    db.execute<DramaBillQueryRow>(sql`
      SELECT bd.book_id, bd.bill_date, bd.promotion_type, bd.promotion_value, bd.order_cnt,
             bd.revenue_usd::float8 AS usd
      FROM cps_bill_daily bd
      JOIN dramas src ON src.id = bd.book_id
      JOIN dramas me ON me.group_key = src.group_key AND me.locale = src.locale
      WHERE me.id = ${id} AND bd.order_cnt > 0
      ORDER BY bd.bill_date DESC LIMIT ${sql.raw(String(DETAIL_BILL_LIMIT))}
    `),
  ]);

  const row = rows.rows[0];
  if (!row) return null;

  return {
    row,
    series: seriesResult.rows.map((r) => ({
      day: r.day,
      revenueRaw: r.usd,
      promoters: r.promoters,
    })),
    clicks: clicksResult.rows.map((r) => ({
      day: r.day,
      human: r.human,
      bot: r.bot,
    })),
    bill: bill.rows.map((r) => ({
      billDate: r.bill_date,
      bookId: r.book_id,
      title: row.title,
      locale: row.locale,
      publishAt: row.publishAt,
      promotionType: r.promotion_type,
      promotionValue: r.promotion_value,
      orderCnt: r.order_cnt,
      revenueUsd: r.usd,
      sameDayClicks: 0,
    })),
    billTruncated: bill.rows.length === DETAIL_BILL_LIMIT,
  };
}
