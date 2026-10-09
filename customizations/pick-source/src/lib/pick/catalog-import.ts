/**
 * 选剧台导入的纯转换：catalog.json / posted.json 的行 → 三张表的行。
 *
 * 【不许 import 任何 server-only 模块】——这个文件要能被 `node --test` 直接加载，
 * 测的才是行为而不是"代码长这样"（同 lib/observe/metrics.ts 的既定做法）。
 * 查库与写库在 scripts/import-catalog.ts，这里只有算法。
 *
 * 源头形状由 scripts/juyuantai/build.py 与 posted.py 决定，两边字段名一一对应；
 * 改了那边的输出，这里的类型与 tests/pick-import.test.ts 一起改。
 */
import { legacyTitleKey } from "@/lib/legacy-url";
import type { SourceDetails } from "@/lib/observe/source-types";

/** build.py 产出的一行（只列导入用到的字段） */
export interface RawCatalogRow {
  k: string;
  p: string;
  src: string;
  t: string;
  cn?: string;
  cr?: string;
  lang?: string;
  kind?: string;
  origin?: string;
  tags?: string;
  date?: string | null;
  pan?: string;
  pw?: string;
  ep?: number | null;
  pay?: number | null;
  yt?: boolean | null;
  n?: number;
  off?: string | null;
  reoff?: string;
  sig?: RawSignal[];
}

/** build.py 的 sig 数组元素。各 kind 的字段集合见 tests/pick-import.test.ts 顶部的样本 */
export interface RawSignal {
  s: string;
  r?: number;
  d?: string;
  g?: string;
  t?: string;
  w?: string;
  weeks?: number;
  best?: number;
  days?: number;
  first?: string;
  note?: string;
  h?: unknown[];
  /** 鹊娱榜：鹊娱自己的剧 id 与剧场侧 playlet_id（2026-09-12 起） */
  qy?: number;
  pid?: string;
}

/** posted.py 产出的一部剧 */
export interface RawPostedDrama {
  sd: string;
  rec?: string;
  t: string;
  tk?: string;
  lang?: string | null;
  plat?: string | null;
  /** 选剧池的「来源」多选（Reelshort榜单 / 至真选剧台 / Google Trends…） */
  src?: string[];
  /** 选剧池的「分类」多选 */
  cats?: string[];
  /** 推荐人 */
  who?: string[];
  life?: string | null;
  sched?: boolean;
  online?: string | null;
  why?: string | null;
  note?: string | null;
  archived?: boolean;
  created?: string | null;
  updated?: string | null;
  posts?: RawPost[];
}

export interface RawPost {
  d?: string | null;
  acct?: string;
  st?: string;
  views?: number | null;
  likes?: number | null;
  favs?: number | null;
  cmts?: number | null;
  shares?: number | null;
  /** 指标日期（播放等数字是哪天回填的） */
  md?: string | null;
  url?: string;
  note?: string | null;
  pid?: string;
}

/** posted.py 产出的账号台账一行 */
export interface RawAccount {
  id?: string;
  name?: string;
  url?: string;
  group?: string;
  form?: string;
  niche?: string;
  status?: string;
  fans?: number | null;
  asOf?: string | null;
}

export interface CatalogRowInsert {
  rowKey: string;
  platform: string;
  sourceTable: string;
  title: string;
  titleCn: string;
  creator: string;
  lang: string;
  kind: string;
  origin: string;
  tags: string;
  listedOn: string | null;
  panUrl: string;
  panPw: string;
  episodes: number | null;
  payStart: number | null;
  youtube: boolean;
  mergedRows: number;
  offOn: string | null;
  reoffNote: string;
  titleKey: string;
  inSiteIds: string[];
  legacyOnly: boolean;
  siteOther: boolean;
  hasSignal: boolean;
  latestEvidenceOn: string | null;
}

export interface CatalogSignalInsert {
  rowKey: string;
  kind: string;
  ord: number;
  evidenceOn: string | null;
  rank: number | null;
  grade: string;
  note: string;
  payload: Record<string, unknown>;
}

export interface CatalogPostedInsert {
  sd: string;
  feishuRecord: string;
  title: string;
  titleKey: string;
  lang: string;
  platform: string;
  life: string;
  scheduled: boolean;
  onlineOn: string | null;
  why: string;
  note: string;
  archived: boolean;
  postCount: number;
  lastPostOn: string | null;
  viewsTotal: number;
  sources: string[];
  cats: string[];
  who: string[];
  accounts: string[];
  createdOn: string | null;
  updatedOn: string | null;
  firstPostOn: string | null;
  metricAt: string | null;
  schedCount: number;
  viewsCount: number;
  posts: RawPost[];
  rowKeys: string[];
  dramaIds: string[];
}

export interface CatalogAccountInsert {
  id: string;
  name: string;
  url: string;
  grp: string;
  form: string;
  niche: string;
  status: string;
  fans: number | null;
  asOf: string | null;
}

/** 与 posted.py 的 `published` 集合逐字相同：只有这两种状态算「已发」，其余（待公开）算排期中 */
export const PUBLISHED_STATES: readonly string[] = ["已回填", "已公开"];

export interface PostSummary {
  postCount: number;
  schedCount: number;
  viewsTotal: number;
  viewsCount: number;
  firstPostOn: string | null;
  lastPostOn: string | null;
  accounts: string[];
  metricAt: string | null;
}

/**
 * 帖子聚合，与 posted.py 里 n / nSched / views / viewsN / first / last / accts / metricAt 那八行逐字同口径。
 * 【只数已发的帖子】：待公开的帖子没有播放量、没有账号意义，posted.py 把它们单独计成 nSched；
 * P0 曾把全部帖子都算成「已发」，5 部剧的 10 条待公开被多算了进去。
 * 指标空着就是空着：views 只把 number 的加起来，viewsCount 告诉页面「有数的有几条」。
 */
export function summarizePosts(posts: RawPost[]): PostSummary {
  const pub = posts.filter((p) => PUBLISHED_STATES.includes(p.st ?? ""));
  const dated = pub.map((p) => p.d).filter((x): x is string => Boolean(x));
  const views = pub.map((p) => p.views).filter((v): v is number => typeof v === "number");
  const metric = pub.map((p) => p.md).filter((x): x is string => Boolean(x));
  return {
    postCount: pub.length,
    schedCount: posts.length - pub.length,
    viewsTotal: views.reduce((n, v) => n + v, 0),
    viewsCount: views.length,
    firstPostOn: dated.length ? dated.reduce((a, b) => (a < b ? a : b)) : null,
    lastPostOn: dated.length ? dated.reduce((a, b) => (a > b ? a : b)) : null,
    accounts: [...new Set(pub.map((p) => p.acct).filter((x): x is string => Boolean(x)))].sort(),
    metricAt: metric.length ? metric.reduce((a, b) => (a > b ? a : b)) : null,
  };
}

/** 账号台账 → catalog_accounts 行；没有 id 的行用 name 顶，两个都没有的丢掉（主键不能空） */
export function toAccountRows(accounts: RawAccount[]): CatalogAccountInsert[] {
  const seen = new Set<string>();
  const out: CatalogAccountInsert[] = [];
  for (const a of accounts) {
    const id = (a.id || a.name || "").trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({
      id,
      name: a.name ?? "",
      url: a.url ?? "",
      grp: a.group ?? "",
      form: a.form ?? "",
      niche: a.niche ?? "",
      status: a.status ?? "",
      fans: typeof a.fans === "number" ? a.fans : null,
      asOf: a.asOf ?? null,
    });
  }
  return out;
}

/** dramas 表的正典行（导入脚本只取这三列），给发布记录对 ReelShort 剧用 */
export interface CanonicalDrama {
  id: string;
  title: string;
  locale: string;
}

/** 剧单里的语种中文名 → 本站 locale。与 render.py 的 LANG_LOC 逐条相同 */
export const LANG_LOC: Record<string, string> = {
  英语: "en",
  西班牙语: "es",
  日语: "ja",
  葡萄牙语: "pt",
  印尼语: "id",
  泰语: "th",
  繁体中文: "zh-hant",
  简体中文: "zh",
  法语: "fr",
  韩语: "ko",
  德语: "de",
  阿拉伯语: "ar",
  意大利语: "it",
  越南语: "vi",
  俄语: "ru",
  土耳其语: "tr",
  菲律宾语: "tl",
  马来语: "ms",
  波兰语: "pl",
  印地语: "hi",
  保加利亚语: "bg",
  捷克语: "cs",
  罗马尼亚语: "ro",
};

/** 运营表里填的剧场名 → 剧单平台键。与 render.py 的 PLAT_KEY 逐条相同 */
export const PLAT_KEY: Record<string, string> = {
  ReelShort: "reelshort",
  DramaBox: "dramabox",
  ShortMax: "shortmax",
  FlickReels: "flickreels",
  KalosTV: "kalos",
  GoodShort: "goodshort",
  StarShort: "starshort",
  MoboReels: "moboreels",
  TouchShort: "touchshort",
  flareflow: "flareflow",
};

/**
 * 运营表剧名的归一，与 posted.py 的 title_key() 逐字等价：
 * NFKC、小写、去掉各种撇号、其余非字母数字折成空格。
 * 【与 legacyTitleKey 不是一个算法】——那个是旧站 URL 的复刻，这个是运营手填剧名的容错。
 */
export function postedTitleKey(title: string | null | undefined): string {
  return String(title ?? "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[‘’'`´]/g, "")
    .replace(/[^\p{L}\p{N}]+|_/gu, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

/** 这条证据自己的日期：榜单日期 / 周起 / 入榜日期；评级与备注没有自己的日期 */
export function signalEvidenceOn(s: RawSignal): string | null {
  switch (s.s) {
    case "kd":
    case "qc":
    case "qr":
    case "kw":
    case "fh":
    case "gh":
    case "gn":
    case "ghh":
      return s.d && /^\d{4}-\d{2}-\d{2}$/.test(s.d) ? s.d : null;
    default:
      return null;
  }
}

/** 一行的 sig 数组 → catalog_signals 的行。ord 是数组下标，同 kind 多条靠它区分 */
export function toSignalRows(row: RawCatalogRow): CatalogSignalInsert[] {
  return (row.sig ?? []).map((s, ord) => {
    const { s: kind, r, d, g, t, note, ...rest } = s;
    return {
      rowKey: row.k,
      kind,
      ord,
      evidenceOn: signalEvidenceOn(s),
      rank: typeof r === "number" && Number.isFinite(r) ? r : null,
      grade: g ?? "",
      /* GoodShort 的理由原文（t）与 KalosTV 日榜的备注（note）都是"这条证据带的一句话" */
      note: t ?? note ?? "",
      payload: { ...(d ? { d } : {}), ...rest },
    };
  });
}

export interface MatchTables {
  /** dramas.title_key → 有这个剧名的语种（已拉详情的行） */
  siteLocales: Map<string, string[]>;
  /** dramas.title_key → 正典行 [{id, locale}] */
  canonical: Map<string, { id: string; locale: string }[]>;
  /** legacy_urls.title_key → 语种 */
  legacyLocales: Map<string, string[]>;
}

export interface MatchResult {
  inSiteIds: string[];
  legacyOnly: boolean;
  siteOther: boolean;
}

/**
 * 与 match.ts + render.py 的合并逻辑等价：
 * - 英文名与中文名各过 legacyTitleKey()，短于 4 个字符的键不参与（太短全是误配）；
 * - 任一名对上本站 → 看这一行自己的语种在不在对上的语种里：在 = 在售，不在 = 同名·他语种在售；
 * - 都没对上本站、任一名对上旧站 → 旧站收录。本站在售的行不算旧站收录。
 */
export function matchRow(row: RawCatalogRow, tables: MatchTables): MatchResult {
  const keys = [row.t, row.cn]
    .filter((t): t is string => Boolean(t))
    .map(legacyTitleKey)
    .filter((k) => k.length >= 4);
  const locales = new Set<string>();
  const ids = new Set<string>();
  let legacy = false;
  for (const k of keys) {
    const s = tables.siteLocales.get(k);
    if (s) {
      s.forEach((l) => locales.add(l));
      (tables.canonical.get(k) ?? []).forEach((c) => ids.add(c.id));
    } else if (tables.legacyLocales.get(k)) legacy = true;
  }
  if (locales.size === 0) return { inSiteIds: [], legacyOnly: legacy, siteOther: false };
  const loc = LANG_LOC[row.lang ?? ""];
  const other = Boolean(loc) && !locales.has(loc);
  return {
    /* 同名·他语种在售的行不带正典 id：id 是别的语种版本的，挂上去会直达一部语言不对的剧 */
    inSiteIds: other ? [] : [...ids],
    legacyOnly: false,
    siteOther: other,
  };
}

export function toCatalogRow(
  row: RawCatalogRow,
  match: MatchResult,
): CatalogRowInsert {
  const dates = (row.sig ?? [])
    .map(signalEvidenceOn)
    .filter((d): d is string => Boolean(d));
  const num = (v: number | null | undefined) =>
    typeof v === "number" && Number.isFinite(v) ? v : null;
  return {
    rowKey: row.k,
    platform: row.p,
    sourceTable: row.src ?? "",
    title: row.t,
    titleCn: row.cn ?? "",
    creator: row.cr ?? "",
    lang: row.lang ?? "",
    kind: row.kind ?? "",
    origin: row.origin ?? "",
    tags: row.tags ?? "",
    listedOn: row.date && /^\d{4}-\d{2}-\d{2}$/.test(row.date) ? row.date : null,
    panUrl: row.pan ?? "",
    panPw: row.pw ?? "",
    episodes: num(row.ep),
    payStart: num(row.pay),
    youtube: row.yt === true,
    mergedRows: Math.max(1, num(row.n) ?? 1),
    offOn: row.reoff ? null : (row.off ?? null),
    reoffNote: row.reoff ?? "",
    titleKey: legacyTitleKey(row.t),
    inSiteIds: match.inSiteIds,
    legacyOnly: match.legacyOnly,
    siteOther: match.siteOther,
    hasSignal: (row.sig ?? []).length > 0,
    latestEvidenceOn: dates.length ? dates.reduce((a, b) => (a > b ? a : b)) : null,
  };
}

/** 运营表里填了剧场 / 语种的记录只挂到同剧场（同语种）的行上，与 render.py 的 compatible() 逐条相同 */
export function postedCompatible(d: RawPostedDrama, row: RawCatalogRow): boolean {
  const pk = PLAT_KEY[d.plat ?? ""];
  if (pk && row.p !== pk) return false;
  if (d.plat === "其他" && row.p === "reelshort") return false;
  if (d.lang && row.lang && d.lang !== row.lang) return false;
  return true;
}

/** ReelShort 行的约束：与 render.py 对 reelshort.json 行的 compatible() 等价（那边 ReelShort 行只比剧名、语种按中文名比） */
export function postedCompatibleDrama(d: RawPostedDrama, drama: CanonicalDrama): boolean {
  const pk = PLAT_KEY[d.plat ?? ""];
  if (pk && pk !== "reelshort") return false;
  if (d.plat === "其他") return false;
  const loc = LANG_LOC[d.lang ?? ""];
  if (d.lang && loc && loc !== drama.locale) return false;
  return true;
}

/**
 * 发布记录 → catalog_posted 行，rowKeys 是对上的剧场剧库行、dramaIds 是对上的 ReelShort 正典行。
 * 剧场行连中文名一起比（两个名任一对上即可），ReelShort 行只比剧名。
 */
export function toPostedRows(
  dramas: RawPostedDrama[],
  rows: RawCatalogRow[],
  canonical: CanonicalDrama[] = [],
): CatalogPostedInsert[] {
  const dramaByKey = new Map<string, CanonicalDrama[]>();
  for (const c of canonical) {
    const k = postedTitleKey(c.title);
    if (!k) continue;
    const arr = dramaByKey.get(k) ?? [];
    arr.push(c);
    dramaByKey.set(k, arr);
  }
  const byKey = new Map<string, RawCatalogRow[]>();
  for (const r of rows) {
    for (const t of [r.t, r.cn]) {
      const k = postedTitleKey(t);
      if (!k) continue;
      const arr = byKey.get(k) ?? [];
      if (!arr.includes(r)) arr.push(r);
      byKey.set(k, arr);
    }
  }
  return dramas.map((d) => {
    const tk = d.tk || postedTitleKey(d.t);
    const rowKeys = (byKey.get(tk) ?? [])
      .filter((r) => postedCompatible(d, r))
      .map((r) => r.k);
    const dramaIds = (dramaByKey.get(tk) ?? [])
      .filter((c) => postedCompatibleDrama(d, c))
      .map((c) => c.id);
    const posts = d.posts ?? [];
    const sum = summarizePosts(posts);
    return {
      sd: d.sd,
      feishuRecord: d.rec ?? "",
      title: d.t,
      titleKey: tk,
      lang: d.lang ?? "",
      platform: d.plat ?? "",
      life: d.life ?? "",
      scheduled: d.sched === true,
      onlineOn: d.online ?? null,
      why: d.why ?? "",
      note: d.note ?? "",
      archived: d.archived === true,
      postCount: sum.postCount,
      lastPostOn: sum.lastPostOn,
      viewsTotal: sum.viewsTotal,
      sources: d.src ?? [],
      cats: d.cats ?? [],
      who: d.who ?? [],
      accounts: sum.accounts,
      createdOn: d.created ?? null,
      updatedOn: d.updated ?? null,
      firstPostOn: sum.firstPostOn,
      metricAt: sum.metricAt,
      schedCount: sum.schedCount,
      viewsCount: sum.viewsCount,
      posts,
      rowKeys,
      dramaIds,
    };
  });
}

/* ---------------------------------------------------------------- 写库阶段与忙标记（feed v2 方案 4.3，P1-5） */

/**
 * 写库阶段的每一步，由 scripts/import-catalog.ts 注入真实实现（beginSource、三次写库、带所有权核验的 success、failSource）；
 * 测试注入假的记下先后顺序。A 是 beginSource 返回的那次 attempt：三次写库与 complete / fail 都只认它。
 * 【互斥】真实实现里每个写库 batch（含 catalog_rows 的每个分块）的第一句都核验并锁住 A 对 pick_catalog 的所有权
 * （sourceGuardSql，同 snapshot / bill 的做法）：另一次导入 begin 顶替了 A 之后，A 的下一批整批回滚并抛错，不会再写。
 */
export interface CatalogWriteSteps<A> {
  /** observe_sources 的 pick_catalog 置 running（它自己提交，先于第一次写） */
  begin(): Promise<A>;
  /** catalog_rows：约 42 次 1,000 行 upsert，再删旧批次；每一批都核验 A 的所有权 */
  writeRows(attempt: A): Promise<void>;
  /** catalog_signals：一个 batch 整表替换，第一句核验 A 的所有权 */
  writeSignals(attempt: A): Promise<void>;
  /** catalog_posted 与 catalog_accounts：一个 batch 一起换，第一句核验 A 的所有权 */
  writePosted(attempt: A): Promise<void>;
  /** 写完对一次行数，对不上就抛；返回记进 observe_sources.details 的计数 */
  verify(): Promise<SourceDetails>;
  /** 标 success：与所有权核验在同一个 batch，被顶替时抛错，不会静悄悄更新 0 行就当作成功 */
  complete(attempt: A, details: SourceDetails): Promise<void>;
  /** 标 failed：只更新 attempt_id 仍是 A 的那一行，被顶替时不碰新的那次的状态 */
  fail(attempt: A): Promise<void>;
  /** 标 failed 本身也失败时打一行说明（原错误照样抛） */
  log(line: string): void;
}

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/**
 * 剧单导入的写库阶段，前后套上 pick_catalog 忙标记。
 *
 * 【为什么要标记】三张表分三次提交（行约 42 次、信号一个 batch、发布记录与账号一个 batch），中间任何时刻来读，
 * 看到的都可能是新行配旧信号；信号是整表替换，替换前后行数可能相同，只看 fingerprint 分不出来。
 * feed v2 导出与带 fp 的 v1 见到 running 就回 503 source_busy，所以：
 * - begin 必须先于第一次写（它失败就一行都不写）；
 * - complete 必须在最后一次写与写后核对之后；
 * - 中途任一步抛错（含 complete 自己失败）都标 failed，原错误原样抛出，后面的写不再执行。
 *   failed 不拦截读取，只做可见性（manifest 的 catalog_import_incomplete、页底「剧单导入」一行），
 *   恢复方式永远是整次重跑导入，不手工改 observe_sources。
 * 守门（preflight）与 --dry 都在这之前：它们一行不写，不该留下 running 或 failed。
 */
export async function writeCatalogMarked<A>(steps: CatalogWriteSteps<A>): Promise<void> {
  let attempt: A;
  try {
    attempt = await steps.begin();
  } catch (error) {
    throw new Error(
      `写库前把 observe_sources 的 pick_catalog 置为 running 没成功，剧单一行都没写。` +
        `多半是这个库还没执行 scripts/sql/observe-source-pick-catalog.sql（observe_sources 的 CHECK 约束不认 pick_catalog；` +
        `上线顺序是先执行这份 SQL 再部署代码），否则看 DATABASE_URL 与 Neon。原错误：${errorText(error)}`,
      { cause: error },
    );
  }
  try {
    await steps.writeRows(attempt);
    await steps.writeSignals(attempt);
    await steps.writePosted(attempt);
    const details = await steps.verify();
    await steps.complete(attempt, details);
  } catch (error) {
    try {
      await steps.fail(attempt);
    } catch (markError) {
      steps.log(
        `pick_catalog 标为 failed 也没写进去（${errorText(markError)}）：observe_sources 会停在 running，` +
          `导出要等 45 分钟后才按僵死处理。修好连库后整次重跑 pnpm catalog-import，不要手工改 observe_sources。`,
      );
    }
    throw error;
  }
}
