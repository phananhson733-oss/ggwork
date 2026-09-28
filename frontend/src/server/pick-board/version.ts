import "server-only";

import { sql } from "drizzle-orm";
import { z } from "zod";

import {
  controlDb as readerControlDb,
  versionDb as readerVersionDb,
  type Executor,
  type VersionScope,
} from "./db";
import {
  errorCode,
  MirrorError,
  MirrorMisconfigured,
  MirrorVersionGone,
} from "./errors";

/**
 * Which mirror version a request reads (plan 7.5 step 4).
 *
 * Step 1 reads pick_mirror.versions through controlDb: the current version,
 * picked the way the agent pins it (published_at DESC, id DESC), the version
 * the URL names, and series_state. Step 2 reads the chosen version's
 * meta.rules and meta.sources. A version pruned between the two steps is
 * answered by running once more without v and flagging it pruned. Step 1
 * finding a table gone is not a prune: pick_mirror is never dropped, so the
 * reader names the wrong database or the migration is missing.
 *
 * `buildRules` turns meta.rules into the board's rules; it is injected so this
 * module does not depend on the pure rules module (P3-2). It throwing means
 * the published rules do not fit the board: MirrorMisconfigured.
 */

const MAX_VERSION_ID = 999_999;
const NO_VERSION = -1;

const READABLE = sql.raw(
  "EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname = v.schema_name AND has_schema_privilege(n.oid, 'USAGE'))",
);

function controlQuery(lookup: number) {
  return sql`SELECT
  (SELECT row_to_json(c) FROM (
     SELECT id::int AS id, schema_name, as_of, published_at, latest_snapshot, freshness, warnings,
            agent_catalog_batch_id, agent_knowledge_batch_id, ${READABLE} AS readable
     FROM pick_mirror.versions v WHERE status = 'published'
     ORDER BY published_at DESC, id DESC LIMIT 1) c) AS current,
  (SELECT row_to_json(r) FROM (
     SELECT id::int AS id, schema_name, status, as_of, latest_snapshot, freshness, warnings,
            ${READABLE} AS readable
     FROM pick_mirror.versions v WHERE id = ${lookup}::bigint) r) AS requested,
  (SELECT row_to_json(s) FROM (
     SELECT through, trimmed_before FROM pick_mirror.series_state WHERE id = 1) s) AS series_state`;
}

const META_QUERY = sql`SELECT key, value FROM meta WHERE key IN ('rules', 'sources', 'control')`;

const versionRow = {
  id: z.number().int().positive(),
  schema_name: z.string(),
  as_of: z.string(),
  latest_snapshot: z.string().nullable(),
  freshness: z.unknown(),
  warnings: z.unknown(),
  readable: z.boolean(),
};

const controlSchema = z.object({
  current: z
    .object({
      ...versionRow,
      published_at: z.string(),
      agent_catalog_batch_id: z.string().nullable(),
      agent_knowledge_batch_id: z.string().nullable(),
    })
    .nullable(),
  requested: z
    .object({
      ...versionRow,
      status: z.enum(["building", "published", "failed", "dropped"]),
    })
    .nullable(),
  series_state: z
    .object({
      through: z.string().nullable(),
      trimmed_before: z.string().nullable(),
    })
    .nullable(),
});

type Control = z.infer<typeof controlSchema>;
type CurrentRow = NonNullable<Control["current"]>;
type ChosenRow = CurrentRow | NonNullable<Control["requested"]>;

export type SeriesState = Readonly<{
  through: string | null;
  trimmedBefore: string | null;
}>;

/** One entry of manifest.meta.warnings, e.g. catalog_import_incomplete. */
export type VersionWarning = Readonly<
  { code: string } & Record<string, unknown>
>;

export type CurrentVersion = Readonly<{
  id: number;
  asOf: string;
  publishedAt: string;
  agentCatalogBatchId: string | null;
  agentKnowledgeBatchId: string | null;
}>;

export type ReadyBoard<R> = Readonly<{
  state: "ready";
  /** What setBoardScope takes. */
  scope: VersionScope<R>;
  current: CurrentVersion;
  latestSnapshot: string | null;
  /** versions.freshness: manifest.meta.freshness, verbatim. */
  freshness: Readonly<Record<string, unknown>> | null;
  warnings: readonly VersionWarning[];
  /** meta.sources, verbatim; null when the version has none. */
  sources: unknown;
  /** 运营发布记录自己的导入时间，不能拿剧单或镜像采集时间代替。 */
  postedImportedAt: string | null;
  series: SeriesState | null;
  /** The v of the URL, as given. */
  requestedV: number | null;
  /** Showing the v of the URL, which is not the current version. */
  pinned: boolean;
  /** The v of the URL was pruned: showing current. */
  pruned: boolean;
  /** The v of the URL is not a published version: showing current. */
  ignoredV: boolean;
  /** The v of the URL is published but the reader may not read it. */
  unreadable: boolean;
}>;

export type EmptyBoard = Readonly<{
  state: "empty";
  requestedV: number | null;
  series: SeriesState | null;
}>;

export type BoardVersion<R> = ReadyBoard<R> | EmptyBoard;

export type RulesBuilder<R> = (raw: unknown, versionId: number) => R;

export type VersionReaders = Readonly<{
  controlDb: () => Executor;
  versionDb: (schema: string) => Executor;
}>;

const READERS: VersionReaders = {
  controlDb: readerControlDb,
  versionDb: readerVersionDb,
};

type Flags = Pick<
  ReadyBoard<unknown>,
  "pinned" | "pruned" | "ignoredV" | "unreadable"
>;
const NO_FLAGS: Flags = {
  pinned: false,
  pruned: false,
  ignoredV: false,
  unreadable: false,
};

function isVersionId(v: number | null): v is number {
  return v !== null && Number.isInteger(v) && v >= 1 && v <= MAX_VERSION_ID;
}

async function readControl(db: Executor, lookup: number): Promise<Control> {
  const { rows } = await db.execute(controlQuery(lookup)).catch((error) => {
    if (!(error instanceof MirrorVersionGone)) throw error;
    throw new MirrorMisconfigured("control_missing", error.sourceCode);
  });
  const parsed = controlSchema.safeParse(rows[0]);
  if (!parsed.success) throw new MirrorMisconfigured("control_shape");
  return parsed.data;
}

/** The version to read, and why, given the current one is readable. */
function choose(
  control: Control & { current: CurrentRow },
  v: number | null,
): { row: ChosenRow; flags: Flags } {
  const { current, requested } = control;
  if (v === null) return { row: current, flags: NO_FLAGS };
  if (!isVersionId(v) || requested === null) {
    return { row: current, flags: { ...NO_FLAGS, ignoredV: true } };
  }
  if (requested.status === "published" && requested.readable) {
    const pinned = requested.id !== current.id;
    return { row: requested, flags: { ...NO_FLAGS, pinned } };
  }
  if (requested.status === "dropped") {
    return { row: current, flags: { ...NO_FLAGS, pruned: true } };
  }
  if (requested.status === "published") {
    console.error("[pick-board] mirror version not readable", {
      versionId: requested.id,
    });
    return { row: current, flags: { ...NO_FLAGS, unreadable: true } };
  }
  return { row: current, flags: { ...NO_FLAGS, ignoredV: true } };
}

async function readMeta(db: Executor): Promise<Map<string, unknown>> {
  const { rows } = await db.execute<{ key: string; value: unknown }>(
    META_QUERY,
  );
  return new Map(rows.map((row) => [row.key, row.value]));
}

function buildOrMisconfigured<R>(
  buildRules: RulesBuilder<R>,
  raw: unknown,
  versionId: number,
): R {
  try {
    return buildRules(raw, versionId);
  } catch (error) {
    // A RangeError is the caller's bug (a bad versionId), not bad data.
    if (error instanceof MirrorError || error instanceof RangeError) {
      throw error;
    }
    console.error("[pick-board] version rules rejected", {
      versionId,
      code:
        errorCode(error) ?? (error instanceof Error ? error.name : "unknown"),
      paths: fieldPaths(error),
    });
    throw new MirrorMisconfigured("rules");
  }
}

/** The field paths a rules error names (P3-2's BoardRulesInvalid), at most ten. */
function fieldPaths(error: unknown): string[] {
  const paths = (error as { paths?: unknown } | null)?.paths;
  if (!Array.isArray(paths)) return [];
  return paths
    .filter((path): path is string => typeof path === "string")
    .slice(0, 10);
}

function record(value: unknown): Readonly<Record<string, unknown>> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function warningsOf(value: unknown): readonly VersionWarning[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is VersionWarning => typeof record(item)?.code === "string",
  );
}

function seriesOf(control: Control): SeriesState | null {
  const state = control.series_state;
  return state
    ? { through: state.through, trimmedBefore: state.trimmed_before }
    : null;
}

function currentOf(row: CurrentRow): CurrentVersion {
  return {
    id: row.id,
    asOf: row.as_of,
    publishedAt: row.published_at,
    agentCatalogBatchId: row.agent_catalog_batch_id,
    agentKnowledgeBatchId: row.agent_knowledge_batch_id,
  };
}

async function resolveOnce<R>(
  v: number | null,
  buildRules: RulesBuilder<R>,
  readers: VersionReaders,
): Promise<BoardVersion<R>> {
  const lookup = isVersionId(v) ? v : NO_VERSION;
  const control = await readControl(readers.controlDb(), lookup);
  const { current } = control;
  if (current === null) {
    return Object.freeze({
      state: "empty",
      requestedV: v,
      series: seriesOf(control),
    });
  }
  if (!current.readable) {
    console.error("[pick-board] current mirror version not readable", {
      versionId: current.id,
    });
    throw new MirrorMisconfigured("current_unreadable");
  }
  const { row, flags } = choose({ ...control, current }, v);
  const meta = await readMeta(readers.versionDb(row.schema_name));
  const rules = buildOrMisconfigured(buildRules, meta.get("rules"), row.id);
  const postedAt = record(record(meta.get("control"))?.postedStats)?.importedAt;
  return Object.freeze({
    state: "ready",
    scope: {
      schema: row.schema_name,
      asOf: row.as_of,
      versionId: row.id,
      rules,
    },
    current: currentOf(current),
    latestSnapshot: row.latest_snapshot,
    freshness: record(row.freshness),
    warnings: warningsOf(row.warnings),
    sources: meta.get("sources") ?? null,
    postedImportedAt: typeof postedAt === "string" ? postedAt : null,
    series: seriesOf(control),
    requestedV: v,
    ...flags,
  });
}

/**
 * The version a request reads, given the URL's v (null when absent). Throws
 * MirrorMisconfigured when the current version cannot be read, and the typed
 * errors of db.ts when the reads fail.
 */
export async function resolveVersion<R>(
  v: number | null,
  buildRules: RulesBuilder<R>,
  readers: VersionReaders = READERS,
): Promise<BoardVersion<R>> {
  try {
    return await resolveOnce(v, buildRules, readers);
  } catch (error) {
    // Only step 2 throws Gone: readControl turns step 1's into Misconfigured.
    if (!(error instanceof MirrorVersionGone)) throw error;
    // Pruned between the two steps: the current version, once more.
    const retried = await resolveOnce(null, buildRules, readers);
    if (retried.state === "empty") {
      return Object.freeze({ ...retried, requestedV: v });
    }
    return Object.freeze({ ...retried, requestedV: v, pruned: v !== null });
  }
}
