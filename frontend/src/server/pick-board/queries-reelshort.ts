// PORTED_FROM: realshort@816ca2e src/lib/pick/queries-reelshort.ts
// 本地改动：ReelShort 分支改读镜像 rs_rows（导出时已算好候选条件、三个证据日与中文语种名，不再在 SQL 里
// 拼语种表、也不再现算账单与出站的子查询）；loadReelshortDetail 先经 rs_ids 把任意 id 归到正典 id，
// canonical_id 为 NULL 或查不到时返回 null（批判 B21）；来源状态取 meta.sources；同名行元素具名为 SameTitleRow。
import "server-only";

import { sql, type SQL } from "drizzle-orm";

import { getDb } from "./db";
import {
  POSTED_COLUMNS,
  toPostedRecord,
  type PostedRecord,
} from "./queries-posted";
import {
  platformOf,
  ROW_COLUMNS,
  SAME_TITLE_LIMIT,
  textArray,
  toRow,
  type PickRow,
  type RawRow,
  type SameTitleRow,
} from "./queries-shared";
import { loadDramaDetail, type DramaDetail } from "./rs-queries";
import { readSources } from "./source-state";

/**
 * ReelShort 作为第十个剧场：选剧 / 全部剧库 tab 的 FROM 是 catalog_rows UNION ALL rs_rows。
 * rs_rows 就是 RealShort 导出时按原来的 reelshortBranch 投影好的公开正典行（列名、列序与 catalog_rows 同形），
 * 外层一律用别名 rows 引用。证据页是 rs-queries 单剧页的四条查询，加同名剧场行与发布记录。
 */

/** rs_rows 的同形 SELECT，外加 rs_clk / rs_bill / rs_gsc、三个证据日、drama_id 与 title_key */
export function reelshortBranch(): SQL {
  return sql`SELECT ${ROW_COLUMNS}, rs_clk, rs_bill, rs_gsc, rs_clk_on, rs_bill_on, rs_gsc_on, drama_id, title_key
    FROM rs_rows`;
}

/** 剧单侧的同形 SELECT：三个 rs 布尔恒 false、三个 rs 日期恒 NULL、drama_id 为空（列序与 reelshortBranch 逐位对齐） */
export function catalogBranch(): SQL {
  return sql`SELECT ${ROW_COLUMNS}, false AS rs_clk, false AS rs_bill, false AS rs_gsc,
      NULL::text AS rs_clk_on, NULL::text AS rs_bill_on, NULL::text AS rs_gsc_on,
      NULL::text AS drama_id, title_key
    FROM catalog_rows`;
}

/** 两段 UNION ALL 起来的「十个剧场」视图，外层一律用别名 rows 引用 */
export function unionRows(): SQL {
  return sql`(${catalogBranch()} UNION ALL ${reelshortBranch()}) AS rows`;
}

export interface ReelshortDetail extends DramaDetail {
  /** 剧场剧单里对上这部剧的行（catalog_rows.in_site_ids 含它），一律标同名未核 */
  sameTitle: SameTitleRow[];
  /** sameTitle 取回条数等于 SAME_TITLE_LIMIT：可能还有没取到的 */
  sameTitleTruncated: boolean;
  /** 运营发布记录里对上这部剧的记录 */
  postedRecords: PostedRecord[];
}

export interface SameTitleRaw extends Record<string, unknown> {
  row_key: string;
  platform: string;
  lang: string;
  title: string;
  title_cn: string;
  off_on: string | null;
}

export function toSameTitle(s: SameTitleRaw): SameTitleRow {
  return {
    rowKey: s.row_key,
    platform: platformOf(s.platform),
    lang: s.lang,
    title: s.title,
    titleCn: s.title_cn,
    offOn: s.off_on,
  };
}

/** 任意 book_id 归到的正典 id（rs_ids.canonical_id，与导出同一口径）；归不上是 null */
async function canonicalOf(id: string): Promise<string | null> {
  const res = await getDb().execute<{ canonical_id: string | null }>(
    sql`SELECT canonical_id FROM rs_ids WHERE id = ${id}`,
  );
  return res.rows[0]?.canonical_id ?? null;
}

/**
 * ReelShort 行的证据页：单剧页的四条查询（指标 / 90 天曲线 / 14 天出站 / 订单明细）加同名剧场行与发布记录。
 * id 是 book_id，可以不是正典 id（经 rs_ids 归到正典行）；同名行与发布记录两个 id 都认。
 */
export async function loadReelshortDetail(
  id: string,
): Promise<ReelshortDetail | null> {
  const canonicalId = await canonicalOf(id);
  if (!canonicalId) return null;
  const detail = await loadDramaDetail(canonicalId);
  if (!detail) return null;
  const db = getDb();
  const ids = [...new Set([id, detail.row.id])];
  const [sameRes, postedRes] = await Promise.all([
    db.execute<SameTitleRaw>(
      sql`SELECT row_key, platform, lang, title, title_cn, off_on FROM catalog_rows
          WHERE in_site_ids && ${textArray(ids)} ORDER BY platform, lang, row_key LIMIT ${sql.raw(String(SAME_TITLE_LIMIT))}`,
    ),
    db.execute(
      sql`SELECT ${POSTED_COLUMNS} FROM catalog_posted WHERE drama_ids && ${textArray(ids)}
          ORDER BY last_post_on DESC NULLS LAST, sd`,
    ),
  ]);
  return {
    ...detail,
    sameTitle: sameRes.rows.map(toSameTitle),
    sameTitleTruncated: sameRes.rows.length === SAME_TITLE_LIMIT,
    postedRecords: postedRes.rows.map(toPostedRecord),
  };
}

/** toRow 之后再套一层：rs 三个布尔与各自的证据日从 RawRow 的可选列上取（不经 union 的查询没有这几列） */
export function toRowWithFlags(r: RawRow): Omit<PickRow, "signals" | "posted"> {
  return {
    ...toRow(r),
    rsFlags: {
      clk: Boolean(r.rs_clk),
      bill: Boolean(r.rs_bill),
      gsc: Boolean(r.rs_gsc),
    },
    rsFlagDates: {
      clk: r.rs_clk_on ?? null,
      bill: r.rs_bill_on ?? null,
      gsc: r.rs_gsc_on ?? null,
    },
  };
}

/** 页底「数据来源与口径」里各采集来源的状态 */
export const loadObserveSources = readSources;
