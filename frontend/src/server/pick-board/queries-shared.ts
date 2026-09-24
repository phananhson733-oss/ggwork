// PORTED_FROM: realshort@816ca2e src/lib/pick/queries-shared.ts
// 本地改动：网盘两列换成 has_pan / hasPan（链接与提取码不进镜像，只知道有没有）；ROW_COLUMN_NAMES 导出；
// 同名核对行具名为 SameTitleRow；分页结果具名为 RowsPage<T>；时间列的解析（原 observe/queries.ts 的 toTimestamp
// 与 pick/queries.ts 的 toDate）合成一个 toTimestamp 放在这里，几个查询文件共用。
import "server-only";

import { sql, type SQL } from "drizzle-orm";

import {
  BASES,
  PLATFORMS,
  RS_ROW_PREFIX,
  type Basis,
  type Platform,
} from "@/core/pick-board/request";

import { getDb } from "./db";
import { type ObserveRow } from "./rs-queries";

/**
 * 选剧台几个查询文件共用的列清单、行转换与小工具。页面与组件只经 `@/server/pick-board` 取类型
 * （组件一律 `import type`），不直接 import 这里。
 * 【所有查询都裁列】：只取显示要用的列。
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

export const ROW_COLUMNS = sql.raw(ROW_COLUMN_NAMES.join(", "));
/** 与 catalog_signals join 时要带表名，否则 row_key 二义 */
export const ROW_COLUMNS_QUALIFIED = sql.raw(
  ROW_COLUMN_NAMES.map((c) => `catalog_rows.${c}`).join(", "),
);

/** 不认识的剧场键（RealShort 新增了剧场）照原样回落成 dramabox，页面靠「规则漂移」横幅提示 */
export function platformOf(raw: string): Platform {
  return (PLATFORMS as readonly string[]).includes(raw)
    ? (raw as Platform)
    : "dramabox";
}

export function toRow(r: RawRow): Omit<PickRow, "signals" | "posted"> {
  return {
    rowKey: r.row_key,
    platform: platformOf(r.platform),
    sourceTable: r.source_table,
    title: r.title,
    titleCn: r.title_cn,
    lang: r.lang,
    kind: r.kind,
    origin: r.origin,
    tags: r.tags,
    listedOn: r.listed_on,
    hasPan: r.has_pan,
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
 * 一列 timestamptz 归一成 Date。读连接把时间类型按文本返回（db.ts 的 TEXT_OIDS），形如
 * `2026-09-01 06:54:13.838991+00`：Postgres 的 `+00` 不是合法的 ISO 偏移，要补成 `+00:00` 再解析。
 * 已经是 Date 的原样用；解析不出来一律 null（「日期未知」有明确的渲染，Invalid Date 会让 toISOString 抛错）。
 */
export function toTimestamp(value: unknown): Date | null {
  if (value === null || value === undefined) return null;
  if (value instanceof Date)
    return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value !== "string" || value === "") return null;
  const iso = value.replace(" ", "T").replace(/([+-]\d{2})$/, "$1:00");
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/**
 * 把 JS 数组变成 SQL 的 text[] 字面量：drizzle 的模板会把数组展开成 ($1, $2, …) 这种 record，
 * 直接 `${keys}::text[]` 会报 cannot cast type record to text[]。这里的数组最多一页（200 个键）。
 */
export function textArray(values: readonly string[]): SQL {
  if (values.length === 0) return sql`ARRAY[]::text[]`;
  return sql`ARRAY[${sql.join(
    values.map((v) => sql`${v}`),
    sql`, `,
  )}]::text[]`;
}

export function whereOf(filters: readonly SQL[]): SQL {
  return filters.length
    ? sql`WHERE ${sql.join([...filters], sql` AND `)}`
    : sql``;
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

function isBasis(kind: string): kind is Basis {
  return (BASES as readonly string[]).includes(kind);
}

function toSignal(r: SignalRaw & { kind: Basis }): PickSignal {
  return {
    kind: r.kind,
    ord: r.ord,
    evidenceOn: r.evidence_on,
    rank: r.rank,
    grade: r.grade,
    note: r.note,
    payload: r.payload ?? {},
  };
}

/** 按键分组；组与组内都保持查询给的顺序（一页最多几百项） */
export function groupBy<T>(
  items: readonly T[],
  keyOf: (item: T) => string,
): Map<string, T[]> {
  const keys = [...new Set(items.map(keyOf))];
  return new Map(
    keys.map((key) => [key, items.filter((item) => keyOf(item) === key)]),
  );
}

/** 一页行的信号；不认识的信号种类（RealShort 新增的）不显示 */
export async function loadSignalsFor(
  rowKeys: readonly string[],
): Promise<Map<string, PickSignal[]>> {
  if (rowKeys.length === 0) return new Map();
  const res = await getDb().execute<SignalRaw>(
    sql`SELECT row_key, kind, ord, evidence_on, rank, grade, note, payload FROM catalog_signals
        WHERE row_key = ANY(${textArray(rowKeys)}) ORDER BY row_key, ord`,
  );
  const known = res.rows.filter((r): r is SignalRaw & { kind: Basis } =>
    isBasis(r.kind),
  );
  const grouped = groupBy(known, (r) => r.row_key);
  return new Map(
    [...grouped].map(([key, rows]) => [key, rows.map(toSignal)] as const),
  );
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

function toPostedTag(r: PostedTagRaw): PickPostedTag {
  return {
    sd: r.sd,
    life: r.life,
    scheduled: r.scheduled,
    postCount: r.post_count,
    schedCount: r.sched_count,
    lastPostOn: r.last_post_on,
    viewsTotal: r.views_total,
  };
}

/** 一页行对上的发布记录标签；ReelShort 行按 drama_ids 对（行键前缀 reelshort-） */
export async function loadPostedFor(
  rowKeys: readonly string[],
): Promise<Map<string, PickPostedTag[]>> {
  if (rowKeys.length === 0) return new Map();
  /* 把 row_keys 数组展开成 (row_key, sd) 对，只要这一页的行 */
  const cols = sql.raw(
    "p.sd, p.life, p.scheduled, p.post_count, p.sched_count, p.last_post_on, p.views_total",
  );
  const res = await getDb().execute<PostedTagRaw>(
    sql`SELECT * FROM (
          SELECT k.row_key, ${cols} FROM catalog_posted p, unnest(p.row_keys) AS k(row_key)
          WHERE k.row_key = ANY(${textArray(rowKeys)})
          UNION ALL
          SELECT ${RS_ROW_PREFIX} || k.id AS row_key, ${cols} FROM catalog_posted p, unnest(p.drama_ids) AS k(id)
          WHERE ${RS_ROW_PREFIX} || k.id = ANY(${textArray(rowKeys)})
        ) u ORDER BY u.last_post_on DESC NULLS LAST, u.sd`,
  );
  const grouped = groupBy(res.rows, (r) => r.row_key);
  return new Map(
    [...grouped].map(([key, rows]) => [key, rows.map(toPostedTag)] as const),
  );
}
