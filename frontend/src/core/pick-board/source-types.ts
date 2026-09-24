// PORTED_FROM: realshort@816ca2e src/lib/observe/source-types.ts
// 本地改动：sourceStatus / catalogImportStatus / sourceRowStatus 的 now 改成必填（原来默认取墙上时钟）。
// 本页的「现在」是版本的 as_of，漏传时要在编译期报错，而不是静默拿墙上时钟判过期。
/**
 * observe_sources.source 的全部取值，顺序即页底「采集来源状态」的行序。
 * 前四个是 ReelShort 侧的采集；pick_catalog 是剧单导入（pnpm catalog-import）的忙标记（feed v2 方案 4.3）。
 * 【加一个值要三处一起改】：这里、SOURCE_LABELS（类型会逼着改）、scripts/sql 里 observe_sources 的 CHECK 约束
 * （最近一次是 observe-source-pick-catalog.sql，tests/pick-sources.test.ts 逐项比对）。
 */
export const OBSERVE_SOURCES = [
  "catalog",
  "snapshot",
  "bill",
  "gsc",
  "pick_catalog",
] as const;
export type ObserveSource = (typeof OBSERVE_SOURCES)[number];

/** 页底「采集来源状态」每行的名字。Record 的类型保证 ObserveSource 的每个值都有一行 */
export const SOURCE_LABELS: Record<ObserveSource, string> = {
  catalog: "ReelShort 片库同步（列表 + 详情）",
  snapshot: "每日快照（drama_observations）",
  bill: "分成账单（bill/app-estimate）",
  gsc: "GSC 搜索（Search Console API）",
  pick_catalog: "剧单导入（pick_catalog）",
};

/**
 * running 超过这么久视为僵死（进程被杀、机器断电，没来得及标 failed）。feed v2 导出据此不再回 503、
 * 只在 manifest 里记告警（方案 4.3）；页底的剧单导入一行据此改说「失败或中断」。两边必须是同一个数。
 */
export const STALE_RUNNING_MS = 45 * 60_000;

export interface SourceAttempt {
  id: string;
  at: Date;
}

/** 只保存采集口径与计数，不保存凭据、原始响应、推广链接。 */
export interface SourceDetails {
  startDate?: string;
  endDate?: string;
  timezone?: string;
  dataState?: string;
  rows?: number;
  expectedRows?: number;
  unresolvedPages?: number;
  unmatchedQueries?: number;
  pageRows?: number;
  queryRows?: number;
  truncated?: boolean;
  partial?: boolean;
  scope?: string;
  ratio?: number | null;
  billPeriod?: string | null;
  termsFetchedAt?: string | null;
}

export interface SourceState {
  source: ObserveSource;
  status: "running" | "success" | "failed";
  attemptedAt: string;
  completedAt: string | null;
  details: SourceDetails;
}

export function sourceStatus(
  state: SourceState | undefined,
  now: Date,
): string {
  if (!state?.completedAt)
    return state?.status === "failed"
      ? "采集失败，尚无完整批次"
      : "尚无完整采集记录，历史覆盖未知";
  if (state.status !== "success") return "最近采集未完成，存量数据可能不完整";
  if (state.details.partial || state.details.truncated)
    return "部分数据，覆盖不完整";
  if (now.getTime() - new Date(state.completedAt).getTime() > 36 * 3600_000)
    return "采集记录已超过 36 小时，可能过期";
  return "最近采集完成";
}

/** 开始于 45 分钟之前的 running；开始时间读不出来的 running 按「正在写」算（与导出一致） */
function isStaleRunning(state: SourceState, now: Date): boolean {
  return (
    state.status === "running" &&
    Date.parse(state.attemptedAt) <= now.getTime() - STALE_RUNNING_MS
  );
}

function startedAt(state: SourceState): string {
  const at = new Date(state.attemptedAt);
  return Number.isNaN(at.getTime())
    ? "开始时间未知"
    : `开始于 ${at.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/**
 * 剧单导入（pick_catalog）的状态文案（方案 4.3、14 export-2）。行、信号、发布记录分三次提交，
 * 失败或中断时剧单可能只写了一半。这里只做可见性、不拦截任何读取：导入是整表替换，给不出
 * 「上一份完整的剧单」。恢复方式永远是整次重跑导入，不手工改 observe_sources。
 */
export function catalogImportStatus(
  state: SourceState | undefined,
  now: Date,
): string {
  if (!state) return "尚无带标记的导入记录";
  if (state.status === "failed" || isStaleRunning(state, now))
    return `剧单导入失败或中断（${startedAt(state)}），剧单可能只写了一半，请整次重跑 pnpm catalog-import`;
  if (state.status === "running")
    return `正在导入（${startedAt(state)}），写完之前剧单可能新旧混合`;
  return sourceStatus(state, now);
}

/** 页底每行的状态：剧单导入有自己的文案，ReelShort 的四个来源照旧 */
export function sourceRowStatus(
  source: ObserveSource,
  state: SourceState | undefined,
  now: Date,
): string {
  return source === "pick_catalog"
    ? catalogImportStatus(state, now)
    : sourceStatus(state, now);
}
