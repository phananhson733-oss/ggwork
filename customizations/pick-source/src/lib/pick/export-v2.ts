import "server-only";

import { sql, type SQL } from "drizzle-orm";

import { getDb } from "@/db";
import {
  billBySibling,
  loadGrowthBaseline,
  loadRowsByIds,
  loadRsCounts,
  notBot,
  sameDayClicks,
  utcDayOffset,
  type ObserveRow,
} from "@/lib/observe/queries";
import { readSources } from "@/lib/observe/source-state";
import { STALE_RUNNING_MS } from "@/lib/observe/source-types";
import { publicCanonical } from "@/lib/queries";

import {
  EXPORT_VERSION,
  PAN_PREFILTER,
  RESOURCE_SPECS,
  ROW_RESOURCES,
  SCRUB_EXEMPT_KEYS,
  addScrubCounts,
  buildRulesMeta,
  exportFingerprint,
  finalizeManifest,
  rulesDigest,
  toExportRow,
  type CursorKey,
  type RowResource,
  type ScrubCounts,
} from "./export-v2-map";
import {
  SERIES_DAY_SPAN,
  cutPageByBytes,
  type ExportBody,
  type ExportFailure,
  type ExportQuery,
  type ExportResult,
  type SourceFailure,
} from "./export-v2-page";
import { loadFacets, loadFreshness, loadPostedList, loadPostedStats, loadRankMeta, reelshortBranch, textArray } from "./queries";
import { parsePickRequest } from "./request";

/**
 * 选剧工作台镜像导出（feed v2，`pick-export-v2`）的查询层：各资源的 keyset 分页、manifest、fingerprint 与忙标记。
 * 方案见 ggwork-deerflow 的 docs/plans/2026-09-23-supabase-pick-board-plan.md 第 3.3、4 节（第 13、14 节优先）。
 * 列白名单、清洗、manifest 的键白名单与 fingerprint 的规范化在纯模块 export-v2-map.ts；参数、游标与按字节截页在 export-v2-page.ts；
 * 这里只负责从库里读、按什么顺序读、读完怎么核。路由（P1-4）是一层薄壳：鉴权、parseExportQuery、按 status 回 HTTP 都在纯模块
 * feed-http.ts（抛错一律回 503 read_failed），路由文件只把 loadExportPage 接进去。v1 feed（feed.ts）每页复用 checkSource。
 *
 * 【禁止字段的保障落在输出上】（4.6）：复用的 RealShort loader 读到 pan_url、usd 没关系（控制总数要与页面同源，必须原样复用），
 * 本文件自己写的 SQL 字面量里不许出现禁止标识符、SELECT * 与 别名.*（tests/pick-export-v2-sql.test.ts 按 AST 扫），
 * 只按名字豁免 HAS_PAN_SQL 与 BILL_RANK_SQL 两个派生（13.3）；每行输出都经 toExportRow 挑白名单列并清洗。
 *
 * 【先读后核】（4.3）：每页先读完本页数据，再读一次来源快照算 fingerprint，与请求带的 fp 比较；有来源在写返回 503，
 * 不同返回 409。manifest 前后各读一次快照，两次不同也是 409（manifest 期间有写入，计数与数据可能对不上）。
 *
 * 【所有时间都钉在 as_of 上】：复用的 loader 一律传 asOf，自己写的 SQL 只用绑定参数的时点；本文件不取当前时间，
 * ctx.now 只用来判断 running 的来源是否僵死（tests/pick-export-v2-db.test.ts 查导出发出的每条 SQL 都没有 now()）。
 */

type Json = Record<string, unknown>;

/** running 超过 STALE_RUNNING_MS（45 分钟）视为僵死：不再拦截导出，只在 manifest 里记告警（方案 4.3）。
 *  阈值定义在 source-types.ts，与旧选剧台页底「剧单导入」一行判「失败或中断」共用 */
export { STALE_RUNNING_MS };
/** 503 source_busy 的 Retry-After（秒） */
export const BUSY_RETRY_AFTER = 60;

export interface ExportContext {
  /** 请求到达的时刻：只用来判断 running 的来源是否僵死，不进任何数据查询 */
  now: Date;
  /** VERCEL_GIT_COMMIT_SHA，本地为 null：进 fingerprint，也是 manifest 的 sourceRevision */
  buildSha: string | null;
  /** meta.rules：manifest 原样输出，它的摘要进 fingerprint，导出途中发版就 409 */
  rules: Json;
}

export function exportContext(now: Date): ExportContext {
  return { now, buildSha: process.env.PICK_SOURCE_REVISION ?? process.env.VERCEL_GIT_COMMIT_SHA ?? null, rules: buildRulesMeta() };
}

/** 结果类型定义在纯模块 export-v2-page.ts（路由外壳 feed-http.ts 也要用），这里转出，调用方照旧从本模块取 */
export type { ExportBody, ExportFailure, ExportResult, SourceFailure };

const sourceChanged = (): SourceFailure => ({ status: 409, error: "source_changed" });
const sourceBusy = (): SourceFailure => ({ status: 503, error: "source_busy", retryAfter: BUSY_RETRY_AFTER });

/* ---------------------------------------------------------------- 来源快照与 fingerprint（4.3） */

interface SourceRow {
  source: string;
  status: string;
  attemptedAt: Date;
}

interface SourceSnapshot {
  aggregates: Json;
  sources: SourceRow[];
}

/**
 * 4.3 那张表的全部聚合，一条语句。时间一律取 epoch 的十进制文本，与会话时区无关、精确到微秒；
 * detail_synced_at 用求和不用 max：详情阶段 4 个 worker 乱序提交时 max 可能不变，求和一定变大。
 * outbound_clicks 只追加、已由 created_at <= as_of 钉住，不进来。
 */
async function readSourceSnapshot(): Promise<SourceSnapshot> {
  const res = await getDb().execute<Record<string, string | null>>(sql`
    SELECT (SELECT count(*) FROM catalog_rows)::text AS rows_n,
           (SELECT extract(epoch FROM max(imported_at)) FROM catalog_rows)::text AS rows_imported,
           (SELECT count(*) FROM catalog_signals)::text AS signals_n,
           (SELECT count(*) FROM catalog_posted)::text AS posted_n,
           (SELECT extract(epoch FROM max(imported_at)) FROM catalog_posted)::text AS posted_imported,
           (SELECT count(*) FROM catalog_accounts)::text AS accounts_n,
           d.n::text AS dramas_n, extract(epoch FROM d.synced_max)::text AS dramas_synced,
           d.detail_sum::text AS dramas_detail_sum, extract(epoch FROM d.search_max)::text AS dramas_search,
           (SELECT count(*) FROM cps_bill_daily)::text AS bill_n,
           (SELECT extract(epoch FROM max(fetched_at)) FROM cps_bill_daily)::text AS bill_fetched,
           o.latest_day AS obs_day,
           (SELECT count(*) FROM drama_observations WHERE observed_on = o.latest_day)::text AS obs_n,
           (SELECT COALESCE(json_agg(json_build_array(s.source, s.status, s.attempt_id,
                      extract(epoch FROM s.attempted_at)::text, extract(epoch FROM s.completed_at)::text) ORDER BY s.source), '[]'::json)
              FROM observe_sources s)::text AS sources
    FROM (SELECT count(*) AS n, max(synced_at) AS synced_max, sum(extract(epoch FROM detail_synced_at)) AS detail_sum,
                 max(search_data_at) AS search_max FROM dramas) d,
         (SELECT max(observed_on) AS latest_day FROM drama_observations) o`);
  const r = res.rows[0] ?? {};
  const v = (k: string) => r[k] ?? null;
  const sources = JSON.parse(r.sources ?? "[]") as [string, string, string, string | null, string | null][];
  return {
    aggregates: {
      catalog_rows: [v("rows_n"), v("rows_imported")],
      catalog_signals: [v("signals_n")],
      catalog_posted: [v("posted_n"), v("posted_imported")],
      catalog_accounts: [v("accounts_n")],
      dramas: [v("dramas_n"), v("dramas_synced"), v("dramas_detail_sum"), v("dramas_search")],
      cps_bill_daily: [v("bill_n"), v("bill_fetched")],
      drama_observations: [v("obs_day"), v("obs_n")],
      observe_sources: sources.map(([source, status, attemptId, , completedAt]) => [source, status, attemptId, completedAt]),
    },
    sources: sources.map(([source, status, , attemptedAt]) => ({ source, status, attemptedAt: new Date(Number(attemptedAt) * 1000) })),
  };
}

function fingerprintOf(snap: SourceSnapshot, ctx: ExportContext): string {
  return exportFingerprint({ aggregates: snap.aggregates, buildSha: ctx.buildSha, rulesDigest: rulesDigest(ctx.rules) });
}

/** 僵死 = running 且开始于 45 分钟之前；开始时间读不出来的 running 按「正在写」算 */
function isStale(s: SourceRow, ctx: ExportContext): boolean {
  return s.status === "running" && s.attemptedAt.getTime() <= ctx.now.getTime() - STALE_RUNNING_MS;
}

function busyOf(snap: SourceSnapshot, ctx: ExportContext): SourceFailure | null {
  return snap.sources.some((s) => s.status === "running" && !isStale(s, ctx)) ? sourceBusy() : null;
}

/**
 * 先读后核的「核」（4.3）：本页数据读完之后调用，读一次来源快照，返回此刻的 fingerprint。
 * 带了 fp 时同时给出核对结果：有来源在写是 503，fingerprint 与 fp 不同是 409，都没有为 null。
 * 不带 fp（只有 v1 会这样，4.7「不传 fp 时行为与今天相同」）只要 fingerprint，不拦截。
 */
export async function checkSource(fp: string | null, ctx: ExportContext): Promise<{ fingerprint: string; problem: SourceFailure | null }> {
  const snap = await readSourceSnapshot();
  const fingerprint = fingerprintOf(snap, ctx);
  if (fp === null) return { fingerprint, problem: null };
  return { fingerprint, problem: busyOf(snap, ctx) ?? (fingerprint === fp ? null : sourceChanged()) };
}

/** v2 行资源的每一页：有来源在写返回 503，fingerprint 与 fp 不同返回 409，都没有返回 null */
export async function verifySource(fp: string, ctx: ExportContext): Promise<SourceFailure | null> {
  return (await checkSource(fp, ctx)).problem;
}

/**
 * manifest 的告警（4.3）：剧单导入失败或僵死时剧单可能只写了一半，记 catalog_import_incomplete（不拦截：导入是整表替换，
 * 拦住只会让智能体一直停在旧数据）；其它来源僵死的 running 记 source_stale_running。
 */
function warningsOf(snap: SourceSnapshot, ctx: ExportContext): Json[] {
  return snap.sources.flatMap((s) => {
    const base = { source: s.source, status: s.status, attemptedAt: s.attemptedAt.toISOString() };
    if (s.source === "pick_catalog" && (s.status === "failed" || isStale(s, ctx))) return [{ code: "catalog_import_incomplete", ...base }];
    return isStale(s, ctx) ? [{ code: "source_stale_running", ...base }] : [];
  });
}

/* ---------------------------------------------------------------- 各资源的 SELECT（3.3、4.4） */

/** 一段查询的外层条件与收尾：keyset 分页是「主键 > 游标 ORDER BY 主键 LIMIT n + 1」，清洗计数的预筛是「文本 ~* 预筛」 */
interface Slice {
  filter: SQL;
  tail: SQL;
}

/** 外层一律用别名 x 引用；列名只来自 RESOURCE_SPECS（写死的常量），不来自请求 */
function xCols(names: readonly string[]): SQL {
  return sql.raw(names.map((n) => `x.${n}`).join(", "));
}

function columnNames(resource: RowResource): string[] {
  return RESOURCE_SPECS[resource].columns.map((c) => c.name);
}

function atSql(asOf: Date): SQL {
  return sql`${asOf.toISOString()}::timestamptz`;
}

/** has_pan（13.3）：与旧页 ResourceCell「网盘 ↗ / 无网盘」同一个判断（cells.tsx 的 /^https?:\/\//i），只输出布尔 */
const HAS_PAN_SQL = sql`COALESCE(x.pan_url ~* '^https?://', false) AS has_pan`;

/**
 * bill_rank（13.3）：对【全体】公开正典按 billBySibling(asOf) 的分成排名次，不是只对本页排。金额相同名次相同，没有账单为 NULL；
 * 资料页按 bill_rank ASC NULLS LAST, drama_id ASC 排，与 RealShort 的 b.usd DESC NULLS LAST, dramas.id ASC 逐行一致。只输出整数。
 */
const BILL_RANK_SQL = sql`CASE WHEN b.usd IS NOT NULL THEN rank() OVER (ORDER BY b.usd DESC NULLS LAST) END`;

/** rs_rows 里取自 reelshortBranch 的列：与 catalog_rows 同形的那些、三个 rs 条件与各自的证据日、drama_id */
const BRANCH_COLUMNS = [
  ...columnNames("catalog_rows").filter((n) => n !== "imported_at" && n !== "has_pan"),
  "rs_clk", "rs_bill", "rs_gsc", "rs_clk_on", "rs_bill_on", "rs_gsc_on", "drama_id",
];

/** 公开正典（每个 (group_key, locale) 至多一行）；胜负规则只在 publicCanonical 一处，与单剧页的正典解析同口径 */
function canonicalSql(): SQL {
  return sql`SELECT dramas.id, dramas.group_key, dramas.locale FROM dramas WHERE ${publicCanonical()}`;
}

/** dramas 全表：非正典 id 的正典解析、剧单行 in_site_ids 与发布记录 drama_ids 的查找都读它（3.3） */
function rsIdsSql(): SQL {
  return sql`SELECT d.id, canon.id AS canonical_id, d.locale, d.slug, d.title, d.chapter_count, d.pay_start,
      COALESCE(canon.id = d.id, false) AS is_public_canonical
    FROM dramas d LEFT JOIN (${canonicalSql()}) canon ON canon.group_key = d.group_key AND canon.locale = d.locale`;
}

/** (as_of − 14 天, as_of] 的点击按兄弟行归到公开正典、按 UTC 日拆真人与爬虫，与单剧页的 14 天出站同口径 */
function clicks14Sql(asOf: Date): SQL {
  return sql`SELECT dramas.id AS drama_id,
      to_char((outbound_clicks.created_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD') AS day,
      count(*) FILTER (WHERE ${notBot()})::int AS human,
      count(*) FILTER (WHERE NOT (${notBot()}))::int AS bot
    FROM outbound_clicks
    JOIN dramas src ON src.id = outbound_clicks.drama_id
    JOIN dramas ON dramas.group_key = src.group_key AND dramas.locale = src.locale
    WHERE outbound_clicks.created_at > ${atSql(asOf)} - interval '14 days' AND outbound_clicks.created_at <= ${atSql(asOf)}
      AND ${publicCanonical()}
    GROUP BY 1, 2`;
}

/** 有订单的原始账单行先过滤、再按 (bill_date, book_id, promotion_type) 分组；source_rows 是组内原始行数（第 14 节 export-4） */
function billGroupsSql(): SQL {
  return sql`SELECT bd.bill_date, bd.book_id, bd.promotion_type, min(bd.book_title) AS book_title,
      sum(bd.order_cnt)::int AS order_cnt, count(*)::int AS source_rows
    FROM cps_bill_daily bd WHERE bd.order_cnt > 0
    GROUP BY bd.bill_date, bd.book_id, bd.promotion_type`;
}

/**
 * 同日出站与对账明细共用 sameDayClicks（它要求外层别名是 bill），只数 as_of 及之前的点击；
 * canonical_id 按组解析，上游下架、库里查不到的 book_id 为 NULL
 */
function billOrdersSql(asOf: Date): SQL {
  return sql`SELECT bill.bill_date, bill.book_id, bill.promotion_type, canon.id AS canonical_id, bill.book_title, bill.order_cnt,
      bill.source_rows, ${sameDayClicks(asOf)} AS same_day_clicks
    FROM (${billGroupsSql()}) bill
    LEFT JOIN dramas src ON src.id = bill.book_id
    LEFT JOIN (${canonicalSql()}) canon ON canon.group_key = src.group_key AND canon.locale = src.locale`;
}

/** 当天所有 metrics_valid 的快照点，不按正典过滤（3.2：资料页按版本的 rs_ids 解析正典再读） */
function seriesDaySql(day: string): SQL {
  return sql`SELECT o.drama_id, o.recent_revenue_cents::float8 AS revenue_cents, o.promoters_cnt
    FROM drama_observations o WHERE o.observed_on = ${day} AND o.metrics_valid IS TRUE`;
}

function selectSql(resource: Exclude<RowResource, "rs_rows">, asOf: Date, day: string | null, s: Slice): SQL {
  const cols = xCols(columnNames(resource));
  switch (resource) {
    case "catalog_rows":
      return sql`SELECT ${xCols(columnNames(resource).filter((n) => n !== "has_pan"))}, ${HAS_PAN_SQL}
        FROM catalog_rows x WHERE ${s.filter} ${s.tail}`;
    case "catalog_signals":
      return sql`SELECT ${cols} FROM catalog_signals x WHERE ${s.filter} ${s.tail}`;
    case "catalog_posted":
      return sql`SELECT ${cols} FROM catalog_posted x WHERE ${s.filter} ${s.tail}`;
    case "catalog_accounts":
      return sql`SELECT ${cols} FROM catalog_accounts x WHERE ${s.filter} ${s.tail}`;
    case "rs_ids":
      return sql`SELECT ${cols} FROM (${rsIdsSql()}) x WHERE ${s.filter} ${s.tail}`;
    case "rs_clicks14":
      return sql`SELECT ${cols} FROM (${clicks14Sql(asOf)}) x WHERE ${s.filter} ${s.tail}`;
    case "rs_bill_orders":
      return sql`SELECT ${cols} FROM (${billOrdersSql(asOf)}) x WHERE ${s.filter} ${s.tail}`;
    case "rs_series_day":
      if (!day) throw new Error("export-v2：rs_series_day 缺 day");
      return sql`SELECT ${cols} FROM (${seriesDaySql(day)}) x WHERE ${s.filter} ${s.tail}`;
  }
}

/* ---------------------------------------------------------------- rs_rows（4.4 的四步） */

/** 第 1 步与第 4 步：按 keyset 取这一页的公开正典 id，同一条 SQL 里对全体公开正典算 bill_rank，再按本页 id 取值 */
function rsRankSql(asOf: Date, s: Slice): SQL {
  return sql`SELECT x.drama_id, x.bill_rank FROM (
      SELECT dramas.id AS drama_id, ${BILL_RANK_SQL} AS bill_rank
      FROM dramas LEFT JOIN (${billBySibling(asOf)}) b ON b.book_id = dramas.id
      WHERE ${publicCanonical()}
    ) x WHERE ${s.filter} ${s.tail}`;
}

/** ObserveRow → rs_rows 的指标列。USD 那几个字段（billUsd*）不在这里，就不会进输出 */
function observeColumns(o: ObserveRow): Json {
  return {
    locale: o.locale, slug: o.slug, publish_at: o.publishAt, chapter_count: o.chapterCount, pay_start_raw: o.payStart,
    rr: o.revenueCents, promoters_cnt: o.promotersCnt, metrics_valid: o.metricsValid, synced_at: o.syncedAt,
    search_impressions: o.searchImpressions, search_data_at: o.searchDataAt, detail_synced_at: o.detailSyncedAt,
    tag_list: o.tags, description: o.description,
    baseline1_at: o.baseline1At, baseline7_at: o.baseline7At, baseline15_at: o.baseline15At,
    rr1: o.revenueCents1, p1: o.promotersCnt1, rr7: o.revenueCents7, p7: o.promotersCnt7, rr15: o.revenueCents15, p15: o.promotersCnt15,
    clicks7: o.clicks7, last_click_on: o.lastClickOn, bill_orders: o.billOrders, last_bill_on: o.lastBillOn,
  };
}

/**
 * 第 2、3 步：对这批 id 并发取 reelshortBranch(asOf) 的同形列、loadRowsByIds(ids, {asOf, fullText}) 的指标列、
 * s1 与 s7 两天的快照原值（orderBy 的 d1 / d7 / dp1 / dp7 用未过滤的值），按 id 合并。
 * 缺了哪一段就缺那几列，toExportRow 会在核过 fingerprint 之后报「缺列」：先核后映射，写入途中的缺行变成 409 而不是 500。
 */
async function rsRowsFor(asOf: Date, ranked: readonly Json[]): Promise<Json[]> {
  if (ranked.length === 0) return [];
  const ids = ranked.map((r) => String(r.drama_id));
  const [d1, d7] = [utcDayOffset(1, asOf), utcDayOffset(7, asOf)];
  const db = getDb();
  const [branch, observed, snaps] = await Promise.all([
    db.execute(sql`SELECT ${xCols(BRANCH_COLUMNS)}, ${HAS_PAN_SQL}
      FROM (${reelshortBranch(asOf)}) x WHERE x.drama_id = ANY(${textArray(ids)})`),
    loadRowsByIds(ids, { asOf, fullText: true }),
    db.execute(sql`SELECT o.drama_id, o.observed_on, o.recent_revenue_cents::float8 AS rr, o.promoters_cnt AS p
      FROM drama_observations o WHERE o.observed_on IN (${d1}, ${d7}) AND o.drama_id = ANY(${textArray(ids)})`),
  ]);
  const byId = new Map(branch.rows.map((r) => [String(r.drama_id), r]));
  const snap = new Map(snaps.rows.map((r) => [`${r.observed_on}|${r.drama_id}`, r]));
  return ranked.map((k) => {
    const id = String(k.drama_id);
    const o = observed.get(id);
    const [s1, s7] = [snap.get(`${d1}|${id}`), snap.get(`${d7}|${id}`)];
    return {
      ...byId.get(id),
      ...(o ? observeColumns(o) : {}),
      s1_rr: s1?.rr ?? null,
      s1_p: s1?.p ?? null,
      s7_rr: s7?.rr ?? null,
      s7_p: s7?.p ?? null,
      bill_rank: k.bill_rank,
    };
  });
}

/** 一次最多给 loadRowsByIds 多少个 id：一页（上限 + 多取的一行） */
const RS_CHUNK = RESOURCE_SPECS.rs_rows.maxLimit + 1;

/** 读一段原始行（未映射、未清洗）；rs_rows 走四步，其余一条 SQL */
async function readRows(resource: RowResource, asOf: Date, day: string | null, s: Slice): Promise<Json[]> {
  if (resource !== "rs_rows") return (await getDb().execute(selectSql(resource, asOf, day, s))).rows;
  const ranked = (await getDb().execute(rsRankSql(asOf, s))).rows;
  const out: Json[] = [];
  for (let i = 0; i < ranked.length; i += RS_CHUNK) out.push(...(await rsRowsFor(asOf, ranked.slice(i, i + RS_CHUNK))));
  return out;
}

function pageSlice(resource: RowResource, cursor: CursorKey | null, limit: number): Slice {
  const keys = xCols(RESOURCE_SPECS[resource].key);
  const after = cursor ? sql`(${keys}) > (${sql.join(cursor.map((v) => sql`${v}`), sql`, `)})` : sql`true`;
  return { filter: after, tail: sql`ORDER BY ${keys} LIMIT ${limit + 1}` };
}

/* ---------------------------------------------------------------- 清洗计数（meta.scrub） */

/**
 * 清洗计数的候选行：只挑文本里含网盘预筛记号的行（PAN_PREFILTER 是清洗正则的超集），再逐行过 toExportRow 精确计数，
 * 与逐页拉取的命中之和相同。要清洗的列按 RESOURCE_SPECS 的类型与豁免清单算，新加的文本列自动进来；
 * rs_rows 的文本都出自 dramas 的 title、locale、tags、description（其余是常量），按正典 id 预筛。
 * 没有要清洗的列（rs_clicks14、rs_series_day）返回 null。
 */
function scrubSlice(resource: RowResource): Slice | null {
  const text = RESOURCE_SPECS[resource].columns.filter(
    (c) => (c.type === "text" || c.type === "text[]" || c.type === "json") && !SCRUB_EXEMPT_KEYS.has(c.name),
  );
  if (text.length === 0) return null;
  const tail = sql`ORDER BY ${xCols(RESOURCE_SPECS[resource].key)}`;
  if (resource === "rs_rows")
    return {
      filter: sql`x.drama_id IN (SELECT dramas.id FROM dramas WHERE ${publicCanonical()}
        AND concat_ws(' ', dramas.title, dramas.locale, array_to_string(dramas.tags, ' '), dramas.description) ~* ${PAN_PREFILTER})`,
      tail,
    };
  return { filter: sql`concat_ws(' ', ${sql.raw(text.map((c) => `x.${c.name}::text`).join(", "))}) ~* ${PAN_PREFILTER}`, tail };
}

async function readScrubCandidates(asOf: Date): Promise<[RowResource, Json[]][]> {
  const jobs = ROW_RESOURCES.flatMap((r) => {
    const s = scrubSlice(r);
    return s ? [readRows(r, asOf, null, s).then((rows): [RowResource, Json[]] => [r, rows])] : [];
  });
  return Promise.all(jobs);
}

function countHits(candidates: readonly [RowResource, Json[]][]): ScrubCounts {
  return candidates.reduce<ScrubCounts>(
    (acc, [resource, rows]) => rows.reduce<ScrubCounts>((a, raw) => addScrubCounts(a, toExportRow(resource, raw).hits), acc),
    {},
  );
}

/* ---------------------------------------------------------------- manifest（4.5） */

const COUNTED = ROW_RESOURCES.filter((r) => r !== "rs_series_day");

/** 每个资源在 as_of 的精确行数，与分页用同一段 FROM / WHERE；另带有订单的订单总数（control.ledger.orders） */
async function readCounts(asOf: Date): Promise<{ counts: Json; ledgerOrders: number }> {
  const res = await getDb().execute<Record<string, number | null>>(sql`
    SELECT (SELECT count(*)::int FROM catalog_rows) AS catalog_rows,
           (SELECT count(*)::int FROM catalog_signals) AS catalog_signals,
           (SELECT count(*)::int FROM catalog_posted) AS catalog_posted,
           (SELECT count(*)::int FROM catalog_accounts) AS catalog_accounts,
           (SELECT count(*)::int FROM dramas WHERE ${publicCanonical()}) AS rs_rows,
           (SELECT count(*)::int FROM dramas) AS rs_ids,
           (SELECT count(*)::int FROM (${clicks14Sql(asOf)}) x) AS rs_clicks14,
           (SELECT count(*)::int FROM (${billGroupsSql()}) x) AS rs_bill_orders,
           (SELECT COALESCE(sum(bd.order_cnt), 0)::int FROM cps_bill_daily bd WHERE bd.order_cnt > 0) AS ledger_orders`);
  const r = res.rows[0] ?? {};
  const n = (k: string) => Number(r[k] ?? 0);
  return { counts: Object.fromEntries(COUNTED.map((k) => [k, n(k)])), ledgerOrders: n("ledger_orders") };
}

/** 最近 93 天（与 rs_series_day 的 day 范围相同）每天 metrics_valid 的快照点数；as_of 之后的日子不算 */
async function readSnapshotDays(asOf: Date): Promise<{ day: string; rows: number }[]> {
  const res = await getDb().execute<{ day: string; n: number }>(sql`
    SELECT o.observed_on AS day, count(*)::int AS n FROM drama_observations o
    WHERE o.observed_on >= ${utcDayOffset(SERIES_DAY_SPAN - 1, asOf)} AND o.observed_on <= ${utcDayOffset(0, asOf)}
      AND o.metrics_valid IS TRUE
    GROUP BY o.observed_on ORDER BY o.observed_on`);
  return res.rows.map((r) => ({ day: r.day, rows: Number(r.n) }));
}

/**
 * manifest 要的全部数据，并发读。控制总数一律经 RealShort 页面自己的函数在 as_of 上算（决策 3、4.5）：
 * 选剧 tab 与全部剧库默认条件下的 loadFacets、榜单默认的 loadRankMeta().counts、loadPostedStats、发布记录四个状态的计数；
 * ledger.rows 取 loadRsCounts().ledger。不用 loadBillTotals：它返回分成金额。
 */
async function readManifestParts(asOf: Date) {
  const [counts, days, freshness, rsCounts, g1, g7, sources, facetsPick, facetsAll, rankMeta, postedStats, posted, scrub] = await Promise.all([
    readCounts(asOf),
    readSnapshotDays(asOf),
    loadFreshness(asOf),
    loadRsCounts(asOf),
    loadGrowthBaseline(1, asOf),
    loadGrowthBaseline(7, asOf),
    readSources(),
    loadFacets(parsePickRequest({}), asOf),
    loadFacets(parsePickRequest({ tab: "all" }), asOf),
    loadRankMeta(parsePickRequest({ tab: "rank" }), asOf),
    loadPostedStats(),
    loadPostedList(parsePickRequest({ tab: "posted" })),
    readScrubCandidates(asOf),
  ]);
  const states = { pub: posted.counts.pub, sched: posted.counts.sched, none: posted.counts.none, nomatch: posted.counts.nomatch };
  return {
    counts: counts.counts,
    snapshotDays: days,
    meta: {
      freshness,
      rsCounts,
      growthBaseline: { 1: g1, 7: g7 },
      sources,
      control: {
        facetsPick,
        facetsAll,
        rankCounts: rankMeta.counts,
        postedStats,
        postedStates: states,
        ledger: { rows: rsCounts.ledger, orders: counts.ledgerOrders },
      },
    },
    scrubCandidates: scrub,
  };
}

/**
 * manifest（4.5）：必须在其它资源之前取，其它资源都带它给的 fp。前后各读一次来源快照：开始时有来源在写直接 503，
 * 期间有写入（两次 fingerprint 不同）返回 409，工作台照常重来。输出经 finalizeManifest：整份过键白名单，
 * meta 里的文本（语种、来源说明、告警……）再过一遍网盘清洗（4.6 第 4 条）。
 */
export async function loadExportManifest(asOf: Date, ctx: ExportContext): Promise<ExportResult> {
  const before = await readSourceSnapshot();
  const busy = busyOf(before, ctx);
  if (busy) return busy;
  const parts = await readManifestParts(asOf);
  const after = await readSourceSnapshot();
  const fingerprint = fingerprintOf(after, ctx);
  const problem = busyOf(after, ctx) ?? (fingerprint === fingerprintOf(before, ctx) ? null : sourceChanged());
  if (problem) return problem;
  const { manifest, hits } = finalizeManifest({
    version: EXPORT_VERSION,
    asOf: asOf.toISOString(),
    fingerprint,
    sourceRevision: ctx.buildSha,
    counts: parts.counts,
    latestSnapshot: parts.snapshotDays.at(-1)?.day ?? null,
    snapshotDays: parts.snapshotDays,
    meta: { ...parts.meta, rules: ctx.rules, scrub: countHits(parts.scrubCandidates), warnings: warningsOf(after, ctx) },
  });
  const body: ExportBody = { ok: true, version: EXPORT_VERSION, resource: "manifest", asOf: asOf.toISOString(), fingerprint, rows: [manifest], nextCursor: null };
  return { status: 200, body, hits };
}

/* ---------------------------------------------------------------- 行资源的一页（4.1、4.4） */

/**
 * 一页：按 keyset 多取一行读原始数据 → 读来源快照核 fp（先读后核）→ 逐行挑白名单列、归一、清洗 → 按条数与 3 MB 截页。
 * 单行超过 4 MB 返回 500 row_too_large（只带资源名与主键）。manifest 也从这里进。
 */
export async function loadExportPage(q: ExportQuery, ctx: ExportContext): Promise<ExportResult> {
  if (q.resource === "manifest") return loadExportManifest(q.asOf, ctx);
  if (!q.fp) throw new Error("export-v2：行资源必须带 fp");
  const resource = q.resource;
  const raws = await readRows(resource, q.asOf, q.day, pageSlice(resource, q.cursor, q.limit));
  const problem = await verifySource(q.fp, ctx);
  if (problem) return problem;
  const mapped = raws.map((raw) => toExportRow(resource, raw));
  const envelope = { ok: true as const, version: EXPORT_VERSION, resource, asOf: q.asOf.toISOString(), fingerprint: q.fp };
  const cut = cutPageByBytes(resource, mapped, q.limit, envelope);
  if (!cut.ok) return { status: 500, error: "row_too_large", resource: cut.resource, key: cut.key };
  const hits = mapped.slice(0, cut.rows.length).reduce<ScrubCounts>((acc, e) => addScrubCounts(acc, e.hits), {});
  return { status: 200, body: { ...envelope, rows: cut.rows, nextCursor: cut.nextCursor }, hits };
}
