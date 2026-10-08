/**
 * 选剧工作台（ggwork-deerflow）只读数据接口的纯映射与入参校验。
 * 【不许 import 任何 server-only 模块】——tests/pick-feed.test.ts 直接加载。
 *
 * 口径与 customizations/pick-workbench/scripts/export-realshort.ts（09-21 人工快照）相同：
 * 只给「有来源信号且未标下架」的候选池，不是全部剧库；网盘链接与提取码一律不出：
 * 行查询只取 FEED_COLUMNS（没有 pan_url / pan_pw），行输出前整行过 v2 同一个 scrubAllText（方案 4.6，豁免清单同 3.3）。
 * 发布记录只给对上的运营选剧池记录的汇总（帖子数 / 待公开数 / 最近发布日 / 发过的账号），
 * 对不上 ≠ 没发过，由接收方按 matched 字段区分。
 *
 * 2026-09-23 起（feed v2 的 P1-4）只增不改：入参多了可选的 as_of / fp（校验同 v2），响应顶层多了 fingerprint（每页都有）；
 * FEED_VERSION、行与信号的形状都不变（工作台的行模型是 extra=forbid）。
 */
import { timingSafeEqual } from "node:crypto";

import { LANG_LOC } from "./catalog-import";
import { scrubAllText, scrubPanText } from "./export-v2-map";
import { parseAsOf, parseFingerprint } from "./export-v2-page";
import { PLATFORM_RULES, YOUTUBE_LABEL } from "./platforms";
import { BASIS_LABELS, PLATFORMS, type Basis, type Platform } from "./request";

export const FEED_VERSION = "pick-feed-v1";
export const FEED_MAX_LIMIT = 1000;
export const FEED_DEFAULT_LIMIT = 500;
export const FEED_SCOPE = "RealShort选剧候选池：有来源信号且未标注下架；不是全部剧库";
const MAX_CURSOR = 512;

export interface FeedQuery {
  cursor: string;
  limit: number;
  /** 钉住的时点（方案 4.7）：候选池、ReelShort 条件信号与首页 freshness 都按它算，capturedAt 就是它；null 时与今天相同 */
  asOf: Date | null;
  /** manifest 给出的 fingerprint：带了它，每一页都在读完之后核对，不同 409、有来源在写 503；null 时不核对 */
  fp: string | null;
}

/** 新参数出现两次、或者给了但不合格，都算参数错（旧的 cursor / limit 与不认识的参数照今天的规则处理） */
function optionalParam<T>(params: URLSearchParams, name: string, parse: (raw: string) => T | null): T | null | undefined {
  const all = params.getAll(name);
  if (all.length === 0) return null;
  if (all.length > 1) return undefined;
  return parse(all[0]) ?? undefined;
}

/**
 * as_of 与 fp 的校验同 v2（4.1）：as_of 精确到分钟、不晚于 now、不早于 now − 30 分钟；fp 是 64 位小写十六进制。
 * 带 fp 必须带 as_of：fingerprint 与 manifest 的 as_of 成对，不钉时点的行没法与它配成一个版本。
 * 返回 null 即参数错（路由回 400）。
 */
export function parseFeedQuery(params: URLSearchParams, now: Date): FeedQuery | null {
  const cursor = params.get("cursor") ?? "";
  const rawLimit = params.get("limit");
  if (cursor.length > MAX_CURSOR) return null;
  const asOf = optionalParam(params, "as_of", (raw) => parseAsOf(raw, now));
  const fp = optionalParam(params, "fp", parseFingerprint);
  if (asOf === undefined || fp === undefined || (fp !== null && asOf === null)) return null;
  if (rawLimit === null) return { cursor, limit: FEED_DEFAULT_LIMIT, asOf, fp };
  if (!/^\d{1,4}$/.test(rawLimit)) return null;
  const limit = Number(rawLimit);
  if (limit < 1 || limit > FEED_MAX_LIMIT) return null;
  return { cursor, limit, asOf, fp };
}

/** Bearer 比较走定长常数时间；token 未配置时调用方直接 404，不进这里 */
export function feedAuthorized(header: string | null, token: string): boolean {
  if (!header || !token) return false;
  const expected = Buffer.from(`Bearer ${token}`);
  const actual = Buffer.from(header);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

export interface FeedSourceRow {
  rowKey: string;
  platform: Platform;
  title: string;
  lang: string;
  tags: string;
  listedOn: string | null;
  offOn: string | null;
  rsFlags?: { clk: boolean; bill: boolean; gsc: boolean };
  /** ReelShort 三个条件各自的证据日（union 的 rs_clk_on / rs_bill_on / rs_gsc_on）；剧单行恒 null，不经 union 的调用方没有 */
  rsFlagDates?: RsFlagDates;
}

type RsFlagDates = { clk: string | null; bill: string | null; gsc: string | null };

/**
 * v1 行查询取的列（方案 4.6：不许 SELECT rows.*）：就是 toFeedSourceRow 读的这几列，输出的来源一目了然。
 * 没有 pan_url / pan_pw，也没有任何金额列（tests/pick-feed.test.ts 按 FORBIDDEN_NAME 钉住）。
 */
export const FEED_COLUMNS = [
  "row_key", "platform", "title", "lang", "tags", "listed_on", "off_on",
  "rs_clk", "rs_bill", "rs_gsc", "rs_clk_on", "rs_bill_on", "rs_gsc_on",
] as const;

/** unionRows() 按 FEED_COLUMNS 查回来的一行；rs_* 六列在剧单支是 false / NULL */
export interface FeedRawRow extends Record<string, unknown> {
  row_key: string;
  platform: string;
  title: string;
  lang: string;
  tags: string;
  listed_on: string | null;
  off_on: string | null;
  rs_clk?: boolean;
  rs_bill?: boolean;
  rs_gsc?: boolean;
  rs_clk_on?: string | null;
  rs_bill_on?: string | null;
  rs_gsc_on?: string | null;
}

/**
 * 查询行 → toFeedRow 的入参。与改动前的 toRowWithFlags 在这几个字段上逐一相同：剧场不认识时落到 dramabox（同 toRow），
 * 三个条件取布尔、三个证据日缺省为 null。只读这几列，查询结果里多出来的列（哪怕是网盘列）不会进行对象。
 */
export function toFeedSourceRow(r: Readonly<FeedRawRow>): FeedSourceRow {
  return {
    rowKey: r.row_key,
    platform: (PLATFORMS as readonly string[]).includes(r.platform) ? (r.platform as Platform) : "dramabox",
    title: r.title,
    lang: r.lang,
    tags: r.tags,
    listedOn: r.listed_on,
    offOn: r.off_on,
    rsFlags: { clk: Boolean(r.rs_clk), bill: Boolean(r.rs_bill), gsc: Boolean(r.rs_gsc) },
    rsFlagDates: { clk: r.rs_clk_on ?? null, bill: r.rs_bill_on ?? null, gsc: r.rs_gsc_on ?? null },
  };
}

export interface FeedSourceSignal {
  kind: Basis;
  evidenceOn: string | null;
  rank: number | null;
  grade: string;
  note: string;
}

export interface FeedPostedTag {
  sd: string;
  postCount: number;
  schedCount: number;
  lastPostOn: string | null;
}

export interface FeedPosted {
  matched: boolean;
  records: string[];
  post_count: number;
  sched_count: number;
  last_post_on: string | null;
  accounts: string[];
}

export interface FeedSignal {
  kind: string;
  label: string;
  source_ref: string;
  observed_at: string | null;
  rank: number | null;
  grade: string;
  note: string;
}

export interface FeedRow {
  source: "realshort-pick";
  source_id: string;
  language: string;
  title: string;
  theater: string;
  tags: string[];
  listed_at: string | null;
  availability: "delisted" | "unknown";
  signals: FeedSignal[];
  channel_rules: { youtube: "denied" | "unknown" };
  detail_url: string;
  posted: FeedPosted;
}

const RS_FLAG_LABELS: Record<string, string> = {
  clk: BASIS_LABELS.clk,
  bill: BASIS_LABELS.bill,
  gsc: BASIS_LABELS.gsc,
};

/** 三个条件的日期各是什么（与选剧台证据行的 BASIS_DATE_LABEL 同口径：最近过滤后出站 / 最近账单 / 采集） */
const RS_FLAG_DATE_MEANING: Record<string, string> = {
  clk: "日期是最近一次过滤后出站的 UTC 日",
  bill: "日期是最近一个有订单的账单日",
  gsc: "日期是已保存的 GSC 搜索数据的采集日（UTC）",
};

function rsFlagNote(kind: string, day: string | null): string {
  const when = day ? `${RS_FLAG_DATE_MEANING[kind] ?? "日期"}，即这类依据最近一次被看到的那天` : "这类依据的日期未知";
  return `RealShort候选条件命中；${when}；仅表示该类依据存在，不表示指标数值或统计周期`;
}

export function rowRef(rowKey: string): string {
  return "https://ggwork-deerflow.vercel.app/workspace/pick-data?tab=row&row=" + encodeURIComponent(rowKey);
}

/**
 * 截断：上限按 UTF-16 码元数，刀口只落在码点边界上。
 * - 直接 `.slice` 会把补充平面字符（emoji、CJK 扩展区）劈成半个代理对，孤立代理项到了接收方编码成 UTF-8 时
 *   会失败或变成替换字符；所以刀口前一个码元是高代理项时往前退一个，整字丢掉。
 * - 上限不按码点数：工作台两头的尺子不同，sync 的 pydantic max_length 数码点，前端 pickEvidenceSchema 的
 *   zod `.max` 数 `.length`（码元），note 原样进候选卡 evidence。码元数 ≥ 码点数，按码元封顶两边都过；
 *   按码点截到 1000 会放出最多约 2000 个码元，前端一条就让整张候选列表解析失败
 */
export function clipText(text: string, max: number): string {
  if (text.length <= max) return text;
  const last = text.charCodeAt(max - 1);
  const splitsPair = last >= 0xd800 && last <= 0xdbff;
  return text.slice(0, splitsPair ? max - 1 : max);
}

/**
 * 截断已清洗的文本，再清洗截出来的那段，直到既不超长、也不再命中网盘清洗（不设轮数上限，循环到干净为止）。
 * - 截断能新造出命中：两条正则的右边界挡住了 115.comx、9 位的码，刀口一旦切掉尾巴，剩下的前缀就命中了；
 *   工作台 v1 闸门见到命中就整批拒收。占位符（9 个码元）可能比 max 长，所以清洗之后还要再截。
 * - 【必然结束】scrubPanText 有命中时整串换成占位符；占位符与它截下来的任何前缀里都没有 MAYBE_PAN 认的字符，
 *   所以至多再清一轮就不再命中。
 */
export function clipScrubbed(text: string, max: number): string {
  let out = clipText(text, max);
  for (let scrubbed = scrubPanText(out); scrubbed.hits > 0; scrubbed = scrubPanText(out)) out = clipText(scrubbed.text, max);
  return out;
}

/**
 * 剧单 tags 列是运营手填的一串；按常见分隔符切开、去重、截到 20 个。
 * ReelShort 行的 tags 是 SQL 用「 · 」拼的一串（queries-reelshort.ts，选剧台页面直接显示它），
 * 所以两侧带空白的间隔号也是分隔符；不带空格的「A·B」是一个标签，不切
 */
export function splitTags(raw: string): string[] {
  const seen = new Set<string>();
  for (const part of raw.split(/[,，、/|;；\n]+|\s+·\s+/)) {
    const tag = clipScrubbed(part.trim(), 100);
    if (tag) seen.add(tag);
    if (seen.size >= 20) break;
  }
  return [...seen];
}

/**
 * 日期列可能是 YYYY-MM-DD 或带时间；只认前 10 位是日历上真有的那天，其余当未知。
 * 只看 Date.parse 是不是 NaN 不够：V8 会把 2026-02-30 顺延成 3 月 2 日照常返回，所以要求解析出的 UTC 日原样等于输入
 */
export function feedDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const day = String(value).slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const ms = Date.parse(day + "T00:00:00Z");
  // 公元 0 年 JS 能表示，工作台 Python 的 date 从 1 年起，接收方会拒整批
  return day >= "0001-01-01" && !Number.isNaN(ms) && new Date(ms).toISOString().slice(0, 10) === day ? day : null;
}

export function summarizePosted(tags: FeedPostedTag[], accountsBySd: Map<string, string[]>): FeedPosted {
  const accounts = new Set<string>();
  let last: string | null = null;
  for (const tag of tags) {
    for (const account of accountsBySd.get(tag.sd) ?? []) if (account.trim()) accounts.add(account.trim());
    const day = feedDate(tag.lastPostOn);
    if (day && (!last || day > last)) last = day;
  }
  return {
    matched: tags.length > 0,
    records: tags.map((t) => t.sd),
    post_count: tags.reduce((n, t) => n + t.postCount, 0),
    sched_count: tags.reduce((n, t) => n + t.schedCount, 0),
    last_post_on: last,
    accounts: [...accounts].sort(),
  };
}

/**
 * 一行候选 → v1 行。输出前整行过 scrubAllText（与 v2 同一个清洗，方案 4.6）：source_id、detail_url、listed_at 在 3.3 的豁免清单里、
 * signals[*].source_ref / observed_at 与 posted.last_post_on 按 SCRUB_EXEMPT_PATHS 的路径点名，原样保留；
 * 其余文本叶子（剧名、标签、备注、账号……）都清洗。
 * 【先清洗、后截断】note 按完整原文清洗之后才截到 1000 码元：先截会把网盘链接切在域名中间，正则就认不出来了；
 * 截断本身也会造出新命中，所以截完再清洗一遍（clipScrubbed），标签同理；
 * tags 先对原串清洗再切分：先按「/」切会把分享路径的每一段留成一个标签。只改值、不改形状。
 */
export function toFeedRow(
  row: FeedSourceRow,
  signals: FeedSourceSignal[],
  posted: FeedPostedTag[],
  accountsBySd: Map<string, string[]>,
): FeedRow {
  const ref = rowRef(row.rowKey);
  const rule = PLATFORM_RULES[row.platform];
  const out: FeedSignal[] = signals.map((s) => ({
    kind: s.kind,
    label: BASIS_LABELS[s.kind] ?? s.kind,
    source_ref: ref,
    observed_at: feedDate(s.evidenceOn),
    rank: s.rank,
    grade: s.grade,
    note: s.note,
  }));
  for (const [kind, present] of Object.entries(row.rsFlags ?? {})) {
    if (!present) continue;
    const day = feedDate(row.rsFlagDates?.[kind as keyof RsFlagDates]);
    out.push({
      kind,
      label: RS_FLAG_LABELS[kind] ?? kind,
      source_ref: ref,
      observed_at: day,
      rank: null,
      grade: "",
      note: rsFlagNote(kind, day),
    });
  }
  const draft: FeedRow = {
    source: "realshort-pick",
    source_id: Buffer.from(row.rowKey).toString("base64url"),
    language: LANG_LOC[row.lang] ?? (row.lang || "und"),
    title: row.title,
    theater: rule?.name ?? row.platform,
    tags: splitTags(scrubPanText(row.tags ?? "").text),
    listed_at: feedDate(row.listedOn),
    availability: row.offOn ? "delisted" : "unknown",
    signals: out.slice(0, 50),
    channel_rules: { youtube: rule?.yt === "no" ? "denied" : "unknown" },
    detail_url: ref,
    posted: summarizePosted(posted, accountsBySd),
  };
  const clean = scrubAllText(draft).value;
  return { ...clean, signals: clean.signals.map((s) => ({ ...s, note: clipScrubbed(s.note, 1000) })) };
}

/**
 * 按 row_key 游标切一页：调用方按 row_key 升序多取一行（limit + 1），这里只返回前 limit 行。
 * 多出来的那行只用来判断后面还有没有，有才给 nextCursor = 本页最后一行的 rowKey（下一页从 row_key > 它 取起）；
 * 恰好取满 limit 行时不给游标，否则接收方要多拉一页空的
 */
export function cutFeedPage<T extends { rowKey: string }>(
  fetched: readonly T[],
  limit: number,
): { rows: T[]; nextCursor: string | null } {
  const rows = fetched.slice(0, limit);
  return { rows, nextCursor: fetched.length > limit ? (rows.at(-1)?.rowKey ?? null) : null };
}

/** 首页才有的三项：候选池总数、数据新鲜度、规则 Markdown；后续页都是 null */
export interface FeedPageMeta {
  total: number;
  freshness: Record<string, string | number | null>;
  rules: string;
}

export interface FeedPage {
  version: string;
  capturedAt: string;
  scope: string;
  total: number | null;
  sourceRevision: string | null;
  freshness: Record<string, string | number | null> | null;
  rules: string | null;
  rows: FeedRow[];
  nextCursor: string | null;
  /**
   * 读完本页之后重算的来源 fingerprint（方案 4.3、4.7），每一页都有、不只是首页。带了 fp 且核对通过时就等于它，
   * 工作台据此核对每页都与 manifest 同源；不带 fp 时只是此刻的值，不拦截。这是 v1 唯一新增的顶层字段。
   */
  fingerprint: string;
}

/** 组一页响应：首页带 meta，后续页 total / freshness / rules 为 null；fingerprint 每页都带 */
export function feedPage(parts: {
  capturedAt: string;
  meta: FeedPageMeta | null;
  sourceRevision: string | null;
  rows: FeedRow[];
  nextCursor: string | null;
  fingerprint: string;
}): FeedPage {
  return {
    version: FEED_VERSION,
    capturedAt: parts.capturedAt,
    scope: FEED_SCOPE,
    total: parts.meta?.total ?? null,
    sourceRevision: parts.sourceRevision,
    freshness: parts.meta?.freshness ?? null,
    rules: parts.meta?.rules ?? null,
    rows: parts.rows,
    nextCursor: parts.nextCursor,
    fingerprint: parts.fingerprint,
  };
}

/** 规则知识：剧场规则表与信号种类说明，接收方当 Markdown 知识批次导入 */
export function feedRulesMarkdown(capturedAt: string): string {
  const theaters = Object.values(PLATFORM_RULES)
    .map((r) =>
      [
        `## ${r.name}`,
        `来源：${r.doc}`,
        `规则核对日期：${r.updated}`,
        `YouTube：${YOUTUBE_LABEL[r.yt]}；${r.ytNote}`,
        `报备：${r.report}`,
        `结算：${r.back}`,
        `必带标签：${r.tag}`,
        `解禁通道：${r.unban}`,
        `素材：${r.material}`,
        `榜单与评级信号：${r.signals}`,
      ].join("\n"),
    )
    .join("\n\n");
  const kinds = Object.entries(BASIS_LABELS)
    .map(([k, label]) => `- ${k}：${label}`)
    .join("\n");
  return [
    "# RealShort 选剧规则与口径",
    `采集时间：${capturedAt}`,
    `范围：${FEED_SCOPE}。个人已选仅指工作台保存的记录；发布记录来自运营飞书选剧池归一，对不上的帖子不计入，所以「未对上发布记录」不等于「从未发布」。渠道规则有附加条件，不能仅据候选收录就认定允许发布。`,
    "## 信号种类",
    kinds,
    theaters,
  ].join("\n\n");
}
