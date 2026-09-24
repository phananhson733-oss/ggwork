// PORTED_FROM: realshort@816ca2e src/lib/observe/queries.ts
// 本地改动：改读镜像 rs_rows / rs_ids / rs_bill_orders / rs_clicks14 与 pick_mirror.series，时点一律取钉住版本的
// as_of（loader 不再有可选的 asOf 参数）；ObserveRow 去掉我方分成金额的累计与四个窗口；BillRow 去掉推广值、金额与
// 上线日期，加 canonicalId 与 sourceRows（合并后一行对应几条原始账单行）；BillTotals 改成行数口径；
// loadDramaDetail 只收正典 id；loadGrowthBaseline 的窗口只有 meta.growthBaseline 给的 1 与 7 天。
// P3-3a 只有类型与签名，函数体由 P3-3 补。
import "server-only";

import { type BaselineSnapshot } from "@/core/pick-board/growth-diagnosis";
import { type ObserveRequest } from "@/core/pick-board/metrics";

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

const pending = (name: string) =>
  new Error(`pick-board: ${name} is not implemented yet (P3-3)`);

/** 总览 / 候选 / 涨幅榜 / 单指标榜 / 单剧页共用的主查询 */
export async function loadRows(
  _req: ObserveRequest,
  _opts: LoadRowsOptions = {},
): Promise<ObserveRowsPage> {
  throw pending("loadRows");
}

/** 选剧台一页里的 ReelShort 行取指标：同一条查询只多一个 id 过滤，不分页、不计数 */
export async function loadRowsByIds(
  _ids: readonly string[],
  _opts: { fullText?: boolean } = {},
): Promise<Map<string, ObserveRow>> {
  throw pending("loadRowsByIds");
}

export async function loadRsCounts(): Promise<RsCounts> {
  throw pending("loadRsCounts");
}

export async function loadGrowthBaseline(
  _windowDays: GrowthWindowDays,
): Promise<GrowthBaseline> {
  throw pending("loadGrowthBaseline");
}

export async function loadBillRows(
  _limit: number = BILL_ROWS_LIMIT,
): Promise<BillRow[]> {
  throw pending("loadBillRows");
}

export async function loadBillTotals(): Promise<BillTotals> {
  throw pending("loadBillTotals");
}

/** 单剧页：指标、90 天曲线、14 天出站、订单明细四条并发。id 须是正典 id（rs_ids.canonical_id） */
export async function loadDramaDetail(
  _canonicalId: string,
): Promise<DramaDetail | null> {
  throw pending("loadDramaDetail");
}
