import "server-only";

import { AsyncLocalStorage } from "node:async_hooks";

import type { SQL } from "drizzle-orm";
import { PgDialect } from "drizzle-orm/pg-core";
import { Pool, types, type CustomTypesConfig } from "pg";
import { cache } from "react";

import {
  DEADLOCK_SQLSTATE,
  errorCode,
  isConnectionFailure,
  MirrorMisconfigured,
  MirrorUnavailable,
  translateMirrorError,
} from "./errors";

/**
 * Read-only access to the pick mirror (pick_mirror + one pickm_vNNNNNN schema
 * per version) as the pick_board_reader role.
 *
 * Every query is its own `BEGIN READ ONLY; SET LOCAL search_path` transaction
 * on a pooled client: behind Supavisor's transaction mode a session setting
 * would leak to the next client, and named statements are not supported, so
 * a query config never carries `name`. Versions are immutable, so parallel
 * queries of one render are consistent without sharing a transaction.
 *
 * The version a request reads is pinned once per request in a holder the page
 * fills before it returns any JSX (setBoardScope); child server components
 * read it back through getDb(). Scripts and tests without a React render use
 * withScriptScope. makeScope builds all of these around an injected holder and
 * pool, so tests can share one holder; production binds React's per-request
 * cache() and the reader pool.
 */

const VERSION_SCHEMA = /^pickm_v[0-9]{6}$/;
const CONTROL_SCHEMA = "pick_mirror";
const READER_PROTOCOLS = new Set(["postgres:", "postgresql:"]);
const PEM_CERTIFICATE = "-----BEGIN CERTIFICATE-----";
const POOL_LIMITS = {
  max: 3,
  idleTimeoutMillis: 5_000,
  connectionTimeoutMillis: 5_000,
  // Client-side backstop behind the role's 8 s statement_timeout.
  query_timeout: 10_000,
  application_name: "pick-board",
} as const;

// The list drizzle-orm 0.45.2's neon-http driver returns as text
// (neon-http/driver.js initMappers): timestamptz, timestamp, date, interval,
// and numeric[], timestamp[], timestamptz[], interval[], date[]. The ported
// RealShort queries expect exactly these shapes. Set on this pool's clients
// only, never on pg's global parsers.
const TEXT_OIDS: ReadonlySet<number> = new Set([
  1184, 1114, 1082, 1186, 1231, 1115, 1185, 1187, 1182,
]);
const identity = (value: string) => value;

export const mirrorTypes: CustomTypesConfig = {
  getTypeParser: ((oid: number, format?: "text" | "binary") =>
    TEXT_OIDS.has(oid)
      ? identity
      : types.getTypeParser(oid, format)) as CustomTypesConfig["getTypeParser"],
};

export type VersionScope<R = unknown> = Readonly<{
  schema: string;
  asOf: string;
  versionId: number;
  rules: R;
}>;

/** The one mutable container at the framework boundary: one per request. */
export type ScopeHolder = { scope: VersionScope | null };

export type QueryRows<R> = Readonly<{ rows: R[] }>;

export type Executor = Readonly<{
  execute: <R = Record<string, unknown>>(query: SQL) => Promise<QueryRows<R>>;
}>;

export type MirrorQuery = Readonly<{ text: string; values: unknown[] }>;

/** The part of pg's PoolClient these reads use. */
export interface MirrorClient {
  query(statement: string | MirrorQuery): Promise<{ rows: unknown[] }>;
  release(destroy?: Error | boolean): void;
}

/** The part of pg's Pool these reads use. */
export interface MirrorPool {
  connect(): Promise<MirrorClient>;
}

export type MirrorConnection = Readonly<{
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
}>;

/** TLS verified against a CA; `false` only from a test entry (NODE_ENV=test). */
export type MirrorTls = false | Readonly<{ ca: string }>;

export function checkVersionSchema(schema: string): string {
  if (!VERSION_SCHEMA.test(schema)) {
    throw new Error("pick-board: not a mirror version schema");
  }
  return schema;
}

function decodePart(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    throw new MirrorMisconfigured("url");
  }
}

/**
 * The reader URL split into explicit connection fields. Any query parameter
 * is refused: pg lets URL parameters override code-supplied settings, and an
 * `sslmode` there would replace the CA-verified ssl object.
 */
export function parseReaderUrl(raw: string): MirrorConnection {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new MirrorMisconfigured("url");
  }
  if (!READER_PROTOCOLS.has(url.protocol) || url.search || url.hash) {
    throw new MirrorMisconfigured("url");
  }
  const connection = {
    host: decodePart(url.hostname).replace(/^\[(.*)\]$/, "$1"),
    port: url.port ? Number(url.port) : 5432,
    user: decodePart(url.username),
    password: decodePart(url.password),
    database: decodePart(url.pathname.replace(/^\//, "")),
  };
  const missing = [connection.host, connection.user, connection.password];
  if (missing.some((part) => !part) || !connection.database) {
    throw new MirrorMisconfigured("url");
  }
  if (connection.database.includes("/")) throw new MirrorMisconfigured("url");
  return Object.freeze(connection);
}

export function createMirrorPool(
  options: Readonly<{ connection: MirrorConnection; ssl: MirrorTls }>,
): Pool {
  const { connection, ssl } = options;
  if (ssl === false && process.env.NODE_ENV !== "test") {
    throw new MirrorMisconfigured("tls");
  }
  const pool = new Pool({
    ...connection,
    ...POOL_LIMITS,
    types: mirrorTypes,
    ssl: ssl === false ? false : { rejectUnauthorized: true, ca: ssl.ca },
  });
  // An idle client's error is emitted on the pool; unhandled, it would crash
  // the process.
  pool.on("error", (error) => {
    console.error("[pick-board] idle connection failed", {
      code: errorCode(error) ?? error.name,
    });
  });
  return pool;
}

function readEnv(name: string): string | undefined {
  const value = process.env[name]?.trim();
  return value === "" ? undefined : value;
}

let readerPool: Pool | null = null;

/** The reader pool, built from the environment on first use. */
export function getPool(): Pool {
  if (readerPool) return readerPool;
  const url = readEnv("PICK_MIRROR_READER_URL");
  if (!url) throw new MirrorUnavailable();
  const ca = readEnv("PICK_MIRROR_CA_PEM");
  // Never `ca: undefined`: that would verify against the system roots.
  if (!ca?.includes(PEM_CERTIFICATE)) throw new MirrorMisconfigured("ca");
  readerPool = createMirrorPool({
    connection: parseReaderUrl(url),
    ssl: { ca },
  });
  return readerPool;
}

/** Test hook: close and forget the reader pool so the next call rebuilds it. */
export async function resetMirrorPoolForTests(): Promise<void> {
  const pool = readerPool;
  readerPool = null;
  await pool?.end();
}

const dialect = new PgDialect();

async function releaseAfterFailure(
  client: MirrorClient,
  error: unknown,
): Promise<Error | undefined> {
  if (isConnectionFailure(error)) {
    return error instanceof Error ? error : new Error("connection failed");
  }
  try {
    await client.query("ROLLBACK");
    return undefined;
  } catch (rollbackError) {
    return rollbackError instanceof Error
      ? rollbackError
      : new Error("rollback failed");
  }
}

async function runOnce(
  pool: MirrorPool,
  searchPath: string,
  query: MirrorQuery,
): Promise<unknown[]> {
  const client = await pool.connect();
  let destroy: Error | undefined;
  try {
    await client.query(
      `BEGIN READ ONLY; SET LOCAL search_path TO ${searchPath}`,
    );
    const result = await client.query(query);
    await client.query("COMMIT");
    return result.rows;
  } catch (error) {
    destroy = await releaseAfterFailure(client, error);
    throw error;
  } finally {
    client.release(destroy);
  }
}

async function run<R>(
  pool: () => MirrorPool,
  searchPath: string,
  query: SQL,
): Promise<QueryRows<R>> {
  const { sql: text, params } = dialect.sqlToQuery(query);
  const statement: MirrorQuery = { text, values: params };
  const attempt = async () => runOnce(pool(), searchPath, statement);
  try {
    const rows = await attempt().catch((error: unknown) => {
      // Read-only, so a deadlock victim is safe to run again, once.
      if (errorCode(error) !== DEADLOCK_SQLSTATE) throw error;
      return attempt();
    });
    return { rows: rows as R[] };
  } catch (error) {
    const typed = translateMirrorError(error);
    if (!(typed instanceof MirrorUnavailable)) {
      console.error("[pick-board] query failed", {
        code:
          errorCode(error) ?? (error instanceof Error ? error.name : "unknown"),
        schema: searchPath.split(",")[0],
      });
    }
    throw typed;
  }
}

function executorFor(
  pool: () => MirrorPool,
  searchPath: () => string,
): Executor {
  return Object.freeze({
    execute: async <R = Record<string, unknown>>(query: SQL) =>
      run<R>(pool, searchPath(), query),
  });
}

function checkedScope<R>(scope: VersionScope<R>): VersionScope<R> {
  checkVersionSchema(scope.schema);
  return Object.freeze({ ...scope });
}

export type BoardScope<R> = Readonly<{
  setBoardScope: (scope: VersionScope<R>) => void;
  boardScope: () => VersionScope<R>;
  withScriptScope: <T>(
    scope: VersionScope<R>,
    fn: () => Promise<T>,
  ) => Promise<T>;
  getDb: () => Executor;
  controlDb: () => Executor;
  versionDb: (schema: string) => Executor;
}>;

export function makeScope<R = unknown>(
  getHolder: () => ScopeHolder,
  pool: () => MirrorPool = getPool,
): BoardScope<R> {
  const script = new AsyncLocalStorage<VersionScope<R>>();

  const boardScope = (): VersionScope<R> => {
    const scope =
      script.getStore() ?? (getHolder().scope as VersionScope<R> | null);
    if (!scope) throw new Error("pick-board query outside a version scope");
    return scope;
  };

  const setBoardScope = (scope: VersionScope<R>): void => {
    const holder = getHolder();
    if (holder.scope && holder.scope.versionId !== scope.versionId) {
      throw new Error(
        "pick-board: another version is already set for this request",
      );
    }
    holder.scope = checkedScope(scope);
  };

  return Object.freeze({
    setBoardScope,
    boardScope,
    withScriptScope: <T>(scope: VersionScope<R>, fn: () => Promise<T>) =>
      script.run(checkedScope(scope), fn),
    getDb: () =>
      executorFor(pool, () => `${boardScope().schema}, ${CONTROL_SCHEMA}`),
    controlDb: () => executorFor(pool, () => CONTROL_SCHEMA),
    versionDb: (schema: string) => {
      const checked = checkVersionSchema(schema);
      return executorFor(pool, () => `${checked}, ${CONTROL_SCHEMA}`);
    },
  });
}

/** One holder per request: React's cache() is scoped to a server render. */
const requestHolder = cache((): ScopeHolder => ({ scope: null }));

export const {
  setBoardScope,
  boardScope,
  withScriptScope,
  getDb,
  controlDb,
  versionDb,
} = makeScope(requestHolder, getPool);
