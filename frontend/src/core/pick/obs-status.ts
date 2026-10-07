/**
 * The observation radar's status codes, banner levels and banner texts (plan
 * TR-25, TR-24; D10; contract section 13). The frontend's copy of
 * ggwork_pick/observe/status_rules.STATUS_LEVELS and STATUS_TEXT:
 * obs-status.test pins it to tests/fixtures/obs_status_cases.json, which
 * Python's test_obs_status_rules reads too.
 *
 * Pure and small, no clock: the imports tab (a client component) words the
 * gateway's banners with it, and the data page's obs-banner-rules.ts words
 * the banners it computes. A code a newer gateway sends and this copy does
 * not know is shown at the gateway's level with a generic text, never
 * dropped: dropping it would silently clear a red banner.
 */

export const OBS_STATUS_CODES = [
  "stale_26h",
  "not_published_low_coverage",
  "extinguished_today",
  "disabled_7d",
  "canary_terminated",
  "usertype_changed",
  "all_zero_jump",
  "legacy_unmapped_2pct",
  "legacy_snapshot_missing",
  "gsc_gap_exceeded",
  "gsc_unverifiable",
  "run_missed",
  "shadow_mode",
  "db_size_cap",
  "parse_error",
] as const;
export type ObsStatusCode = (typeof OBS_STATUS_CODES)[number];

export const BANNER_LEVELS = ["red", "warn", "info"] as const;
export type BannerLevel = (typeof BANNER_LEVELS)[number];

export const OBS_CHANNELS = ["trends", "gsc"] as const;
export type ObsChannel = (typeof OBS_CHANNELS)[number];

export const OBS_CHANNEL_LABELS: Readonly<Record<ObsChannel, string>> = {
  trends: "Google Trends",
  gsc: "GSC（站内搜索表现）",
};

export const OBS_STATUS_LEVELS: Readonly<Record<ObsStatusCode, BannerLevel>> = {
  stale_26h: "red",
  not_published_low_coverage: "red",
  extinguished_today: "red",
  disabled_7d: "red",
  canary_terminated: "red",
  usertype_changed: "warn",
  all_zero_jump: "warn",
  legacy_unmapped_2pct: "warn",
  legacy_snapshot_missing: "red",
  gsc_gap_exceeded: "warn",
  gsc_unverifiable: "warn",
  run_missed: "red",
  shadow_mode: "info",
  db_size_cap: "red",
  parse_error: "red",
};

export const OBS_STATUS_TEXT: Readonly<Record<ObsStatusCode, string>> = {
  stale_26h:
    "Trends 数据过期：到 02:30 UTC 还没有采完当天的趋势表批次（或当前生效的集合已超过 26 小时），表里是更早的结果",
  not_published_low_coverage:
    "A 档覆盖率低于 80%，本批没有发布，上一个集合继续生效",
  extinguished_today:
    "今天的直连采集已熄火（限流、验证页或同意墙），剩余单元未采",
  disabled_7d: "7 天内熄火 3 次，直连已停用，人工重置后才恢复",
  canary_terminated: "采集验证已停止，请检查恢复记录后再运行",
  usertype_changed: "Trends 返回的 userType 变了，会话可能被标成自动访问",
  all_zero_jump: "全零序列的比例比前一天明显升高",
  legacy_unmapped_2pct:
    "未映射的旧页点击超过旧页总点击的 2%，需要 RealShort 重跑旧页解析导出",
  legacy_snapshot_missing: "没有可用的旧页解析快照，GSC 集合没有发布",
  gsc_gap_exceeded: "GSC 明细与全站总量的缺口超过门槛，本轮只出描述性标签",
  gsc_unverifiable: "GSC 全站总量取不到，覆盖无法核对，本轮只出描述性标签",
  run_missed:
    "采集没有按时运行：02:30 UTC 仍没有当天的 Trends 批次，或 GSC 超过 4 小时没有新轮次",
  shadow_mode: "发布开关关着：新集合只发成影子，智能体与联动不使用",
  db_size_cap: "数据库容量达到上限，集合没有发布",
  parse_error: "Trends 线上合同检查解析失败，接口可能改版，没有写入数值",
};

export function isObsStatusCode(code: string): code is ObsStatusCode {
  return (OBS_STATUS_CODES as readonly string[]).includes(code);
}

/** The banner text of a code; a code this copy does not know is named, not explained. */
export function obsBannerText(code: string): string {
  return isObsStatusCode(code)
    ? OBS_STATUS_TEXT[code]
    : `状态码 ${code}：这个页面版本还没有它的说明`;
}

/** Banner order across the page: red, warn, info. */
export function levelRank(level: BannerLevel): number {
  return BANNER_LEVELS.indexOf(level);
}
