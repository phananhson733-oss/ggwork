import "server-only";
import { AsyncLocalStorage } from "node:async_hooks";
import { Pool, type PoolClient } from "pg";
import { drizzle } from "drizzle-orm/node-postgres";
import { type SQL } from "drizzle-orm";
import * as schema from "./schema";
import { getSyncSignal } from "@/lib/sync-deadline";

/** The source owns a private schema; never resolve application tables in public. */
export function sourcePool(
  connectionString = process.env.PICK_SOURCE_DATABASE_URL,
) {
  if (!connectionString)
    throw new Error("PICK_SOURCE_DATABASE_URL is required");
  let url: URL;
  try {
    url = new URL(connectionString);
  } catch {
    throw new Error("Invalid source database configuration");
  }
  if (!["postgres:", "postgresql:"].includes(url.protocol))
    throw new Error("PostgreSQL source URL is required");
  const local = ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
  if (!local && url.port === "6543")
    throw new Error(
      "Source collection requires a session-stable PostgreSQL endpoint",
    );
  const ca = process.env.PICK_SOURCE_CA_PEM;
  if (!local && !ca?.includes("-----BEGIN CERTIFICATE-----"))
    throw new Error("Source database CA certificate is required");
  for (const key of [...url.searchParams.keys()])
    if (key.startsWith("ssl") || key === "uselibpqcompat" || key === "options")
      url.searchParams.delete(key);
  return new Pool({
    connectionString: url.toString(),
    max: 4,
    connectionTimeoutMillis: 55_000,
    ssl: local ? false : { rejectUnauthorized: true, ca },
  });
}
let pool: Pool | undefined;
export function getPool() {
  return (pool ??= sourcePool());
}
const transaction = new AsyncLocalStorage<PoolClient>();
const SCOPE =
  "SET LOCAL search_path TO pick_source,pg_catalog; SET LOCAL statement_timeout='60s'";
// Drizzle builders call this query surface too, so ORM inserts and raw SELECTs obey the same scope.
const scopedClient = (signal?: AbortSignal) => ({
  query: async (
    query: string | import("pg").QueryConfig,
    values?: unknown[],
  ) => {
    const c = await getPool().connect();
    try {
      signal?.throwIfAborted();
      await c.query("BEGIN");
      await c.query(SCOPE);
      signal?.throwIfAborted();
      const result = await c.query(query, values);
      signal?.throwIfAborted();
      await c.query("COMMIT");
      return result;
    } catch (error) {
      await c.query("ROLLBACK");
      throw error;
    } finally {
      c.release();
    }
  },
});
function database(client: Pool | PoolClient, signal?: AbortSignal) {
  const db = drizzle(client, { schema });
  return Object.assign(db, {
    /** Compatibility with the source's atomic Neon batches, now real PG transactions. */
    async batch(queries: { getSQL(): SQL }[]) {
      const owned = transaction.getStore();
      const c = owned ?? (await getPool().connect());
      try {
        if (!owned) {
          await c.query("BEGIN");
          await c.query(SCOPE);
        }
        const tx = drizzle(c, { schema });
        const results = [];
        for (const q of queries) {
          signal?.throwIfAborted();
          results.push(await tx.execute(q.getSQL()));
        }
        signal?.throwIfAborted();
        if (!owned) await c.query("COMMIT");
        return results;
      } catch (error) {
        if (!owned) await c.query("ROLLBACK");
        throw error;
      } finally {
        if (!owned) c.release();
      }
    },
  });
}
export function getDb(signal = getSyncSignal()) {
  signal?.throwIfAborted();
  return database(
    transaction.getStore() ?? (scopedClient(signal) as unknown as Pool),
    signal,
  );
}
/** Catalog import publishes its four related tables in one transaction. */
export async function atomic<T>(work: () => Promise<T>): Promise<T> {
  if (transaction.getStore()) return work();
  const c = await getPool().connect();
  try {
    await c.query("BEGIN");
    await c.query(SCOPE);
    const result = await transaction.run(c, work);
    getSyncSignal()?.throwIfAborted();
    await c.query("COMMIT");
    return result;
  } catch (error) {
    await c.query("ROLLBACK");
    throw error;
  } finally {
    c.release();
  }
}
export { schema };
