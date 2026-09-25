/**
 * The observation radar's wording and display (plan TR-16; premise 1, D8, D10, D31).
 *
 * The frontend's copy of the backend's wording (ggwork_pick/observe/contract.py and wording.py; obs-format.test reads
 * the Python source and keeps them equal). An unobserved value reads "未观测到", never zero, and a value that breaks
 * the contract is never shown. format.ts hands obs_* evidence and the observation conditions here; the data page
 * (TR-24) shares the wording. Pure and small: no pick-board or server module, no clock (client-bundle.test).
 */
import { LINK_STATES, type PickConditions, type PickEvidence } from "./types";

// ---- wording (premise 1) --------------------------------------------------------------------------------------------

export const UNOBSERVED = "未观测到";
export const UNOBSERVED_TRENDS = "在 Google Trends 返回的数据里未观测到";
export const UNOBSERVED_GSC = "在 GSC 返回的数据里未观测到";
export const FROM_ZERO_NOTE = "基线未观测到";
/** wording.FORBIDDEN_TERMS, word for word; forbiddenIn also catches the variants wording.forbidden_in catches */
export const FORBIDDEN_TERMS = [
  "0 曝光",
  "零曝光",
  "为零",
  "没有曝光",
] as const;

const COUNTED = "(?:曝光|点击)";
const FORBIDDEN_PATTERNS: readonly RegExp[] = [
  // 0 曝光, 0 次点击; not W0 曝光 or 20 次曝光
  new RegExp(String.raw`(?<![0-9A-Za-z.])0\s*(?:次\s*)?${COUNTED}`, "g"),
  new RegExp(String.raw`零\s*(?:次\s*)?${COUNTED}`, "g"),
  /为\s*零/g,
  // 曝光为 0, 点击数为0, 曝光：0 次
  new RegExp(
    String.raw`${COUNTED}(?:数|量)?\s*(?:为|是|=|：|:)\s*0(?![0-9.])`,
    "g",
  ),
  // W−1 曝光 0, 点击 0 次; not 曝光 0.5 万 or 点击 0.8%
  new RegExp(String.raw`${COUNTED}(?:数|量)?\s*0(?![0-9.%])(?:\s*次)?`, "g"),
  new RegExp(String.raw`没有\s*(?:任何\s*)?${COUNTED}`, "g"),
];

/** The passages of text that call an unobserved count zero (premise 1); empty when the text is clean. */
export function forbiddenIn(text: string): string[] {
  return FORBIDDEN_PATTERNS.flatMap((pattern) =>
    [...text.matchAll(pattern)].map((found) => found[0]),
  );
}

type TrendState = NonNullable<PickConditions["trend_state"]>;
type GscState = NonNullable<PickConditions["gsc_state"]>;

export const TRENDS_STATE_TEXT: Readonly<
  Record<TrendState | "cooling", string>
> = {
  rising: "上升观察",
  emerging: "从零起量观察",
  cooling: "冲高回退",
};

export const GSC_STATE_TEXT: Readonly<Record<GscState, string>> = {
  surge: "曝光飙升",
  from_zero: `从零起量（${FROM_ZERO_NOTE}）`,
  high_ctr: "高点击率",
  rank_push: "排名冲顶",
  rising: "7 天上升",
  present: "在该国有两层准入的观测",
};

/** contract.LINK_LABELS: the five link_state values, then two that are shown but never asked for */
export const LINK_LABELS = [
  ...LINK_STATES,
  "global_parallel",
  "different_markets",
] as const;
export type LinkLabel = (typeof LINK_LABELS)[number];

export const LINK_LABEL_TEXT: Readonly<Record<LinkLabel, string>> = {
  both_rising: "双涨",
  trends_lead_page: "站外先行·补页",
  trends_lead_distribution: "站外先行·仅分发",
  site_only: "站内独涨",
  cooling: "退潮",
  global_parallel: "全球同向",
  different_markets: "不同市场信号",
};

/** The seven observation condition fields (D31), in UNMAPPABLE_OBS_ORDER */
export const OBS_CONDITION_FIELDS = [
  "trend_state",
  "trend_geos",
  "trend_include_first",
  "trend_include_presumed",
  "gsc_state",
  "gsc_countries",
  "link_state",
] as const;
export type ObsConditionField = (typeof OBS_CONDITION_FIELDS)[number];

/** contract.OBS_CONDITION_LABELS: the data page's words for them (unmappable_cases.json) */
export const OBS_CONDITION_LABELS: Readonly<Record<ObsConditionField, string>> =
  {
    trend_state: "Google Trends 状态（上升或从零起量观察）",
    trend_geos: "Google Trends 地区",
    trend_include_first: "接受只观察到一天的上升（首次）",
    trend_include_presumed: "接受推定对应（强证据、未人工确认）",
    gsc_state: "站内搜索（GSC）标签",
    gsc_countries: "站内搜索（GSC）国家",
    link_state: "两个通道的同国家联动",
  };

// ---- evidence (D8) --------------------------------------------------------------------------------------------------

export const EVIDENCE_KINDS = [
  "obs_trends",
  "obs_gsc",
  "obs_discovery",
] as const;
export type ObsKind = (typeof EVIDENCE_KINDS)[number];

const KIND_NAME: Readonly<Record<ObsKind, string>> = {
  obs_trends: "Google Trends",
  obs_gsc: "站内搜索（GSC）",
  obs_discovery: "发现段",
};
const KIND_UNOBSERVED: Readonly<Record<ObsKind, string>> = {
  obs_trends: UNOBSERVED_TRENDS,
  obs_gsc: UNOBSERVED_GSC,
  obs_discovery: UNOBSERVED,
};
const TREND_VALUES: ReadonlySet<unknown> = new Set(
  Object.keys(TRENDS_STATE_TEXT),
);
const ID_EVIDENCE_TEXT: ReadonlyMap<string, string> = new Map([
  ["strong", "强"],
  ["medium", "中"],
  ["weak", "弱"],
]);
/** Shown in place of a value the contract does not allow: it is never echoed, so a 0 never reads as a count */
export const OFF_CONTRACT_VALUE = "取值不符合约定，未展示";

export function isObsEvidence(
  evidence: Pick<PickEvidence, "kind">,
): evidence is PickEvidence & { kind: ObsKind } {
  return (EVIDENCE_KINDS as readonly string[]).includes(evidence.kind);
}

function isEmpty(value: PickEvidence["value"] | undefined): boolean {
  return value === null || value === undefined || value === "";
}

/** A value the kind allows, in words; null where the label already says it (Trends names its state there). */
function valueText(kind: ObsKind, evidence: PickEvidence): string | null {
  const { value } = evidence;
  if (kind === "obs_gsc")
    return typeof value === "number" && Number.isInteger(value) && value >= 1
      ? `曝光 ${value}`
      : OFF_CONTRACT_VALUE;
  if (kind === "obs_discovery")
    return typeof value === "string"
      ? `发现词「${value}」`
      : OFF_CONTRACT_VALUE;
  if (!TREND_VALUES.has(value)) return OFF_CONTRACT_VALUE;
  return evidence.label?.trim()
    ? null
    : TRENDS_STATE_TEXT[value as TrendState | "cooling"];
}

/** The kind's own unobserved phrase, unless the note already says it */
function unobservedText(
  kind: ObsKind,
  note: string | undefined,
): string | null {
  const phrase = KIND_UNOBSERVED[kind];
  return note?.includes(phrase) ? null : phrase;
}

/** Only a discovery's grade says something its label and note do not: the identity evidence */
function gradeText(kind: ObsKind, grade: string | undefined): string | null {
  const level =
    kind === "obs_discovery" ? ID_EVIDENCE_TEXT.get(grade ?? "") : undefined;
  return level ? `身份证据 ${level}` : null;
}

/**
 * One obs_* entry as the card shows it: the backend's label, the value in words (or the unobserved phrase), a
 * discovery's identity evidence, then the backend's note (rules version, as-of moment, counts, link segment).
 */
export function obsEvidenceLine(
  evidence: PickEvidence & { kind: ObsKind },
): string {
  const { kind } = evidence;
  const parts = [
    evidence.label?.trim() ? evidence.label : KIND_NAME[kind],
    isEmpty(evidence.value)
      ? unobservedText(kind, evidence.note)
      : valueText(kind, evidence),
    gradeText(kind, evidence.grade),
    evidence.note?.trim() ? evidence.note : null,
  ];
  return parts.filter((part): part is string => Boolean(part)).join(" · ");
}

// ---- conditions (D19, D31) ------------------------------------------------------------------------------------------

export const OBS_SORT = "obs";
const PLACE_TEXT: ReadonlyMap<string, string> = new Map([
  ["WW", "全球"],
  ["ALL", "全站"],
]);

function places(codes: readonly string[] | undefined): string {
  return (codes ?? []).map((code) => PLACE_TEXT.get(code) ?? code).join("、");
}

/** The observation conditions and the obs order in words, in the fields' order; empty without them */
export function obsConditionParts(c: PickConditions): string[] {
  const geos = places(c.trend_geos);
  const countries = places(c.gsc_countries);
  const parts = [
    c.trend_state ? `Google Trends ${TRENDS_STATE_TEXT[c.trend_state]}` : null,
    geos ? `Trends 地区 ${geos}` : null,
    c.trend_include_first ? OBS_CONDITION_LABELS.trend_include_first : null,
    c.trend_include_presumed
      ? OBS_CONDITION_LABELS.trend_include_presumed
      : null,
    c.gsc_state ? `站内搜索 ${GSC_STATE_TEXT[c.gsc_state]}` : null,
    countries ? `GSC 国家 ${countries}` : null,
    c.link_state ? `同国家联动 ${LINK_LABEL_TEXT[c.link_state]}` : null,
    c.sort === OBS_SORT ? "按观测状态排序" : null,
  ];
  return parts.filter((part): part is string => part !== null);
}
