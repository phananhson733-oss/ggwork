// PORTED_FROM: realshort@816ca2e src/lib/pick/queries-rank.ts
// 本地改动：loader 不再有可选的 asOf 参数（时点取钉住版本的 as_of）；剧场榜一半照搬，只把网盘两列换成 has_pan；
// rs 榜改调 rs-queries；订单对账的来源状态改读 meta.sources.bill；RsRankResult 的对账分支带新的 BillRow /
// BillTotals（没有金额）；涨幅榜窗口只取 meta.growthBaseline 给的 1 与 7 天。
import "server-only";

import { sql, type SQL } from "drizzle-orm";

import {
  diagnoseGrowthEmpty,
  type GrowthDiagnosis,
} from "@/core/pick-board/growth-diagnosis";
import {
  BASES,
  GRADES,
  GROWTH_LIMIT,
  GROWTH_SORTS,
  isDailyRank,
  isRsRank,
  resolveDay,
  resolveWeek,
  THEATER_BASES,
  type Basis,
  type Grade,
  type PeriodResolution,
  type PickRequest,
  type RankKey,
  type RsRank,
  type RsSort,
  type TheaterBasis,
  type WeekOption,
} from "@/core/pick-board/request";
import { type SourceState } from "@/core/pick-board/source-types";

import { getDb } from "./db";
import {
  loadPostedFor,
  ROW_COLUMNS_QUALIFIED,
  toRow,
  type PickRow,
  type PickSignal,
  type RawRow,
  type RowsPage,
} from "./queries-shared";
import {
  loadBillRows,
  loadBillTotals,
  loadGrowthBaseline,
  loadRows,
  loadRsCounts,
  type BillRow,
  type BillTotals,
  type GrowthWindowDays,
  type LoadRowsOptions,
  type ObserveRow,
} from "./rs-queries";
import { readSources } from "./source-state";

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

const NO_ROWS = Promise.resolve({ rows: [] as Record<string, unknown>[] });

/** 日榜的日子、周榜的周、评级榜的档：只查当前这张榜要的那一条 */
function periodQueries(kind: RankKey) {
  const db = getDb();
  return Promise.all([
    isDailyRank(kind)
      ? db.execute<{ d: string | null }>(
          sql`SELECT DISTINCT x->>0 AS d FROM catalog_signals s, jsonb_array_elements(s.payload->'h') x
              WHERE s.kind = ${kind} AND jsonb_typeof(s.payload->'h') = 'array' ORDER BY d DESC`,
        )
      : NO_ROWS,
    kind === "kw"
      ? db.execute<{ w: string | null; start: string | null }>(
          sql`SELECT x->>0 AS start, max(x->>1) AS w FROM catalog_signals s, jsonb_array_elements(s.payload->'h') x
              WHERE s.kind = 'kw' AND jsonb_typeof(s.payload->'h') = 'array' GROUP BY start ORDER BY start DESC`,
        )
      : NO_ROWS,
    kind === "sm" || kind === "mg"
      ? db.execute<{ grade: string; n: number }>(
          sql`SELECT grade, count(DISTINCT row_key)::int AS n FROM catalog_signals WHERE kind = ${kind} GROUP BY grade`,
        )
      : NO_ROWS,
  ]);
}

function theaterCounts(rows: readonly { kind: string; n: number }[]) {
  return Object.fromEntries(
    rows
      .filter((r) => (THEATER_BASES as readonly string[]).includes(r.kind))
      .map((r) => [r.kind as TheaterBasis, r.n]),
  ) as Partial<Record<TheaterBasis, number>>;
}

function weeksOf(rows: readonly Record<string, unknown>[]): WeekOption[] {
  return rows
    .map((r) => ({ w: r.w, start: r.start }))
    .filter(
      (r): r is { w: string; start: string } =>
        typeof r.w === "string" &&
        r.w !== "" &&
        typeof r.start === "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(r.start),
    )
    .map((r) => ({ week: r.w, start: r.start }));
}

function gradesOf(
  rows: readonly Record<string, unknown>[],
): Partial<Record<Grade, number>> {
  return Object.fromEntries(
    rows
      .filter((r) => (GRADES as readonly string[]).includes(String(r.grade)))
      .map((r) => [r.grade as Grade, Number(r.n)]),
  ) as Partial<Record<Grade, number>>;
}

/** 剧场榜的计数、日 / 周 / 档候选读剧单表；rs 榜的计数是 meta.rsCounts */
export async function loadRankMeta(req: PickRequest): Promise<RankMeta> {
  const kind = req.rank;
  const [countRes, rsCounts, [dayRes, weekRes, gradeRes]] = await Promise.all([
    getDb().execute<{ kind: string; n: number }>(
      sql`SELECT kind, count(DISTINCT row_key)::int AS n FROM catalog_signals GROUP BY kind`,
    ),
    loadRsCounts(),
    periodQueries(kind),
  ]);
  /* 涨幅榜的数跟当前排序走：d7 还没攒够快照时它就是 0，与榜一致；另两种增量的数给空态当出口 */
  const growthSort = rsSortFor("rs_growth", req.rsSort);
  const growthCounts = {
    d1: rsCounts.growthD1,
    d7: rsCounts.growthD7,
    dp1: rsCounts.growthDp1,
    dp7: rsCounts.growthDp7,
  } as const;
  const counts: Partial<Record<RankKey, number>> = {
    rs_rr: rsCounts.all,
    rs_growth: growthCounts[growthSort as keyof typeof growthCounts] ?? 0,
    rs_cand: rsCounts.cand,
    rs_pc: rsCounts.pc,
    rs_clk: rsCounts.clk,
    rs_gsc: rsCounts.gsc,
    rs_bill: rsCounts.bill,
    rs_ledger: rsCounts.ledger,
    ...theaterCounts(countRes.rows),
  };
  const days = dayRes.rows
    .map((r) => r.d)
    .filter((d): d is string => typeof d === "string" && d !== "");
  const weeks = weeksOf(weekRes.rows);
  const dayPick = isDailyRank(kind)
    ? resolveDay(days, req.day)
    : { day: "", how: "latest" as const };
  const weekPick =
    kind === "kw"
      ? resolveWeek(weeks, req.week)
      : { start: "", how: "latest" as const };
  return {
    counts,
    growthCounts,
    days,
    weeks,
    grades: gradesOf(gradeRes.rows),
    day: dayPick.day,
    week: weekPick.start,
    dayResolution: dayPick.how,
    weekResolution: weekPick.how,
  };
}

const SIGNAL_COLUMNS = sql.raw(
  "s.kind, s.ord, s.evidence_on, s.rank, s.grade, s.note, s.payload",
);

/** 评级档位的排序位置：不在 GRADES 里的排最后 */
function gradeOrder(): SQL {
  return sql`array_position(ARRAY[${sql.join(
    GRADES.map((g) => sql`${g}`),
    sql`, `,
  )}]::text[], s.grade) NULLS LAST`;
}

type RankSelect = { from: SQL; order: SQL; countFrom: SQL };

/**
 * 日榜用 LATERAL 取所选日期那一格。payload->'h' 是 [[日期, 名次, 备注?]]，在 SQL 里取当天名次，
 * 不把整包历史搬回来再在 JS 里找。
 */
function dailySelect(meta: RankMeta, kind: TheaterBasis): RankSelect {
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

function rankOrder(kind: TheaterBasis): SQL {
  if (kind === "kw")
    return sql`ORDER BY (s.payload->>'weeks')::int DESC NULLS LAST, catalog_rows.title ASC, catalog_rows.row_key ASC`;
  if (kind === "sm" || kind === "mg")
    return sql`ORDER BY ${gradeOrder()}, catalog_rows.listed_on DESC NULLS LAST, catalog_rows.title ASC, catalog_rows.row_key ASC`;
  return sql`ORDER BY s.evidence_on DESC NULLS LAST, catalog_rows.listed_on DESC NULLS LAST, catalog_rows.title ASC, catalog_rows.row_key ASC`;
}

/**
 * 每张榜的「信号子查询」与排序。非日榜的种类先按 row_key 去重（一行在同一张榜上有两条时留一条），
 * 再与 catalog_rows join。【榜单 tab 一律含已下架的行】：那一天的榜是历史事实。
 */
function rankSelect(
  req: PickRequest,
  meta: RankMeta,
  kind: TheaterBasis,
): RankSelect {
  if (isDailyRank(kind)) return dailySelect(meta, kind);
  const weekly = kind === "kw";
  const filters: SQL[] = [
    sql`s.kind = ${kind}`,
    /* 按周起日期（h 的第 0 格）过滤，不按周标签：标签跨年重名，按标签会把两年的行混进同一张榜 */
    ...(weekly
      ? [
          sql`jsonb_typeof(s.payload->'h') = 'array' AND EXISTS (SELECT 1 FROM jsonb_array_elements(s.payload->'h') x WHERE x->>0 = ${meta.week})`,
        ]
      : []),
    ...((kind === "sm" || kind === "mg") && req.grade
      ? [sql`s.grade = ${req.grade}`]
      : []),
  ];
  const innerOrder = weekly
    ? sql`(s.payload->>'weeks')::int DESC NULLS LAST, s.ord ASC`
    : sql`s.ord ASC`;
  const where = sql.join(filters, sql` AND `);
  return {
    from: sql`FROM (
      SELECT DISTINCT ON (s.row_key) s.row_key, ${SIGNAL_COLUMNS} FROM catalog_signals s WHERE ${where}
      ORDER BY s.row_key, ${innerOrder}
    ) s JOIN catalog_rows ON catalog_rows.row_key = s.row_key`,
    countFrom: sql`FROM (SELECT DISTINCT s.row_key FROM catalog_signals s WHERE ${where}) s`,
    order: rankOrder(kind),
  };
}

function toRankRow(
  r: RawRow & SignalRaw,
  kind: TheaterBasis,
  posted: Map<string, PickRow["posted"]>,
): RankRow {
  const signal: PickSignal = {
    kind: (BASES as readonly string[]).includes(r.kind)
      ? (r.kind as Basis)
      : kind,
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
}

/** 剧场榜的一页；rs_* 榜走 loadRsRank */
export async function loadRankRows(
  req: PickRequest,
  meta: RankMeta,
): Promise<RowsPage<RankRow>> {
  if (isRsRank(req.rank)) throw new Error("ReelShort 榜走 loadRsRank");
  const kind: TheaterBasis = req.rank;
  if ((isDailyRank(kind) && !meta.day) || (kind === "kw" && !meta.week))
    return { rows: [], total: 0, hasMore: false };
  const db = getDb();
  const { from, order, countFrom } = rankSelect(req, meta, kind);
  const offset = (req.page - 1) * req.size;
  const extra = isDailyRank(kind) ? sql`, e.day_rank, e.day_note` : sql``;
  const [page, count] = await Promise.all([
    db.execute<RawRow & SignalRaw>(
      sql`SELECT ${ROW_COLUMNS_QUALIFIED}, ${SIGNAL_COLUMNS}${extra} ${from} ${order} LIMIT ${req.size} OFFSET ${offset}`,
    ),
    db.execute<{ n: number }>(sql`SELECT count(*)::int AS n ${countFrom}`),
  ]);
  const posted = await loadPostedFor(page.rows.map((r) => r.row_key));
  const total = Number(count.rows[0]?.n ?? 0);
  const rows = page.rows.map((r) => toRankRow(r, kind, posted));
  return { rows, total, hasMore: offset + rows.length < total };
}

/** 涨幅榜每种排序比的是几天前的快照（comparableOnly：d1 / dp1 看 s1，其余看 s7） */
const GROWTH_WINDOW_DAYS: Record<string, GrowthWindowDays> = {
  d1: 1,
  d7: 7,
  dp1: 1,
  dp7: 7,
};

/**
 * 涨幅榜 0 行时为什么是空的：判断在纯模块 growth-diagnosis.ts。全库可比行数与基线快照状态都是
 * 版本 meta（榜单 chip 同一份），只在空榜时调。
 */
export async function loadGrowthDiagnosis(
  req: PickRequest,
): Promise<GrowthDiagnosis> {
  const sort = rsSortFor("rs_growth", req.rsSort);
  const windowDays = GROWTH_WINDOW_DAYS[sort] ?? 7;
  const [counts, baseline] = await Promise.all([
    loadRsCounts(),
    loadGrowthBaseline(windowDays),
  ]);
  const bySort: Record<string, number> = {
    d1: counts.growthD1,
    d7: counts.growthD7,
    dp1: counts.growthDp1,
    dp7: counts.growthDp7,
  };
  return diagnoseGrowthEmpty({
    ...baseline,
    windowDays,
    filtered: req.rsLocale !== "" || req.rsBucket !== null || req.q !== "",
    comparableWithoutFilters: (bySort[sort] ?? 0) > 0,
  });
}

/** rs 榜的有效排序：涨幅榜限增量排序（别的回落到较 7 天前）；单指标榜固定；总览 / 候选清单随 rs 参数 */
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

const ONLY_OF: Partial<Record<RsRank, LoadRowsOptions["only"]>> = {
  rs_pc: "promoters",
  rs_clk: "clicks",
  rs_gsc: "gsc",
  rs_bill: "bill",
};

/**
 * ReelShort 的榜：PickRequest 到 rs 查询的映射只在这里做一次。
 * - rs_rr：全部正典行，按 rs 排序；rs_growth：comparableOnly + 前 50，不分页；rs_cand：candidatesOnly；
 * - rs_pc / clk / gsc / bill：固定排序且只留那一项 > 0 的行；
 * - rs_ledger：订单对账（合并后的订单行 + 全量合计 + 版本里账单来源的状态）。
 */
export async function loadRsRank(
  req: PickRequest,
  rank: RsRank,
): Promise<RsRankResult> {
  if (rank === "rs_ledger") {
    const [rows, totals, sources] = await Promise.all([
      loadBillRows(),
      loadBillTotals(),
      readSources(),
    ]);
    return { kind: "ledger", rows, totals, source: sources.bill };
  }
  const sort = rsSortFor(rank, req.rsSort);
  const growth = rank === "rs_growth";
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
      only: ONLY_OF[rank],
    },
  );
  return { kind: "rows", rows, total, hasMore, sort };
}
