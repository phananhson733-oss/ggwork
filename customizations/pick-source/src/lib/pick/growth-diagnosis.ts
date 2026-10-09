/**
 * 涨幅榜 0 行时为什么是空的（2026-09-14，问答审计 P0-1）。纯函数：输入是查询层取回的几件事实，不读时钟、不查库，
 * tests/pick-growth-diagnosis.test.ts 直接跑。页面空态（pick/page.tsx 的 GrowthEmpty）与问答 list_rank（lib/ask/tools.ts）共用这一个判断：
 * 两处各写一份的话，页面说「历史不足」、问答说「筛空了」，而两边各自看都对。
 *
 * 可比条件是「当前行已校验 + 基线那天的快照已校验 + 度量非空」（observe/queries.ts 的 comparableOnly），原因按这个顺序判：
 * - 筛了语种 / 上线分桶 / 搜索，而不加筛选时全库按这个排序有可比行 → filters_empty；
 * - 保留期（90 天）内一个已校验快照都没有 → no_verified_snapshot；
 * - 基线日早于保留期内第一个已校验快照日 → baseline_before_first_verified_snapshot。只有这时才给「最早可能有数的日子」
 *   = 第一个已校验日 + 窗口天数（基线日 = 今天 − 窗口天数早于它，所以它一定晚于今天）；
 * - 基线那天没有快照 / 只有未校验的 → baseline_snapshot_missing / baseline_snapshot_unverified。不给恢复日期：断档之后下一个可用的基线日
 *   要看全部快照日期，这几件事实推不出来（gpt-6-astra 评审给的反例：「基线日全无效」与「中间断档」两种情况这几件事实完全一样，下一个候选日却不同）；
 * - 基线快照在、仍然 0 行 → no_comparable_rows，原因未知。
 * 「最早可能有数」只按快照日期推，不保证那天当前行也已校验，更不保证筛选下有行。
 */
export type GrowthEmptyReason =
  | "filters_empty"
  | "no_verified_snapshot"
  | "baseline_before_first_verified_snapshot"
  | "baseline_snapshot_missing"
  | "baseline_snapshot_unverified"
  | "no_comparable_rows";

/** 基线那天的快照：一行都没有 / 有但全部未校验 / 至少一行已校验 */
export type BaselineSnapshot = "none" | "unverified_only" | "verified";

export interface GrowthFacts {
  /** 这种排序比的是几天前的快照：较昨日 1，较 7 天前与推广人数 7 天变化 7 */
  windowDays: number;
  /** 基线日（UTC，YYYY-MM-DD）= 今天 − windowDays，与 comparableOnly 用的是同一个日子 */
  baselineDay: string;
  baselineSnapshot: BaselineSnapshot;
  /** 保留期内最早一个有已校验快照的日子；一个都没有是 null */
  earliestVerifiedOn: string | null;
  /** 请求带了语种 / 上线分桶 / 搜索 */
  filtered: boolean;
  /** 不加筛选、按这个排序，全库有没有可比行（与榜单 chip 同一条 loadRsCounts） */
  comparableWithoutFilters: boolean;
}

export interface GrowthDiagnosis extends GrowthFacts {
  reason: GrowthEmptyReason;
  /** 只在 baseline_before_first_verified_snapshot 时给：earliestVerifiedOn + windowDays */
  earliestPossibleOn: string | null;
}

/** YYYY-MM-DD 往后推 n 个 UTC 日；不是这个形状回 null */
export function addUtcDays(day: string, n: number): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const t = Date.parse(`${day}T00:00:00Z`);
  return Number.isNaN(t) ? null : new Date(t + n * 86_400_000).toISOString().slice(0, 10);
}

function reasonOf(f: GrowthFacts): GrowthEmptyReason {
  if (f.filtered && f.comparableWithoutFilters) return "filters_empty";
  if (f.earliestVerifiedOn === null) return "no_verified_snapshot";
  if (f.baselineDay < f.earliestVerifiedOn) return "baseline_before_first_verified_snapshot";
  if (f.baselineSnapshot === "none") return "baseline_snapshot_missing";
  if (f.baselineSnapshot === "unverified_only") return "baseline_snapshot_unverified";
  return "no_comparable_rows";
}

export function diagnoseGrowthEmpty(f: GrowthFacts): GrowthDiagnosis {
  const reason = reasonOf(f);
  const earliestPossibleOn = reason === "baseline_before_first_verified_snapshot" && f.earliestVerifiedOn !== null ? addUtcDays(f.earliestVerifiedOn, f.windowDays) : null;
  return { ...f, reason, earliestPossibleOn };
}
