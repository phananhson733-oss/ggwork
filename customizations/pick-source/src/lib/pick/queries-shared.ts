import "server-only";

import { sql, type SQL } from "drizzle-orm";

import { getDb } from "@/db";
import type { ObserveRow } from "@/lib/observe/queries";
import { BASES, PLATFORMS, RS_ROW_PREFIX, type Basis, type Platform } from "./request";

/**
 * 选剧台三个查询文件共用的列清单、行转换与小工具。
 * 【对页面层不可见】：页面与组件只许 import `@/lib/pick/queries`（那边 `export *` 转出），
 * `tests/admin-contracts.test.ts` 钉着 pick 子树不许直接 import `@/lib/pick/queries-*`。
 */

/**
 * 证据页「同名剧场行」一次最多取几条（loadRowDetail 与 loadReelshortDetail 共用这一个数）。取回条数等于它就当被截断了：
 * 两个查询返回 sameTitleTruncated 标记，问答的投影与未取全提示读标记，不在 ask 层另写一份上限（gpt-6-astra 2026-09-13 审计 N4b）
 */
export const SAME_TITLE_LIMIT = 50;

export interface PickSignal {
  kind: Basis;
  ord: number;
  evidenceOn: string | null;
  rank: number | null;
  grade: string;
  note: string;
  payload: Record<string, unknown>;
}

/** 行上挂的发布记录标签（一行可能对上多条运营记录） */
export interface PickPostedTag {
  sd: string;
  life: string;
  scheduled: boolean;
  postCount: number;
  /** 待公开的帖子数（已排期未发的那一类），与 postCount 分开数 */
  schedCount: number;
  lastPostOn: string | null;
  viewsTotal: number;
}

export interface PickRow {
  rowKey: string;
  platform: Platform;
  sourceTable: string;
  title: string;
  titleCn: string;
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
  inSiteIds: string[];
  legacyOnly: boolean;
  siteOther: boolean;
  hasSignal: boolean;
  latestEvidenceOn: string | null;
  signals: PickSignal[];
  posted: PickPostedTag[];
  /** ReelShort 行的三个候选条件（剧单行恒 false）；只有经 union 查出来的行才有 */
  rsFlags?: { clk: boolean; bill: boolean; gsc: boolean };
  /** 三个条件各自的证据日 YYYY-MM-DD（剧单行恒 null）；同样只有经 union 查出来的行才有，目前只有只读 feed 读它 */
  rsFlagDates?: { clk: string | null; bill: string | null; gsc: string | null };
  /** ReelShort 行的指标（观测台同一条查询），剧单行没有 */
  rs?: ObserveRow;
}

export interface RawRow extends Record<string, unknown> {
  row_key: string;
  platform: string;
  source_table: string;
  title: string;
  title_cn: string;
  lang: string;
  kind: string;
  origin: string;
  tags: string;
  listed_on: string | null;
  pan_url: string;
  pan_pw: string;
  episodes: number | null;
  pay_start: number | null;
  youtube: boolean;
  merged_rows: number;
  off_on: string | null;
  reoff_note: string;
  in_site_ids: string[] | null;
  legacy_only: boolean;
  site_other: boolean;
  has_signal: boolean;
  latest_evidence_on: string | null;
  rs_clk?: boolean;
  rs_bill?: boolean;
  rs_gsc?: boolean;
  rs_clk_on?: string | null;
  rs_bill_on?: string | null;
  rs_gsc_on?: string | null;
  drama_id?: string | null;
}

/** 显示要用的列，主查询、证据页与榜单共用一份，避免几处各漏一列 */
const ROW_COLUMN_NAMES = [
  "row_key", "platform", "source_table", "title", "title_cn", "lang", "kind", "origin", "tags",
  "listed_on", "pan_url", "pan_pw", "episodes", "pay_start", "youtube", "merged_rows", "off_on", "reoff_note",
  "in_site_ids", "legacy_only", "site_other", "has_signal", "latest_evidence_on",
] as const;
export const ROW_COLUMNS = sql.raw(ROW_COLUMN_NAMES.join(", "));
/** 与 catalog_signals join 时要带表名，否则 row_key 二义 */
export const ROW_COLUMNS_QUALIFIED = sql.raw(ROW_COLUMN_NAMES.map((c) => `catalog_rows.${c}`).join(", "));

export function toRow(r: RawRow): Omit<PickRow, "signals" | "posted"> {
  return {
    rowKey: r.row_key,
    platform: (PLATFORMS as readonly string[]).includes(r.platform)
      ? (r.platform as Platform)
      : "dramabox",
    sourceTable: r.source_table,
    title: r.title,
    titleCn: r.title_cn,
    lang: r.lang,
    kind: r.kind,
    origin: r.origin,
    tags: r.tags,
    listedOn: r.listed_on,
    panUrl: r.pan_url,
    panPw: r.pan_pw,
    episodes: r.episodes,
    payStart: r.pay_start,
    youtube: r.youtube,
    mergedRows: r.merged_rows,
    offOn: r.off_on,
    reoffNote: r.reoff_note,
    inSiteIds: r.in_site_ids ?? [],
    legacyOnly: r.legacy_only,
    siteOther: r.site_other,
    hasSignal: r.has_signal,
    latestEvidenceOn: r.latest_evidence_on,
  };
}

/**
 * 把 JS 数组变成 SQL 的 text[] 字面量：drizzle 的模板会把数组展开成 ($1, $2, …) 这种 record，
 * 直接 `${keys}::text[]` 会报 cannot cast type record to text[]。这里的数组最多一页（200 个键）。
 */
export function textArray(values: string[]): SQL {
  if (values.length === 0) return sql`ARRAY[]::text[]`;
  return sql`ARRAY[${sql.join(values.map((v) => sql`${v}`), sql`, `)}]::text[]`;
}

export function whereOf(filters: SQL[]): SQL {
  return filters.length ? sql`WHERE ${sql.join(filters, sql` AND `)}` : sql``;
}

interface SignalRaw extends Record<string, unknown> {
  row_key: string;
  kind: string;
  ord: number;
  evidence_on: string | null;
  rank: number | null;
  grade: string;
  note: string;
  payload: Record<string, unknown> | null;
}

export async function loadSignalsFor(rowKeys: string[]): Promise<Map<string, PickSignal[]>> {
  const out = new Map<string, PickSignal[]>();
  if (rowKeys.length === 0) return out;
  const db = getDb();
  const res = await db.execute(
    sql`SELECT row_key, kind, ord, evidence_on, rank, grade, note, payload FROM catalog_signals
        WHERE row_key = ANY(${textArray(rowKeys)}) ORDER BY row_key, ord`,
  );
  for (const r of res.rows as SignalRaw[]) {
    if (!(BASES as readonly string[]).includes(r.kind)) continue;
    const arr = out.get(r.row_key) ?? [];
    arr.push({
      kind: r.kind as Basis,
      ord: r.ord,
      evidenceOn: r.evidence_on,
      rank: r.rank,
      grade: r.grade,
      note: r.note,
      payload: r.payload ?? {},
    });
    out.set(r.row_key, arr);
  }
  return out;
}

interface PostedTagRaw extends Record<string, unknown> {
  row_key: string;
  sd: string;
  life: string;
  scheduled: boolean;
  post_count: number;
  sched_count: number;
  last_post_on: string | null;
  views_total: number;
}

export async function loadPostedFor(rowKeys: string[]): Promise<Map<string, PickPostedTag[]>> {
  const out = new Map<string, PickPostedTag[]>();
  if (rowKeys.length === 0) return out;
  const db = getDb();
  /* 把 row_keys 数组展开成 (row_key, sd) 对，只要这一页的行；ReelShort 行按 drama_ids 对，行键前缀 reelshort- */
  const cols = sql.raw("p.sd, p.life, p.scheduled, p.post_count, p.sched_count, p.last_post_on, p.views_total");
  const res = await db.execute(
    sql`SELECT * FROM (
          SELECT k.row_key, ${cols} FROM catalog_posted p, unnest(p.row_keys) AS k(row_key)
          WHERE k.row_key = ANY(${textArray(rowKeys)})
          UNION ALL
          SELECT ${RS_ROW_PREFIX} || k.id AS row_key, ${cols} FROM catalog_posted p, unnest(p.drama_ids) AS k(id)
          WHERE ${RS_ROW_PREFIX} || k.id = ANY(${textArray(rowKeys)})
        ) u ORDER BY u.last_post_on DESC NULLS LAST, u.sd`,
  );
  for (const r of res.rows as PostedTagRaw[]) {
    const arr = out.get(r.row_key) ?? [];
    arr.push({
      sd: r.sd,
      life: r.life,
      scheduled: r.scheduled,
      postCount: r.post_count,
      schedCount: r.sched_count,
      lastPostOn: r.last_post_on,
      viewsTotal: r.views_total,
    });
    out.set(r.row_key, arr);
  }
  return out;
}

