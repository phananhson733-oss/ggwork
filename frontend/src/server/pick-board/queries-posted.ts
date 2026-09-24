// PORTED_FROM: realshort@816ca2e src/lib/pick/queries-posted.ts
// 本地改动：记录对上的 ReelShort 剧改从 rs_ids 取（原 dramas）；单条记录页的返回具名为 PostedRecordDetail；
// 统计里的最近导入时间用 toTimestamp 解析（读连接按文本返回 timestamptz，`+00` 要补成 `+00:00`）。
import "server-only";

import { sql, type SQL } from "drizzle-orm";

import { type RawPost } from "@/core/pick-board/catalog-lang";
import {
  type PickRequest,
  type Platform,
  type PostedState,
} from "@/core/pick-board/request";

import { getDb } from "./db";
import { platformOf, textArray, toTimestamp } from "./queries-shared";

/**
 * 发布记录 tab：运营「选剧池 / 发布记录」的全表视图，加账号台账与单条记录页。
 * 已发 = post_count > 0，已排期未发 = post_count = 0 且 sched_count > 0，未排期 = 两个都是 0，
 * 剧库未对上 = row_keys 与 drama_ids 都空。四个筛选与 chips 计数共用同一组条件。
 */

export interface PostedRecord {
  sd: string;
  feishuRecord: string;
  title: string;
  lang: string;
  platform: string;
  life: string;
  scheduled: boolean;
  onlineOn: string | null;
  why: string;
  note: string;
  archived: boolean;
  postCount: number;
  schedCount: number;
  firstPostOn: string | null;
  lastPostOn: string | null;
  viewsTotal: number;
  viewsCount: number;
  metricAt: string | null;
  sources: string[];
  cats: string[];
  who: string[];
  accounts: string[];
  createdOn: string | null;
  updatedOn: string | null;
  rowKeys: string[];
  dramaIds: string[];
  /** 帖子明细；url 只在以 https:// 开头时渲染成链接 */
  posts: RawPost[];
}

/** 记录对上的剧场行，只取链接与一行显示要用的列 */
export interface LinkedRow {
  rowKey: string;
  platform: Platform;
  lang: string;
  title: string;
  offOn: string | null;
}

/** 记录对上的 ReelShort 剧（rs_ids） */
export interface LinkedDrama {
  id: string;
  locale: string;
  slug: string;
  title: string;
}

export interface PostedLinks {
  rows: Map<string, LinkedRow>;
  dramas: Map<string, LinkedDrama>;
}

export interface PostedList {
  rows: PostedRecord[];
  total: number;
  hasMore: boolean;
  /** 各状态多少条（带当前搜索词，不带当前状态筛选）；"" 是全部 */
  counts: Record<PostedState, number>;
  links: PostedLinks;
}

export interface PostedRecordDetail {
  record: PostedRecord;
  links: PostedLinks;
}

export interface PostedStats {
  total: number;
  pubCount: number;
  postsSum: number;
  viewsSum: number;
  /** 帖子指标（播放数）最晚回填到哪天 */
  metricAt: string | null;
  importedAt: Date | null;
  accountCount: number;
}

export interface CatalogAccount {
  id: string;
  name: string;
  /** 账号主页；只在以 https:// 开头时渲染成链接 */
  url: string;
  grp: string;
  form: string;
  niche: string;
  status: string;
  fans: number | null;
  asOf: string | null;
}

interface PostedRecordRaw extends Record<string, unknown> {
  sd: string;
  feishu_record: string;
  title: string;
  lang: string;
  platform: string;
  life: string;
  scheduled: boolean;
  online_on: string | null;
  why: string;
  note: string;
  archived: boolean;
  post_count: number;
  sched_count: number;
  first_post_on: string | null;
  last_post_on: string | null;
  views_total: number;
  views_count: number;
  metric_at: string | null;
  sources: string[] | null;
  cats: string[] | null;
  who: string[] | null;
  accounts: string[] | null;
  created_on: string | null;
  updated_on: string | null;
  row_keys: string[] | null;
  drama_ids: string[] | null;
  posts: RawPost[] | null;
}

/** 发布记录的列清单：列表、单条记录与证据页上「对上的发布记录」卡片读同一份 */
export const POSTED_COLUMNS = sql.raw(
  [
    "sd",
    "feishu_record",
    "title",
    "lang",
    "platform",
    "life",
    "scheduled",
    "online_on",
    "why",
    "note",
    "archived",
    "post_count",
    "sched_count",
    "first_post_on",
    "last_post_on",
    "views_total",
    "views_count",
    "metric_at",
    "sources",
    "cats",
    "who",
    "accounts",
    "created_on",
    "updated_on",
    "row_keys",
    "drama_ids",
    "posts",
  ].join(", "),
);

export function toPostedRecord(p: Record<string, unknown>): PostedRecord {
  const r = p as PostedRecordRaw;
  return {
    sd: r.sd,
    feishuRecord: r.feishu_record,
    title: r.title,
    lang: r.lang,
    platform: r.platform,
    life: r.life,
    scheduled: r.scheduled,
    onlineOn: r.online_on,
    why: r.why,
    note: r.note,
    archived: r.archived,
    postCount: r.post_count,
    schedCount: r.sched_count,
    firstPostOn: r.first_post_on,
    lastPostOn: r.last_post_on,
    viewsTotal: r.views_total,
    viewsCount: r.views_count,
    metricAt: r.metric_at,
    sources: r.sources ?? [],
    cats: r.cats ?? [],
    who: r.who ?? [],
    accounts: r.accounts ?? [],
    createdOn: r.created_on,
    updatedOn: r.updated_on,
    rowKeys: r.row_keys ?? [],
    dramaIds: r.drama_ids ?? [],
    posts: r.posts ?? [],
  };
}

/** 四个状态的条件：筛选与 chips 计数共用这一份，两边漂开的症状是「chip 上写 3，点进去 1 条」 */
const STATE_WHERE: Record<Exclude<PostedState, "">, SQL> = {
  pub: sql`post_count > 0`,
  sched: sql`post_count = 0 AND sched_count > 0`,
  none: sql`post_count = 0 AND sched_count = 0`,
  nomatch: sql`cardinality(row_keys) = 0 AND cardinality(drama_ids) = 0`,
};

function searchWhere(q: string): SQL | null {
  if (!q) return null;
  /* 剧名 / 编号 / 理由 / 备注 / 来源 / 账号都搜。ILIKE 没有索引，几十行、人工搜索 */
  const like = `%${q}%`;
  return sql`(title ILIKE ${like} OR sd ILIKE ${like} OR why ILIKE ${like} OR note ILIKE ${like}
    OR platform ILIKE ${like} OR lang ILIKE ${like} OR life ILIKE ${like}
    OR array_to_string(sources, ' ') ILIKE ${like} OR array_to_string(accounts, ' ') ILIKE ${like}
    OR array_to_string(cats, ' ') ILIKE ${like} OR array_to_string(who, ' ') ILIKE ${like})`;
}

function whereClause(parts: readonly (SQL | null)[]): SQL {
  const f = parts.filter((p): p is SQL => p !== null);
  return f.length ? sql`WHERE ${sql.join(f, sql` AND `)}` : sql``;
}

type StateCounts = Record<"total" | Exclude<PostedState, "">, number>;

export async function loadPostedList(req: PickRequest): Promise<PostedList> {
  const db = getDb();
  const search = searchWhere(req.q);
  const state = req.postedState ? STATE_WHERE[req.postedState] : null;
  const where = whereClause([search, state]);
  const offset = (req.page - 1) * req.size;
  const [page, count] = await Promise.all([
    db.execute(
      sql`SELECT ${POSTED_COLUMNS} FROM catalog_posted ${where}
          ORDER BY last_post_on DESC NULLS LAST, created_on DESC NULLS LAST, sd
          LIMIT ${req.size} OFFSET ${offset}`,
    ),
    db.execute<StateCounts>(
      sql`SELECT count(*)::int AS total,
            count(*) FILTER (WHERE ${STATE_WHERE.pub})::int AS pub,
            count(*) FILTER (WHERE ${STATE_WHERE.sched})::int AS sched,
            count(*) FILTER (WHERE ${STATE_WHERE.none})::int AS none,
            count(*) FILTER (WHERE ${STATE_WHERE.nomatch})::int AS nomatch
          FROM catalog_posted ${whereClause([search])}`,
    ),
  ]);
  const rows = page.rows.map(toPostedRecord);
  const c = count.rows[0] ?? {
    total: 0,
    pub: 0,
    sched: 0,
    none: 0,
    nomatch: 0,
  };
  const counts: Record<PostedState, number> = {
    "": c.total,
    pub: c.pub,
    sched: c.sched,
    none: c.none,
    nomatch: c.nomatch,
  };
  const total = state ? counts[req.postedState] : c.total;
  const links = await loadPostedLinks(rows);
  return { rows, total, hasMore: offset + rows.length < total, counts, links };
}

export async function loadPostedRecord(
  sd: string,
): Promise<PostedRecordDetail | null> {
  const res = await getDb().execute(
    sql`SELECT ${POSTED_COLUMNS} FROM catalog_posted WHERE sd = ${sd}`,
  );
  const [raw] = res.rows;
  if (!raw) return null;
  const record = toPostedRecord(raw);
  return { record, links: await loadPostedLinks([record]) };
}

interface LinkedRowRaw extends Record<string, unknown> {
  row_key: string;
  platform: string;
  lang: string;
  title: string;
  off_on: string | null;
}

/** 这一页记录对上的剧场行与 ReelShort 剧，各一条 ANY 查询 */
async function loadPostedLinks(
  records: readonly PostedRecord[],
): Promise<PostedLinks> {
  const db = getDb();
  const rowKeys = [...new Set(records.flatMap((r) => r.rowKeys))];
  const dramaIds = [...new Set(records.flatMap((r) => r.dramaIds))];
  const [rowRes, dramaRes] = await Promise.all([
    rowKeys.length
      ? db.execute<LinkedRowRaw>(
          sql`SELECT row_key, platform, lang, title, off_on FROM catalog_rows WHERE row_key = ANY(${textArray(rowKeys)})`,
        )
      : Promise.resolve({ rows: [] as LinkedRowRaw[] }),
    dramaIds.length
      ? db.execute<LinkedDrama>(
          sql`SELECT id, locale, slug, title FROM rs_ids WHERE id = ANY(${textArray(dramaIds)})`,
        )
      : Promise.resolve({ rows: [] as LinkedDrama[] }),
  ]);
  const rows = new Map(
    rowRes.rows.map((r) => [
      r.row_key,
      {
        rowKey: r.row_key,
        platform: platformOf(r.platform),
        lang: r.lang,
        title: r.title,
        offOn: r.off_on,
      },
    ]),
  );
  const dramas = new Map(
    dramaRes.rows.map((d) => [
      d.id,
      { id: d.id, locale: d.locale, slug: d.slug, title: d.title },
    ]),
  );
  return { rows, dramas };
}

interface PostedStatsRaw extends Record<string, unknown> {
  total: number;
  pub: number;
  posts: number;
  views: string | number;
  metric_at: string | null;
  imported_at: string | null;
  accounts: number;
}

export async function loadPostedStats(): Promise<PostedStats> {
  const res = await getDb().execute<PostedStatsRaw>(
    sql`SELECT count(*)::int AS total, count(*) FILTER (WHERE post_count > 0)::int AS pub,
          coalesce(sum(post_count), 0)::int AS posts, coalesce(sum(views_total), 0)::bigint AS views,
          max(metric_at) AS metric_at, max(imported_at) AS imported_at,
          (SELECT count(*)::int FROM catalog_accounts) AS accounts
        FROM catalog_posted`,
  );
  const r = res.rows[0];
  return {
    total: r?.total ?? 0,
    pubCount: r?.pub ?? 0,
    postsSum: r?.posts ?? 0,
    viewsSum: Number(r?.views ?? 0),
    metricAt: r?.metric_at ?? null,
    importedAt: toTimestamp(r?.imported_at),
    accountCount: r?.accounts ?? 0,
  };
}

interface AccountRaw extends Record<string, unknown> {
  id: string;
  name: string;
  url: string;
  grp: string;
  form: string;
  niche: string;
  status: string;
  fans: number | null;
  as_of: string | null;
}

export async function loadAccounts(): Promise<CatalogAccount[]> {
  const res = await getDb().execute<AccountRaw>(
    sql`SELECT id, name, url, grp, form, niche, status, fans, as_of FROM catalog_accounts ORDER BY grp, name, id`,
  );
  return res.rows.map((a) => ({
    id: a.id,
    name: a.name,
    url: a.url,
    grp: a.grp,
    form: a.form,
    niche: a.niche,
    status: a.status,
    fans: a.fans,
    asOf: a.as_of,
  }));
}
