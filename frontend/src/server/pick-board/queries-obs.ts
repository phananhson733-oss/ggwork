import "server-only";

import { sql, type SQL } from "drizzle-orm";
import { z } from "zod";

import {
  DISCOVERY_ROUTES,
  obsDiscoverySchema,
  obsLinkSchema,
  obsRunSchema,
  obsSetBriefSchema,
  obsSetSchema,
  obsStateSchema,
  type DiscoveryRoute,
  type ObsDiscovery,
  type ObsLink,
  type ObsRun,
  type ObsSet,
  type ObsSetBrief,
  type ObsState,
} from "@/core/pick/obs-rows";
import type { ObsChannel } from "@/core/pick/obs-status";
import type { ObsTab } from "@/core/pick-board/request";

import { makeCacheCell, remember, type CacheCell } from "./cache";
import { obsDb, type Executor } from "./db";
import { ObsRowInvalid, type ObsViewName } from "./errors";

/**
 * The radar's two tabs and an identity's detail (plan TR-24; design 3.6; D9,
 * D13): reads of the pick_obs views through obsDb, never the ggwp tables.
 *
 * Which set a channel shows is read live on every request: one statement for
 * the channel's head (its newest published sets, its newest live set and its
 * latest run, one snapshot), then the shown set with status = 'published'.
 * The shown set is the pinned one (?obs=) when it is this channel's and still
 * published, else the live one, else the newest published one (a shadow set
 * before go-live). What belongs to one published set, or to one pair of them,
 * never changes, so it is remembered by set id, and only ever looked up after
 * this request has read that set as published: a pruned set is never shown
 * from the cache. Link facts are read as frozen; their actionability is the
 * view's, at request time (obs-link.ts).
 *
 * Every row is parsed (obs-rows.ts) before it is returned; a row the page
 * cannot read is ObsRowInvalid, naming the view only.
 */

export const RECENT_SETS = 8;
export const DISCOVERY_LIMIT = 500;
export const STATE_LIMIT = 300;
/** A few sets' discoveries and states, and the identities someone opened lately. */
const OBS_CACHE_ENTRIES = 48;

export type ObsDeps = Readonly<{
  db: () => Executor;
  cell: CacheCell<Promise<unknown>>;
}>;

let obsCache = makeCacheCell<Promise<unknown>>(OBS_CACHE_ENTRIES);

/** Test hook: an empty cache. */
export function resetObsCacheForTests(): void {
  obsCache = makeCacheCell<Promise<unknown>>(OBS_CACHE_ENTRIES);
}

const defaultDeps = (): ObsDeps => ({ db: obsDb, cell: obsCache });

export type ObsPin = "none" | "shown" | "missing";

export type ObsChannelLoad = Readonly<{
  channel: ObsChannel;
  /** The set this page shows for the channel; null when none is published. */
  shown: ObsSet | null;
  /** The newest live set, for the stale banner; null before go-live. */
  live: ObsSetBrief | null;
  latestRun: ObsRun | null;
  /** none: no ?obs=; shown: the pinned set is shown; missing: it is not this channel's published set. */
  pin: ObsPin;
  /** The newest published sets, newest first, to pin one. */
  recent: readonly ObsSetBrief[];
}>;

export type ObsDiscoveryPage = Readonly<{
  rows: readonly ObsDiscovery[];
  /** Every route's count in the set, whatever the limit cut; a route with no row is absent. */
  counts: Readonly<Partial<Record<DiscoveryRoute, number>>>;
  truncated: boolean;
}>;

export type ObsStatePage = Readonly<{
  /** The rows with at least one label hit, formal first. */
  rows: readonly ObsState[];
  total: number;
  labeled: number;
  truncated: boolean;
}>;

export type ObsTabData =
  | Readonly<{
      kind: "trends";
      trends: ObsChannelLoad;
      discoveries: ObsDiscoveryPage | null;
    }>
  | Readonly<{
      kind: "search";
      gsc: ObsChannelLoad;
      states: ObsStatePage | null;
    }>
  | Readonly<{
      kind: "detail";
      tab: ObsTab;
      identity: string;
      trends: ObsChannelLoad;
      gsc: ObsChannelLoad;
      states: readonly ObsState[];
      discoveries: readonly ObsDiscovery[];
      links: readonly ObsLink[];
    }>;

export type ObsRequest = Readonly<{ tab: ObsTab; obs: string; oid: string }>;

type Row = Record<string, unknown>;

function parsed<S extends z.ZodTypeAny>(
  view: ObsViewName,
  schema: S,
  value: unknown,
): z.output<S> {
  const result = schema.safeParse(value);
  if (result.success) return result.data as z.output<S>;
  console.warn(
    "[pick-obs] row refused",
    view,
    result.error.issues.map((issue) => issue.path.join(".")),
  );
  throw new ObsRowInvalid(view);
}

// ---- columns ------------------------------------------------------------------------------------------------------

const SET_COLUMNS = sql.raw(
  "set_id, channel, mode, status, published_at, as_of, source_catalog_batch_id, collector_version, rules_version, link_rules_version, alias_version, decisions_version, target_date, window_end, round_id, frozen_inputs, summary",
);
const RUN_COLUMNS = sql.raw(
  "channel, batch_id, mode, target_date, round_id, started_at, finished_at, outcome, requests, published_set_id, status_codes",
);
const STATE_COLUMNS = sql.raw(
  "row_id, set_id, channel, mode, identity, title, language, theater, scope, window_kind, state, confirmation, admission, labels, tier, correspondence, id_evidence, ambiguity, flags, carried_over, stale, window_end, latest_block_end, metrics, quality_note, paste_row, created_at",
);
const DISCOVERY_COLUMNS = sql.raw(
  "discovery_id, set_id, mode, geo, seed, property, term, normalized_term, language, match_status, route, matched_identity, breakout, first_seen_at",
);
const LINK_COLUMNS = sql.raw(
  "id, link_rules_version, trends_set_id, gsc_set_id, mode, identity, country, trends_geo, label, trends_row_id, gsc_row_id, trends_anchor, gsc_anchor, pair_gap_minutes, timely, published_gap_minutes, stale, created_at",
);

// ---- a channel ----------------------------------------------------------------------------------------------------

type HeadRow = Readonly<{ recent: unknown; live: unknown; run: unknown }>;

/** The newest published sets, the newest live set and the latest run of one channel, in one snapshot. */
function headStatement(channel: ObsChannel): SQL {
  return sql`SELECT
    (SELECT json_agg(r ORDER BY r.published_at DESC, r.set_id DESC) FROM (
      SELECT set_id, mode, published_at FROM sets WHERE channel = ${channel} AND status = 'published'
      ORDER BY published_at DESC, set_id DESC LIMIT ${RECENT_SETS}) r) AS recent,
    (SELECT row_to_json(l) FROM (
      SELECT set_id, mode, published_at FROM sets WHERE channel = ${channel} AND status = 'published' AND mode = 'live'
      ORDER BY published_at DESC, set_id DESC LIMIT 1) l) AS live,
    (SELECT row_to_json(x) FROM (
      SELECT ${RUN_COLUMNS} FROM run_status WHERE channel = ${channel}
      ORDER BY started_at DESC, batch_id DESC LIMIT 1) x) AS run`;
}

async function readSet(
  db: Executor,
  channel: ObsChannel,
  setId: string,
): Promise<ObsSet | null> {
  const { rows } = await db.execute<Row>(
    sql`SELECT ${SET_COLUMNS} FROM sets WHERE set_id = ${setId} AND channel = ${channel} AND status = 'published'`,
  );
  return rows[0] === undefined ? null : parsed("sets", obsSetSchema, rows[0]);
}

async function loadChannel(
  channel: ObsChannel,
  pinned: string,
  deps: ObsDeps,
): Promise<ObsChannelLoad> {
  const db = deps.db();
  const { rows } = await db.execute<HeadRow>(headStatement(channel));
  const head = rows[0];
  const recent = parsed("sets", z.array(obsSetBriefSchema), head?.recent ?? []);
  const live =
    head?.live == null ? null : parsed("sets", obsSetBriefSchema, head.live);
  const latestRun =
    head?.run == null ? null : parsed("run_status", obsRunSchema, head.run);
  const pinnedSet = pinned ? await readSet(db, channel, pinned) : null;
  const fallback = live?.set_id ?? recent[0]?.set_id;
  const shown =
    pinnedSet ?? (fallback ? await readSet(db, channel, fallback) : null);
  const pin: ObsPin = !pinned ? "none" : pinnedSet ? "shown" : "missing";
  return { channel, shown, live, latestRun, pin, recent };
}

// ---- one set's rows (immutable once published) --------------------------------------------------------------------

function rememberFor<V>(
  deps: ObsDeps,
  key: string,
  load: () => Promise<V>,
): Promise<V> {
  return remember(deps.cell as CacheCell<Promise<V>>, key, load);
}

const routeCountsSchema = z.array(
  z.object({ route: z.enum(DISCOVERY_ROUTES), n: z.number().int().min(1) }),
);

function readDiscoveries(
  set: ObsSet,
  deps: ObsDeps,
): Promise<ObsDiscoveryPage> {
  return rememberFor(deps, `discoveries|${set.set_id}`, async () => {
    const db = deps.db();
    const [list, counted] = await Promise.all([
      db.execute<Row>(
        sql`SELECT ${DISCOVERY_COLUMNS} FROM discoveries WHERE set_id = ${set.set_id}
            ORDER BY CASE route WHEN 'queue' THEN 0 WHEN 'display_only' THEN 1 ELSE 2 END,
              breakout DESC, geo, normalized_term, discovery_id
            LIMIT ${DISCOVERY_LIMIT + 1}`,
      ),
      db.execute<Row>(
        sql`SELECT route, count(*)::int AS n FROM discoveries WHERE set_id = ${set.set_id} GROUP BY route`,
      ),
    ]);
    const rows = list.rows.map((row) =>
      parsed("discoveries", obsDiscoverySchema, row),
    );
    const counts = parsed("discoveries", routeCountsSchema, counted.rows);
    return {
      rows: rows.slice(0, DISCOVERY_LIMIT),
      counts: Object.fromEntries(counts.map(({ route, n }) => [route, n])),
      truncated: rows.length > DISCOVERY_LIMIT,
    };
  });
}

const stateCountsSchema = z.object({
  total: z.number().int().nonnegative(),
  labeled: z.number().int().nonnegative(),
});

function readStates(set: ObsSet, deps: ObsDeps): Promise<ObsStatePage> {
  return rememberFor(deps, `states|${set.set_id}`, async () => {
    const db = deps.db();
    const [list, counted] = await Promise.all([
      db.execute<Row>(
        sql`SELECT ${STATE_COLUMNS} FROM states WHERE set_id = ${set.set_id} AND json_array_length(labels) > 0
            ORDER BY (admission = 'formal') DESC, (scope = 'ALL') DESC, window_kind, scope, title, row_id
            LIMIT ${STATE_LIMIT + 1}`,
      ),
      db.execute<Row>(
        sql`SELECT count(*)::int AS total, (count(*) FILTER (WHERE json_array_length(labels) > 0))::int AS labeled
            FROM states WHERE set_id = ${set.set_id}`,
      ),
    ]);
    const rows = list.rows.map((row) => parsed("states", obsStateSchema, row));
    const { total, labeled } = parsed(
      "states",
      stateCountsSchema,
      counted.rows[0] ?? { total: 0, labeled: 0 },
    );
    return {
      rows: rows.slice(0, STATE_LIMIT),
      total,
      labeled,
      truncated: rows.length > STATE_LIMIT,
    };
  });
}

// ---- one identity -------------------------------------------------------------------------------------------------

type IdentityRows = Readonly<{
  states: readonly ObsState[];
  discoveries: readonly ObsDiscovery[];
  links: readonly ObsLink[];
}>;

const NO_ROWS: IdentityRows = { states: [], discoveries: [], links: [] };

/** The identity's rows of the two shown sets, and the link facts of exactly that pair, in one snapshot. */
function identityStatement(
  trendsId: string | null,
  gscId: string | null,
  identity: string,
): SQL {
  return sql`SELECT
    (SELECT coalesce(json_agg(s ORDER BY s.channel DESC, s.scope, s.window_kind, s.row_id), '[]'::json) FROM (
      SELECT ${STATE_COLUMNS} FROM states WHERE set_id IN (${trendsId}, ${gscId}) AND identity = ${identity}) s) AS states,
    (SELECT coalesce(json_agg(d ORDER BY d.discovery_id), '[]'::json) FROM (
      SELECT ${DISCOVERY_COLUMNS} FROM discoveries WHERE set_id = ${trendsId} AND matched_identity = ${identity}) d) AS discoveries,
    (SELECT coalesce(json_agg(l ORDER BY l.country NULLS LAST, l.id), '[]'::json) FROM (
      SELECT ${LINK_COLUMNS} FROM links
      WHERE trends_set_id = ${trendsId} AND gsc_set_id = ${gscId} AND identity = ${identity}) l) AS links`;
}

function readIdentity(
  trends: ObsSet | null,
  gsc: ObsSet | null,
  identity: string,
  deps: ObsDeps,
): Promise<IdentityRows> {
  if (!trends && !gsc) return Promise.resolve(NO_ROWS);
  const trendsId = trends?.set_id ?? null;
  const gscId = gsc?.set_id ?? null;
  const key = `identity|${trendsId ?? "-"}|${gscId ?? "-"}|${identity}`;
  return rememberFor(deps, key, async () => {
    const { rows } = await deps
      .db()
      .execute<Row>(identityStatement(trendsId, gscId, identity));
    const row = rows[0] ?? { states: [], discoveries: [], links: [] };
    return {
      states: parsed("states", z.array(obsStateSchema), row.states),
      discoveries: parsed(
        "discoveries",
        z.array(obsDiscoverySchema),
        row.discoveries,
      ),
      links: parsed("links", z.array(obsLinkSchema), row.links),
    };
  });
}

// ---- the page's entry ---------------------------------------------------------------------------------------------

const CHANNEL_OF_TAB: Readonly<Record<ObsTab, ObsChannel>> = {
  trends: "trends",
  search: "gsc",
};

function pinFor(req: ObsRequest, channel: ObsChannel): string {
  return CHANNEL_OF_TAB[req.tab] === channel ? req.obs : "";
}

async function loadDetail(req: ObsRequest, deps: ObsDeps): Promise<ObsTabData> {
  const [trends, gsc] = await Promise.all([
    loadChannel("trends", pinFor(req, "trends"), deps),
    loadChannel("gsc", pinFor(req, "gsc"), deps),
  ]);
  const rows = await readIdentity(trends.shown, gsc.shown, req.oid, deps);
  return {
    kind: "detail",
    tab: req.tab,
    identity: req.oid,
    trends,
    gsc,
    ...rows,
  };
}

/** One radar tab, or an identity's detail when ?oid= is set. */
export async function loadObsTab(
  req: ObsRequest,
  deps: ObsDeps = defaultDeps(),
): Promise<ObsTabData> {
  if (req.oid) return loadDetail(req, deps);
  if (req.tab === "trends") {
    const trends = await loadChannel("trends", req.obs, deps);
    const discoveries = trends.shown
      ? await readDiscoveries(trends.shown, deps)
      : null;
    return { kind: "trends", trends, discoveries };
  }
  const gsc = await loadChannel("gsc", req.obs, deps);
  const states = gsc.shown ? await readStates(gsc.shown, deps) : null;
  return { kind: "search", gsc, states };
}
