/**
 * The replay's row reads against a real mirror, as the reader role (P4-2).
 *
 * Runs only when PICK_BOARD_TEST_PG_URL names a database built by
 * customizations/pick-workbench/tests/mirror/board_fixture.py; skipped
 * otherwise, like queries.integration.test.ts.
 *
 * The consistency case (critique B22) does not write the agent's list by hand:
 * it reads board_replay_cases.json, the backend's own /api/pick/replay answer
 * for board_fixture's v2 v1 pull (gate_world's V1_CANDIDATES, worked out with
 * feed-map's semantics), and compares it with the 选剧 tab's default rows on
 * the same board. That only shows the board reads one pair the way the agent
 * does. The guarantee for real data is P2's gate G8 (a version's candidates
 * are exactly the v1 rows of its pair) and P4-4's check of real cards.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it, rs } from "@rstest/core";
import { sql } from "drizzle-orm";
import { type Pool } from "pg";

import { rowKeyFromIdentity } from "@/core/pick/identity";
import { parsePickRequest } from "@/core/pick-board/request";
import { buildBoardRules, type BoardRules } from "@/core/pick-board/rules";
import type * as DbModule from "@/server/pick-board/db";
import {
  createMirrorPool,
  getDb,
  parseReaderUrl,
  withScriptScope,
  type ScopeHolder,
} from "@/server/pick-board/db";
import {
  loadMissingKeys,
  loadPickRows,
  loadRowsByKeys,
} from "@/server/pick-board/queries";
import { resolveVersion, type ReadyBoard } from "@/server/pick-board/version";

const READER_URL = process.env.PICK_BOARD_TEST_PG_URL ?? "";
const REPO_ROOT = path.resolve(__dirname, "../../../../..");
const CASES = JSON.parse(
  readFileSync(
    path.join(
      REPO_ROOT,
      "customizations/pick-workbench/tests/fixtures/board_replay_cases.json",
    ),
    "utf8",
  ),
) as { replay: { identities: string[] }; row_keys: string[] };

const pg = rs.hoisted(() => ({
  pool: null as Pool | null,
  holder: { scope: null } as ScopeHolder,
}));

rs.mock("@/server/pick-board/db", () => {
  const actual = rs.requireActual<typeof DbModule>("@/server/pick-board/db");
  const scope = actual.makeScope(
    () => pg.holder,
    () => {
      if (!pg.pool) throw new Error("the test pool is not open");
      return pg.pool;
    },
  );
  return { ...actual, ...scope };
});

type Board = ReadyBoard<BoardRules>;

function decoded(identities: readonly string[]): string[] {
  return identities.map((identity) => {
    const key = rowKeyFromIdentity(identity);
    if (key === null) throw new Error(`no row key in ${identity}`);
    return key;
  });
}

describe.runIf(READER_URL !== "")("the replay on a real mirror", () => {
  let v1: Board;
  let v2: Board;
  const inV1 = <T>(fn: () => Promise<T>) => withScriptScope(v1.scope, fn);
  const inV2 = <T>(fn: () => Promise<T>) => withScriptScope(v2.scope, fn);

  beforeAll(async () => {
    pg.pool = createMirrorPool({
      connection: parseReaderUrl(READER_URL),
      ssl: false,
    });
    const current = await resolveVersion(null, buildBoardRules);
    if (current.state !== "ready") throw new Error("no published version");
    v2 = current;
    const { rows } = await withScriptScope(v2.scope, () =>
      getDb().execute<{ id: number }>(
        sql`SELECT id::int AS id FROM pick_mirror.versions WHERE status = 'published' AND id <> ${v2.scope.versionId}`,
      ),
    );
    const older = await resolveVersion(rows[0]?.id ?? null, buildBoardRules);
    if (older.state !== "ready") throw new Error("no older version");
    v1 = older;
  });

  afterAll(async () => {
    await pg.pool?.end();
  });

  it("B22: the agent's default candidates are the 选剧 tab's default rows", async () => {
    const keys = decoded(CASES.replay.identities);
    expect(keys).toEqual(CASES.row_keys);
    const page = await inV2(() =>
      loadPickRows(parsePickRequest({ size: "200" })),
    );
    expect(page.hasMore).toBe(false);
    expect([...keys].sort()).toEqual(page.rows.map((r) => r.rowKey).sort());
  });

  it("reads the agent's list in its own order, every row found", async () => {
    const keys = decoded(CASES.replay.identities);
    const rows = await inV2(() => loadRowsByKeys(keys));
    expect(rows.map((r) => r.rowKey)).toEqual(keys);
    expect(await inV2(() => loadMissingKeys(keys))).toEqual([]);
    const c1 = rows.find((r) => r.rowKey === "c-1");
    expect(c1?.signals.map((s) => s.kind)).toEqual(["kd", "kw", "kd"]);
    expect(rows.find((r) => r.rowKey === "reelshort-rs0001")?.rs?.id).toBe(
      "rs0001",
    );
  });

  it("keeps any order it is given; delisted rows are read, absent keys are not", async () => {
    const keys = ["reelshort-rs0005", "c-4", "no-such-row", "c-1", "c-3"];
    const rows = await inV2(() => loadRowsByKeys(keys));
    expect(rows.map((r) => r.rowKey)).toEqual([
      "reelshort-rs0005",
      "c-4",
      "c-1",
      "c-3",
    ]);
    expect(rows.find((r) => r.rowKey === "c-4")?.offOn).not.toBeNull();
    expect(await inV2(() => loadMissingKeys(keys))).toEqual(["no-such-row"]);
  });

  it("the whole list is one bound array: quotes, braces, commas and CJK come back as given", async () => {
    const odd = ['a"b', "c\\d", "{e,f}", "g h", "剧 一", "NULL"];
    const keys = ["c-1", ...odd, "c-2"];
    expect(await inV2(() => loadMissingKeys(keys))).toEqual(odd);
    const rows = await inV2(() => loadRowsByKeys(keys));
    expect(rows.map((r) => r.rowKey)).toEqual(["c-1", "c-2"]);
  });

  it("reads the pinned version's rows: c-1 was renamed in v2", async () => {
    const [old] = await inV1(() => loadRowsByKeys(["c-1"]));
    const [now] = await inV2(() => loadRowsByKeys(["c-1"]));
    expect(old?.title).toBe("c-1 的剧名");
    expect(now?.title).toBe("c-1 的剧名（v2 改名）");
  });
});
