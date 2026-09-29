/**
 * The data page's radar wording (plan TR-24; design 2.2, 4.7, 5.6, 5.8, 6.1;
 * premises 1 and 2). The phrases the backend owns are copied from
 * ggwork_pick/observe/wording.py and contract.py (obs-wording.test reads the
 * Python source and keeps them equal); the rest are the page's own names for
 * the contract's enums, and obs-wording.test checks every one of them against
 * the forbidden words. An unobserved count reads "未观测到", never zero; an
 * admission text never claims completeness, verification or independence.
 */
import { GSC_STATE_TEXT, UNOBSERVED } from "./obs-format";
import type {
  DiscoveryRoute,
  ObsLabelHit,
  ObsPasteRow,
  DISCOVERY_MATCHES,
  GSC_FLAGS,
  RUN_OUTCOMES,
  SITE_ADMISSIONS,
  TRENDS_FLAGS,
  TRENDS_ROW_STATES,
  UNATTRIBUTED_KINDS,
  UNCOVERED_REASONS,
} from "./obs-rows";

type TextOf<T extends readonly string[]> = Readonly<Record<T[number], string>>;

// ---- the backend's (wording.py) -------------------------------------------------------------------------------------

export const SMALL_BASE_SURGE = "小基数曝光上升";
export const FROM_ZERO_UNCHECKED = "新出现，基线未核对";
export const NOT_JUDGED_AMBIGUOUS = "歧义，不判定（设计如此）";
export const PRESUMED_CORRESPONDENCE = "推定对应";
export const DATA_SOURCE_TRENDS = "Data source: Google Trends";
export const LINK_UNTIMELY = "时效不符";
export const ADMISSION_AGREED = "两份下界一致（准入）";
export const CONFIRMATION_TEXT: Readonly<Record<string, string>> = {
  confirmed: "已确认（相邻两天都成立）",
  first: "首次（只观察到一天）",
};
export const LINK_ACTION_TEXT: Readonly<Record<string, string>> = {
  both_rising: "进发布清单；生成「可粘贴」行",
  trends_lead_page: "写博客、补内链、提交索引、进切条候选",
  trends_lead_distribution: "进 YouTube 等渠道切条候选",
  site_only: "先核对同名与 URL 迁移；自动进 B 档",
  cooling: "只拦截该国的「加码」建议",
};

// ---- the page's own -------------------------------------------------------------------------------------------------

/** G2 (2026-09-29) chose b_only: the plan's words for the Trends tab (TR-24, section 8.1). */
export const TRENDS_B_ONLY = "逐剧趋势未上线，发现队列已启用";
export const SHADOW_MARK = "影子";
export const WINDOW_COMPLETE = "完整";
export const WINDOW_PROVISIONAL = "暂定";

export const TRENDS_ROW_STATE_TEXT: TextOf<typeof TRENDS_ROW_STATES> = {
  rising: "上升观察",
  emerging: "从零起量观察",
  cooling: "冲高回退",
  flat: "平稳",
  sparse: "稀疏",
  insufficient_window: "窗口不足，不判定",
  ambiguous: NOT_JUDGED_AMBIGUOUS,
  failed: "采集失败，不判定",
};

export const TRENDS_FLAG_TEXT: TextOf<typeof TRENDS_FLAGS> = {
  shared_title: "同名剧共享搜索词",
  unstable: "不稳定",
  control_unavailable: "对照词不可用",
};

export const GSC_FLAG_TEXT: TextOf<typeof GSC_FLAGS> = {
  migration_suspect: "疑似 URL 迁移",
  mapping_changed: "页面映射有变",
  gap_exceeded: "全站缺口超限",
  unverifiable: "全站无法核对",
  detail_gap: "逐剧两份下界不一致",
  stale_slice: "用到陈旧切片",
  short_history: "历史不足",
};

export const UNCOVERED_REASON_TEXT: TextOf<typeof UNCOVERED_REASONS> = {
  truncated: "预算截断",
  skipped_breaker: "熔断跳过",
  deadline: "到截止时间未取",
};

export const UNATTRIBUTED_TEXT: TextOf<typeof UNATTRIBUTED_KINDS> = {
  legacy_unmapped: "旧页未收录",
  delisted: "目标已下架",
  noncanonical: "非正典",
  locale_mismatch: "locale 不符",
  site_level: "站点级",
  editorial: "编辑页",
  offsite: "站外",
  blog: "已转博客",
};

export const SITE_ADMISSION_TEXT: TextOf<typeof SITE_ADMISSIONS> = {
  usable: "可用（缺口不超过 τ）",
  gap_exceeded: "缺口超过 τ，只出描述性标签",
  unverifiable: "两种全站总量都没拿到，无法核对，只出描述性标签",
};

export const DISCOVERY_ROUTE_TEXT: Readonly<Record<DiscoveryRoute, string>> = {
  queue: "发现队列（池内唯一匹配）",
  display_only: "只展示（池外命中，等人工找片源）",
  a_tier: "A 档（14 天）",
};

export const DISCOVERY_MATCH_TEXT: TextOf<typeof DISCOVERY_MATCHES> = {
  unique: "唯一匹配",
  multiple: "多个匹配",
  out_of_pool: "池外",
};

export const RUN_OUTCOME_TEXT: TextOf<typeof RUN_OUTCOMES> = {
  running: "运行中",
  published: "已发布集合",
  withheld: "未发布（扣下）",
  failed: "失败",
};

export const WINDOW_TEXT: Readonly<Record<string, string>> = {
  H: "小时级",
  D: "日级",
  "24h": "24 小时",
  "7d": "7 天",
};

export const METRIC_TEXT: Readonly<Record<string, string>> = {
  impressions: "曝光",
  clicks: "点击",
};

/** A label's raw count keys, as the design writes the windows. */
const COUNT_KEY_TEXT: Readonly<Record<string, string>> = {
  w0: "W0",
  w_minus_1: "W−1",
};

/** A count as shown: a missing one is "未观测到", never 0 (premise 1). */
export function countText(value: number | null | undefined): string {
  return value === null || value === undefined ? UNOBSERVED : String(value);
}

/** A label's raw counts, "W0 325 · W−1 未观测到", in stored order. */
export function labelCountsText(
  counts: Readonly<Record<string, number | null>>,
): string {
  return Object.entries(counts)
    .map(([key, value]) => `${COUNT_KEY_TEXT[key] ?? key} ${countText(value)}`)
    .join(" · ");
}

/** A text for a code the page does not know yet: the code itself, said to be unknown. */
export function unknownCodeText(code: string): string {
  return `${code}（这个页面版本还没有它的说明）`;
}

export function textOf(
  table: Readonly<Record<string, string>>,
  code: string,
): string {
  return table[code] ?? unknownCodeText(code);
}

/**
 * A label hit's title (design 5.8). A surge that is descriptive because its base is small is shown only as a
 * small-base rise, and a from-zero hit whose base was not checked as new with its base unchecked; rules.py writes
 * those very phrases into the condition, so the condition says which, and a hit descriptive for any other reason
 * (admission, a dropped flag) keeps its name.
 */
export function gscLabelTitle(
  hit: Pick<ObsLabelHit, "label" | "formal" | "condition">,
): string {
  if (
    hit.label === "surge" &&
    !hit.formal &&
    hit.condition.includes(SMALL_BASE_SURGE)
  )
    return SMALL_BASE_SURGE;
  if (hit.label === "from_zero" && hit.condition.includes(FROM_ZERO_UNCHECKED))
    return FROM_ZERO_UNCHECKED;
  return textOf(GSC_STATE_TEXT, hit.label);
}

/** A cell of the paste row on one line: a tab or line break inside a field would split its columns or rows. */
function oneLine(text: string): string {
  return text.replace(/[\t\r\n]+/g, " ");
}

/**
 * The editorial sheet's seven columns (design 2.2; RS:src/lib/editorial-sheet.ts:93-100), tab-separated for one
 * paste: title, URL, impressions, clicks, query, date, note. Unobserved counts read "未观测到"; the note names the
 * window the counts are from. Tabs and line breaks inside a field become spaces; the stored text is not changed.
 */
export function pasteRowText(row: ObsPasteRow): string {
  const note = [row.note, `窗口 ${WINDOW_TEXT[row.window_kind]}`]
    .filter(Boolean)
    .join("；");
  return [
    row.title,
    row.url,
    countText(row.impressions),
    countText(row.clicks),
    row.top_query ?? "",
    row.verified_on,
    note,
  ]
    .map(oneLine)
    .join("\t");
}
