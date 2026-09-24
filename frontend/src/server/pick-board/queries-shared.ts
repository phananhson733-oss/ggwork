// PORTED_FROM: realshort@816ca2e src/lib/pick/queries-shared.ts
// 本地改动：网盘两列换成 has_pan / hasPan（链接与提取码不进镜像，只知道有没有）；ROW_COLUMN_NAMES 导出；
// 同名核对行具名为 SameTitleRow；分页结果具名为 RowsPage<T>。PickFacets / PickFreshness / SiteDrama / RowDetail
// 原在 queries.ts，P3-3a 先放这里：queries.ts 一落地就会打开 P3-2 的源码形状门控，要等 P3-3 连同实现一起落。
// P3-3a 只有类型与列清单；列清单派生的 SQL 片段、行转换与 loadSignalsFor / loadPostedFor 由 P3-3 补。
import "server-only";

import {
  type Basis,
  type Platform,
  type PostedFilter,
} from "@/core/pick-board/request";

import { type PostedRecord } from "./queries-posted";
import { type ObserveRow } from "./rs-queries";

/**
 * 选剧台几个查询文件共用的列清单与行形状。页面与组件只经 `@/server/pick-board` 取类型
 * （组件一律 `import type`），不直接 import 这里。
 */

/**
 * 证据页「同名剧场行」一次最多取几条（loadRowDetail 与 loadReelshortDetail 共用这一个数）。
 * 取回条数等于它就当被截断了，两个查询返回 sameTitleTruncated 标记。
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
  /** 这一行剧单附没附网盘。链接与提取码不同步到镜像，要看就到 RealShort 证据页 */
  hasPan: boolean;
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
  /** 三个条件各自的证据日 YYYY-MM-DD（剧单行恒 null）；同样只有经 union 查出来的行才有 */
  rsFlagDates?: {
    clk: string | null;
    bill: string | null;
    gsc: string | null;
  };
  /** ReelShort 行的指标（与 rs 榜同一条查询），剧单行没有 */
  rs?: ObserveRow;
}

/** catalog_rows 与 rs_rows 的同形列（外加 union 时 ReelShort 分支才有的几列），类型照镜像 DDL */
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
  has_pan: boolean;
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

/**
 * 显示要用的列，主查询、证据页与榜单共用一份，避免几处各漏一列。
 * 列序固定：UNION ALL 两边按位对齐，has_pan 占原来两列网盘的位置（listed_on 之后）。
 */
export const ROW_COLUMN_NAMES = [
  "row_key",
  "platform",
  "source_table",
  "title",
  "title_cn",
  "lang",
  "kind",
  "origin",
  "tags",
  "listed_on",
  "has_pan",
  "episodes",
  "pay_start",
  "youtube",
  "merged_rows",
  "off_on",
  "reoff_note",
  "in_site_ids",
  "legacy_only",
  "site_other",
  "has_signal",
  "latest_evidence_on",
] as const satisfies readonly (keyof RawRow)[];

/** 分页的一页：rows 是这一页，total 是同一组 WHERE 的全量计数 */
export interface RowsPage<T> {
  rows: T[];
  total: number;
  hasMore: boolean;
}

/** 同名核对：同 title_key（或对上同一部 ReelShort 剧）的其它剧场行，一律标「未核」 */
export interface SameTitleRow {
  rowKey: string;
  platform: Platform;
  lang: string;
  title: string;
  titleCn: string;
  offOn: string | null;
}

export interface PickFacets {
  platforms: Partial<Record<Platform, number>>;
  langs: { lang: string; n: number }[];
  bases: Partial<Record<Basis, number>>;
  /** 未发过 / 发过 / 在选剧池 各有几行；「不限」不计 */
  posted: Record<Exclude<PostedFilter, "">, number>;
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
