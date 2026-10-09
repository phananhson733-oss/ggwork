import "server-only";

import { sql, type SQL } from "drizzle-orm";

import { getDb } from "@/db";
import type { RawPost } from "./catalog-import";
import { textArray } from "./queries-shared";
import { PLATFORMS, type PickRequest, type Platform, type PostedState } from "./request";

/**
 * 发布记录 tab：运营那张飞书「选剧池 / 发布记录」的全表视图，加账号台账与单条记录页。
 * 证据页上那张「对上的发布记录」卡片也用这里的 `toPostedRecord()`，两处读同一份列清单。
 *
 * 【已发的口径与 posted.py 相同】：只数状态是「已回填 / 已公开」的帖子（导入时由 `summarizePosts()` 算好，
 * `post_count` / `sched_count` 是列不是渲染时数），所以「已发」= post_count > 0、
 * 「已排期未发」= post_count = 0 且 sched_count > 0、「未排期」= 两个都是 0、
 * 「剧库未对上」= row_keys 与 drama_ids 都空。四个筛选与 chips 计数共用同一组条件。
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
  posts: RawPost[];
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

export const POSTED_COLUMNS = sql.raw(
  [
    "sd", "feishu_record", "title", "lang", "platform", "life", "scheduled", "online_on", "why", "note", "archived",
    "post_count", "sched_count", "first_post_on", "last_post_on", "views_total", "views_count", "metric_at",
    "sources", "cats", "who", "accounts", "created_on", "updated_on", "row_keys", "drama_ids", "posts",
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

/** 记录对上的剧场行 / ReelShort 剧，只取链接与一行显示要用的列 */
export interface LinkedRow {
  rowKey: string;
  platform: Platform;
  lang: string;
  title: string;
  offOn: string | null;
}
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

const STATE_WHERE: Record<Exclude<PostedState, "">, SQL> = {
  pub: sql`post_count > 0`,
  sched: sql`post_count = 0 AND sched_count > 0`,
  none: sql`post_count = 0 AND sched_count = 0`,
  nomatch: sql`cardinality(row_keys) = 0 AND cardinality(drama_ids) = 0`,
};

function searchWhere(q: string): SQL | null {
  if (!q) return null;
  /* 剧名 / 编号 / 理由 / 备注 / 来源 / 账号都搜。ILIKE 没有索引，71 行、人工搜索 */
  const like = `%${q}%`;
  return sql`(title ILIKE ${like} OR sd ILIKE ${like} OR why ILIKE ${like} OR note ILIKE ${like}
    OR platform ILIKE ${like} OR lang ILIKE ${like} OR life ILIKE ${like}
    OR array_to_string(sources, ' ') ILIKE ${like} OR array_to_string(accounts, ' ') ILIKE ${like}
    OR array_to_string(cats, ' ') ILIKE ${like} OR array_to_string(who, ' ') ILIKE ${like})`;
}

function whereClause(parts: (SQL | null)[]): SQL {
  const f = parts.filter((p): p is SQL => p !== null);
  return f.length ? sql`WHERE ${sql.join(f, sql` AND `)}` : sql``;
}

export interface PostedList {
  rows: PostedRecord[];
  total: number;
  hasMore: boolean;
  /** 四个状态各多少条（带当前搜索词，不带当前状态筛选） */
  counts: Record<PostedState, number>;
  links: PostedLinks;
}

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
    db.execute(
      sql`SELECT count(*)::int AS total,
            count(*) FILTER (WHERE ${STATE_WHERE.pub})::int AS pub,
            count(*) FILTER (WHERE ${STATE_WHERE.sched})::int AS sched,
            count(*) FILTER (WHERE ${STATE_WHERE.none})::int AS none,
            count(*) FILTER (WHERE ${STATE_WHERE.nomatch})::int AS nomatch
          FROM catalog_posted ${whereClause([search])}`,
    ),
  ]);
  const rows = page.rows.map(toPostedRecord);
  const c = count.rows[0] as { total: number; pub: number; sched: number; none: number; nomatch: number };
  const counts = { "": c.total, pub: c.pub, sched: c.sched, none: c.none, nomatch: c.nomatch };
  const total = state ? counts[req.postedState] : c.total;
  const links = await loadPostedLinks(rows);
  return { rows, total, hasMore: offset + rows.length < total, counts, links };
}

export async function loadPostedRecord(sd: string): Promise<{ record: PostedRecord; links: PostedLinks } | null> {
  const db = getDb();
  const res = await db.execute(sql`SELECT ${POSTED_COLUMNS} FROM catalog_posted WHERE sd = ${sd}`);
  if (!res.rows[0]) return null;
  const record = toPostedRecord(res.rows[0]);
  return { record, links: await loadPostedLinks([record]) };
}

/** 这一页记录对上的剧场行与 ReelShort 剧，各一条 ANY 查询 */
async function loadPostedLinks(records: PostedRecord[]): Promise<PostedLinks> {
  const db = getDb();
  const rowKeys = [...new Set(records.flatMap((r) => r.rowKeys))];
  const dramaIds = [...new Set(records.flatMap((r) => r.dramaIds))];
  const [rowRes, dramaRes] = await Promise.all([
    rowKeys.length
      ? db.execute(
          sql`SELECT row_key, platform, lang, title, off_on FROM catalog_rows WHERE row_key = ANY(${textArray(rowKeys)})`,
        )
      : Promise.resolve({ rows: [] as Record<string, unknown>[] }),
    dramaIds.length
      ? db.execute(sql`SELECT id, locale, slug, title FROM dramas WHERE id = ANY(${textArray(dramaIds)})`)
      : Promise.resolve({ rows: [] as Record<string, unknown>[] }),
  ]);
  const rows = new Map<string, LinkedRow>();
  for (const r of rowRes.rows as { row_key: string; platform: string; lang: string; title: string; off_on: string | null }[])
    rows.set(r.row_key, {
      rowKey: r.row_key,
      platform: (PLATFORMS as readonly string[]).includes(r.platform) ? (r.platform as Platform) : "dramabox",
      lang: r.lang,
      title: r.title,
      offOn: r.off_on,
    });
  const dramas = new Map<string, LinkedDrama>();
  for (const d of dramaRes.rows as { id: string; locale: string; slug: string; title: string }[])
    dramas.set(d.id, { id: d.id, locale: d.locale, slug: d.slug, title: d.title });
  return { rows, dramas };
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

export async function loadPostedStats(): Promise<PostedStats> {
  const db = getDb();
  const res = await db.execute(
    sql`SELECT count(*)::int AS total, count(*) FILTER (WHERE post_count > 0)::int AS pub,
          coalesce(sum(post_count), 0)::int AS posts, coalesce(sum(views_total), 0)::bigint AS views,
          max(metric_at) AS metric_at, max(imported_at) AS imported_at,
          (SELECT count(*)::int FROM catalog_accounts) AS accounts
        FROM catalog_posted`,
  );
  const r = res.rows[0] as {
    total: number;
    pub: number;
    posts: number;
    views: string | number;
    metric_at: string | null;
    imported_at: string | null;
    accounts: number;
  };
  return {
    total: r.total,
    pubCount: r.pub,
    postsSum: r.posts,
    viewsSum: Number(r.views),
    metricAt: r.metric_at,
    importedAt: r.imported_at ? new Date(r.imported_at) : null,
    accountCount: r.accounts,
  };
}

export interface CatalogAccount {
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

export async function loadAccounts(): Promise<CatalogAccount[]> {
  const db = getDb();
  const res = await db.execute(
    sql`SELECT id, name, url, grp, form, niche, status, fans, as_of FROM catalog_accounts ORDER BY grp, name, id`,
  );
  return (
    res.rows as { id: string; name: string; url: string; grp: string; form: string; niche: string; status: string; fans: number | null; as_of: string | null }[]
  ).map((a) => ({
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
