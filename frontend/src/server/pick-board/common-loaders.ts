import "server-only";

import { sql } from "drizzle-orm";
import { cache } from "react";
import { z } from "zod";

import {
  commonQuerySchema,
  queryPinSchema,
  type queryBoardDataSchema,
  type CommonQuery,
  type QueryPin,
  type QueryResponse,
} from "@/core/pick/completion-types";
import {
  addUtcDays,
  diagnoseGrowthEmpty,
  type BaselineSnapshot,
} from "@/core/pick-board/growth-diagnosis";
import {
  BASES,
  GRADES,
  PLATFORMS,
  RANKS,
  UNKNOWN_LANGUAGE,
  isDailyRank,
  isRsRank,
  parsePickRequest,
  type Basis,
  type Grade,
  type Platform,
  type PickRequest,
  type RankKey,
  type RsRank,
} from "@/core/pick-board/request";
import { buildBoardRules, type BoardRules } from "@/core/pick-board/rules";

import { resolveBoard as resolveLegacyBoard } from "./cache";
import { queryPickBoard } from "./common-query";
import { boardScope, controlDb } from "./db";
import {
  MirrorBusy,
  MirrorMisconfigured,
  MirrorPeriodMissing,
  MirrorSessionRequired,
  MirrorVersionGone,
} from "./errors";
import { type PickFacets } from "./queries";
import {
  toPostedRecord,
  type PostedList,
  type PostedStats,
  type CatalogAccount,
} from "./queries-posted";
import {
  rsSortFor,
  type RankMeta,
  type RankRow,
  type RsRankResult,
} from "./queries-rank";
import { toRowWithFlags } from "./queries-reelshort";
import {
  platformOf,
  toRow,
  toTimestamp,
  type PickRow,
  type PickSignal,
  type RowsPage,
} from "./queries-shared";
import { toObserveRow } from "./rs-queries";
import { sourcesOf } from "./source-state";
import { type BoardVersion } from "./version";

function validDay(value: string): boolean {
  return (
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    !Number.isNaN(Date.parse(value)) &&
    new Date(value).toISOString().slice(0, 10) === value
  );
}

/** Translate board controls, retaining their defaults rather than Agent-selection defaults. */
export function boardQuery(req: PickRequest, pin: QueryPin): CommonQuery {
  const domain =
    req.tab === "rank"
      ? "rankings"
      : req.tab === "posted"
        ? "posted"
        : req.tab === "rules"
          ? "rules"
          : req.tab === "all"
            ? "catalog"
            : "candidates";
  if (domain === "rules")
    return commonQuerySchema.parse({ domain, scope: "full_catalog", pin });
  const list = req.tab === "pick" || req.tab === "all";
  const rsRank =
    req.tab === "rank" && isRsRank(req.rank) && req.rank !== "rs_ledger";
  const pool =
    req.tab === "pick" ? !req.wide : req.tab === "all" && req.signalOnly;
  const fixedLimit =
    req.tab === "rank" && req.rank === "rs_growth"
      ? 50
      : req.tab === "rank" && req.rank === "rs_ledger"
        ? 200
        : null;
  const weekDate = validDay(req.week);
  return commonQuerySchema.parse({
    domain,
    scope: pool ? "candidate_pool" : "full_catalog",
    pin,
    query: list || req.tab === "posted" || rsRank ? req.q || null : null,
    language: list
      ? req.lang === UNKNOWN_LANGUAGE
        ? ""
        : req.lang || null
      : null,
    theater: list ? req.platform || null : null,
    signal_kind: list ? req.basis || null : null,
    posted_filter: list ? req.posted : "",
    posted_state: req.tab === "posted" ? req.postedState : "",
    with_off: list && req.withOff,
    signal_only: list && req.signalOnly,
    wide: list && req.wide,
    youtube_ok: list && req.youtubeOk,
    dated_only: list && req.datedOnly,
    in_use_only: list && req.inUseOnly,
    order:
      req.tab === "rank"
        ? "rank"
        : req.tab === "posted"
          ? "published_at"
          : req.sort,
    offset: fixedLimit ? 0 : (req.page - 1) * req.size,
    limit: fixedLimit ?? req.size,
    rank: req.tab === "rank" ? req.rank : null,
    grade: req.tab === "rank" && !isRsRank(req.rank) ? req.grade || null : null,
    rs_sort: rsRank ? req.rsSort : "rr",
    rs_locale: rsRank ? req.rsLocale || null : null,
    rs_bucket: rsRank ? req.rsBucket : null,
    period:
      req.tab === "rank" && isDailyRank(req.rank) && validDay(req.day)
        ? { kind: "daily", value: req.day }
        : req.tab === "rank" && req.rank === "kw" && weekDate
          ? { kind: "weekly", value: req.week }
          : { kind: "latest", value: null },
    legacy_week_label:
      req.tab === "rank" && req.rank === "kw" && req.week && !weekDate
        ? req.week
        : null,
    confirmed_eligible_only: false,
    exclude_selected: false,
    exclude_posted: false,
  });
}

type BoardData = z.infer<typeof queryBoardDataSchema>;
const pairSchema = z.object({
  agent_catalog_batch_id: z.string().min(1),
  agent_knowledge_batch_id: z.string().nullable(),
  status: z.string(),
});
/** Published immutable version pairing, including historical versions, never the current pointer. */
export const pinForVersion = cache(
  async (version: number): Promise<QueryPin> => {
    const result = await controlDb().execute(
      sql`SELECT agent_catalog_batch_id,agent_knowledge_batch_id,status FROM pick_mirror.versions WHERE id=${version}`,
    );
    const pair = pairSchema.safeParse(result.rows[0]);
    if (!pair.success || pair.data.status !== "published")
      throw new MirrorVersionGone();
    return queryPinSchema.parse({
      catalog_batch_id: pair.data.agent_catalog_batch_id,
      knowledge_batch_id: pair.data.agent_knowledge_batch_id,
      mirror_version: version,
      rule_version: `mirror-rules-v${version}`,
      feedback_version_id: null,
    });
  },
);

const readPinned = cache(async (body: string): Promise<QueryResponse> => {
  const request = commonQuerySchema.parse(JSON.parse(body));
  const result = await queryPickBoard(request);
  if (!result.ok) {
    if (result.code === "period_missing") throw new MirrorPeriodMissing();
    if (result.status === 410 || result.status === 404 || result.status === 409)
      throw new MirrorVersionGone();
    if (result.status === 401 || result.status === 403)
      throw new MirrorSessionRequired();
    if (result.status === 422) throw new MirrorMisconfigured("control_shape");
    throw new MirrorBusy();
  }
  if (JSON.stringify(result.data.pin) !== JSON.stringify(request.pin))
    throw new MirrorVersionGone();
  if (result.data.board === null)
    throw new MirrorMisconfigured("control_shape");
  return result.data;
});
async function read(req: PickRequest): Promise<QueryResponse> {
  return readPinned(
    JSON.stringify(
      boardQuery(req, await pinForVersion(boardScope().versionId)),
    ),
  );
}
function dataOf(response: QueryResponse): BoardData {
  if (!response.board) throw new MirrorMisconfigured("control_shape");
  return response.board;
}

/** No historical URL fallback: a requested missing/denied version has no current substitute. */
export async function resolveCommonBoard(
  version: number | null,
): Promise<BoardVersion<BoardRules>> {
  const resolved = await resolveLegacyBoard(version);
  if (resolved.state === "empty") return resolved;
  if (
    version !== null &&
    (resolved.scope.versionId !== version || resolved.unreadable)
  )
    throw new MirrorVersionGone();
  const pin = await pinForVersion(resolved.scope.versionId);
  const response = await readPinned(
    JSON.stringify(boardQuery(parsePickRequest({ tab: "rules" }), pin)),
  );
  const rules = dataOf(response).rules;
  if (!rules) throw new MirrorMisconfigured("rules");
  return {
    ...resolved,
    scope: {
      ...resolved.scope,
      rules: buildBoardRules(rules, resolved.scope.versionId),
    },
  };
}
function signals(board: BoardData, key: string): PickSignal[] {
  return board.signals
    .filter(
      (signal) =>
        signal.row_key === key &&
        (BASES as readonly string[]).includes(signal.kind),
    )
    .sort((a, b) => a.ord - b.ord)
    .map((signal) => ({
      kind: signal.kind as Basis,
      ord: signal.ord,
      evidenceOn: signal.evidence_on,
      rank: signal.rank,
      grade: signal.grade,
      note: signal.note,
      payload: signal.payload,
    }));
}
export function catalogPage(response: QueryResponse): RowsPage<PickRow> {
  const board = dataOf(response);
  const catalog = new Map(board.catalog_rows.map((row) => [row.row_key, row]));
  const rs = new Map(board.rs_rows.map((row) => [row.row_key, row]));
  const rows = board.row_keys.map((key) => {
    const special = rs.get(key);
    const row = catalog.get(key) ?? special;
    if (!row) throw new MirrorMisconfigured("control_shape");
    const base = toRowWithFlags({
      ...row,
      rs_clk: special?.rs_clk ?? false,
      rs_bill: special?.rs_bill ?? false,
      rs_gsc: special?.rs_gsc ?? false,
      rs_clk_on: special?.rs_clk_on ?? null,
      rs_bill_on: special?.rs_bill_on ?? null,
      rs_gsc_on: special?.rs_gsc_on ?? null,
    });
    const posted = board.posted.flatMap((post) => {
      const occurrences =
        post.row_keys.filter((rowKey) => rowKey === key).length +
        post.drama_ids.filter((id) => `reelshort-${id}` === key).length;
      return Array.from({ length: occurrences }, () => ({
        sd: post.sd,
        life: post.life,
        scheduled: post.scheduled,
        postCount: post.post_count,
        schedCount: post.sched_count,
        lastPostOn: post.last_post_on,
        viewsTotal: post.views_total,
      }));
    });
    return {
      ...base,
      signals: signals(board, key),
      posted,
      rs: special
        ? toObserveRow({
            ...special,
            id: special.drama_id,
            pay_start: special.pay_start_raw,
            tags:
              response.request.domain !== "rankings" &&
              board.rs_rows.length === 1
                ? special.tag_list
                : special.tag_list.slice(0, 8),
            description:
              response.request.domain !== "rankings" &&
              board.rs_rows.length === 1
                ? special.description
                : "",
            clicks: special.clicks7,
          })
        : undefined,
    };
  });
  return {
    rows,
    total: response.counts.matched,
    hasMore: response.next_offset !== null,
  };
}
export async function loadCommonPickRows(
  req: PickRequest,
): Promise<RowsPage<PickRow>> {
  return catalogPage(await read(req));
}
export async function loadCommonFacets(req: PickRequest): Promise<PickFacets> {
  return catalogFacets(await read(req));
}
export function catalogFacets(response: QueryResponse): PickFacets {
  const facets = response.facets;
  if (!facets) throw new MirrorMisconfigured("control_shape");
  return {
    platforms: Object.fromEntries(
      Object.entries(facets.platforms).filter(([key]) =>
        (PLATFORMS as readonly string[]).includes(key),
      ),
    ) as Partial<Record<Platform, number>>,
    langs: facets.language_order.map((lang) => {
      const n = Object.hasOwn(facets.languages, lang)
        ? facets.languages[lang]
        : undefined;
      if (typeof n !== "number" || !Number.isSafeInteger(n) || n < 0)
        throw new MirrorMisconfigured("control_shape");
      return { lang, n };
    }),
    bases: Object.fromEntries(
      Object.entries(facets.bases).filter(([key]) =>
        (BASES as readonly string[]).includes(key),
      ),
    ) as Partial<Record<Basis, number>>,
    posted: {
      pool: facets.posted.pool ?? 0,
      yes: facets.posted.yes ?? 0,
      no: facets.posted.no ?? 0,
    },
  };
}
export async function loadCommonCandidatePool(): Promise<number> {
  return (await read(parsePickRequest({ tab: "pick" }))).counts.matched;
}

function count(value: unknown): number {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}
function commonRankMeta(response: QueryResponse, req: PickRequest): RankMeta {
  const data = dataOf(response);
  const facets = response.facets;
  const periods = response.period_options;
  if (!facets || !periods || !data.rs_counts)
    throw new MirrorMisconfigured("control_shape");
  const daily = isDailyRank(req.rank);
  const weekly = req.rank === "kw";
  return {
    counts: Object.fromEntries(
      Object.entries(facets.ranks).filter(([key]) =>
        (RANKS as readonly string[]).includes(key),
      ),
    ) as Partial<Record<RankKey, number>>,
    grades: Object.fromEntries(
      Object.entries(facets.grades).filter(([key]) =>
        (GRADES as readonly string[]).includes(key),
      ),
    ) as Partial<Record<Grade, number>>,
    growthCounts: {
      d1: count(data.rs_counts.growthD1),
      d7: count(data.rs_counts.growthD7),
      dp1: count(data.rs_counts.growthDp1),
      dp7: count(data.rs_counts.growthDp7),
    },
    days: periods.days,
    weeks: periods.weeks,
    day:
      daily && response.actual_period?.kind === "daily"
        ? (response.actual_period.value ?? "")
        : "",
    week:
      weekly && response.actual_period?.kind === "weekly"
        ? (response.actual_period.value ?? "")
        : "",
    dayResolution: daily
      ? req.day && !validDay(req.day)
        ? "missing"
        : periods.resolution
      : "latest",
    weekResolution: weekly ? periods.resolution : "latest",
  };
}
export async function loadCommonRankMeta(req: PickRequest): Promise<RankMeta> {
  return commonRankMeta(await read(req), req);
}
export async function loadCommonRankRows(
  req: PickRequest,
  _meta: RankMeta,
): Promise<RowsPage<RankRow>> {
  if (isRsRank(req.rank)) throw new Error("ReelShort 榜走 loadRsRank");
  const response = await read(req);
  const board = dataOf(response);
  const page = catalogPage(response);
  const raw = new Map(board.catalog_rows.map((row) => [row.row_key, row]));
  return {
    ...page,
    rows: page.rows.map((row, index) => {
      const rank = board.rank_rows[index];
      const base = raw.get(row.rowKey);
      if (!rank || !base || rank.row_key !== row.rowKey)
        throw new MirrorMisconfigured("control_shape");
      const signal: PickSignal = {
        kind: (BASES as readonly string[]).includes(rank.signal.kind)
          ? (rank.signal.kind as Basis)
          : (req.rank as Basis),
        ord: rank.signal.ord,
        evidenceOn: rank.signal.evidence_on,
        rank: rank.signal.rank,
        grade: rank.signal.grade,
        note: rank.signal.note,
        payload: rank.signal.payload,
      };
      return {
        // Legacy rank SQL selects signal.kind after catalog.kind under the same column name.
        ...toRow({ ...base, kind: rank.signal.kind }),
        signals: [signal],
        posted: row.posted,
        signal,
        dayRank: rank.day_rank,
        dayNote: rank.day_note,
      };
    }),
  };
}
export async function loadCommonRsRank(
  req: PickRequest,
  rank: RsRank,
): Promise<RsRankResult> {
  const response = await read(req);
  const board = dataOf(response);
  if (rank === "rs_ledger") {
    if (!board.bill_totals) throw new MirrorMisconfigured("control_shape");
    const totals = board.bill_totals;
    return {
      kind: "ledger",
      rows: board.bill_rows.map((row) => ({
        billDate: row.bill_date,
        bookId: row.book_id,
        promotionType: row.promotion_type,
        canonicalId: row.canonical_id,
        title: row.title,
        locale: row.locale ?? "",
        orderCnt: row.order_cnt,
        sourceRows: row.source_rows,
        sameDayClicks: row.same_day_clicks,
      })),
      totals: {
        rows: totals.rows,
        mergedRows: totals.merged_rows,
        orders: totals.orders,
        mergedWithClicks: totals.merged_with_clicks,
        rowsWithClicks: totals.rows_with_clicks,
      },
      source: sourcesOf(board.sources).bill,
    };
  }
  const rows = catalogPage(response).rows.map((row) => {
    if (!row.rs) throw new MirrorMisconfigured("control_shape");
    return row.rs;
  });
  return {
    kind: "rows",
    rows,
    total: rank === "rs_growth" ? null : response.counts.matched,
    hasMore:
      rank === "rs_growth"
        ? response.counts.matched > rows.length
        : response.next_offset !== null,
    sort: rsSortFor(rank, req.rsSort),
  };
}
export async function loadCommonGrowthDiagnosis(req: PickRequest) {
  const response = await read(req);
  const board = dataOf(response);
  const sort = rsSortFor("rs_growth", req.rsSort);
  const windowDays = sort === "d1" || sort === "dp1" ? 1 : 7;
  const raw = board.growth_baseline[String(windowDays)];
  const fallback =
    addUtcDays(
      new Date(boardScope().asOf).toISOString().slice(0, 10),
      -windowDays,
    ) ?? "";
  const snapshot = raw?.baselineSnapshot;
  return diagnoseGrowthEmpty({
    windowDays,
    baselineDay:
      typeof raw?.baselineDay === "string" ? raw.baselineDay : fallback,
    baselineSnapshot:
      typeof snapshot === "string" &&
      ["none", "unverified_only", "verified"].includes(snapshot)
        ? (snapshot as BaselineSnapshot)
        : "none",
    earliestVerifiedOn:
      typeof raw?.earliestVerifiedOn === "string"
        ? raw.earliestVerifiedOn
        : null,
    filtered: req.rsLocale !== "" || req.rsBucket !== null || req.q !== "",
    comparableWithoutFilters:
      (commonRankMeta(response, req).growthCounts[
        sort as "d1" | "d7" | "dp1" | "dp7"
      ] ?? 0) > 0,
  });
}
export async function loadCommonPostedList(
  req: PickRequest,
): Promise<PostedList> {
  const response = await read(req);
  const board = dataOf(response);
  const states = response.facets?.posted_states;
  if (!states) throw new MirrorMisconfigured("control_shape");
  const pub = states.pub ?? 0,
    sched = states.sched ?? 0,
    none = states.none ?? 0;
  return {
    rows: board.posted.map(toPostedRecord),
    total: response.counts.matched,
    hasMore: response.next_offset !== null,
    counts: {
      "": pub + sched + none,
      pub,
      sched,
      none,
      nomatch: states.nomatch ?? 0,
    },
    links: {
      rows: new Map(
        board.catalog_rows.map((row) => [
          row.row_key,
          {
            rowKey: row.row_key,
            platform: platformOf(row.platform),
            lang: row.lang,
            title: row.title,
            offOn: row.off_on,
          },
        ]),
      ),
      dramas: new Map(
        board.rs_ids.map((row) => [
          row.id,
          { id: row.id, locale: row.locale, slug: row.slug, title: row.title },
        ]),
      ),
    },
  };
}
const readPosted = () => read(parsePickRequest({ tab: "posted" }));
export async function loadCommonPostedStats(): Promise<PostedStats> {
  const raw = dataOf(await readPosted()).posted_stats;
  if (!raw) throw new MirrorMisconfigured("control_shape");
  return {
    total: count(raw.total),
    pubCount: count(raw.pubCount),
    postsSum: count(raw.postsSum),
    viewsSum: count(raw.viewsSum),
    metricAt: typeof raw.metricAt === "string" ? raw.metricAt : null,
    importedAt: toTimestamp(raw.importedAt),
    accountCount: count(raw.accountCount),
  };
}
export async function loadCommonAccounts(): Promise<CatalogAccount[]> {
  return dataOf(await readPosted()).accounts.map((row) => ({
    id: row.id,
    name: row.name,
    url: row.url,
    grp: row.grp,
    form: row.form,
    niche: row.niche,
    status: row.status,
    fans: row.fans,
    asOf: row.as_of,
  }));
}
