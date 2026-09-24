/**
 * db.ts and version.ts against a real mirror, as the reader role.
 *
 * Runs only when PICK_BOARD_TEST_PG_URL names a database built by
 * customizations/pick-workbench/tests/mirror/board_fixture.py (its --url-file);
 * skipped otherwise. The local cluster has no TLS, so the pool comes from the
 * explicit test entry with ssl:false.
 */
import { afterAll, beforeAll, describe, expect, it } from "@rstest/core";
import { sql } from "drizzle-orm";
import { type Pool } from "pg";

import {
  createMirrorPool,
  makeScope,
  parseReaderUrl,
  type ScopeHolder,
} from "@/server/pick-board/db";
import {
  MirrorMisconfigured,
  MirrorVersionGone,
} from "@/server/pick-board/errors";
import {
  resolveVersion,
  type BoardVersion,
  type ReadyBoard,
} from "@/server/pick-board/version";

const READER_URL = process.env.PICK_BOARD_TEST_PG_URL ?? "";

type Versions = Readonly<{
  dropped: number;
  v1: number;
  v2: number;
  building: number;
  failed: number;
}>;

const buildRules = (raw: unknown, versionId: number) => ({ raw, versionId });

function schemaOf(id: number): string {
  return `pickm_v${String(id).padStart(6, "0")}`;
}

function ready<R>(board: BoardVersion<R>): ReadyBoard<R> {
  if (board.state !== "ready") throw new Error("expected a published version");
  return board;
}

describe.runIf(READER_URL !== "")("the mirror, read as the reader", () => {
  let pool: Pool;
  let versions: Versions;
  const scopeFor = (holder: ScopeHolder = { scope: null }) =>
    makeScope(
      () => holder,
      () => pool,
    );
  const readers = () => {
    const scope = scopeFor();
    return { controlDb: scope.controlDb, versionDb: scope.versionDb };
  };

  beforeAll(async () => {
    pool = createMirrorPool({
      connection: parseReaderUrl(READER_URL),
      ssl: false,
    });
    const { rows } = await scopeFor()
      .controlDb()
      .execute<{ id: number; status: string }>(
        sql`SELECT id::int AS id, status FROM pick_mirror.versions ORDER BY id`,
      );
    const idsOf = (status: string) =>
      rows.filter((row) => row.status === status).map((row) => row.id);
    const [v1 = 0, v2 = 0] = idsOf("published");
    versions = {
      dropped: idsOf("dropped")[0] ?? 0,
      v1,
      v2,
      building: idsOf("building")[0] ?? 0,
      failed: idsOf("failed")[0] ?? 0,
    };
  });

  afterAll(async () => {
    await pool.end();
  });

  it("resolves the current version and reads its rows in a script scope", async () => {
    const board = ready(await resolveVersion(null, buildRules, readers()));
    expect(board.scope.versionId).toBe(versions.v2);
    expect(board.pinned).toBe(false);
    expect(board.sources).not.toBeNull();
    expect(board.freshness).not.toBeNull();
    expect(board.series).toEqual({
      through: "2026-09-23",
      trimmedBefore: "2026-06-25",
    });
    const scope = scopeFor();
    const n = await scope.withScriptScope(board.scope, async () => {
      const { rows } = await scope.getDb().execute<{
        n: number;
      }>(sql`SELECT count(*)::int AS n FROM catalog_rows`);
      return rows[0]?.n ?? 0;
    });
    expect(n).toBeGreaterThan(0);
  });

  it("pins an older published version, whose rows differ", async () => {
    const titleIn = async (board: ReadyBoard<unknown>) => {
      const holder: ScopeHolder = { scope: null };
      const scope = scopeFor(holder);
      scope.setBoardScope(board.scope);
      const { rows } = await scope.getDb().execute<{
        title: string;
      }>(sql`SELECT title FROM catalog_rows WHERE row_key = ${"c-1"}`);
      return rows[0]?.title;
    };
    const v1 = ready(await resolveVersion(versions.v1, buildRules, readers()));
    const v2 = ready(await resolveVersion(null, buildRules, readers()));
    expect(v1).toMatchObject({ pinned: true, requestedV: versions.v1 });
    expect(v1.scope.rules).toMatchObject({ versionId: versions.v1 });
    expect(await titleIn(v1)).not.toBe(await titleIn(v2));
  });

  it("falls back to current for dropped, building and failed versions", async () => {
    const dropped = ready(
      await resolveVersion(versions.dropped, buildRules, readers()),
    );
    expect(dropped).toMatchObject({ pruned: true, ignoredV: false });
    for (const v of [versions.building, versions.failed, 999_999]) {
      const board = ready(await resolveVersion(v, buildRules, readers()));
      expect(board).toMatchObject({ ignoredV: true, pruned: false });
      expect(board.scope.versionId).toBe(versions.v2);
    }
  });

  it("returns date and time columns as text", async () => {
    const { rows } = await scopeFor()
      .controlDb()
      .execute<{
        as_of: unknown;
        published_at: unknown;
        latest_snapshot: unknown;
      }>(
        sql`SELECT as_of, published_at, latest_snapshot FROM pick_mirror.versions WHERE id = ${versions.v2}`,
      );
    const row = rows[0];
    expect(typeof row?.as_of).toBe("string");
    expect(typeof row?.published_at).toBe("string");
    expect(row?.latest_snapshot).toBe("2026-09-23");
  });

  it("may not read pick_mirror.control: MirrorMisconfigured", async () => {
    const thrown = await scopeFor()
      .controlDb()
      .execute(sql`SELECT * FROM pick_mirror.control`)
      .catch((error: unknown) => error);
    expect(thrown).toBeInstanceOf(MirrorMisconfigured);
    expect((thrown as MirrorMisconfigured).reason).toBe("permission");
  });

  it("has no USAGE on a building version's schema", async () => {
    const building = schemaOf(versions.building);
    // search_path skips a schema without USAGE, so an unqualified name is
    // simply not found; which is why resolveVersion checks USAGE up front.
    await expect(
      scopeFor()
        .versionDb(building)
        .execute(sql`SELECT count(*) FROM meta`),
    ).rejects.toBeInstanceOf(MirrorVersionGone);
    const qualified = await scopeFor()
      .controlDb()
      .execute(sql`SELECT count(*) FROM ${sql.raw(building)}.meta`)
      .catch((error: unknown) => error);
    expect(qualified).toBeInstanceOf(MirrorMisconfigured);
    expect((qualified as MirrorMisconfigured).reason).toBe("permission");
  });

  it("finds a dropped version's tables gone: MirrorVersionGone", async () => {
    await expect(
      scopeFor()
        .versionDb(schemaOf(versions.dropped))
        .execute(sql`SELECT count(*) FROM meta`),
    ).rejects.toBeInstanceOf(MirrorVersionGone);
  });

  it("reads in read-only transactions", async () => {
    const thrown = await scopeFor()
      .versionDb(schemaOf(versions.v2))
      .execute(sql`CREATE TEMP TABLE scratch (n int)`)
      .catch((error: unknown) => error);
    expect((thrown as { code?: unknown }).code).toBe("25006");
  });
});
