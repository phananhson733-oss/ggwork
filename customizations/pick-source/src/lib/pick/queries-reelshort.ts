import "server-only";

import { sql, type SQL } from "drizzle-orm";

import { getDb } from "@/db";
import { publicCanonical } from "@/lib/queries";
import {
  billBySibling,
  candidateFilter,
  clicks7BySibling,
  loadDramaDetail,
  type DramaDetail,
} from "@/lib/observe/queries";

import { readSources } from "@/lib/observe/source-state";

import { LANG_LOC } from "./catalog-import";
import { POSTED_COLUMNS, toPostedRecord, type PostedRecord } from "./queries-posted";
import { ROW_COLUMNS, SAME_TITLE_LIMIT, textArray, toRow, type PickRow, type RawRow } from "./queries-shared";
import { PLATFORM_LABELS, PLATFORMS, RS_ROW_PREFIX, type Platform } from "./request";

/**
 * ReelShort 作为第十个剧场（2026-09-11 观测台并入选剧台）。
 *
 * 【行是查询时从 dramas 投影出来的，不落成第 14 张表、也不在导入时写死】：dramas 每 6 小时变、
 * 出站实时、账单每日，落一份副本只会得到一份必然过期的剧单。代价是选剧 / 全部剧库 tab 每次多跑一段
 * 与观测台同口径的子查询（观测台实测 count 300–760 ms），这一页一天开几次，可接受。
 *
 * 【候选口径与榜单逐字相同】：has_signal = candidateFilter()（搜索有展示 / 近 7 天有过滤后出站 / 有订单），
 * b / c 两段子查询直接用 lib/observe/queries 导出的那两份。这个文件是选剧台 lib 里唯一允许
 * import 观测台查询的两处之一（另一处 queries-rank.ts），方向单向，合同测试钉着。
 */

/** 剧单侧 lang 是中文名，ReelShort 行按 LANG_LOC 反查成同一套；查不到的 locale 码原样显示 */
function langValues(): SQL {
  const pairs = Object.entries(LANG_LOC).map(([cn, loc]) => sql`(${loc}, ${cn})`);
  return sql`(VALUES ${sql.join(pairs, sql`, `)}) AS ll(loc, lang)`;
}

/** GSC 采集日：有展示时取 search_data_at 的 UTC 日（YYYY-MM-DD 文本），latest_evidence_on 与 rs_gsc_on 共用这一份 */
function gscOn(): SQL {
  return sql`CASE WHEN dramas.search_impressions > 0 THEN to_char((dramas.search_data_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD') END`;
}

/**
 * 与 catalog_rows 同形的一段 SELECT（列名与 ROW_COLUMNS 一一对应，外加 rs_clk / rs_bill / rs_gsc /
 * rs_clk_on / rs_bill_on / rs_gsc_on / drama_id / title_key），供 queries.ts 的 UNION ALL 用。
 * - listed_on = 上线日期（publish_at 的 UTC 日），没有就 null；
 * - latest_evidence_on = 最近出站日 / 最近账单日 / GSC 采集日里最近的一条，GREATEST 会忽略 NULL；
 * - rs_clk_on / rs_bill_on / rs_gsc_on = 这三个日期各自单列（都是 YYYY-MM-DD 文本），只读 feed 给三个条件信号填 observed_at；
 * - youtube = true：ReelShort 规则表是「YouTube 可发」，导流走本站付费墙；
 * - 网盘 / 下架 / 同名匹配三组列给空：它自己就是本站在售的那一行。
 * 传了 asOf 时 b / c 两段（账单窗口、7 天出站）钉在 asOf 上（feed v2 导出，方案 4.2）；不传时 SQL 与改动前逐字相同。
 */
export function reelshortBranch(asOf?: Date): SQL {
  return sql`SELECT ${RS_ROW_PREFIX} || dramas.id AS row_key,
      'reelshort' AS platform,
      '本站 CPS 片库' AS source_table,
      dramas.title AS title,
      '' AS title_cn,
      COALESCE(ll.lang, dramas.locale) AS lang,
      '' AS kind,
      '' AS origin,
      COALESCE(array_to_string((dramas.tags)[1:6], ' · '), '') AS tags,
      to_char((dramas.publish_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD') AS listed_on,
      '' AS pan_url,
      '' AS pan_pw,
      dramas.chapter_count AS episodes,
      NULLIF(dramas.pay_start, 0) AS pay_start,
      true AS youtube,
      1 AS merged_rows,
      NULL::text AS off_on,
      '' AS reoff_note,
      ARRAY[]::text[] AS in_site_ids,
      false AS legacy_only,
      false AS site_other,
      ${candidateFilter()} AS has_signal,
      GREATEST(c.last_on, b.last_on, ${gscOn()}) AS latest_evidence_on,
      COALESCE(c.n, 0) > 0 AS rs_clk,
      COALESCE(b.orders, 0) > 0 AS rs_bill,
      dramas.search_impressions > 0 AS rs_gsc,
      c.last_on AS rs_clk_on,
      b.last_on AS rs_bill_on,
      ${gscOn()} AS rs_gsc_on,
      dramas.id AS drama_id,
      dramas.title_key AS title_key
    FROM dramas
    LEFT JOIN (${billBySibling(asOf)}) b ON b.book_id = dramas.id
    LEFT JOIN (${clicks7BySibling(asOf)}) c ON c.drama_id = dramas.id
    LEFT JOIN ${langValues()} ON ll.loc = dramas.locale
    WHERE ${publicCanonical()}`;
}

/** 剧单侧的同形 SELECT：三个 rs 布尔恒 false、三个 rs 日期恒 NULL、drama_id 为空（列序与 reelshortBranch 逐位对齐） */
export function catalogBranch(): SQL {
  return sql`SELECT ${ROW_COLUMNS}, false AS rs_clk, false AS rs_bill, false AS rs_gsc,
      NULL::text AS rs_clk_on, NULL::text AS rs_bill_on, NULL::text AS rs_gsc_on,
      NULL::text AS drama_id, title_key
    FROM catalog_rows`;
}

/** 两段 UNION ALL 起来的「十个剧场」视图，外层一律用别名 rows 引用；asOf 只影响 ReelShort 分支（剧单三张表没有时间维度） */
export function unionRows(asOf?: Date): SQL {
  return sql`(${catalogBranch()} UNION ALL ${reelshortBranch(asOf)}) AS rows`;
}

/** ReelShort 行的选剧台形态：与 catalog 行同一个 PickRow，指标挂在 rs 上（由 loadRowsByIds 填） */
export interface ReelshortDetail extends DramaDetail {
  /** 剧场剧单里对上这部剧的行（catalog_rows.in_site_ids 含它），一律标同名未核 */
  sameTitle: { rowKey: string; platform: Platform; lang: string; title: string; titleCn: string; offOn: string | null }[];
  /** sameTitle 取回条数等于 SAME_TITLE_LIMIT：可能还有没取到的（问答的未取全提示读它） */
  sameTitleTruncated: boolean;
  /** 运营发布记录里对上这部剧的记录 */
  postedRecords: PostedRecord[];
}

/**
 * ReelShort 行的证据页：观测台单剧页的四条查询（指标 / 90 天曲线 / 14 天出站 / 分成明细）
 * + 同名剧场行 + 发布记录。id 是 book_id；loadDramaDetail 会把非正典 id 归到同组同语种正典行。
 * asOf 只传给 loadDramaDetail（出站与曲线）；同名行与发布记录读剧单表，没有时间维度。
 */
export async function loadReelshortDetail(id: string, asOf?: Date): Promise<ReelshortDetail | null> {
  const detail = await loadDramaDetail(id, asOf);
  if (!detail) return null;
  const db = getDb();
  const ids = [id, detail.row.id].filter((v, i, a) => a.indexOf(v) === i);
  const [sameRes, postedRes] = await Promise.all([
    db.execute(
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
    sameTitle: (
      sameRes.rows as { row_key: string; platform: string; lang: string; title: string; title_cn: string; off_on: string | null }[]
    ).map((s) => ({
      rowKey: s.row_key,
      platform: (PLATFORMS as readonly string[]).includes(s.platform) ? (s.platform as Platform) : "dramabox",
      lang: s.lang,
      title: s.title,
      titleCn: s.title_cn,
      offOn: s.off_on,
    })),
    sameTitleTruncated: sameRes.rows.length === SAME_TITLE_LIMIT,
    postedRecords: postedRes.rows.map(toPostedRecord),
  };
}

/** 页面上区分两类证据页用的判别：给 ReelShort 行造一个「空」PickRow 时 sourceTable 的文案 */
export const RS_SOURCE_TABLE = "本站 CPS 片库";
export const RS_PLATFORM_LABEL = PLATFORM_LABELS.reelshort;

/** toRow 之后再套一层：rs 三个布尔与各自的证据日从 RawRow 的可选列上取（榜单 / 证据页那些不经 union 的查询没有这几列） */
export function toRowWithFlags(r: RawRow): Omit<PickRow, "signals" | "posted"> {
  return {
    ...toRow(r),
    rsFlags: { clk: Boolean(r.rs_clk), bill: Boolean(r.rs_bill), gsc: Boolean(r.rs_gsc) },
    rsFlagDates: { clk: r.rs_clk_on ?? null, bill: r.rs_bill_on ?? null, gsc: r.rs_gsc_on ?? null },
  };
}

/** 页底「数据来源与口径」里四条采集来源的状态（只读 observe_sources，便宜） */
export const loadObserveSources = readSources;

/**
 * 观测台那几个行类型经这里转出：pick 的组件树只许 import `@/lib/pick/queries` 这一个入口
 * （tests/admin-contracts.test.ts），连类型也不例外——白名单按文本匹配，开一个「只是类型」的口子等于没有白名单。
 */
export type { BillRow, BillTotals, DramaDetail, ObserveRow } from "@/lib/observe/queries";
