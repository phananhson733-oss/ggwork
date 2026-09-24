// PORTED_FROM: realshort@816ca2e src/lib/pick/queries.ts
// 本地改动：loader 不再有可选的 asOf 参数（时点取钉住版本的 as_of）；「YouTube 可发」与「收起其他剧场」两个条件
// 改从钉住版本的规则取（rules.ytBlocked / ytListOnly / inUse），原来的模块常量与静态剧场表不再用；
// loadFreshness 改读版本的 freshness（meta，按版本缓存），另给纯函数 freshnessOf 让页面直接用 resolveVersion
// 带回来的那份；证据页对上的 ReelShort 行改从 rs_ids 取（原 dramas）；新增按版本缓存的候选池 N
// （loadCandidatePool，页头「智能体候选池」）。类型 PickFacets / PickFreshness / SiteDrama / RowDetail 回到本文件。
import "server-only";

import { sql, type SQL } from "drizzle-orm";

import {
  BASES,
  isRsBasis,
  PLATFORMS,
  reelshortId,
  RS_ROW_PREFIX,
  type Basis,
  type PickRequest,
  type Platform,
  type PostedFilter,
  type Sort,
} from "@/core/pick-board/request";

import { readVersionMeta, rememberForVersion } from "./cache";
import { boardScope, getDb } from "./db";
import {
  POSTED_COLUMNS,
  toPostedRecord,
  type PostedRecord,
} from "./queries-posted";
import {
  toRowWithFlags,
  toSameTitle,
  unionRows,
  type SameTitleRaw,
} from "./queries-reelshort";
import {
  loadPostedFor,
  loadSignalsFor,
  ROW_COLUMNS,
  SAME_TITLE_LIMIT,
  textArray,
  toRow,
  toTimestamp,
  whereOf,
  type PickRow,
  type RawRow,
  type RowsPage,
  type SameTitleRow,
} from "./queries-shared";
import { loadRowsByIds } from "./rs-queries";

/**
 * 选剧资料页的读查询：选剧 / 全部剧库 / 证据页三个 tab。榜单 tab 在 queries-rank.ts、发布记录 tab 在
 * queries-posted.ts，ReelShort 分支在 queries-reelshort.ts，共用的列清单与行转换在 queries-shared.ts。
 *
 * 【十个剧场 = catalog_rows UNION ALL rs_rows】：选剧 / 全部剧库的 FROM 是 unionRows()，WHERE / ORDER BY
 * 一律用别名 rows。ReelShort 行的指标另一条 loadRowsByIds 按这一页的 id 取，口径与榜单逐字相同。
 * 【WHERE 由主查询与 count 共用】：两边一旦漂开，症状是「页码说有 40 页，翻到第 7 页却是空的」。
 * 【剧场规则随版本】：yt / inuse 两个条件读钉住版本的 rules，RealShort 改了剧场规则，本页、RealShort 与智能体一致。
 */

/**
 * 发布记录筛选的三个 WHERE 片段。筛选与 chips 计数共用同一份，两边一旦漂开，症状是「chip 上写 3，点进去 1 条」。
 * 剧单行按 row_keys 对，ReelShort 行按 drama_ids 对（行键前缀 reelshort-，与 loadPostedFor 同一种拼法）。
 * 【先摊成一张 (row_key, published) 小键表，再用 `IN (子查询)` 对它】；【必须是 IN，不能是 EXISTS】：这三段
 * 既进 WHERE 也进聚合的 FILTER，FILTER 里的关联 EXISTS 会退化成逐行重跑的 SubPlan。
 * `NOT IN` 那一段依赖键表里没有 NULL（`IS NOT NULL` 那一句不能省，否则「未发过」那一档静默变 0）。
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

function platformArray(list: readonly string[]): SQL {
  return textArray(list);
}

type FacetDimension = "platform" | "basis" | "lang" | "posted";

/** 剧名 / 中文名模糊 + row_key / book_id 精确。ILIKE 没有索引，但这是人工搜索、一天几次 */
function searchFilter(q: string): SQL {
  const like = `%${q}%`;
  return sql`(rows.title ILIKE ${like} OR rows.title_cn ILIKE ${like} OR rows.row_key = ${q} OR rows.drama_id = ${q})`;
}

/** 「YouTube 可发」：禁 YouTube 的剧场去掉；只能投 YouTube 剧单的剧场只留在剧单上的行。两份名单来自版本规则 */
function youtubeFilter(): SQL {
  const { ytBlocked, ytListOnly } = boardScope().rules;
  return sql`(rows.platform <> ALL(${platformArray(ytBlocked)}) AND (rows.platform <> ALL(${platformArray(ytListOnly)}) OR rows.youtube))`;
}

/** 剧场维：选了剧场就只看它；否则「收起其他剧场」只看版本规则里在用的剧场 */
function platformFilter(req: PickRequest): SQL[] {
  if (req.platform) return [sql`rows.platform = ${req.platform}`];
  if (!req.inUseOnly) return [];
  const { inUse } = boardScope().rules;
  return [sql`rows.platform = ANY(${platformArray(inUse)})`];
}

/**
 * 过滤条件。`skip` 用来给 facet 计数：算「剧场 chips 各有几行」时不能带上当前选中的剧场，
 * 否则除了选中的那个全是 0。
 */
function filtersFor(
  req: PickRequest,
  skip: FacetDimension | null = null,
): SQL[] {
  const f: SQL[] = [];
  const poolOnly = req.tab === "pick" ? !req.wide : req.signalOnly;
  if (poolOnly) f.push(sql`rows.has_signal`);
  if (!req.withOff) f.push(sql`rows.off_on IS NULL`);
  if (skip !== "platform") f.push(...platformFilter(req));
  if (req.lang && skip !== "lang") f.push(sql`rows.lang = ${req.lang}`);
  if (req.basis && skip !== "basis") f.push(basisFilter(req.basis));
  if (req.posted && skip !== "posted") f.push(POSTED_WHERE[req.posted]);
  if (req.youtubeOk) f.push(youtubeFilter());
  if (req.datedOnly) f.push(sql`rows.latest_evidence_on IS NOT NULL`);
  if (req.q) f.push(searchFilter(req.q));
  return f;
}

/** 依据：ReelShort 的三种是 union 里的布尔列，剧场的是 catalog_signals 里有没有那一种 */
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
async function decorate(
  base: readonly Omit<PickRow, "signals" | "posted">[],
): Promise<PickRow[]> {
  const keys = base.map((r) => r.rowKey);
  const rsIds = base
    .map((r) => (r.platform === "reelshort" ? reelshortId(r.rowKey) : ""))
    .filter(Boolean);
  const [signals, posted, rs] = await Promise.all([
    loadSignalsFor(keys),
    loadPostedFor(keys),
    loadRowsByIds(rsIds),
  ]);
  return base.map((r) => ({
    ...r,
    signals: signals.get(r.rowKey) ?? [],
    posted: posted.get(r.rowKey) ?? [],
    rs: r.platform === "reelshort" ? rs.get(reelshortId(r.rowKey)) : undefined,
  }));
}

export async function loadPickRows(
  req: PickRequest,
): Promise<RowsPage<PickRow>> {
  const db = getDb();
  const where = whereOf(filtersFor(req));
  const offset = (req.page - 1) * req.size;
  const [page, count] = await Promise.all([
    db.execute<RawRow>(
      sql`SELECT rows.* FROM ${unionRows()} ${where} ORDER BY ${orderBy(req.sort)} LIMIT ${req.size} OFFSET ${offset}`,
    ),
    db.execute<{ n: number }>(
      sql`SELECT count(*)::int AS n FROM ${unionRows()} ${where}`,
    ),
  ]);
  const rows = await decorate(page.rows.map(toRowWithFlags));
  const total = Number(count.rows[0]?.n ?? 0);
  return { rows, total, hasMore: offset + rows.length < total };
}

export interface PickFacets {
  platforms: Partial<Record<Platform, number>>;
  langs: { lang: string; n: number }[];
  bases: Partial<Record<Basis, number>>;
  /** 未发过 / 发过 / 在选剧池 各有几行；「不限」不计 */
  posted: Record<Exclude<PostedFilter, "">, number>;
}

type Count = { k: string; n: number };
type Trio<K extends string> = Record<K, number>;

function facetQueries(req: PickRequest) {
  const db = getDb();
  return Promise.all([
    db.execute<Count>(
      sql`SELECT rows.platform AS k, count(*)::int AS n FROM ${unionRows()} ${whereOf(filtersFor(req, "platform"))} GROUP BY rows.platform`,
    ),
    db.execute<Count>(
      sql`SELECT rows.lang AS k, count(*)::int AS n FROM ${unionRows()} ${whereOf(filtersFor(req, "lang"))} GROUP BY rows.lang ORDER BY n DESC, k ASC`,
    ),
    db.execute<Count>(
      sql`SELECT s.kind AS k, count(DISTINCT s.row_key)::int AS n FROM catalog_signals s
          JOIN ${unionRows()} ON rows.row_key = s.row_key ${whereOf(filtersFor(req, "basis"))} GROUP BY s.kind`,
    ),
    db.execute<Trio<"clk" | "bill" | "gsc">>(
      sql`SELECT count(*) FILTER (WHERE rows.rs_clk)::int AS clk,
                 count(*) FILTER (WHERE rows.rs_bill)::int AS bill,
                 count(*) FILTER (WHERE rows.rs_gsc)::int AS gsc
          FROM ${unionRows()} ${whereOf([sql`rows.platform = 'reelshort'`, ...filtersFor(req, "basis")])}`,
    ),
    db.execute<Trio<"pool" | "yes" | "no">>(
      sql`SELECT count(*) FILTER (WHERE ${POSTED_WHERE.pool})::int AS pool,
                 count(*) FILTER (WHERE ${POSTED_WHERE.yes})::int AS yes,
                 count(*) FILTER (WHERE ${POSTED_WHERE.no})::int AS no
          FROM ${unionRows()} ${whereOf(filtersFor(req, "posted"))}`,
    ),
  ]);
}

function known<K extends string>(
  rows: readonly Count[],
  keys: readonly K[],
): Partial<Record<K, number>> {
  return Object.fromEntries(
    rows
      .filter((r) => (keys as readonly string[]).includes(r.k))
      .map((r) => [r.k, r.n]),
  ) as Partial<Record<K, number>>;
}

/**
 * 四组 chips 的计数，各自不带自己那一维的过滤（选了一个剧场之后别的剧场仍要有数，
 * 否则人不知道换过去有没有行）。依据的计数按行数不按信号条数；ReelShort 三种依据一条 FILTER 查询数完。
 * 发布记录那一组一条查询三个 FILTER，与筛选共用 POSTED_WHERE。
 */
export async function loadFacets(req: PickRequest): Promise<PickFacets> {
  const [pl, la, ba, rs, po] = await facetQueries(req);
  const rsRow = rs.rows[0] ?? { clk: 0, bill: 0, gsc: 0 };
  const posted = po.rows[0] ?? { pool: 0, yes: 0, no: 0 };
  return {
    platforms: known(pl.rows, PLATFORMS),
    langs: la.rows.map((r) => ({ lang: r.k, n: r.n })),
    bases: {
      ...known(ba.rows, BASES),
      clk: rsRow.clk,
      bill: rsRow.bill,
      gsc: rsRow.gsc,
    },
    posted: { pool: posted.pool, yes: posted.yes, no: posted.no },
  };
}

/** 版本的 versions.freshness（即 meta.freshness），时间列转成 Date */
export interface PickFreshness {
  importedAt: Date | null;
  rows: number;
  withSignal: number;
  signals: number;
  posted: number;
  /** ReelShort：正典行数 / 候选数 / 指标最近采集时间 */
  rsCanonical: number;
  rsCandidates: number;
  rsSyncedAt: Date | null;
}

function count(value: unknown): number {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

/**
 * RealShort 导出时算好的新鲜度（versions.freshness 与 meta.freshness 同一份）转成页头要的形状。
 * 页面手上已经有 resolveVersion 带回来的 board.freshness 时直接用它，不必再查。
 */
export function freshnessOf(raw: unknown): PickFreshness {
  const f =
    typeof raw === "object" && raw !== null && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  return {
    importedAt: toTimestamp(f.importedAt),
    rows: count(f.rows),
    withSignal: count(f.withSignal),
    signals: count(f.signals),
    posted: count(f.posted),
    rsCanonical: count(f.rsCanonical),
    rsCandidates: count(f.rsCandidates),
    rsSyncedAt: toTimestamp(f.rsSyncedAt),
  };
}

/** 钉住版本的新鲜度（读一次 meta.freshness，按版本缓存） */
export async function loadFreshness(): Promise<PickFreshness> {
  return freshnessOf(await readVersionMeta("freshness"));
}

/**
 * 智能体候选池 N：有信号、未下架的剧单行加 ReelShort 候选行，与智能体取候选同一个口径（选剧 tab 默认视图的总数）。
 * 不等于 tab 徽标（徽标含已下架行）。只取决于版本，按版本缓存。
 */
export async function loadCandidatePool(): Promise<number> {
  return rememberForVersion("candidate-pool", async () => {
    const res = await getDb().execute<{ n: number }>(
      sql`SELECT ((SELECT count(*) FROM catalog_rows WHERE has_signal AND off_on IS NULL)
               + (SELECT count(*) FROM rs_rows WHERE has_signal AND off_on IS NULL))::int AS n`,
    );
    return Number(res.rows[0]?.n ?? 0);
  });
}

/** 证据页里对上的 ReelShort 行（取自 rs_ids），只取链接与集数要用的列 */
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
  sameTitle: SameTitleRow[];
  /** sameTitle 取回条数等于 SAME_TITLE_LIMIT：可能还有没取到的 */
  sameTitleTruncated: boolean;
  /**
   * 这个剧场当前版本的剧单里，集数 / 起付费两列有没有任何一行填了：
   * 分得清「这一行没填」与「这个剧场的剧单根本不给这一列」。只给布尔不给行数。
   */
  columnFilled: { episodes: boolean; payStart: boolean };
}

interface SiteDramaRaw extends Record<string, unknown> {
  id: string;
  locale: string;
  slug: string;
  title: string;
  chapter_count: number;
  pay_start: number;
}

function rowDetailParts(
  base: Omit<PickRow, "signals" | "posted">,
  titleKey: string,
) {
  const db = getDb();
  const { rowKey, platform, inSiteIds } = base;
  return Promise.all([
    loadSignalsFor([rowKey]),
    loadPostedFor([rowKey]),
    inSiteIds.length
      ? db.execute<SiteDramaRaw>(
          sql`SELECT id, locale, slug, title, chapter_count, pay_start FROM rs_ids WHERE id = ANY(${textArray(inSiteIds)}) ORDER BY locale, id`,
        )
      : Promise.resolve({ rows: [] as SiteDramaRaw[] }),
    db.execute(
      sql`SELECT ${POSTED_COLUMNS} FROM catalog_posted WHERE ${rowKey} = ANY(row_keys) ORDER BY last_post_on DESC NULLS LAST, sd`,
    ),
    titleKey
      ? db.execute<SameTitleRaw>(
          sql`SELECT row_key, platform, lang, title, title_cn, off_on FROM catalog_rows
              WHERE title_key = ${titleKey} AND row_key <> ${rowKey} ORDER BY platform, lang, row_key LIMIT ${sql.raw(String(SAME_TITLE_LIMIT))}`,
        )
      : Promise.resolve({ rows: [] as SameTitleRaw[] }),
    /* 碰到第一行填了的就停；全空的剧场要扫完它自己那几千行，证据页一次 */
    db.execute<{ episodes: unknown; pay_start: unknown }>(
      sql`SELECT EXISTS (SELECT 1 FROM catalog_rows WHERE platform = ${platform} AND episodes IS NOT NULL) AS episodes,
                 EXISTS (SELECT 1 FROM catalog_rows WHERE platform = ${platform} AND pay_start IS NOT NULL) AS pay_start`,
    ),
  ]);
}

/** 剧场行的证据页；ReelShort 行（行键 reelshort-<id>）走 queries-reelshort 的 loadReelshortDetail */
export async function loadRowDetail(rowKey: string): Promise<RowDetail | null> {
  const res = await getDb().execute<RawRow & { title_key: string }>(
    sql`SELECT ${ROW_COLUMNS}, title_key FROM catalog_rows WHERE row_key = ${rowKey}`,
  );
  const [raw] = res.rows;
  if (!raw) return null;
  const base = toRow(raw);
  const [signals, postedTags, siteRes, postedRes, sameRes, filledRes] =
    await rowDetailParts(base, raw.title_key);
  const filled = filledRes.rows[0];
  return {
    row: {
      ...base,
      signals: signals.get(rowKey) ?? [],
      posted: postedTags.get(rowKey) ?? [],
    },
    siteDramas: siteRes.rows.map((d) => ({
      id: d.id,
      locale: d.locale,
      slug: d.slug,
      title: d.title,
      chapterCount: d.chapter_count,
      payStart: d.pay_start,
    })),
    postedRecords: postedRes.rows.map(toPostedRecord),
    sameTitle: sameRes.rows.map(toSameTitle),
    sameTitleTruncated: sameRes.rows.length === SAME_TITLE_LIMIT,
    columnFilled: {
      episodes: filled?.episodes === true,
      payStart: filled?.pay_start === true,
    },
  };
}
