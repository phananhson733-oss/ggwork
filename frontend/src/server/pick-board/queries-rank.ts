// PORTED_FROM: realshort@816ca2e src/lib/pick/queries-rank.ts
// 本地改动：loader 不再有可选的 asOf 参数（时点取钉住版本的 as_of）；rs 榜改调 rs-queries；订单对账的
// 来源状态改读 meta.sources.bill；RsRankResult 的对账分支带新的 BillRow / BillTotals（没有金额）。
// P3-3a 只有类型与签名，函数体由 P3-3 补。
import "server-only";

import { type GrowthDiagnosis } from "@/core/pick-board/growth-diagnosis";
import {
  type Grade,
  type PeriodResolution,
  type PickRequest,
  type RankKey,
  type RsRank,
  type RsSort,
  type WeekOption,
} from "@/core/pick-board/request";
import { type SourceState } from "@/core/pick-board/source-types";

import { type PickRow, type PickSignal, type RowsPage } from "./queries-shared";
import { type BillRow, type BillTotals, type ObserveRow } from "./rs-queries";

/**
 * 榜单 tab：一次只看一张榜的原样。剧场榜读 catalog_signals；rk 以 rs_ 开头时走 rs-queries 的
 * ReelShort 七张榜加订单对账。【榜单 tab 一律含已下架的行】：那一天的榜是历史事实。
 */

export interface RankRow extends PickRow {
  /** 这张榜上的那条信号（一行在同一张榜上有两条时取名次 / 周数更好的那条） */
  signal: PickSignal;
  /** 日榜：所选日期的名次与当日备注 */
  dayRank: number | null;
  dayNote: string;
}

export interface RankMeta {
  /** 每张榜有多少行（全库，不带别的过滤），chips 用；rs_* 那几张取 meta.rsCounts */
  counts: Partial<Record<RankKey, number>>;
  /** 涨幅榜三种增量各自的可比行数（空态里指路用） */
  growthCounts: Record<"d1" | "d7" | "dp1" | "dp7", number>;
  /** 日榜：所有有榜的日子，新的在前 */
  days: string[];
  /** 周榜：所有有榜的周，按周起日期分组，新的在前 */
  weeks: WeekOption[];
  /** 评级榜：各档行数 */
  grades: Partial<Record<Grade, number>>;
  /** 解析后的选中日 / 周起日期（请求里没有或不存在就取最近的） */
  day: string;
  week: string;
  /** 请求里的日 / 周怎么落到这一期的；不是日榜 / 周榜恒为 latest */
  dayResolution: PeriodResolution;
  weekResolution: PeriodResolution;
}

export type RsRankResult =
  | {
      kind: "rows";
      rows: ObserveRow[];
      total: number | null;
      hasMore: boolean;
      sort: RsSort;
    }
  | {
      kind: "ledger";
      rows: BillRow[];
      totals: BillTotals;
      /** 账单采集来源的状态（meta.sources.bill）；版本里没有时 undefined */
      source: SourceState | undefined;
    };

export type {
  GrowthDiagnosis,
  GrowthEmptyReason,
} from "@/core/pick-board/growth-diagnosis";

const pending = (name: string) =>
  new Error(`pick-board: ${name} is not implemented yet (P3-3)`);

export async function loadRankMeta(_req: PickRequest): Promise<RankMeta> {
  throw pending("loadRankMeta");
}

/** 剧场榜的一页；rs_* 榜走 loadRsRank */
export async function loadRankRows(
  _req: PickRequest,
  _meta: RankMeta,
): Promise<RowsPage<RankRow>> {
  throw pending("loadRankRows");
}

/** 涨幅榜 0 行时为什么是空的（只在空榜时调） */
export async function loadGrowthDiagnosis(
  _req: PickRequest,
): Promise<GrowthDiagnosis> {
  throw pending("loadGrowthDiagnosis");
}

/** rs 榜的有效排序：涨幅榜限增量排序；单指标榜固定；总览 / 候选清单随 rs 参数 */
export function rsSortFor(_rank: RsRank, _sort: RsSort): RsSort {
  throw pending("rsSortFor");
}

/** ReelShort 的七张榜与订单对账：PickRequest 到 rs 查询的映射只在这里做一次 */
export async function loadRsRank(
  _req: PickRequest,
  _rank: RsRank,
): Promise<RsRankResult> {
  throw pending("loadRsRank");
}
