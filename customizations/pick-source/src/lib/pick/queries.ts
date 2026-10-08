import "server-only";

import { sql, type SQL } from "drizzle-orm";

import { getDb } from "@/db";
import { loadRowsByIds } from "@/lib/observe/queries";
import { PLATFORM_RULES } from "./platforms";
import { POSTED_COLUMNS, toPostedRecord, type PostedRecord } from "./queries-posted";
import { reelshortBranch, toRowWithFlags, unionRows } from "./queries-reelshort";
import {
  loadPostedFor,
  loadSignalsFor,
  ROW_COLUMNS,
  SAME_TITLE_LIMIT,
  textArray,
  toRow,
  whereOf,
  type PickRow,
  type RawRow,
} from "./queries-shared";
import {
  BASES,
  IN_USE,
  PLATFORMS,
  RS_ROW_PREFIX,
  isRsBasis,
  reelshortId,
  type Basis,
  type PickRequest,
  type Platform,
  type PostedFilter,
  type Sort,
} from "./request";

/**
 * 选剧台的读查询：选剧 / 全部剧库 / 证据页三个 tab。榜单 tab 在 queries-rank.ts、
 * 发布记录 tab 在 queries-posted.ts，ReelShort 分支在 queries-reelshort.ts，
 * 共用的列清单与行转换在 queries-shared.ts；
 * 【这个文件是页面层唯一允许的入口】，四个内部文件由下面的 `export *` 转出。
 *
 * 【后台里唯一允许在渲染路径上查库的模块】（2026-09-11 观测台并入后），`tests/admin-contracts.test.ts`
 * 用一条窄白名单钉住：只有 app/admin/(protected)/pick 与 components/admin/pick 能 import 它，
 * 而且它们只能经这一个模块查库；lib 层可以单向读 @/lib/observe/queries。理由：
 * 这一页整页就是数据，一天被打开几次，相对爬虫在播放页上跑出来的量级可以忽略。
 *
 * 【十个剧场 = catalog_rows UNION ALL ReelShort 分支】：选剧 / 全部剧库的 FROM 是 queries-reelshort 的
 * unionRows()，WHERE / ORDER BY 一律用别名 rows。ReelShort 行的指标（30 天指标 / 推广 / 增量 / 分成）
 * 另一条 loadRowsByIds 按这一页的 id 取，口径与榜单逐字相同。
 * 【所有查询都裁列】：catalog_rows 一行约 470 字节，页面只取显示要用的列。
 * 【WHERE 由主查询与 count 共用】：两边一旦漂开，症状是「页码说有 40 页，翻到第 7 页却是空的」。
 * 【可选的 asOf】（feed v2 导出与核对脚本，方案 4.2）：只影响 ReelShort 分支与它的指标；不传时 SQL 与改动前逐字相同。
 */

export * from "./queries-shared";
export * from "./queries-rank";
export * from "./queries-posted";
export * from "./queries-reelshort";

/**
 * 发布记录筛选的三个 WHERE 片段。筛选与 chips 计数共用同一份（同 queries-posted 的 STATE_WHERE），
 * 两边一旦漂开，症状是「chip 上写 3，点进去 1 条」。剧单行按 row_keys 对，ReelShort 行按 drama_ids 对
 * （行键前缀 reelshort-，与 loadPostedFor 同一种拼法）。
 *
 * 【先把 71 条记录摊成一张 (row_key, published) 小键表，再用 `IN (子查询)` 对它】。原来是对每一行跑
 * `rows.row_key = ANY(p.row_keys)` 的关联子查询：ReelShort 并入后全部剧库是 7 万行 × 71 条 × 三个 FILTER，
 * 2026-09-11 实测那一条 facet 在库里要 3.85 秒（其余四条都在 350 ms 内）。
 * 【必须是 IN，不能是 EXISTS】：这三段既进 WHERE 也进聚合的 FILTER (WHERE …)。WHERE 里的关联 EXISTS 会被
 * 拉成 semi-join，FILTER 里的不会——它退化成逐行重跑的 SubPlan，实测 17 秒；无关联的 IN (子查询) 在两处
 * 都是只算一次的 Hashed SubPlan，实测 0.1 秒。`NOT IN` 那一段依赖键表里没有 NULL（`IS NOT NULL` 那一句不能省，
 * 否则整列结果是 NULL、「未排期」那一档静默变 0）。
 */
function postedKeys(): SQL {
  return sql`SELECT k.row_key, bool_or(k.post_count > 0) AS published
      FROM (SELECT unnest(p.row_keys) AS row_key, p.post_count FROM catalog_posted p
            UNION ALL
            SELECT ${RS_ROW_PREFIX} || unnest(p.drama_ids), p.post_count FROM catalog_posted p) k
      WHERE k.row_key IS NOT NULL
      GROUP BY k.row_key`;
}
const POSTED_WHERE: Record<Exclude<PostedFilter, "">, SQL> = {
  pool: sql`rows.row_key IN (SELECT pk.row_key FROM (${postedKeys()}) pk)`,
  yes: sql`rows.row_key IN (SELECT pk.row_key FROM (${postedKeys()}) pk WHERE pk.published)`,
  no: sql`rows.row_key NOT IN (SELECT pk.row_key FROM (${postedKeys()}) pk WHERE pk.published)`,
};

/** 规则表里禁 YouTube 的剧场，与只能投 YouTube 剧单的剧场；「YouTube 可发」条件按它们算 */
const YT_BLOCKED = PLATFORMS.filter((p) => PLATFORM_RULES[p].yt === "no");
const YT_LIST_ONLY = PLATFORMS.filter((p) => PLATFORM_RULES[p].yt === "only");

function platformArray(list: readonly Platform[]): SQL {
  return textArray([...list]);
}

/**
 * 过滤条件。`skip` 用来给 facet 计数：算「剧场 chips 各有几行」时不能带上当前选中的剧场，
 * 否则除了选中的那个全是 0。
 */
function filtersFor(
  req: PickRequest,
  skip: "platform" | "basis" | "lang" | "posted" | null = null,
): SQL[] {
  const f: SQL[] = [];
  const poolOnly = req.tab === "pick" ? !req.wide : req.signalOnly;
  if (poolOnly) f.push(sql`rows.has_signal`);
  if (!req.withOff) f.push(sql`rows.off_on IS NULL`);
  if (req.platform && skip !== "platform") f.push(sql`rows.platform = ${req.platform}`);
  else if (req.inUseOnly && skip !== "platform") f.push(sql`rows.platform = ANY(${platformArray(IN_USE)})`);
  if (req.lang && skip !== "lang") f.push(sql`rows.lang = ${req.lang}`);
  if (req.basis && skip !== "basis") f.push(basisFilter(req.basis));
  if (req.posted && skip !== "posted") f.push(POSTED_WHERE[req.posted]);
  if (req.youtubeOk)
    f.push(
      sql`(rows.platform <> ALL(${platformArray(YT_BLOCKED)}) AND (rows.platform <> ALL(${platformArray(YT_LIST_ONLY)}) OR rows.youtube))`,
    );
  if (req.datedOnly) f.push(sql`rows.latest_evidence_on IS NOT NULL`);
  if (req.q) {
    /* 剧名 / 中文名模糊 + row_key / book_id 精确。ILIKE 没有索引，但这是人工搜索、一天几次 */
    const like = `%${req.q}%`;
    f.push(
      sql`(rows.title ILIKE ${like} OR rows.title_cn ILIKE ${like} OR rows.row_key = ${req.q} OR rows.drama_id = ${req.q})`,
    );
  }
  return f;
}

/** 依据：ReelShort 的三种是 union 里现算的布尔列，剧场的是 catalog_signals 里有没有那一种 */
function basisFilter(basis: Basis): SQL {
  if (isRsBasis(basis)) return sql.raw(`rows.rs_${basis}`);
  return sql`EXISTS (SELECT 1 FROM catalog_signals s WHERE s.row_key = rows.row_key AND s.kind = ${basis})`;
}

/**
 * 排序白名单 → ORDER BY。每条都以 row_key 收尾：同日同名的行没有稳定第二键时会在翻页之间来回跳。
 * 「证据时间」把没有日期的证据排最后，再按剧单日期；不拿剧单日期代填证据日期。
 */
function orderBy(sort: Sort): SQL {
  switch (sort) {
    case "listed":
      return sql`rows.listed_on DESC NULLS LAST, rows.title ASC, rows.row_key ASC`;
    case "title":
      return sql`rows.title ASC, rows.platform ASC, rows.row_key ASC`;
    case "evidence":
    default:
      return sql`rows.latest_evidence_on DESC NULLS LAST, rows.listed_on DESC NULLS LAST, rows.title ASC, rows.platform ASC, rows.row_key ASC`;
  }
}

/** 一页行上挂信号 / 发布记录标签 / ReelShort 指标，三条查询并发 */
async function decorate(base: Omit<PickRow, "signals" | "posted">[], asOf?: Date): Promise<PickRow[]> {
  const keys = base.map((r) => r.rowKey);
  const rsIds = base.map((r) => (r.platform === "reelshort" ? reelshortId(r.rowKey) : "")).filter(Boolean);
  const [signals, posted, rs] = await Promise.all([loadSignalsFor(keys), loadPostedFor(keys), loadRowsByIds(rsIds, { asOf })]);
  return base.map((r) => ({
    ...r,
    signals: signals.get(r.rowKey) ?? [],
    posted: posted.get(r.rowKey) ?? [],
    rs: r.platform === "reelshort" ? rs.get(reelshortId(r.rowKey)) : undefined,
  }));
}

export async function loadPickRows(
  req: PickRequest,
  asOf?: Date,
): Promise<{ rows: PickRow[]; total: number; hasMore: boolean }> {
  const db = getDb();
  const where = whereOf(filtersFor(req));
  const offset = (req.page - 1) * req.size;
  const [page, count] = await Promise.all([
    db.execute(
      sql`SELECT rows.* FROM ${unionRows(asOf)} ${where} ORDER BY ${orderBy(req.sort)} LIMIT ${req.size} OFFSET ${offset}`,
    ),
    db.execute(sql`SELECT count(*)::int AS n FROM ${unionRows(asOf)} ${where}`),
  ]);
  const rows = await decorate((page.rows as RawRow[]).map(toRowWithFlags), asOf);
  const total = Number((count.rows[0] as { n: number }).n);
  return { rows, total, hasMore: offset + rows.length < total };
}

export interface PickFacets {
  platforms: Partial<Record<Platform, number>>;
  langs: { lang: string; n: number }[];
  bases: Partial<Record<Basis, number>>;
  /** 未发过 / 发过 / 在选剧池 各有几行；「不限」不计 */
  posted: Record<Exclude<PostedFilter, "">, number>;
}

/**
 * 四组 chips 的计数，各自不带自己那一维的过滤（选了 KalosTV 之后别的剧场仍要有数，
 * 否则人不知道换过去有没有行）。依据的计数按行数不按信号条数：一行有两条日榜记录算一行；
 * ReelShort 三种依据是 union 里的布尔列，一条 FILTER 查询数完。
 * 发布记录那一组一条查询三个 FILTER，与筛选共用 POSTED_WHERE（/qa 2026-09-11 ISSUE-004）。
 */
export async function loadFacets(req: PickRequest, asOf?: Date): Promise<PickFacets> {
  const db = getDb();
  const [pl, la, ba, rs, po] = await Promise.all([
    db.execute(
      sql`SELECT rows.platform AS k, count(*)::int AS n FROM ${unionRows(asOf)} ${whereOf(filtersFor(req, "platform"))} GROUP BY rows.platform`,
    ),
    db.execute(
      sql`SELECT rows.lang AS k, count(*)::int AS n FROM ${unionRows(asOf)} ${whereOf(filtersFor(req, "lang"))} GROUP BY rows.lang ORDER BY n DESC, k ASC`,
    ),
    db.execute(
      sql`SELECT s.kind AS k, count(DISTINCT s.row_key)::int AS n FROM catalog_signals s
          JOIN ${unionRows(asOf)} ON rows.row_key = s.row_key ${whereOf(filtersFor(req, "basis"))} GROUP BY s.kind`,
    ),
    db.execute(
      sql`SELECT count(*) FILTER (WHERE rows.rs_clk)::int AS clk,
                 count(*) FILTER (WHERE rows.rs_bill)::int AS bill,
                 count(*) FILTER (WHERE rows.rs_gsc)::int AS gsc
          FROM ${unionRows(asOf)} ${whereOf([sql`rows.platform = 'reelshort'`, ...filtersFor(req, "basis")])}`,
    ),
    db.execute(
      sql`SELECT count(*) FILTER (WHERE ${POSTED_WHERE.pool})::int AS pool,
                 count(*) FILTER (WHERE ${POSTED_WHERE.yes})::int AS yes,
                 count(*) FILTER (WHERE ${POSTED_WHERE.no})::int AS no
          FROM ${unionRows(asOf)} ${whereOf(filtersFor(req, "posted"))}`,
    ),
  ]);
  const platforms: Partial<Record<Platform, number>> = {};
  for (const r of pl.rows as { k: string; n: number }[])
    if ((PLATFORMS as readonly string[]).includes(r.k)) platforms[r.k as Platform] = r.n;
  const bases: Partial<Record<Basis, number>> = {};
  for (const r of ba.rows as { k: string; n: number }[])
    if ((BASES as readonly string[]).includes(r.k)) bases[r.k as Basis] = r.n;
  const rsRow = (rs.rows[0] ?? { clk: 0, bill: 0, gsc: 0 }) as { clk: number; bill: number; gsc: number };
  bases.clk = rsRow.clk;
  bases.bill = rsRow.bill;
  bases.gsc = rsRow.gsc;
  const posted = (po.rows[0] ?? { pool: 0, yes: 0, no: 0 }) as { pool: number; yes: number; no: number };
  return {
    platforms,
    langs: (la.rows as { k: string; n: number }[]).map((r) => ({ lang: r.k, n: r.n })),
    bases,
    posted: { pool: posted.pool, yes: posted.yes, no: posted.no },
  };
}

export interface PickFreshness {
  importedAt: Date | null;
  rows: number;
  withSignal: number;
  signals: number;
  posted: number;
  /** ReelShort：正典行数 / 候选数 / 指标最近采集时间（dramas.synced_at 的 max） */
  rsCanonical: number;
  rsCandidates: number;
  rsSyncedAt: Date | null;
}

function toDate(v: unknown): Date | null {
  if (!v) return null;
  const d = new Date(String(v).replace(" ", "T").replace(/([+-]\d{2})$/, "$1:00"));
  return Number.isNaN(d.getTime()) ? null : d;
}

/** asOf 只影响 ReelShort 正典 / 候选两个数（候选条件含 7 天出站与账单）；剧单导入时间与各表行数没有时间维度 */
export async function loadFreshness(asOf?: Date): Promise<PickFreshness> {
  const db = getDb();
  const [res, rs] = await Promise.all([
    db.execute(
      sql`SELECT (SELECT max(imported_at) FROM catalog_rows) AS imported_at,
                 (SELECT count(*)::int FROM catalog_rows) AS rows,
                 (SELECT count(*)::int FROM catalog_rows WHERE has_signal) AS with_signal,
                 (SELECT count(*)::int FROM catalog_signals) AS signals,
                 (SELECT count(*)::int FROM catalog_posted) AS posted,
                 (SELECT max(synced_at) FROM dramas) AS rs_synced_at`,
    ),
    db.execute(
      sql`SELECT count(*)::int AS canonical, count(*) FILTER (WHERE has_signal)::int AS cand FROM (${reelshortBranch(asOf)}) r`,
    ),
  ]);
  const r = res.rows[0] as {
    imported_at: string | Date | null;
    rows: number;
    with_signal: number;
    signals: number;
    posted: number;
    rs_synced_at: string | Date | null;
  };
  const c = (rs.rows[0] ?? { canonical: 0, cand: 0 }) as { canonical: number; cand: number };
  return {
    importedAt: toDate(r.imported_at),
    rows: r.rows,
    withSignal: r.with_signal,
    signals: r.signals,
    posted: r.posted,
    rsCanonical: c.canonical,
    rsCandidates: c.cand,
    rsSyncedAt: toDate(r.rs_synced_at),
  };
}

/** 证据页里对上的 ReelShort 正典行，只取链接与集数要用的列 */
export interface SiteDrama {
  id: string;
  locale: string;
  slug: string;
  title: string;
  chapterCount: number;
  payStart: number;
}

export interface RowDetail {
  row: PickRow;
  siteDramas: SiteDrama[];
  postedRecords: PostedRecord[];
  /** 同名核对：同 title_key 的其它剧场行（一律标「未核」） */
  sameTitle: { rowKey: string; platform: Platform; lang: string; title: string; titleCn: string; offOn: string | null }[];
  /** sameTitle 取回条数等于 SAME_TITLE_LIMIT：可能还有没取到的（问答的未取全提示读它） */
  sameTitleTruncated: boolean;
  /**
   * 这个剧场当前导入的剧单里，集数 / 起付费两列有没有任何一行填了（问答审计 P2-11）：分得清「这一行没填」与「这个剧场的剧单根本不给这一列」。
   * 只给布尔不给行数：计数会被问答核对层当材料，替「该剧场共 120 部」背书；也分不清上游没这一列、没填还是导入时丢了，只说「当前导入的数据里没有值」
   */
  columnFilled: { episodes: boolean; payStart: boolean };
}

/** 剧场行的证据页；ReelShort 行（行键 reelshort-<id>）走 queries-reelshort 的 loadReelshortDetail */
export async function loadRowDetail(rowKey: string): Promise<RowDetail | null> {
  const db = getDb();
  const res = await db.execute(
    sql`SELECT ${ROW_COLUMNS}, title_key FROM catalog_rows WHERE row_key = ${rowKey}`,
  );
  const raw = res.rows[0] as (RawRow & { title_key: string }) | undefined;
  if (!raw) return null;
  const base = toRow(raw);
  const [signals, postedTags, siteRes, postedRes, sameRes, filledRes] = await Promise.all([
    loadSignalsFor([rowKey]),
    loadPostedFor([rowKey]),
    base.inSiteIds.length
      ? db.execute(
          sql`SELECT id, locale, slug, title, chapter_count, pay_start FROM dramas WHERE id = ANY(${textArray(base.inSiteIds)}) ORDER BY locale, id`,
        )
      : Promise.resolve({ rows: [] as Record<string, unknown>[] }),
    db.execute(
      sql`SELECT ${POSTED_COLUMNS} FROM catalog_posted WHERE ${rowKey} = ANY(row_keys) ORDER BY last_post_on DESC NULLS LAST, sd`,
    ),
    raw.title_key
      ? db.execute(
          sql`SELECT row_key, platform, lang, title, title_cn, off_on FROM catalog_rows
              WHERE title_key = ${raw.title_key} AND row_key <> ${rowKey} ORDER BY platform, lang, row_key LIMIT ${sql.raw(String(SAME_TITLE_LIMIT))}`,
        )
      : Promise.resolve({ rows: [] as Record<string, unknown>[] }),
    /* 碰到第一行填了的就停；全空的剧场要扫完它自己那几千行，证据页一次 */
    db.execute(
      sql`SELECT EXISTS (SELECT 1 FROM catalog_rows WHERE platform = ${base.platform} AND episodes IS NOT NULL) AS episodes,
                 EXISTS (SELECT 1 FROM catalog_rows WHERE platform = ${base.platform} AND pay_start IS NOT NULL) AS pay_start`,
    ),
  ]);
  const filled = (filledRes.rows[0] ?? {}) as { episodes?: unknown; pay_start?: unknown };
  const yes = (v: unknown) => v === true || v === "t" || v === "true";
  return {
    row: { ...base, signals: signals.get(rowKey) ?? [], posted: postedTags.get(rowKey) ?? [] },
    siteDramas: (
      siteRes.rows as {
        id: string;
        locale: string;
        slug: string;
        title: string;
        chapter_count: number;
        pay_start: number;
      }[]
    ).map((d) => ({
      id: d.id,
      locale: d.locale,
      slug: d.slug,
      title: d.title,
      chapterCount: d.chapter_count,
      payStart: d.pay_start,
    })),
    postedRecords: postedRes.rows.map(toPostedRecord),
    sameTitle: (
      sameRes.rows as { row_key: string; platform: string; lang: string; title: string; title_cn: string; off_on: string | null }[]
    ).map((s) => ({
      rowKey: s.row_key,
      platform: (PLATFORMS as readonly string[]).includes(s.platform)
        ? (s.platform as Platform)
        : "dramabox",
      lang: s.lang,
      title: s.title,
      titleCn: s.title_cn,
      offOn: s.off_on,
    })),
    sameTitleTruncated: sameRes.rows.length === SAME_TITLE_LIMIT,
    columnFilled: { episodes: yes(filled.episodes), payStart: yes(filled.pay_start) },
  };
}
