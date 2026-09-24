import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { sql } from "drizzle-orm";

import {
  createMirrorPool,
  getPool,
  makeScope,
  mirrorTypes,
  parseReaderUrl,
  resetMirrorPoolForTests,
  type MirrorClient,
  type MirrorPool,
  type ScopeHolder,
  type VersionScope,
} from "@/server/pick-board/db";
import {
  MirrorBusy,
  MirrorMisconfigured,
  MirrorUnavailable,
  MirrorVersionGone,
} from "@/server/pick-board/errors";

const V1: VersionScope = {
  schema: "pickm_v000123",
  asOf: "2026-09-23T10:15:00.000Z",
  versionId: 123,
  rules: { tag: "v1" },
};
const V2: VersionScope = { ...V1, schema: "pickm_v000124", versionId: 124 };
const CA = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n";
const URL_OK =
  "postgresql://pick_board_reader:s%40cret@db.example.test:6543/postgres";

type Statement = string | { text: string; values?: unknown[]; name?: string };

function pgError(code: string, message = "synthetic"): Error {
  return Object.assign(new Error(message), { code });
}

/** A pool of one client that records every statement and release. */
function recorder(
  fail: (text: string, n: number) => Error | null = () => null,
) {
  const log = { statements: [] as Statement[], releases: [] as unknown[] };
  const client: MirrorClient = {
    query: async (statement) => {
      log.statements.push(statement);
      const text = typeof statement === "string" ? statement : statement.text;
      const error = fail(text, log.statements.length);
      if (error) throw error;
      return { rows: [{ n: 1 }] };
    },
    release: (destroy?: Error | boolean) => {
      log.releases.push(destroy);
    },
  };
  const pool: MirrorPool = { connect: async () => client };
  return { log, pool };
}

function scopeWith(pool: MirrorPool, holder: ScopeHolder = { scope: null }) {
  return makeScope(
    () => holder,
    () => pool,
  );
}

let errorLog: ReturnType<typeof rs.spyOn>;

beforeEach(() => {
  errorLog = rs.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(async () => {
  errorLog.mockRestore();
  rs.unstubAllEnvs();
  await resetMirrorPoolForTests();
});

describe("one read-only transaction per query", () => {
  it("runs each query in its own read-only transaction", async () => {
    const { log, pool } = recorder();
    const scope = scopeWith(pool, { scope: V1 });
    const result = await scope.getDb().execute(sql`SELECT 1`);
    expect(result.rows).toEqual([{ n: 1 }]);
    expect(log.statements).toEqual([
      "BEGIN READ ONLY; SET LOCAL search_path TO pickm_v000123, pick_mirror",
      { text: "SELECT 1", values: [] },
      "COMMIT",
    ]);
    expect(log.releases).toEqual([undefined]);
    expect(log.statements[1]).not.toHaveProperty("name");
  });

  it("binds values as parameters, never into the text", async () => {
    const { log, pool } = recorder();
    const scope = scopeWith(pool, { scope: V1 });
    await scope.getDb().execute(sql`SELECT ${"x'; DROP"}::text AS v`);
    expect(log.statements[1]).toEqual({
      text: "SELECT $1::text AS v",
      values: ["x'; DROP"],
    });
  });

  it("rolls back and releases on error", async () => {
    const boom = pgError("22P02");
    const { log, pool } = recorder((text) =>
      text === "SELECT 1" ? boom : null,
    );
    const scope = scopeWith(pool, { scope: V1 });
    await expect(scope.getDb().execute(sql`SELECT 1`)).rejects.toBe(boom);
    expect(log.statements.at(-1)).toBe("ROLLBACK");
    expect(log.releases).toEqual([undefined]);
  });

  it("a failing ROLLBACK does not mask the query error", async () => {
    const boom = pgError("22P02");
    const broken = new Error("rollback failed");
    const { log, pool } = recorder((text) =>
      text === "SELECT 1" ? boom : text === "ROLLBACK" ? broken : null,
    );
    const scope = scopeWith(pool, { scope: V1 });
    await expect(scope.getDb().execute(sql`SELECT 1`)).rejects.toBe(boom);
    // A client whose ROLLBACK failed is in an unknown state: destroyed, not reused.
    expect(log.releases).toEqual([broken]);
  });

  it("destroys a client whose connection broke, without a ROLLBACK", async () => {
    const reset = pgError("ECONNRESET", "read ECONNRESET");
    const { log, pool } = recorder((text) =>
      text === "SELECT 1" ? reset : null,
    );
    const scope = scopeWith(pool, { scope: V1 });
    await expect(scope.getDb().execute(sql`SELECT 1`)).rejects.toBeInstanceOf(
      MirrorBusy,
    );
    expect(log.statements).not.toContain("ROLLBACK");
    expect(log.releases).toEqual([reset]);
  });

  it("controlDb searches pick_mirror only", async () => {
    const { log, pool } = recorder();
    await scopeWith(pool)
      .controlDb()
      .execute(sql`SELECT 1`);
    expect(log.statements[0]).toBe(
      "BEGIN READ ONLY; SET LOCAL search_path TO pick_mirror",
    );
  });

  it("versionDb searches the named version, after checking its name", async () => {
    const { log, pool } = recorder();
    const scope = scopeWith(pool);
    await scope.versionDb("pickm_v000007").execute(sql`SELECT 1`);
    expect(log.statements[0]).toBe(
      "BEGIN READ ONLY; SET LOCAL search_path TO pickm_v000007, pick_mirror",
    );
    expect(() => scope.versionDb("pickm_v7; DROP")).toThrow();
  });
});

describe("date and time types come back as text", () => {
  it("nine date/time OIDs come back as text", () => {
    const oids = [1184, 1114, 1082, 1186, 1231, 1115, 1185, 1187, 1182];
    for (const oid of oids) {
      const parse = mirrorTypes.getTypeParser(oid, "text") as (
        value: string,
      ) => unknown;
      expect(parse("2026-09-24 03:40:00+00")).toBe("2026-09-24 03:40:00+00");
    }
    const int4 = mirrorTypes.getTypeParser(23, "text") as (
      v: string,
    ) => unknown;
    expect(int4("42")).toBe(42);
  });
});

describe("the version scope", () => {
  it("throws outside a version scope", async () => {
    const { log, pool } = recorder();
    await expect(
      scopeWith(pool)
        .getDb()
        .execute(sql`SELECT 1`),
    ).rejects.toThrow("pick-board query outside a version scope");
    expect(log.statements).toEqual([]);
  });

  it("a second, different version in one request throws", () => {
    const holder: ScopeHolder = { scope: null };
    const scope = scopeWith(recorder().pool, holder);
    scope.setBoardScope(V1);
    expect(() => scope.setBoardScope(V2)).toThrow();
    expect(() => scope.setBoardScope(V1)).not.toThrow();
    expect(scope.boardScope().versionId).toBe(123);
  });

  it("freezes the scope it stores", () => {
    const holder: ScopeHolder = { scope: null };
    scopeWith(recorder().pool, holder).setBoardScope(V1);
    expect(Object.isFrozen(holder.scope)).toBe(true);
  });

  it("rejects a schema name not of the pickm_v shape", () => {
    const scope = scopeWith(recorder().pool);
    for (const schema of ["pickm_v12", "public", "pickm_v000123, public"]) {
      expect(() => scope.setBoardScope({ ...V1, schema })).toThrow();
    }
  });

  it("withScriptScope is visible only inside fn", async () => {
    const { log, pool } = recorder();
    const scope = scopeWith(pool);
    expect(() => scope.boardScope()).toThrow();
    const inside = await scope.withScriptScope(V2, async () => {
      await scope.getDb().execute(sql`SELECT 1`);
      return scope.boardScope().versionId;
    });
    expect(inside).toBe(124);
    expect(log.statements[0]).toContain("pickm_v000124");
    expect(() => scope.boardScope()).toThrow();
  });
});

describe("typed errors", () => {
  async function failWith(error: Error) {
    const { pool } = recorder((text) => (text === "SELECT 1" ? error : null));
    return scopeWith(pool, { scope: V1 })
      .getDb()
      .execute(sql`SELECT 1`)
      .then(
        () => null,
        (thrown: unknown) => thrown,
      );
  }

  it("maps SQLSTATE to typed errors", async () => {
    expect(await failWith(pgError("42P01"))).toBeInstanceOf(MirrorVersionGone);
    expect(await failWith(pgError("3F000"))).toBeInstanceOf(MirrorVersionGone);
    expect(await failWith(pgError("57014"))).toBeInstanceOf(MirrorBusy);
    expect(await failWith(pgError("55P03"))).toBeInstanceOf(MirrorBusy);
    expect(await failWith(pgError("42501"))).toBeInstanceOf(
      MirrorMisconfigured,
    );
  });

  it("maps pool exhaustion and broken connections to MirrorBusy", async () => {
    const busy = [
      new Error("timeout exceeded when trying to connect"),
      new Error("Query read timeout"),
      new Error("Connection terminated unexpectedly"),
      pgError("ECONNRESET"),
      pgError("ETIMEDOUT"),
      pgError("08006"),
      pgError("08P01"),
      pgError("53300", "sorry, too many clients already"),
      pgError("XX000", "Max client connections reached"),
    ];
    for (const error of busy) {
      expect(await failWith(error)).toBeInstanceOf(MirrorBusy);
    }
  });

  it("passes unknown errors through unchanged", async () => {
    const other = pgError("22P02");
    expect(await failWith(other)).toBe(other);
  });

  it("retries a deadlock once inside run", async () => {
    let attempts = 0;
    const { pool } = recorder((text) => {
      if (text !== "SELECT 1") return null;
      attempts += 1;
      return attempts === 1 ? pgError("40P01") : null;
    });
    const result = await scopeWith(pool, { scope: V1 })
      .getDb()
      .execute(sql`SELECT 1`);
    expect(result.rows).toEqual([{ n: 1 }]);
    expect(attempts).toBe(2);
  });

  it("logs only the code and the schema", async () => {
    await failWith(pgError("42P01", 'relation "catalog_rows" secret value'));
    expect(errorLog).toHaveBeenCalledWith("[pick-board] query failed", {
      code: "42P01",
      schema: "pickm_v000123",
    });
    expect(JSON.stringify(errorLog.mock.calls)).not.toContain("secret");
  });

  it("maps a pool that cannot hand out a client", async () => {
    const pool: MirrorPool = {
      connect: async () => {
        throw new Error("timeout exceeded when trying to connect");
      },
    };
    await expect(
      scopeWith(pool)
        .controlDb()
        .execute(sql`SELECT 1`),
    ).rejects.toBeInstanceOf(MirrorBusy);
  });
});

describe("the reader connection", () => {
  it("parses host, port, user, password and database out of the URL", () => {
    expect(parseReaderUrl(URL_OK)).toEqual({
      host: "db.example.test",
      port: 6543,
      user: "pick_board_reader",
      password: "s@cret",
      database: "postgres",
    });
  });

  it("rejects a reader URL with ssl parameters", () => {
    const bad = [
      `${URL_OK}?sslmode=require`,
      `${URL_OK}?application_name=x&ssl=true`,
      `${URL_OK}?sslrootcert=/tmp/ca.pem`,
      `${URL_OK}?uselibpqcompat=true`,
      `${URL_OK}?options=-c%20search_path%3Dpublic`,
    ];
    for (const url of bad) {
      expect(() => parseReaderUrl(url)).toThrow(MirrorMisconfigured);
    }
  });

  it("rejects URLs that are not a complete postgres URL", () => {
    const bad = [
      "not a url",
      "mysql://u:p@h:5432/db",
      "postgresql://u:p@h:5432/",
      "postgresql://u@h:5432/db",
      "postgresql://:p@h:5432/db",
    ];
    for (const url of bad) {
      expect(() => parseReaderUrl(url)).toThrow(MirrorMisconfigured);
    }
  });

  it("builds the pool from the parts, verifying TLS against the CA", async () => {
    const pool = createMirrorPool({
      connection: parseReaderUrl(URL_OK),
      ssl: { ca: CA },
    });
    try {
      const options = (pool as unknown as { options: Record<string, unknown> })
        .options;
      expect(options.connectionString).toBeUndefined();
      expect(options.host).toBe("db.example.test");
      expect(options.ssl).toEqual({ rejectUnauthorized: true, ca: CA });
      expect(options.max).toBe(3);
      expect(options.query_timeout).toBe(10_000);
      expect(options.connectionTimeoutMillis).toBe(5_000);
      expect(options.types).toBe(mirrorTypes);
      expect(pool.listenerCount("error")).toBe(1);
    } finally {
      await pool.end();
    }
  });

  it("allows plaintext only to the explicit test entry, under NODE_ENV=test", async () => {
    const connection = parseReaderUrl(URL_OK);
    const plain = createMirrorPool({ connection, ssl: false });
    await plain.end();
    rs.stubEnv("NODE_ENV", "production");
    expect(() => createMirrorPool({ connection, ssl: false })).toThrow(
      MirrorMisconfigured,
    );
  });

  it("MirrorUnavailable without PICK_MIRROR_READER_URL", () => {
    rs.stubEnv("PICK_MIRROR_READER_URL", undefined);
    rs.stubEnv("PICK_MIRROR_CA_PEM", CA);
    expect(() => getPool()).toThrow(MirrorUnavailable);
  });

  it("MirrorMisconfigured without PICK_MIRROR_CA_PEM, never the system roots", () => {
    rs.stubEnv("PICK_MIRROR_READER_URL", URL_OK);
    rs.stubEnv("PICK_MIRROR_CA_PEM", undefined);
    expect(() => getPool()).toThrow(MirrorMisconfigured);
    rs.stubEnv("PICK_MIRROR_CA_PEM", "not a certificate");
    expect(() => getPool()).toThrow(MirrorMisconfigured);
  });

  it("reads the environment when called, and reuses the pool until reset", async () => {
    rs.stubEnv("PICK_MIRROR_READER_URL", URL_OK);
    rs.stubEnv("PICK_MIRROR_CA_PEM", CA);
    const first = getPool();
    expect(getPool()).toBe(first);
    await resetMirrorPoolForTests();
    expect(getPool()).not.toBe(first);
  });
});
