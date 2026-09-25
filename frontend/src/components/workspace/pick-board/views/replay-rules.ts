// 工作台新建（RealShort 没有回放）：回放视图的纯规则（P4-2，方案 2.5 第 4 条，批判 B10、B11）。
// 名单切页与全局序号、行取自哪个版本、「近似筛选」的映射、说明与卡片当时有何不同的横幅。组件里不读墙上时钟。
import { pickHref } from "@/components/workspace/pick-board/toolbar";
import { rowKeyFromIdentity } from "@/core/pick/identity";
import {
  OBS_CONDITION_FIELDS,
  OBS_CONDITION_LABELS,
} from "@/core/pick/obs-format";
import type { PickConditions } from "@/core/pick/types";
import { formatObservedAt } from "@/core/pick-board/metrics";
import {
  BASES,
  PLATFORMS,
  THEATER_BASES,
  parsePickRequest,
  type Basis,
  type PickRequest,
  type Platform,
  type TheaterBasis,
} from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import type { ReplayAnswer } from "@/server/pick-board";

import type { Banner, BannerBoard } from "./banner-rules";

// ---- 名单与分页（B10） --------------------------------------------------------------------------------------

export type ReplayEntry = Readonly<{
  /** 在整份名单里的序号，从 1 起 */
  ordinal: number;
  identity: string;
  /** 镜像里的行键；个人导入的剧、编号解不出行键的为 null */
  rowKey: string | null;
  /** 在卡片展示的前 limit 部里（按全局序号，不按在这一页的位置） */
  shown: boolean;
}>;

export type ReplayPage = Readonly<{
  entries: readonly ReplayEntry[];
  /** 这一页的名单项 */
  page: readonly ReplayEntry[];
  /** 这一页第一项、最后一项的序号；空页为 0 */
  first: number;
  last: number;
  hasMore: boolean;
  /** 这一页要从镜像取的行键（去重，名单序） */
  pageKeys: readonly string[];
  /** 整份名单的行键（去重，名单序）：「当前版本已无此行」一次查全 */
  allKeys: readonly string[];
}>;

export function replayEntries(answer: ReplayAnswer): ReplayEntry[] {
  return answer.identities.map((identity, index) => ({
    ordinal: index + 1,
    identity,
    rowKey: rowKeyFromIdentity(identity),
    shown: index < answer.limit,
  }));
}

function keysOf(entries: readonly ReplayEntry[]): string[] {
  const keys = entries.flatMap((e) => (e.rowKey === null ? [] : [e.rowKey]));
  return [...new Set(keys)];
}

/** 第 page 页（每页 size 项）：只查这一页的行，缺行一次查全名单（批判 B10） */
export function replayPage(
  answer: ReplayAnswer,
  page: number,
  size: number,
): ReplayPage {
  const entries = replayEntries(answer);
  const offset = (page - 1) * size;
  const slice = entries.slice(offset, offset + size);
  return {
    entries,
    page: slice,
    first: slice[0]?.ordinal ?? 0,
    last: slice.at(-1)?.ordinal ?? 0,
    hasMore: offset + slice.length < entries.length,
    pageKeys: keysOf(slice),
    allKeys: keysOf(entries),
  };
}

// ---- 版本（B11） --------------------------------------------------------------------------------------------

/**
 * 行取自哪个版本：结果配对的镜像版本优先，链接里的 v 与它不一致时不用（横幅说明）；
 * 结果没有配对版本（降级发布）时按链接的 v，没有就是当前版本。
 */
export function replayVersion(
  answer: ReplayAnswer,
  urlV: number | null,
): number | null {
  return answer.mirrorVersion ?? urlV;
}

/**
 * 交给 bannersFor 的版本标记：配对版本的四种情形都由 replayBanners 说；没有配对版本时只有「钉住」要换说法
 * （bannersFor 的那条说「智能体当时用的版本」，这里并没有），链接里的 v 清理了、读不到、不存在照常说。
 */
export function replayBannerBoard(
  board: BannerBoard,
  mirrorVersion: number | null,
): BannerBoard {
  if (mirrorVersion === null) return { ...board, pinned: false };
  return {
    ...board,
    pinned: false,
    pruned: false,
    ignoredV: false,
    unreadable: false,
  };
}

export type ReplayBannerInput = Readonly<{
  board: BannerBoard;
  answer: ReplayAnswer;
  /** 链接里的 v，原样 */
  urlV: number | null;
  req: PickRequest;
}>;

const FALLBACK = "名单与顺序按智能体当时的批次，行数据取自当前版本";

function when(on: boolean, banner: Banner): Banner[] {
  return on ? [banner] : [];
}

/** 结果没有配对的镜像版本：行取自链接钉住的版本或当前版本 */
function unpairedBanner({ board, req }: ReplayBannerInput): Banner {
  const shown = board.scope.versionId;
  const latest = board.current.id;
  if (shown === latest)
    return {
      key: "replay-unpaired",
      role: "status",
      text: `这份候选当时没有配对的镜像版本，行数据取自当前版本 v${shown}`,
    };
  return {
    key: "replay-unpaired",
    role: "status",
    text: `这份候选当时没有配对的镜像版本，行数据取自镜像 v${shown}（当前最新 v${latest}）`,
    link: { href: pickHref(req, { v: null }), text: "改用当前版本" },
  };
}

/** 结果配对了 vN：它读得到就按它回放，否则回退到当前版本（方案 2.5 第 4 条的原话） */
function pairedBanners(input: ReplayBannerInput, paired: number): Banner[] {
  const { board, req } = input;
  const shown = board.scope.versionId;
  const latest = board.current.id;
  return [
    ...when(board.pruned, {
      key: "replay-pruned",
      role: "status",
      text: `镜像 v${paired} 已清理：${FALLBACK} v${shown}，可能与当时不同`,
    }),
    ...when(board.unreadable, {
      key: "replay-unreadable",
      role: "alert",
      text: `镜像 v${paired} 本页读不到（授权缺失）：${FALLBACK} v${shown}，可能与当时不同；请联系管理员`,
    }),
    ...when(board.ignoredV, {
      key: "replay-ignored",
      role: "status",
      text: `这份候选配对的镜像 v${paired} 不存在或未发布：${FALLBACK} v${shown}，可能与当时不同`,
    }),
    ...when(shown === paired && paired !== latest, {
      key: "replay-pinned",
      role: "status",
      text: `按这份候选配对的镜像版本 v${paired} 回放（采集于 ${formatObservedAt(board.scope.asOf)}），当前最新 v${latest}`,
      link: {
        href: pickHref(req, { v: latest, result: "" }),
        text: "看当前版本的选剧列表",
      },
    }),
  ];
}

/** 链接里的 v 既不是配对版本、也不是正在显示的版本（翻页链接带的是显示的版本）：说一声用的是哪个 */
function mismatchBanner({ board, urlV }: ReplayBannerInput, paired: number) {
  const off =
    urlV !== null && urlV !== paired && urlV !== board.scope.versionId;
  return when(off, {
    key: "replay-v-mismatch",
    role: "status",
    text: `链接里的版本 v${String(urlV)} 与这份候选配对的版本 v${paired} 不一致，已按配对的版本回放`,
  });
}

function answerBanners({ answer }: ReplayBannerInput): Banner[] {
  const count = (n: number) => n.toLocaleString("en-US");
  return [
    ...when(answer.truncated, {
      key: "replay-truncated",
      role: "status",
      text: `名单共 ${count(answer.total)} 部，只列出前 ${count(answer.identities.length)} 部`,
    }),
    ...when(!answer.excludedReproducible, {
      key: "replay-excluded",
      role: "status",
      text: "这份候选早于排除记录：「排除已选」与「换一批」无法复现，名单里可能有卡片当时排除掉的剧",
    }),
    ...when(!answer.rankingReproducible, {
      key: "replay-ranking",
      role: "status",
      text: "规则或排序版本已变：名单按现在的规则重跑，顺序可能与卡片当时不同",
    }),
  ];
}

/** 回放的横幅：版本怎么落的（配对 / 没有配对），链接的 v 对不上，再是名单本身的三条 */
export function replayBanners(input: ReplayBannerInput): Banner[] {
  const paired = input.answer.mirrorVersion;
  const version =
    paired === null
      ? [unpairedBanner(input)]
      : [...pairedBanners(input, paired), ...mismatchBanner(input, paired)];
  return [...version, ...answerBanners(input)];
}

// ---- 近似筛选 -----------------------------------------------------------------------------------------------

export type UnmappedCondition = Readonly<{
  key: string;
  label: string;
  /** 结果里存的值（本人的结果）；布尔条件为空串 */
  value: string;
}>;

export type NearFilter = Readonly<{
  /** 选剧 tab 上最接近的筛选；拿不到结果的条件时 null */
  href: string | null;
  /** sort=rank 且依据是剧场榜：那张榜的链接 */
  rankHref: string | null;
  /** 反查不到的值与资料页没有对应筛选的条件，逐条 */
  unmapped: readonly UnmappedCondition[];
}>;

const AGENT_ONLY_LABELS: Readonly<Record<string, string>> = {
  tags: "标签",
  posted_account: "排除这个账号发过的",
  channel: "渠道",
  confirmed_eligible_only: "只要确认可发、未下架的",
  query: "搜索词（智能体搜剧名加标签，本页搜剧名或精确的行键）",
  exclude_selected: "排除个人清单里已保存的剧",
  exclude_previous: "换一批：排除上一批展示过的",
  // The seven observation fields (plan TR-16, contract TR-33): only the agent filters by them.
  ...OBS_CONDITION_LABELS,
};

function agentOnlyValue(key: string, c: PickConditions | null): string {
  if (c === null) return "";
  switch (key) {
    case "tags":
      return (c.tags ?? []).join("、");
    case "trend_geos":
      return (c.trend_geos ?? []).join("、");
    case "gsc_countries":
      return (c.gsc_countries ?? []).join("、");
    case "posted_account":
    case "channel":
    case "query":
    case "trend_state":
    case "gsc_state":
    case "link_state":
      return c[key] ?? "";
    default:
      return "";
  }
}

/** selection.unmappable_conditions's truth test: None, [], false and "" are not set */
function isSet(value: unknown): boolean {
  return Array.isArray(value) ? value.length > 0 : Boolean(value);
}

/**
 * 资料页没有对应筛选的条件，顺序与 selection.unmappable_conditions 相同（replay-rules.test 用
 * test_mirror_replay.py 的用例对照）。回放成功时用接口给的清单；409 / 410 没有接口清单，用这里算的。
 */
export function agentOnlyConditions(c: PickConditions): string[] {
  const hasChannel = c.channel !== null && c.channel !== undefined;
  const present: [string, boolean][] = [
    ["tags", (c.tags ?? []).length > 0],
    ["posted_account", Boolean(c.posted_account)],
    ["channel", hasChannel],
    [
      "confirmed_eligible_only",
      hasChannel && c.confirmed_eligible_only !== false,
    ],
    ["query", Boolean(c.query)],
    ["exclude_selected", c.exclude_selected],
    ["exclude_previous", c.exclude_previous === true],
    // UNMAPPABLE_OBS_ORDER, each when truthy (unmappable_cases.json)
    ...OBS_CONDITION_FIELDS.map((name): [string, boolean] => [
      name,
      isSet(c[name]),
    ]),
  ];
  return present.filter(([, on]) => on).map(([name]) => name);
}

type Mapped<T> = Readonly<{ value: T | null; unmapped: UnmappedCondition[] }>;

function unmapped(key: string, label: string, value: string) {
  return { value: null, unmapped: [{ key, label, value }] };
}

const lower = (text: string) => text.toLowerCase();

/** 剧场显示名 → 剧场键（按版本规则的 name 反查）；feed 在规则缺失时写的是剧场键本身（feed-map.ts:319） */
function platformOf(theater: string, rules: BoardRules): Mapped<Platform> {
  const byName = PLATFORMS.filter(
    (p) => lower(rules.platformRules[p]?.name ?? "") === lower(theater),
  );
  const byKey = PLATFORMS.find((p) => p === lower(theater));
  const found = byName.length === 1 ? byName[0] : byKey;
  return found
    ? { value: found, unmapped: [] }
    : unmapped("theater", "剧场：本页没有这个剧场", theater);
}

/** 语种代码 → 剧单语种名（按版本规则的 langLoc 反查）；feed 在对不上时写的就是语种名（feed-map.ts:317） */
function langOf(language: string, rules: BoardRules): Mapped<string> {
  const names = Object.entries(rules.langLoc)
    .filter(([, locale]) => lower(locale) === lower(language))
    .map(([name]) => name);
  if (names.length === 1 && names[0]) return { value: names[0], unmapped: [] };
  const isCode = /^[a-z]{2,3}(-[a-z0-9]{2,8})*$/i.test(language);
  if (!isCode && /^[\p{L}]{1,8}$/u.test(language))
    return { value: language, unmapped: [] };
  return unmapped("language", "语种：反查不到剧单里的语种名", language);
}

function basisOf(kind: string): Mapped<Basis> {
  const found = BASES.find((b) => b === kind);
  return found
    ? { value: found, unmapped: [] }
    : unmapped("signal_kind", "依据：本页没有这一种", kind);
}

function rankOf(c: PickConditions): Mapped<TheaterBasis> {
  if (c.sort !== "rank" || !c.signal_kind) return { value: null, unmapped: [] };
  const found = THEATER_BASES.find((b) => b === c.signal_kind);
  return found
    ? { value: found, unmapped: [] }
    : unmapped("sort", "按名次排：这类依据没有榜单", c.signal_kind);
}

function agentOnlyList(
  keys: readonly string[],
  c: PickConditions | null,
): UnmappedCondition[] {
  return keys.map((key) => ({
    key,
    label: AGENT_ONLY_LABELS[key] ?? key,
    value: agentOnlyValue(key, c),
  }));
}

/**
 * 选剧 tab 上最接近这份候选的筛选（方案 2.5 第 4 条）：剧场、语种、依据、未发过四项能映射；排序另给榜单链接。
 * 链接钉住 version，不带 result（去的是普通列表）。agentOnly 是资料页没有对应筛选的条件名。
 */
export function nearFilter(
  c: PickConditions | null,
  rules: BoardRules,
  version: number,
  agentOnly: readonly string[],
): NearFilter {
  if (c === null)
    return {
      href: null,
      rankHref: null,
      unmapped: agentOnlyList(agentOnly, null),
    };
  const platform = c.theater ? platformOf(c.theater, rules) : null;
  const lang = c.language ? langOf(c.language, rules) : null;
  const basis = c.signal_kind ? basisOf(c.signal_kind) : null;
  const rank = rankOf(c);
  const base: PickRequest = { ...parsePickRequest({}), v: version };
  const patch: Partial<PickRequest> = {
    ...(platform?.value ? { platform: platform.value } : {}),
    ...(lang?.value ? { lang: lang.value } : {}),
    ...(basis?.value ? { basis: basis.value } : {}),
    ...(c.exclude_posted ? { posted: "no" as const } : {}),
  };
  return {
    href: pickHref(base, patch),
    rankHref: rank.value
      ? pickHref(base, { tab: "rank", rank: rank.value })
      : null,
    unmapped: [
      ...(platform?.unmapped ?? []),
      ...(lang?.unmapped ?? []),
      ...(basis?.unmapped ?? []),
      ...rank.unmapped,
      ...agentOnlyList(agentOnly, c),
    ],
  };
}
