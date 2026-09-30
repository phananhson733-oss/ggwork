/**
 * The simplified radar's table wording (simplified scope 2026-09-30, section 2): what a row's result, its fetch status
 * or stop reason, its basis and the night's outcome read as. A code this page does not know is named, not explained
 * (obs-wording.textOf). Pure: no pick-board or server module.
 */
import { UNCOVERED_REASON_TEXT, textOf } from "./obs-wording";
import type {
  BasisKind,
  TableResult,
  TrendsTableBasis,
} from "./trends-table-schema";

export const RESULT_TEXT: Readonly<Record<TableResult, string>> = {
  data: "有数据",
  no_data: "Google 未返回数据",
  not_fetched: "这晚未查到",
  pending: "还在查",
};

/** The fetch status of the unit's line (trends/source.FetchStatus), or why the night stopped before it. */
export const FETCH_STATUS_TEXT: Readonly<Record<string, string>> = {
  ok: "",
  ok_zero: "Google 返回的近 30 天曲线全是 0",
  no_data: "Google 没有返回曲线",
  rate_limited: "被限流（429）",
  blocked_redirect: "被转到验证页或同意页",
  html_body: "接口返回了网页而不是数据",
  forbidden: "被拒绝（403）",
  server_error: "Google 服务端出错",
  timeout: "请求超时",
  parse_error: "返回的格式读不了",
  ...UNCOVERED_REASON_TEXT,
  not_reached: "这晚没轮到（采集提前结束）",
  unreadable: "存下的数据读不了",
};

export function fetchStatusText(status: string | null): string {
  return status === null ? "" : textOf(FETCH_STATUS_TEXT, status);
}

export const BASIS_TEXT: Readonly<Record<BasisKind, string>> = {
  qc: "鹊娱 7 日转化率榜",
  qr: "鹊娱 7 日总收入榜",
  kd: "KalosTV 日榜",
  revenue: "ReelShort 近 30 天收入",
};

/** One basis: a board, its issue and the rank; or the revenue rank and its snapshot day. */
export function basisText(basis: TrendsTableBasis): string {
  if (basis.kind === "revenue") {
    const day = basis.board_date ? `（${basis.board_date} 快照）` : "";
    return `${BASIS_TEXT.revenue}第 ${basis.rank} 名${day}`;
  }
  const issue = basis.board_date ? `${basis.board_date} 期` : "日期不详的一期";
  return `${BASIS_TEXT[basis.kind]} ${issue}第 ${basis.rank} 名`;
}

/** The night's batch as the table reads it: it publishes nothing, so a night that ended without failing is done. */
export const NIGHT_OUTCOME_TEXT: Readonly<Record<string, string>> = {
  running: "还在采集",
  withheld: "已采完",
  published: "已采完",
  failed: "中途失败",
};

export const STOPPED_SHORT = "没有采完：采集中途停了";

/** A night still running past its deadline (the process died, nothing closed it yet) stopped short. */
export function nightOutcomeText(outcome: string, collecting: boolean): string {
  if (outcome === "running" && !collecting) return STOPPED_SHORT;
  return textOf(NIGHT_OUTCOME_TEXT, outcome);
}

/** Why the revenue fill had nothing (trends/top_dramas.Revenue.reason). */
export const REVENUE_REASON_TEXT: Readonly<Record<string, string>> = {
  not_needed: "榜单已凑满，没有用收入补",
  sqlite: "本地库没有镜像，收入读不了",
  unreadable: "镜像读不了",
  no_version: "还没有已发布的镜像版本",
};
