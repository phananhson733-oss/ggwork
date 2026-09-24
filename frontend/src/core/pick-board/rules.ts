/**
 * 版本规则：meta.rules（P2 镜像按版本存的 RealShort buildRulesMeta()）校验并归一成页面要用的形状。
 * 工作台新建，RealShort 没有这个文件；类型由 platforms.ts、glossary.ts 拼成，字段与 gp/mirror/contracts.py 的 Rules 一一对应。
 *
 * 【校验和 Python 一样宽】：P2 写入时对规则放得很宽（每个字段是任意标量，youtubeLabels 的键可以缺），
 * 这里若收得更窄，一个 P2 已经接受并发布的版本会让所有数据 tab 进错误页。所以只拒 Python 也拒的形状，
 * 到这里再归一：yt 不在枚举里记作 null（页面显示「规则未知」），标签缺失用静态值兜底，链接不是字符串就置空。
 * 校验失败抛 BoardRulesInvalid（只带字段路径，不带值），由服务端接线映射成 MirrorMisconfigured。
 *
 * 链接：站内的 `/admin/pick?…` 改写成 `/workspace/pick-data?…&v=<版本>`，`https://` 原样保留，其余一律置空，
 * 版本数据里出现 `javascript:` 之类的链接也到不了 href。
 */
import { z } from "zod";

import { type GlossaryGroup, type GlossaryTerm } from "./glossary";
import { SORT_LABELS, SORTS as RS_SORTS, type Sort as RsSort } from "./metrics";
import {
  YOUTUBE_LABEL,
  YOUTUBE_RULES,
  isYoutubeRule,
  type PlatformRule,
  type YoutubeRule,
} from "./platforms";
import {
  BASES,
  BASIS_DATE_LABEL,
  BASIS_LABELS,
  PLATFORMS,
  RS_RANK_LABELS,
  RS_RANKS,
  type Basis,
  type RsRank,
} from "./request";

/** pick_mirror 的 MAX_VERSION_ID（gp/mirror/versions.py），schema 名 pickm_vNNNNNN 是 6 位 */
const MAX_VERSION_ID = 999_999;
const ADMIN_PICK = "/admin/pick?";
const BOARD_PATH = "/workspace/pick-data";

/** RealShort 的 SCALAR：字符串、数、布尔或 null（contracts.py 的 _scalar） */
const scalar = z.union([z.string(), z.number(), z.boolean(), z.null()]);
type Scalar = z.infer<typeof scalar>;
const scalarRecord = z.record(z.string(), scalar);

const platformRuleSchema = z
  .object({
    key: scalar,
    name: scalar,
    doc: scalar,
    updated: scalar,
    back: scalar,
    report: scalar,
    yt: scalar,
    ytNote: scalar,
    tag: scalar,
    unban: scalar,
    material: scalar,
    signals: scalar,
  })
  .strict();

const glossaryItemSchema = z
  .object({
    t: scalar.optional(),
    a: scalar.optional(),
    ask: scalar.optional(),
    d: scalar.optional(),
    h: scalar.optional(),
  })
  .strict();

const glossaryGroupSchema = z
  .object({ g: scalar, d: scalar, items: z.array(glossaryItemSchema) })
  .strict();

const youtubeLabelsSchema = z
  .object({
    ok: scalar.optional(),
    only: scalar.optional(),
    warn: scalar.optional(),
    no: scalar.optional(),
  })
  .strict();

const rulesSchema = z
  .object({
    platformRules: z.record(z.string(), platformRuleSchema),
    inUse: z.array(scalar),
    basisLabels: scalarRecord,
    basisDateLabels: scalarRecord,
    rsRankLabels: scalarRecord,
    youtubeLabels: youtubeLabelsSchema,
    glossary: z.array(glossaryGroupSchema),
    ruleHints: scalarRecord,
    langLoc: scalarRecord,
    postedPoolUrl: scalar,
    sortLabels: scalarRecord,
  })
  .strict();

type RawRules = z.infer<typeof rulesSchema>;
type RawPlatformRule = z.infer<typeof platformRuleSchema>;
type RawGlossaryGroup = z.infer<typeof glossaryGroupSchema>;
type RawGlossaryItem = z.infer<typeof glossaryItemSchema>;

/** 与 contracts.py 逐键对照（rules.test.ts 读 Python 源码比对） */
export const RULES_KEYS: readonly string[] = Object.freeze(
  Object.keys(rulesSchema.shape),
);
export const PLATFORM_RULE_KEYS: readonly string[] = Object.freeze(
  Object.keys(platformRuleSchema.shape),
);
export const GLOSSARY_ITEM_KEYS: readonly string[] = Object.freeze(
  Object.keys(glossaryItemSchema.shape),
);

/** 一个版本的规则，归一后。前 11 个字段就是 meta.rules；后三个由 platformRules 派生 */
export interface BoardRules {
  /** 键是剧场键，含本地不认识的剧场；缺哪个剧场就查不到（页面按「规则未知」处理） */
  readonly platformRules: Readonly<Record<string, Readonly<PlatformRule>>>;
  /** 运营在用的剧场（只留字符串） */
  readonly inUse: readonly string[];
  readonly basisLabels: Readonly<Record<Basis, string>>;
  readonly basisDateLabels: Readonly<Record<Basis, string>>;
  /** rs_ledger 固定是「ReelShort 订单对账」 */
  readonly rsRankLabels: Readonly<Record<RsRank, string>>;
  readonly youtubeLabels: Readonly<Record<YoutubeRule, string>>;
  readonly glossary: readonly GlossaryGroup[];
  /** 规则表列头的悬停提示；空串的不留 */
  readonly ruleHints: Readonly<Record<string, string>>;
  /** 剧单语种中文名 → locale；空串的不留 */
  readonly langLoc: Readonly<Record<string, string>>;
  /** 发布记录来源表的链接，规则同 doc；空串 = 不给链接 */
  readonly postedPoolUrl: string;
  readonly sortLabels: Readonly<Record<RsSort, string>>;
  /** platformRules 里本地 PLATFORMS 没有的剧场键（RealShort 新增了剧场），按版本里的顺序 */
  readonly unknownPlatforms: readonly string[];
  /** 禁 YouTube 的剧场：先按 PLATFORMS 的顺序，再接 unknownPlatforms */
  readonly ytBlocked: readonly string[];
  /** 只能投 YouTube 剧单的剧场，顺序同上 */
  readonly ytListOnly: readonly string[];
}

/** meta.rules 没过校验。paths 是出问题的字段路径，不像普通名字的键写成 <key>，永远不带值 */
export class BoardRulesInvalid extends Error {
  readonly code = "board_rules_invalid";
  readonly paths: readonly string[];

  constructor(paths: readonly string[]) {
    super(`pick-board meta.rules failed validation at ${paths.join(", ")}`);
    this.name = "BoardRulesInvalid";
    this.paths = Object.freeze([...paths]);
  }
}

const PLAIN_PATH_PART = /^[A-Za-z0-9_]{1,64}$/;
const MAX_REPORTED_PATHS = 10;

function pathOf(parts: readonly (string | number)[]): string {
  if (parts.length === 0) return "(root)";
  return parts
    .map((part) =>
      typeof part === "number" || PLAIN_PATH_PART.test(part)
        ? String(part)
        : "<key>",
    )
    .join(".");
}

function issuePaths(issues: readonly z.ZodIssue[]): string[] {
  return [...new Set(issues.map((issue) => pathOf(issue.path)))].slice(
    0,
    MAX_REPORTED_PATHS,
  );
}

/** 标量按页面上会显示的样子转成文本：字符串原样，数转字符串，null 与布尔是空串（React 也不渲染它们） */
function text(value: Scalar | undefined): string {
  if (typeof value === "string") return value;
  return typeof value === "number" ? String(value) : "";
}

/** doc / postedPoolUrl：站内旧地址改写并钉版本，https 外链原样，其余置空 */
function safeLink(value: Scalar, versionId: number): string {
  if (typeof value !== "string") return "";
  if (value.startsWith("https://")) return value;
  if (!value.startsWith(ADMIN_PICK)) return "";
  const params = new URLSearchParams(
    value.slice(ADMIN_PICK.length).replace(/#[\s\S]*$/, ""),
  );
  params.delete("v");
  params.set("v", String(versionId));
  return `${BOARD_PATH}?${params.toString()}`;
}

/** 已知键的标签表：版本给了文本就用版本的，缺键或不是文本用静态值；版本多出来的键不留 */
function labels<K extends string>(
  keys: readonly K[],
  fallback: Readonly<Record<K, string>>,
  raw: Readonly<Partial<Record<string, Scalar>>>,
): Record<K, string> {
  const pick = (key: K): string => {
    const value = Object.hasOwn(raw, key) ? raw[key] : undefined;
    return typeof value === "string" || typeof value === "number"
      ? String(value)
      : fallback[key];
  };
  return Object.fromEntries(keys.map((key) => [key, pick(key)])) as Record<
    K,
    string
  >;
}

/** 开放键的文本表（ruleHints、langLoc）：值转文本，空的不留 */
function textRecord(
  raw: Readonly<Record<string, Scalar>>,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(raw)
      .map(([key, value]) => [key, text(value)] as const)
      .filter(([, value]) => value !== ""),
  );
}

function platformRule(
  key: string,
  raw: RawPlatformRule,
  versionId: number,
): PlatformRule {
  return {
    key,
    name: text(raw.name),
    doc: safeLink(raw.doc, versionId),
    updated: text(raw.updated),
    back: text(raw.back),
    report: text(raw.report),
    yt: isYoutubeRule(raw.yt) ? raw.yt : null,
    ytNote: text(raw.ytNote),
    tag: text(raw.tag),
    unban: text(raw.unban),
    material: text(raw.material),
    signals: text(raw.signals),
  };
}

function glossaryTerm(raw: RawGlossaryItem): GlossaryTerm {
  const a = text(raw.a);
  const h = text(raw.h);
  return {
    t: text(raw.t),
    d: text(raw.d),
    ...(a ? { a } : {}),
    ...(h ? { h } : {}),
    ...(raw.ask === false ? { ask: false as const } : {}),
  };
}

function glossaryGroup(raw: RawGlossaryGroup): GlossaryGroup {
  return { g: text(raw.g), d: text(raw.d), items: raw.items.map(glossaryTerm) };
}

type PlatformPart = Pick<
  BoardRules,
  "platformRules" | "unknownPlatforms" | "ytBlocked" | "ytListOnly"
>;

function platformPart(
  raw: RawRules["platformRules"],
  versionId: number,
): PlatformPart {
  const platformRules: Record<string, PlatformRule> = Object.fromEntries(
    Object.entries(raw).map(([key, rule]) => [
      key,
      platformRule(key, rule, versionId),
    ]),
  );
  const known: readonly string[] = PLATFORMS;
  const unknownPlatforms = Object.keys(platformRules).filter(
    (key) => !known.includes(key),
  );
  const ordered = [
    ...PLATFORMS.filter((p) => Object.hasOwn(platformRules, p)),
    ...unknownPlatforms,
  ];
  const withYt = (yt: YoutubeRule) =>
    ordered.filter((key) => platformRules[key]?.yt === yt);
  return {
    platformRules,
    unknownPlatforms,
    ytBlocked: withYt("no"),
    ytListOnly: withYt("only"),
  };
}

function normalize(raw: RawRules, versionId: number): BoardRules {
  return {
    ...platformPart(raw.platformRules, versionId),
    inUse: raw.inUse.filter(
      (value): value is string => typeof value === "string",
    ),
    basisLabels: labels(BASES, BASIS_LABELS, raw.basisLabels),
    basisDateLabels: labels(BASES, BASIS_DATE_LABEL, raw.basisDateLabels),
    rsRankLabels: {
      ...labels(RS_RANKS, RS_RANK_LABELS, raw.rsRankLabels),
      rs_ledger: RS_RANK_LABELS.rs_ledger,
    },
    youtubeLabels: labels(YOUTUBE_RULES, YOUTUBE_LABEL, raw.youtubeLabels),
    glossary: raw.glossary.map(glossaryGroup),
    ruleHints: textRecord(raw.ruleHints),
    langLoc: textRecord(raw.langLoc),
    postedPoolUrl: safeLink(raw.postedPoolUrl, versionId),
    sortLabels: labels(RS_SORTS, SORT_LABELS, raw.sortLabels),
  };
}

/** 冻结新建的整棵对象（输入不动：normalize 里每一层都是新对象） */
function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

/**
 * 校验并归一一个版本的 meta.rules。versionId 是 pick_mirror.versions.id（1–999999），写进改写后的站内链接。
 * 失败抛 BoardRulesInvalid；versionId 越界是调用方的错，抛 RangeError。
 */
export function buildBoardRules(raw: unknown, versionId: number): BoardRules {
  if (
    !Number.isInteger(versionId) ||
    versionId < 1 ||
    versionId > MAX_VERSION_ID
  )
    throw new RangeError("versionId must be an integer in 1..999999");
  const parsed = rulesSchema.safeParse(raw);
  if (!parsed.success)
    throw new BoardRulesInvalid(issuePaths(parsed.error.issues));
  return deepFreeze(normalize(parsed.data, versionId));
}
