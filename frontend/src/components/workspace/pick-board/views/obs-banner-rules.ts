// 工作台新建（TR-24，D10）：资料页的趋势雷达横幅，ggwork_pick/observe/status_rules.channel_banners 的 TS 双实现。
// 资料页不经 gateway，直接读 pick_obs.run_status 与当前 live 集合，按请求时刻算：最新一次运行写下的全部状态码，
// 加三个按时间算的码。stale_26h（只看 Trends）：当前 live 集合发布超过 26 小时，恰好 26 小时不算；没有 live 集合不算；
// 或者简化版趋势表（不发集合）最新采完的批次 tableThrough 早于应到日期；从没采完过时，第一个表批次 tableSince
// 已经应到（首晚就崩的情况）；两种都要 run_missed 不成立，一个表批次都没有不算。
// run_missed：Trends 从 02:30 UTC 起应到的是当天的批次（之前是前一天），最新批次的 target_date 更早就算；GSC 最新轮次
// 开始超过 4 小时就算；从没运行过的通道不算（上线前的常态，页面写「还没有运行记录」）。shadow_mode：最新一次运行发成影子。
// 每个码只出现一次，先按级别（red、warn、info）再按 STATUS_CODES 的顺序排。时间按微秒比较（obs-instants.ts）：
// JS 的 Date 只到毫秒，夹具里「多一微秒」的用例靠它区分。采集端写出本页面不认识的码时，按红色显示在已知码之后，
// 不丢：丢了就等于悄悄撤下一条红色横幅。与 Python 的对照夹具是 tests/fixtures/obs_status_cases.json。
import {
  HOUR_US,
  instantMicros,
  stampMicros,
  utcDayOf,
} from "@/core/pick/obs-instants";
import {
  OBS_STATUS_CODES,
  OBS_STATUS_LEVELS,
  isObsStatusCode,
  levelRank,
  type BannerLevel,
  type ObsChannel,
} from "@/core/pick/obs-status";

const TRENDS_STALE_AFTER_US = 26 * HOUR_US;
const GSC_MISSED_AFTER_US = 4 * HOUR_US;
const TRENDS_DUE_MINUTES = 2 * 60 + 30; // 02:30 UTC

/** The fields of a channel's latest pick_obs.run_status row that decide a banner. */
export type LatestRunInput = Readonly<{
  started_at: string;
  mode: "live" | "shadow";
  target_date: string | null;
  status_codes: readonly string[];
}>;

export type ChannelBanner = Readonly<{ code: string; level: BannerLevel }>;

type Now = string | Date;

export function trendsSetStale(
  livePublishedAt: string | null,
  now: Now,
): boolean {
  return (
    livePublishedAt !== null &&
    instantMicros(now) - stampMicros(livePublishedAt) > TRENDS_STALE_AFTER_US
  );
}

/**
 * The newest finished table batch (tableThrough) is for a target date before the one due at `now`; or none ever
 * finished although the first table batch (tableSince) was due by now. No table batch at all is not stale.
 */
export function trendsTableStale(
  tableThrough: string | null,
  now: Now,
  tableSince: string | null = null,
): boolean {
  const due = trendsDueDate(now);
  if (tableThrough !== null) return tableThrough < due;
  return tableSince !== null && tableSince <= due;
}

/** The target date whose Trends batch must exist by now: today from 02:30 UTC, yesterday before it. */
export function trendsDueDate(now: Now): string {
  const moment = instantMicros(now);
  const today = utcDayOf(moment);
  const dueAt = stampMicros(`${today}T00:00:00Z`) + TRENDS_DUE_MINUTES * 60e6;
  return moment >= dueAt ? today : utcDayOf(moment - 24 * HOUR_US);
}

export function runMissed(
  channel: ObsChannel,
  latestRun: LatestRunInput | null,
  now: Now,
): boolean {
  if (latestRun === null) return false;
  if (channel === "trends")
    return (
      latestRun.target_date !== null &&
      latestRun.target_date < trendsDueDate(now)
    );
  return (
    instantMicros(now) - stampMicros(latestRun.started_at) > GSC_MISSED_AFTER_US
  );
}

function levelOf(code: string): BannerLevel {
  return isObsStatusCode(code) ? OBS_STATUS_LEVELS[code] : "red";
}

function codeRank(code: string): number {
  const index = (OBS_STATUS_CODES as readonly string[]).indexOf(code);
  return index === -1 ? OBS_STATUS_CODES.length : index;
}

function byBannerOrder(a: string, b: string): number {
  return (
    levelRank(levelOf(a)) - levelRank(levelOf(b)) ||
    codeRank(a) - codeRank(b) ||
    a.localeCompare(b)
  );
}

/**
 * One channel's banners at `now`: the latest run's codes and the three time-based ones, each once, in banner order.
 * tableThrough is the target date of the newest finished table batch, tableSince that of the first table batch,
 * finished or not (Trends only; null or absent before the first).
 */
export function channelBanners(
  channel: ObsChannel,
  input: Readonly<{
    latestRun: LatestRunInput | null;
    livePublishedAt: string | null;
    now: Now;
    tableThrough?: string | null;
    tableSince?: string | null;
  }>,
): ChannelBanner[] {
  const { latestRun, livePublishedAt, now } = input;
  const tableThrough = input.tableThrough ?? null;
  const tableSince = input.tableSince ?? null;
  if ((tableThrough !== null || tableSince !== null) && channel !== "trends")
    throw new Error("tableThrough、tableSince 只属于 Trends");
  const missed = runMissed(channel, latestRun, now);
  const tableBehind =
    trendsTableStale(tableThrough, now, tableSince) && !missed;
  const computed: [string, boolean][] = [
    [
      "stale_26h",
      channel === "trends" &&
        (trendsSetStale(livePublishedAt, now) || tableBehind),
    ],
    ["run_missed", missed],
    ["shadow_mode", latestRun?.mode === "shadow"],
  ];
  const codes = new Set([
    ...computed.filter(([, holds]) => holds).map(([code]) => code),
    ...(latestRun?.status_codes ?? []),
  ]);
  return [...codes]
    .sort(byBannerOrder)
    .map((code) => ({ code, level: levelOf(code) }));
}
