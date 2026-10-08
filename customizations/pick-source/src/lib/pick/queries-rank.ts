import "server-only";

import { sql, type SQL } from "drizzle-orm";

import { getDb } from "@/db";
import {
  loadBillRows,
  loadBillTotals,
  loadGrowthBaseline,
  loadRows,
  loadRsCounts,
  type BillRow,
  type BillTotals,
  type ObserveRow,
} from "@/lib/observe/queries";
import { readSources } from "@/lib/observe/source-state";
import { diagnoseGrowthEmpty, type GrowthDiagnosis } from "./growth-diagnosis";
import type { SourceState } from "@/lib/observe/source-types";
import {
  loadPostedFor,
  ROW_COLUMNS_QUALIFIED,
  toRow,
  type PickRow,
  type PickSignal,
  type RawRow,
} from "./queries-shared";
import {
  BASES,
  GRADES,
  GROWTH_LIMIT,
  GROWTH_SORTS,
  THEATER_BASES,
  isDailyRank,
  isRsRank,
  type Basis,
  type Grade,
  type PickRequest,
  type RankKey,
  type RsRank,
  type RsSort,
  type TheaterBasis,
  resolveDay,
  resolveWeek,
  type PeriodResolution,
  type WeekOption,
} from "./request";

/**
 * 榜单 tab：一次只看一张榜的原样，每张榜有自己的子筛选与排序。
 *
 * 【日榜 / 周榜的「那一天 / 那一周有哪些剧」在 SQL 里做】：`catalog_signals.payload->'h'` 是
 * `[[日期, 名次, 备注?]]`（日榜）/ `[[周起, 周标签]]`（周榜），用 jsonb_array_elements 的 LATERAL
 * 取当天名次，不把 187 条日榜（含最大 2.5 KB 的历史）整包搬回来再在 JS 里找。
 * 【榜单 tab 一律含已下架的行】：那一天的榜是历史事实，把后来下架的剧从第 3 名抠掉，榜上就出现一个洞；
 * 页面把它们标出来。选剧 tab 默认排除已下架是为了「现在能取」，两处目的不同。
 *
 * 【ReelShort 的七张榜 + 分成对账】（2026-09-11 观测台并入）：rk 以 rs_ 开头时把 PickRequest 映射成
 * 观测台的 ObserveRequest 调 lib/observe/queries 的 loadRows / loadBillRows——同一条查询，口径逐字相同。
 * 这个文件是选剧台 lib 里允许 import 观测台查询的两处之一（另一处 queries-reelshort.ts）。
 */

export interface RankRow extends PickRow {
  /** 这张榜上的那条信号（一行在同一张榜上有两条时取名次 / 周数更好的那条） */
  signal: PickSignal;
  /** 日榜：所选日期的名次与当日备注 */
  dayRank: number | null;
  dayNote: string;
}

export interface RankMeta {
  /** 每张榜有多少行（全库，不带别的过滤），chips 用；rs_* 那几张是观测台口径的计数 */
  counts: Partial<Record<RankKey, number>>;
  /** 涨幅榜三种增量各自的可比行数（空态里指路用） */
  growthCounts: Record<"d1" | "d7" | "dp1" | "dp7", number>;
  /** 日榜：所有有榜的日子，新的在前 */
  days: string[];
  /** 周榜：所有有榜的周，按周起日期分组（周标签不带年份、跨年重名），新的在前 */
  weeks: WeekOption[];
  /** 评级榜：各档行数 */
  grades: Partial<Record<Grade, number>>;
  /** 解析后的选中日 / 周起日期（请求里没有或不存在就取最近的） */
  day: string;
  week: string;
  /** 请求里的日 / 周怎么落到这一期的：页面与问答据此照说「请求的那一期没有榜」；不是日榜 / 周榜恒为 latest */
  dayResolution: PeriodResolution;
  weekResolution: PeriodResolution;
}

interface SignalRaw extends Record<string, unknown> {
  kind: string;
  ord: number;
  evidence_on: string | null;
  rank: number | null;
  grade: string;
  note: string;
  payload: Record<string, unknown> | null;
  day_rank?: number | null;
  day_note?: string | null;
}

/** asOf 只影响 rs 榜的计数（loadRsCounts）；剧场榜的计数、日 / 周 / 档候选读剧单表，没有时间维度 */
export async function loadRankMeta(req: PickRequest, asOf?: Date): Promise<RankMeta> {
  const db = getDb();
  const kind = req.rank;
  const [countRes, rsCounts, dayRes, weekRes, gradeRes] = await Promise.all([
    db.execute(sql`SELECT kind, count(DISTINCT row_key)::int AS n FROM catalog_signals GROUP BY kind`),
    loadRsCounts(asOf),
    isDailyRank(kind)
      ? db.execute(
          sql`SELECT DISTINCT x->>0 AS d FROM catalog_signals s, jsonb_array_elements(s.payload->'h') x
              WHERE s.kind = ${kind} AND jsonb_typeof(s.payload->'h') = 'array' ORDER BY d DESC`,
        )
      : Promise.resolve({ rows: [] as Record<string, unknown>[] }),
    kind === "kw"
      ? db.execute(
          sql`SELECT x->>0 AS start, max(x->>1) AS w FROM catalog_signals s, jsonb_array_elements(s.payload->'h') x
              WHERE s.kind = 'kw' AND jsonb_typeof(s.payload->'h') = 'array' GROUP BY start ORDER BY start DESC`,
        )
      : Promise.resolve({ rows: [] as Record<string, unknown>[] }),
    kind === "sm" || kind === "mg"
      ? db.execute(
          sql`SELECT grade, count(DISTINCT row_key)::int AS n FROM catalog_signals WHERE kind = ${kind} GROUP BY grade`,
        )
      : Promise.resolve({ rows: [] as Record<string, unknown>[] }),
  ]);
  /* 涨幅榜的数跟当前排序走：d7 还没攒够快照时它就是 0，与榜一致；另两种增量的数给空态当出口 */
  const growthSort = rsSortFor("rs_growth", req.rsSort);
  const growthCounts = { d1: rsCounts.growthD1, d7: rsCounts.growthD7, dp1: rsCounts.growthDp1, dp7: rsCounts.growthDp7 } as const;
  const counts: Partial<Record<RankKey, number>> = {
    rs_rr: rsCounts.all,
    rs_growth: growthCounts[growthSort as keyof typeof growthCounts] ?? 0,
    rs_cand: rsCounts.cand,
    rs_pc: rsCounts.pc,
    rs_clk: rsCounts.clk,
    rs_gsc: rsCounts.gsc,
    rs_bill: rsCounts.bill,
    rs_ledger: rsCounts.ledger,
  };
  for (const r of countRes.rows as { kind: string; n: number }[])
    if ((THEATER_BASES as readonly string[]).includes(r.kind)) counts[r.kind as TheaterBasis] = r.n;
  const days = (dayRes.rows as { d: string | null }[]).map((r) => r.d).filter((d): d is string => Boolean(d));
  const weeks: WeekOption[] = (weekRes.rows as { w: string | null; start: string | null }[])
    .filter((r) => r.w && r.start && /^\d{4}-\d{2}-\d{2}$/.test(r.start))
    .map((r) => ({ week: r.w as string, start: r.start as string }));
  const dayPick = isDailyRank(kind) ? resolveDay(days, req.day) : { day: "", how: "latest" as const };
  const weekPick = kind === "kw" ? resolveWeek(weeks, req.week) : { start: "", how: "latest" as const };
  const grades: Partial<Record<Grade, number>> = {};
  for (const r of gradeRes.rows as { grade: string; n: number }[])
    if ((GRADES as readonly string[]).includes(r.grade)) grades[r.grade as Grade] = r.n;
  return {
    counts,
    growthCounts,
    days,
    weeks,
    grades,
    day: dayPick.day,
    week: weekPick.start,
    dayResolution: dayPick.how,
    weekResolution: weekPick.how,
  };
}

const SIGNAL_COLUMNS = sql.raw("s.kind, s.ord, s.evidence_on, s.rank, s.grade, s.note, s.payload");

/** 评级档位的排序位置：不在 GRADES 里的排最后 */
function gradeOrder(): SQL {
  return sql`array_position(ARRAY[${sql.join(
    GRADES.map((g) => sql`${g}`),
    sql`, `,
  )}]::text[], s.grade) NULLS LAST`;
}

/**
 * 每张榜的「信号子查询」与排序。非日榜的种类先按 row_key 去重（一行在同一张榜上有两条时留一条），
 * 再与 catalog_rows join；日榜用 LATERAL 取所选日期那一格。
 */
function rankSelect(req: PickRequest, meta: RankMeta, kind: TheaterBasis): { from: SQL; order: SQL; countFrom: SQL } {
  if (isDailyRank(kind)) {
    const from = sql`FROM catalog_signals s
      JOIN catalog_rows ON catalog_rows.row_key = s.row_key
      JOIN LATERAL (
        SELECT (x->>1)::int AS day_rank, coalesce(x->>2, '') AS day_note
        FROM jsonb_array_elements(s.payload->'h') x
        WHERE x->>0 = ${meta.day} AND jsonb_typeof(x->1) = 'number' LIMIT 1
      ) e ON true
      WHERE s.kind = ${kind} AND jsonb_typeof(s.payload->'h') = 'array'`;
    return {
      from,
      countFrom: from,
      order: sql`ORDER BY e.day_rank ASC, catalog_rows.title ASC, catalog_rows.row_key ASC`,
    };
  }
  const filters: SQL[] = [sql`s.kind = ${kind}`];
  let innerOrder = sql`s.ord ASC`;
  if (kind === "kw") {
    filters.push(
      /* 按周起日期（h 的第 0 格）过滤，不按周标签：标签跨年重名，按标签会把两年的行混进同一张榜（问答审计 P1-6） */
      sql`jsonb_typeof(s.payload->'h') = 'array' AND EXISTS (SELECT 1 FROM jsonb_array_elements(s.payload->'h') x WHERE x->>0 = ${meta.week})`,
    );
    innerOrder = sql`(s.payload->>'weeks')::int DESC NULLS LAST, s.ord ASC`;
  }
  if ((kind === "sm" || kind === "mg") && req.grade) filters.push(sql`s.grade = ${req.grade}`);
  const where = sql.join(filters, sql` AND `);
  const from = sql`FROM (
      SELECT DISTINCT ON (s.row_key) s.row_key, ${SIGNAL_COLUMNS} FROM catalog_signals s WHERE ${where}
      ORDER BY s.row_key, ${innerOrder}
    ) s JOIN catalog_rows ON catalog_rows.row_key = s.row_key`;
  const countFrom = sql`FROM (SELECT DISTINCT s.row_key FROM catalog_signals s WHERE ${where}) s`;
  let order: SQL;
  if (kind === "kw")
    order = sql`ORDER BY (s.payload->>'weeks')::int DESC NULLS LAST, catalog_rows.title ASC, catalog_rows.row_key ASC`;
  else if (kind === "sm" || kind === "mg")
    order = sql`ORDER BY ${gradeOrder()}, catalog_rows.listed_on DESC NULLS LAST, catalog_rows.title ASC, catalog_rows.row_key ASC`;
  else
    order = sql`ORDER BY s.evidence_on DESC NULLS LAST, catalog_rows.listed_on DESC NULLS LAST, catalog_rows.title ASC, catalog_rows.row_key ASC`;
  return { from, order, countFrom };
}

export async function loadRankRows(
  req: PickRequest,
  meta: RankMeta,
): Promise<{ rows: RankRow[]; total: number; hasMore: boolean }> {
  const db = getDb();
  if (isRsRank(req.rank)) throw new Error("ReelShort 榜走 loadRsRank");
  const kind: TheaterBasis = req.rank;
  if ((isDailyRank(kind) && !meta.day) || (kind === "kw" && !meta.week)) return { rows: [], total: 0, hasMore: false };
  const { from, order, countFrom } = rankSelect(req, meta, kind);
  const offset = (req.page - 1) * req.size;
  const extra = isDailyRank(kind) ? sql`, e.day_rank, e.day_note` : sql``;
  const [page, count] = await Promise.all([
    db.execute(
      sql`SELECT ${ROW_COLUMNS_QUALIFIED}, ${SIGNAL_COLUMNS}${extra} ${from} ${order} LIMIT ${req.size} OFFSET ${offset}`,
    ),
    db.execute(sql`SELECT count(*)::int AS n ${countFrom}`),
  ]);
  const raws = page.rows as (RawRow & SignalRaw)[];
  const posted = await loadPostedFor(raws.map((r) => r.row_key));
  const total = Number((count.rows[0] as { n: number }).n);
  const rows: RankRow[] = raws.map((r) => {
    const signal: PickSignal = {
      kind: (BASES as readonly string[]).includes(r.kind) ? (r.kind as Basis) : kind,
      ord: r.ord,
      evidenceOn: r.evidence_on,
      rank: r.rank,
      grade: r.grade,
      note: r.note,
      payload: r.payload ?? {},
    };
    return {
      ...toRow(r),
      signals: [signal],
      posted: posted.get(r.row_key) ?? [],
      signal,
      dayRank: typeof r.day_rank === "number" ? r.day_rank : null,
      dayNote: r.day_note ?? "",
    };
  });
  return { rows, total, hasMore: offset + rows.length < total };
}

export type { GrowthDiagnosis, GrowthEmptyReason } from "./growth-diagnosis";

/** 涨幅榜每种排序比的是几天前的快照（observe/queries.ts 的 comparableOnly：d1 / dp1 看 s1，其余看 s7） */
const GROWTH_WINDOW_DAYS: Record<string, number> = { d1: 1, d7: 7, dp1: 1, dp7: 7 };

/**
 * 涨幅榜 0 行时为什么是空的：页面空态与问答 list_rank 共用，判断在纯模块 growth-diagnosis.ts。
 * 全库可比行数走 loadRsCounts（榜单 chip 同一条），基线快照状态走 loadGrowthBaseline；两条并发，只在空榜时付
 */
export async function loadGrowthDiagnosis(req: PickRequest, asOf?: Date): Promise<GrowthDiagnosis> {
  const sort = rsSortFor("rs_growth", req.rsSort);
  const windowDays = GROWTH_WINDOW_DAYS[sort] ?? 7;
  const [counts, baseline] = await Promise.all([loadRsCounts(asOf), loadGrowthBaseline(windowDays, asOf)]);
  const bySort: Record<string, number> = { d1: counts.growthD1, d7: counts.growthD7, dp1: counts.growthDp1, dp7: counts.growthDp7 };
  return diagnoseGrowthEmpty({
    ...baseline,
    windowDays,
    filtered: Boolean(req.rsLocale || req.rsBucket || req.q),
    comparableWithoutFilters: (bySort[sort] ?? 0) > 0,
  });
}

/** 涨幅榜只认四种增量排序（GROWTH_SORTS）；别的排序落到「较 7 天前」（与原观测台 ListView 同一条回落） */

/** rs 榜的有效排序：涨幅榜限三种增量；单指标榜固定；总览 / 候选清单随 rs 参数 */
export function rsSortFor(rank: RsRank, sort: RsSort): RsSort {
  switch (rank) {
    case "rs_growth":
      return GROWTH_SORTS.includes(sort) ? sort : "d7";
    case "rs_pc":
      return "promoters";
    case "rs_clk":
      return "clicks";
    case "rs_gsc":
      return "gsc";
    case "rs_bill":
      return "bill";
    default:
      return sort;
  }
}

export type RsRankResult =
  | { kind: "rows"; rows: ObserveRow[]; total: number | null; hasMore: boolean; sort: RsSort }
  | { kind: "ledger"; rows: BillRow[]; totals: BillTotals; source: SourceState | undefined };

/**
 * ReelShort 的榜：PickRequest → ObserveRequest 的映射只在这里做一次。
 * - rs_rr：全部正典行，按 rs 排序（默认 30 天指标）；
 * - rs_growth：comparableOnly + 前 50，不分页；
 * - rs_cand：candidatesOnly；
 * - rs_pc / clk / gsc / bill：固定排序且只留那一项 > 0 的行；
 * - rs_ledger：分成对账（有订单的账单行 + 全量合计 + 采集来源状态）。
 * asOf 原样传给 loadRows / loadBillRows / loadBillTotals；采集来源状态是「现在」的状态，不钉（导出用 fingerprint 核它）。
 */
export async function loadRsRank(req: PickRequest, rank: RsRank, asOf?: Date): Promise<RsRankResult> {
  if (rank === "rs_ledger") {
    const [rows, totals, sources] = await Promise.all([loadBillRows(undefined, asOf), loadBillTotals(asOf), readSources()]);
    return { kind: "ledger", rows, totals, source: sources.bill };
  }
  const sort = rsSortFor(rank, req.rsSort);
  const growth = rank === "rs_growth";
  const only =
    rank === "rs_pc" ? "promoters" : rank === "rs_clk" ? "clicks" : rank === "rs_gsc" ? "gsc" : rank === "rs_bill" ? "bill" : undefined;
  const { rows, hasMore, total } = await loadRows(
    {
      tab: "all",
      sort,
      locale: req.rsLocale,
      bucket: req.rsBucket,
      page: req.page,
      size: req.size,
      q: req.q,
      dramaId: "",
    },
    {
      candidatesOnly: rank === "rs_cand",
      comparableOnly: growth,
      limit: growth ? GROWTH_LIMIT : undefined,
      only,
      asOf,
    },
  );
  return { kind: "rows", rows, total, hasMore, sort };
}
