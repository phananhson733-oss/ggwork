import "server-only";

import { sql } from "drizzle-orm";

import { getDb } from "@/db";
import { checkSource, exportContext, type ExportContext } from "./export-v2";
import type { FeedResult } from "./feed-http";
import {
  FEED_COLUMNS,
  cutFeedPage,
  feedPage,
  feedRulesMarkdown,
  toFeedRow,
  toFeedSourceRow,
  type FeedPageMeta,
  type FeedQuery,
  type FeedRawRow,
} from "./feed-map";
import { loadFreshness, loadPostedFor, loadSignalsFor, textArray, unionRows } from "./queries";

/**
 * 选剧工作台只读 feed：按 row_key 游标分页读候选池（与选剧 tab 同一个 unionRows()）。
 * 只 SELECT；分页之间不是同一事务，接收方用首页的 total 核对拼完的条数，不一致整批丢弃。
 * 带了 as_of / fp 时（feed v2 的 P1-4，方案 4.7）每一页都先读后核：读完本页的行、信号、发布记录与首页 meta，
 * 再用 v2 同一个 checkSource 读一次来源快照，fingerprint 不同 409、有来源在写 503。
 */
export type { FeedPage } from "./feed-map";

/**
 * v1 每页核对用的上下文，与 v2 是同一个 exportContext（构建 SHA 与规则常量，都进 fingerprint）。
 * v1 路由只经这里取，不直接 import export-v2（tests/admin-contracts.test.ts 的白名单）。
 */
export const feedContext = exportContext;

const CANDIDATE = sql`rows.has_signal AND rows.off_on IS NULL`;
/** 显式列（方案 4.6：不许 SELECT rows.*），列名只来自写死的 FEED_COLUMNS */
const FEED_SELECT = sql.raw(FEED_COLUMNS.map((c) => `rows.${c}`).join(", "));

async function accountsFor(sds: string[]): Promise<Map<string, string[]>> {
  const out = new Map<string, string[]>();
  if (sds.length === 0) return out;
  const res = await getDb().execute(
    sql`SELECT sd, accounts FROM catalog_posted WHERE sd = ANY(${textArray(sds)})`,
  );
  for (const r of res.rows as { sd: string; accounts: string[] | null }[]) out.set(r.sd, r.accounts ?? []);
  return out;
}

async function firstPageMeta(capturedAt: string, asOf?: Date): Promise<FeedPageMeta> {
  const [count, fresh] = await Promise.all([
    getDb().execute(sql`SELECT count(*)::int AS n FROM ${unionRows(asOf)} WHERE ${CANDIDATE}`),
    loadFreshness(asOf),
  ]);
  return {
    total: (count.rows[0] as { n: number }).n,
    freshness: {
      catalogImportedAt: fresh.importedAt?.toISOString() ?? null,
      reelshortSyncedAt: fresh.rsSyncedAt?.toISOString() ?? null,
      catalogRows: fresh.rows,
      ...(process.env.PICK_SOURCE_RETAIN_MOBOREELS === "1" ? {retainedSources:"MoboReels 剧单使用上次成功资料，未重新采集"} : {}),
      withSignal: fresh.withSignal,
      postedRecords: fresh.posted,
    },
    rules: feedRulesMarkdown(capturedAt),
  };
}

/**
 * 传了 asOf 时候选池、ReelShort 条件信号与首页 freshness 都钉在这个时点上，capturedAt 就是它
 * （工作台把 capturedAt 当批次的 source_as_of，与镜像版本的 as_of 相等，方案 4.7）；不传时与改动前相同。
 * 【先读后核】来源快照是本页发出的最后一条查询：一次写入落在本页读取期间，本页自己就能发现（4.3）。
 * 不带 fp 时照样算 fingerprint 放进响应（每页都有），但不核对、不拦截，行为与今天相同。
 */
export async function loadFeedPage(query: FeedQuery, ctx: ExportContext): Promise<FeedResult> {
  const asOf = query.asOf ?? undefined;
  const capturedAt = (query.asOf ?? new Date()).toISOString();
  const after = query.cursor ? sql` AND rows.row_key > ${query.cursor}` : sql``;
  const raw = await getDb().execute(
    sql`SELECT ${FEED_SELECT} FROM ${unionRows(asOf)} WHERE ${CANDIDATE}${after} ORDER BY rows.row_key LIMIT ${query.limit + 1}`,
  );
  const { rows: base, nextCursor } = cutFeedPage((raw.rows as FeedRawRow[]).map(toFeedSourceRow), query.limit);
  const keys = base.map((r) => r.rowKey);
  const [signals, posted] = await Promise.all([loadSignalsFor(keys), loadPostedFor(keys)]);
  const sds = [...new Set([...posted.values()].flat().map((t) => t.sd))];
  const accounts = await accountsFor(sds);
  const meta = query.cursor ? null : await firstPageMeta(capturedAt, asOf);
  const source = await checkSource(query.fp, ctx);
  if (source.problem) return source.problem;
  return {
    status: 200,
    page: feedPage({
      capturedAt,
      meta,
      sourceRevision: ctx.buildSha,
      rows: base.map((r) => toFeedRow(r, signals.get(r.rowKey) ?? [], posted.get(r.rowKey) ?? [], accounts)),
      nextCursor,
      fingerprint: source.fingerprint,
    }),
  };
}
