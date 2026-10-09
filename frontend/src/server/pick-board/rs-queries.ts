// PORTED_FROM: realshort@816ca2e src/lib/observe/queries.ts
// 本地改动：改读镜像 rs_rows / rs_ids / rs_bill_orders / rs_clicks14 与 pick_mirror.series，时点一律取钉住版本的
// as_of（loader 不再有可选的 asOf 参数）；ObserveRow 去掉我方分成金额的累计与四个窗口；BillRow 去掉推广值、金额与
// 上线日期，加 canonicalId 与 sourceRows（合并后一行对应几条原始账单行）；BillTotals 改成行数口径；
// loadDramaDetail 只收正典 id，指标查询显式按 ids:[正典 id] 取（批判 B21）；loadGrowthBaseline 的窗口只有
// meta.growthBaseline 给的 1 与 7 天；loadRsCounts / loadGrowthBaseline 读 meta（按版本缓存）。
// 排序：d1 / d7 / eff 转 numeric 计算（批判 C35：镜像的 rr 是 float8，numeric(14,2) 原值经 float8 往返仍精确，
// 转回 numeric 才能复现原来的并列与顺序）；bill 改按导出的 bill_rank（本页没有金额）；clicks7 缺省是 0，
// 与原 toRow 的 `?? 0` 一致，不映射回 null（批判 B16）。
import "server-only";

import { sql, type SQL } from "drizzle-orm";

import {
  addUtcDays,
  type BaselineSnapshot,
} from "@/core/pick-board/growth-diagnosis";
import {
  PAGE_SIZE,
  SERIES_DAYS,
  type Bucket,
  type ObserveRequest,
  type Sort,
} from "@/core/pick-board/metrics";

import { readVersionMeta } from "./cache";
import { boardScope, getDb } from "./db";
import { textArray, toTimestamp, whereOf } from "./queries-shared";

/**
 * ReelShort 行的读查询（原观测台的数据层，现在是选剧台 rs 榜、ReelShort 行指标与单剧证据页的来源）。
 * 只有 src/server/pick-board 里的查询文件 import 这里；页面与组件经 `@/server/pick-board` 取这些类型。
 */

/** 订单对账一次最多取几条（按合并后的行计） */
export const BILL_ROWS_LIMIT = 200;

/** 单剧证据页订单明细最多取几条（最近的在前）；取回条数等于它就当被截断了 */
export const DETAIL_BILL_LIMIT = 50;

/** 页面上的一行 ReelShort 剧 */
export interface ObserveRow {
  id: string;
  title: string;
  locale: string;
  slug: string;
  publishAt: Date | null;
  chapterCount: number;
  payStart: number;
  /** 上游 recent_revenue（全平台 30 天大盘），单位美分；不是我方分成 */
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
  /** 15 天前那一行的值 */
  baseline15At: Date | null;
  revenueCents15: number | null;
  promotersCnt15: number | null;
  /** 有订单的账单行数（订单笔数合计），本页只显示笔数 */
  billOrders: number;
  /** 上游简介。只有单剧页（limit === 1）或 fullText 时取，列表里是空串 */
  description: string;
  /** 近 7 天排除爬虫后的出站点击 */
  clicks7: number;
  /** 近 7 天里最近一次过滤后出站的 UTC 日；没有就 null */
  lastClickOn: string | null;
  /** 最近一条有订单的账单日（上游 bill_date 原文）；没有就 null */
  lastBillOn: string | null;
}

/** 单剧 90 天曲线的一点：大盘销售额原值（美分）与推广人数 */
export interface SeriesPoint {
  day: string;
  revenueRaw: number;
  promoters: number;
}

/**
 * 订单对账的一行：rs_bill_orders 里同一天、同一部剧、同一推广类型合并后的一行。
 * 【sameDayClicks 是启发式，页面上必须说明】：同日站内有出站只是时间和位置对得上，不是归因证明。
 */
export interface BillRow {
  billDate: string;
  bookId: string;
  /** 账单上的 book_id 归到的正典 id；归不上时为 null（页面显示 book_title，不给链接） */
  canonicalId: string | null;
  /** rs_ids 里的剧名，查不到时退回账单随附的标题 */
  title: string;
  locale: string;
  promotionType: string;
  orderCnt: number;
  /** 合并前的原始账单行数 */
  sourceRows: number;
  /** 账单日当天同组同语种兄弟行的过滤后出站数 */
  sameDayClicks: number;
}

/** 订单对账顶部的合计：全量，不受 BILL_ROWS_LIMIT 影响 */
export interface BillTotals {
  /** 原始账单行数（= sum(source_rows)） */
  rows: number;
  /** 合并后的行数 */
  mergedRows: number;
  orders: number;
  /** 同日站内有过滤后出站的合并行数 */
  mergedWithClicks: number;
  /** 同上，按原始账单行计 */
  rowsWithClicks: number;
}

export interface DramaDetail {
  row: ObserveRow;
  series: SeriesPoint[];
  bill: BillRow[];
  /** bill 取回条数等于 DETAIL_BILL_LIMIT：更早的订单日可能没取到 */
  billTruncated: boolean;
  /** 近 14 天出站，按天拆成真人与爬虫 */
  clicks: { day: string; human: number; bot: number }[];
}

/** 榜单 tab 里 ReelShort 七张榜加订单对账的 chips 计数（meta.rsCounts） */
export interface RsCounts {
  all: number;
  cand: number;
  /** 涨幅榜三种增量各自的可比行数 */
  growthD1: number;
  growthD7: number;
  growthDp1: number;
  growthDp7: number;
  pc: number;
  clk: number;
  gsc: number;
  bill: number;
  /** 订单对账：原始账单行数 */
  ledger: number;
}

/** meta.growthBaseline 只有这两个窗口 */
export type GrowthWindowDays = 1 | 7;

/** 涨幅榜空榜诊断要的三件事（meta.growthBaseline[窗口]） */
export interface GrowthBaseline {
  /** 基线日（UTC）= as_of 当天 − 窗口天数 */
  baselineDay: string;
  baselineSnapshot: BaselineSnapshot;
  /** 保留期内最早一个有已校验快照的日子；没有是 null */
  earliestVerifiedOn: string | null;
}

export interface LoadRowsOptions {
  candidatesOnly?: boolean;
  /** 固定取前 N（涨幅榜、单剧页），不分页也不计总数 */
  limit?: number;
  comparableOnly?: boolean;
  /** 单指标榜：只留那一项 > 0 的行 */
  only?: "promoters" | "clicks" | "gsc" | "bill";
  /** 只取这几部（一页里的 ReelShort 行、单剧页的正典 id） */
  ids?: readonly string[];
  /** 取完整 tags 与 description；默认只有 limit === 1 时取全 */
  fullText?: boolean;
}

export interface ObserveRowsPage {
  rows: ObserveRow[];
  hasMore: boolean;
  /** 分页时的全量计数；opts.limit 固定取前 N 时为 null */
  total: number | null;
}

/** 一个 meta 值当成 JSON 对象读；不是对象时是空对象（字段各自回落） */
function record(value: unknown): Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** SQL 里的「现在」：钉住版本的 as_of，绑定参数（版本不可变，整页同一个时点） */
function asOfSql(): SQL {
  return sql`${boardScope().asOf}::timestamptz`;
}

/** as_of 的 UTC 日（date） */
function asOfDaySql(): SQL {
  return sql`(${asOfSql()} AT TIME ZONE 'UTC')::date`;
}

/** 上线时段过滤。publish_at 为空的行在任何一个桶里都不出现，这是有意的。天数按 as_of 的 UTC 日算 */
function bucketFilter(bucket: Bucket): SQL {
  const age = sql`(${asOfDaySql()} - (publish_at AT TIME ZONE 'UTC')::date)`;
  switch (bucket) {
    case "0-7":
      return sql`publish_at IS NOT NULL AND ${age} >= 0 AND ${age} <= 7`;
    case "8-30":
      return sql`publish_at IS NOT NULL AND ${age} > 7 AND ${age} <= 30`;
    case "31-90":
      return sql`publish_at IS NOT NULL AND ${age} > 30 AND ${age} <= 90`;
    case "91-365":
      return sql`publish_at IS NOT NULL AND ${age} > 90 AND ${age} <= 365`;
    case "366+":
      return sql`publish_at IS NOT NULL AND ${age} > 365`;
  }
}

/**
 * 排序片段。【必须走这个 switch，不许把 req.sort 拼进 SQL】：ORDER BY 的位置没有参数化占位符。
 * 【每条都以 drama_id 收尾】：排序键有大量并列值，没有稳定的第二键时同一行会在翻页之间来回跳。
 * d1 / d7 用未过滤的 s1 / s7 原值（与原来 LEFT JOIN 快照一致），eff 先把 rr 还原成两位小数的 numeric。
 */
function orderBy(sort: Sort): SQL {
  const tail = sql` NULLS LAST, drama_id ASC`;
  switch (sort) {
    case "d1":
      return sql`(rr::numeric - s1_rr::numeric) DESC${tail}`;
    case "d7":
      return sql`(rr::numeric - s7_rr::numeric) DESC${tail}`;
    case "dp1":
      return sql`(promoters_cnt - s1_p) DESC${tail}`;
    case "dp7":
      return sql`(promoters_cnt - s7_p) DESC${tail}`;
    case "promoters":
      return sql`promoters_cnt DESC${tail}`;
    case "publish":
      return sql`publish_at DESC${tail}`;
    case "bill":
      return sql`bill_rank ASC${tail}`;
    case "eff":
      return sql`(round(rr::numeric, 2) / NULLIF(promoters_cnt, 0)) DESC${tail}`;
    case "gsc":
      return sql`search_impressions DESC${tail}`;
    case "clicks":
      return sql`clicks7 DESC${tail}`;
    case "rr":
    default:
      return sql`rr DESC${tail}`;
  }
}

/**
 * 涨幅榜的「可比」：较昨日的两种（d1 / dp1）看 s1，其余看 s7。rr1 / p1 / rr7 / p7 只在那一天的快照与本行
 * 都校验过时才非空，所以一列非空就等于原来的三个条件；loadRsCounts 的四个 growth* 是同一张对照。
 */
function comparableFilter(sort: Sort): SQL {
  switch (sort) {
    case "d1":
      return sql`rr1 IS NOT NULL`;
    case "dp1":
      return sql`p1 IS NOT NULL`;
    case "dp7":
      return sql`p7 IS NOT NULL`;
    default:
      return sql`rr7 IS NOT NULL`;
  }
}

const ONLY: Record<NonNullable<LoadRowsOptions["only"]>, SQL> = {
  promoters: sql`promoters_cnt > 0`,
  clicks: sql`clicks7 > 0`,
  gsc: sql`search_impressions > 0`,
  bill: sql`bill_orders > 0`,
};

/** 剧名模糊 + book_id 精确；非正典的 book_id 经 rs_ids 归到它的正典行（原来的同组同语种 EXISTS） */
function searchFilter(q: string): SQL {
  return sql`(title ILIKE ${`%${q}%`} OR drama_id = ${q}
    OR drama_id = (SELECT canonical_id FROM rs_ids WHERE id = ${q}))`;
}

function rowFilters(req: ObserveRequest, opts: LoadRowsOptions): SQL[] {
  return [
    ...(req.locale ? [sql`locale = ${req.locale}`] : []),
    ...(req.bucket ? [bucketFilter(req.bucket)] : []),
    ...(req.q ? [searchFilter(req.q)] : []),
    ...(opts.candidatesOnly ? [sql`has_signal`] : []),
    ...(opts.ids ? [sql`drama_id = ANY(${textArray(opts.ids)})`] : []),
    ...(opts.only ? [ONLY[opts.only]] : []),
    ...(opts.comparableOnly ? [comparableFilter(req.sort)] : []),
  ];
}

/** 列表截到 tag_list[1:8]、简介置空；单剧页（limit === 1）与 fullText 取全 */
function observeColumns(full: boolean): SQL {
  return sql`drama_id AS id, title, locale, slug, publish_at, chapter_count, pay_start_raw AS pay_start,
    rr, promoters_cnt, metrics_valid, synced_at, baseline1_at, baseline7_at, baseline15_at,
    search_impressions, search_data_at, detail_synced_at,
    ${full ? sql`tag_list` : sql`tag_list[1:8]`} AS tags, ${full ? sql`description` : sql`''`} AS description,
    rr1, p1, rr7, p7, rr15, p15, bill_orders, clicks7 AS clicks, last_click_on, last_bill_on`;
}

interface ObserveRaw extends Record<string, unknown> {
  id: string;
  title: string;
  locale: string;
  slug: string;
  publish_at: string | null;
  chapter_count: number;
  pay_start: number;
  rr: number;
  promoters_cnt: number;
  metrics_valid: boolean | null;
  synced_at: string | null;
  baseline1_at: string | null;
  baseline7_at: string | null;
  baseline15_at: string | null;
  search_impressions: number;
  search_data_at: string | null;
  detail_synced_at: string | null;
  tags: string[] | null;
  description: string | null;
  rr1: number | null;
  p1: number | null;
  rr7: number | null;
  p7: number | null;
  rr15: number | null;
  p15: number | null;
  bill_orders: number | null;
  clicks: number | null;
  last_click_on: string | null;
  last_bill_on: string | null;
}

export function toObserveRow(r: ObserveRaw): ObserveRow {
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
    metricsValid: typeof r.metrics_valid === "boolean" ? r.metrics_valid : null,
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
    billOrders: r.bill_orders ?? 0,
    description: r.description ?? "",
    clicks7: r.clicks ?? 0,
    lastClickOn: r.last_click_on ?? null,
    lastBillOn: r.last_bill_on ?? null,
  };
}

/**
 * 总览 / 候选 / 涨幅榜 / 单指标榜 / 单剧页共用的主查询：只差过滤条件和排序，拆开就是同一份口径维护几遍。
 * 分页时并发再发一条 count（WHERE 与主查询共用同一份）；opts.limit 是「固定取前 N」，不分页也不计总数。
 */
export async function loadRows(
  req: ObserveRequest,
  opts: LoadRowsOptions = {},
): Promise<ObserveRowsPage> {
  const db = getDb();
  const paged = opts.limit === undefined;
  const limit = opts.limit ?? req.size;
  const offset = paged ? (req.page - 1) * limit : 0;
  const where = whereOf(rowFilters(req, opts));
  const full = opts.limit === 1 || opts.fullText === true;
  const [result, totalRows] = await Promise.all([
    db.execute<ObserveRaw>(
      sql`SELECT ${observeColumns(full)} FROM rs_rows ${where}
          ORDER BY ${orderBy(req.sort)} LIMIT ${limit + 1} OFFSET ${offset}`,
    ),
    paged
      ? db.execute<{ n: number }>(
          sql`SELECT count(*)::int AS n FROM rs_rows ${where}`,
        )
      : Promise.resolve(null),
  ]);
  const raw = result.rows;
  return {
    rows: raw.slice(0, limit).map(toObserveRow),
    /* 多取一行判断有没有下一页 */
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
 * 选剧台一页里的 ReelShort 行取指标：同一条 SELECT，只多一个 id 过滤；不分页、不 count。
 * limit 是 ids.length：一页里恰好只有一个 ReelShort 行时取全文，与原来一致。
 */
export async function loadRowsByIds(
  ids: readonly string[],
  opts: { fullText?: boolean } = {},
): Promise<Map<string, ObserveRow>> {
  if (ids.length === 0) return new Map();
  const { rows } = await loadRows(EMPTY_REQ, {
    ids,
    limit: ids.length,
    fullText: opts.fullText,
  });
  return new Map(rows.map((r) => [r.id, r]));
}

const RS_COUNT_KEYS = [
  "all",
  "cand",
  "growthD1",
  "growthD7",
  "growthDp1",
  "growthDp7",
  "pc",
  "clk",
  "gsc",
  "bill",
  "ledger",
] as const satisfies readonly (keyof RsCounts)[];

/** 榜单 chips 的计数：RealShort 导出时按同一套过滤算好的 meta.rsCounts（U30，集成测试拿 rs_rows 重算核对） */
export async function loadRsCounts(): Promise<RsCounts> {
  const raw = record(await readVersionMeta("rsCounts"));
  const n = (key: keyof RsCounts) => {
    const value = Number(raw[key] ?? 0);
    return Number.isFinite(value) ? value : 0;
  };
  return Object.fromEntries(
    RS_COUNT_KEYS.map((key) => [key, n(key)]),
  ) as unknown as RsCounts;
}

const BASELINE_SNAPSHOTS: readonly string[] = [
  "none",
  "unverified_only",
  "verified",
];

/** 涨幅榜空榜诊断的三件事：meta.growthBaseline[窗口]；缺项时基线日按 as_of 往前推，快照按「没有」算 */
export async function loadGrowthBaseline(
  windowDays: GrowthWindowDays,
): Promise<GrowthBaseline> {
  const all = record(await readVersionMeta("growthBaseline"));
  const raw = record(all[String(windowDays)]);
  const asOfDay = new Date(boardScope().asOf).toISOString().slice(0, 10);
  const fallbackDay = addUtcDays(asOfDay, -windowDays) ?? "";
  const snapshot = raw.baselineSnapshot;
  return {
    baselineDay:
      typeof raw.baselineDay === "string" ? raw.baselineDay : fallbackDay,
    baselineSnapshot:
      typeof snapshot === "string" && BASELINE_SNAPSHOTS.includes(snapshot)
        ? (snapshot as BaselineSnapshot)
        : "none",
    earliestVerifiedOn:
      typeof raw.earliestVerifiedOn === "string"
        ? raw.earliestVerifiedOn
        : null,
  };
}

interface BillRaw extends Record<string, unknown> {
  bill_date: string;
  book_id: string;
  promotion_type: string;
  canonical_id: string | null;
  book_title: string;
  order_cnt: number;
  source_rows: number;
  same_day_clicks: number;
  title: string | null;
  locale: string | null;
}

function toBillRow(r: BillRaw): BillRow {
  return {
    billDate: r.bill_date,
    bookId: r.book_id,
    canonicalId: r.canonical_id,
    /* rs_ids 里查不到时退回账单随附的标题：那种情况是这部剧已被上游下架 */
    title: r.title ?? r.book_title,
    locale: r.locale ?? "",
    promotionType: r.promotion_type,
    orderCnt: r.order_cnt,
    sourceRows: r.source_rows,
    sameDayClicks: r.same_day_clicks,
  };
}

/**
 * 订单对账明细：同一天、同一部剧、同一推广类型合并后的行。原来按金额排，本页没有金额，改按
 * 订单日新到旧、订单数多到少，再以 book_id、推广类型收尾；LIMIT 按合并后的行计（U27）。
 */
export async function loadBillRows(
  limit: number = BILL_ROWS_LIMIT,
): Promise<BillRow[]> {
  const res = await getDb().execute<BillRaw>(
    sql`SELECT o.bill_date, o.book_id, o.promotion_type, o.canonical_id, o.book_title, o.order_cnt,
               o.source_rows, o.same_day_clicks, i.title, i.locale
        FROM rs_bill_orders o LEFT JOIN rs_ids i ON i.id = o.book_id
        ORDER BY o.bill_date DESC, o.order_cnt DESC, o.book_id, o.promotion_type
        LIMIT ${limit}`,
  );
  return res.rows.map(toBillRow);
}

/**
 * 对账页顶部的合计。【必须单独查，不能对 loadBillRows 的返回求和】：那是 LIMIT 之后的结果集。
 * 行数口径是原始账单行（sum(source_rows)），合并后的行数另给。
 */
export async function loadBillTotals(): Promise<BillTotals> {
  const res = await getDb().execute<BillTotals>(
    sql`SELECT coalesce(sum(source_rows), 0)::int AS rows,
               count(*)::int AS "mergedRows",
               coalesce(sum(order_cnt), 0)::int AS orders,
               count(*) FILTER (WHERE same_day_clicks > 0)::int AS "mergedWithClicks",
               coalesce(sum(source_rows) FILTER (WHERE same_day_clicks > 0), 0)::int AS "rowsWithClicks"
        FROM rs_bill_orders`,
  );
  const row = res.rows[0];
  return {
    rows: row?.rows ?? 0,
    mergedRows: row?.mergedRows ?? 0,
    orders: row?.orders ?? 0,
    mergedWithClicks: row?.mergedWithClicks ?? 0,
    rowsWithClicks: row?.rowsWithClicks ?? 0,
  };
}

/**
 * 90 天曲线：pick_mirror.series 按正典 id 存成三列数组，unnest 成逐日行。上界取 latest_snapshot 与 as_of
 * 当天里较早的那个（原来是 observed_on <= as_of 当天；latest_snapshot 缺省时 LEAST 忽略它）。
 * series 在发布后还会折叠截断，trimmed_before 由 resolveVersion 一并读到。
 */
function seriesQuery(canonicalId: string): SQL {
  const latest = sql`(SELECT (value #>> '{}')::date FROM meta WHERE key = 'latestSnapshot')`;
  return sql`SELECT to_char(d.day, 'YYYY-MM-DD') AS day, d.rc, d.p AS promoters
    FROM pick_mirror.series s, unnest(s.days, s.revenue_cents, s.promoters) AS d(day, rc, p)
    WHERE s.drama_id = ${canonicalId}
      AND d.day >= ${asOfDaySql()} - ${SERIES_DAYS}::int
      AND d.day <= LEAST(${latest}, ${asOfDaySql()})
    ORDER BY d.day`;
}

interface DetailBillRaw extends Record<string, unknown> {
  bill_date: string;
  book_id: string;
  promotion_type: string;
  order_cnt: number;
  source_rows: number;
  same_day_clicks: number;
}

async function detailParts(canonicalId: string) {
  const db = getDb();
  return Promise.all([
    loadRows(
      { ...EMPTY_REQ, tab: "drama", size: 1, dramaId: canonicalId },
      { ids: [canonicalId], limit: 1 },
    ),
    db.execute<{ day: string; rc: number; promoters: number }>(
      seriesQuery(canonicalId),
    ),
    db.execute<{ day: string; human: number; bot: number }>(
      sql`SELECT day, human, bot FROM rs_clicks14 WHERE drama_id = ${canonicalId} ORDER BY day`,
    ),
    db.execute<DetailBillRaw>(
      sql`SELECT bill_date, book_id, promotion_type, order_cnt, source_rows, same_day_clicks
          FROM rs_bill_orders WHERE canonical_id = ${canonicalId}
          ORDER BY bill_date DESC, book_id, promotion_type LIMIT ${DETAIL_BILL_LIMIT}`,
    ),
  ]);
}

/** 单剧页：指标、90 天曲线、14 天出站、订单明细四条并发。id 须是正典 id（rs_ids.canonical_id） */
export async function loadDramaDetail(
  canonicalId: string,
): Promise<DramaDetail | null> {
  const [rows, series, clicks, bill] = await detailParts(canonicalId);
  const row = rows.rows[0];
  if (!row) return null;
  return {
    row,
    series: series.rows.map((r) => ({
      day: r.day,
      revenueRaw: r.rc,
      promoters: r.promoters,
    })),
    clicks: clicks.rows.map((r) => ({
      day: r.day,
      human: r.human,
      bot: r.bot,
    })),
    bill: bill.rows.map((r) => ({
      billDate: r.bill_date,
      bookId: r.book_id,
      canonicalId,
      title: row.title,
      locale: row.locale,
      promotionType: r.promotion_type,
      orderCnt: r.order_cnt,
      sourceRows: r.source_rows,
      sameDayClicks: r.same_day_clicks,
    })),
    billTruncated: bill.rows.length === DETAIL_BILL_LIMIT,
  };
}
