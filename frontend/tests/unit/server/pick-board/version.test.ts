import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { type SQL } from "drizzle-orm";
import { PgDialect } from "drizzle-orm/pg-core";

import { type Executor } from "@/server/pick-board/db";
import {
  MirrorMisconfigured,
  MirrorVersionGone,
} from "@/server/pick-board/errors";
import {
  resolveVersion,
  type VersionReaders,
} from "@/server/pick-board/version";

const dialect = new PgDialect();
const RULES = { platformRules: { reelshort: { doc: "/admin/pick?tab=rank" } } };
const SOURCES = { bill: { source: "bill", status: "success" } };
const SERIES = { through: "2026-09-23", trimmed_before: "2026-06-25" };

function schemaOf(id: number): string {
  return `pickm_v${String(id).padStart(6, "0")}`;
}

function currentRow(id = 7, extra: Record<string, unknown> = {}) {
  return {
    id,
    schema_name: schemaOf(id),
    as_of: `2026-09-24T03:${String(id).padStart(2, "0")}:00+00:00`,
    published_at: "2026-09-24T03:52:00.123456+00:00",
    latest_snapshot: "2026-09-23",
    freshness: { rows: id },
    warnings: [{ code: "catalog_import_incomplete", source: "pick_catalog" }],
    agent_catalog_batch_id: `cat-${id}`,
    agent_knowledge_batch_id: `kn-${id}`,
    readable: true,
    ...extra,
  };
}

const CURRENT_ONLY = new Set([
  "published_at",
  "agent_catalog_batch_id",
  "agent_knowledge_batch_id",
]);

function requestedRow(id: number, status: string, readable = true) {
  const row = Object.entries(currentRow(id, { readable })).filter(
    ([key]) => !CURRENT_ONLY.has(key),
  );
  return { ...Object.fromEntries(row), status };
}

type Control = {
  current: unknown;
  requested: unknown;
  series_state: unknown;
};

/** Readers that answer each control query in turn, and meta per schema. */
function readers(
  controls: Control[],
  meta: (schema: string) => Error | { key: string; value: unknown }[] = () => [
    { key: "rules", value: RULES },
    { key: "sources", value: SOURCES },
  ],
) {
  const seen = { lookups: [] as unknown[], schemas: [] as string[] };
  const answers = [...controls];
  const controlDb = (): Executor => ({
    execute: async <R>(query: SQL) => {
      seen.lookups.push(dialect.sqlToQuery(query).params[0]);
      const answer = answers.shift();
      if (!answer) throw new Error("no more control answers");
      return { rows: [answer as R] };
    },
  });
  const versionDb = (schema: string): Executor => ({
    execute: async <R>() => {
      seen.schemas.push(schema);
      const rows = meta(schema);
      if (rows instanceof Error) throw rows;
      return { rows: rows as R[] };
    },
  });
  const deps: VersionReaders = { controlDb, versionDb };
  return { deps, seen };
}

function control(current: unknown, requested: unknown = null): Control {
  return { current, requested, series_state: SERIES };
}

const buildRules = (raw: unknown, versionId: number) => ({ raw, versionId });

let errorLog: ReturnType<typeof rs.spyOn>;

beforeEach(() => {
  errorLog = rs.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  errorLog.mockRestore();
});

describe("resolveVersion", () => {
  it("no v → current", async () => {
    const { deps, seen } = readers([control(currentRow())]);
    const board = await resolveVersion(null, buildRules, deps);
    expect(seen.lookups).toEqual([-1]);
    expect(seen.schemas).toEqual(["pickm_v000007"]);
    if (board.state !== "ready") throw new Error("expected a ready board");
    expect(board.scope).toEqual({
      schema: "pickm_v000007",
      asOf: "2026-09-24T03:07:00+00:00",
      versionId: 7,
      rules: { raw: RULES, versionId: 7 },
    });
    expect(board.current).toEqual({
      id: 7,
      asOf: "2026-09-24T03:07:00+00:00",
      publishedAt: "2026-09-24T03:52:00.123456+00:00",
      agentCatalogBatchId: "cat-7",
      agentKnowledgeBatchId: "kn-7",
    });
    expect(board.sources).toEqual(SOURCES);
    expect(board.freshness).toEqual({ rows: 7 });
    expect(board.latestSnapshot).toBe("2026-09-23");
    expect(board.warnings).toEqual([
      { code: "catalog_import_incomplete", source: "pick_catalog" },
    ]);
    expect(board.series).toEqual({
      through: "2026-09-23",
      trimmedBefore: "2026-06-25",
    });
    expect(board).toMatchObject({
      requestedV: null,
      pinned: false,
      pruned: false,
      ignoredV: false,
      unreadable: false,
    });
    expect(Object.isFrozen(board)).toBe(true);
  });

  it("pinned when requested ≠ current", async () => {
    const { deps, seen } = readers([
      control(currentRow(), requestedRow(5, "published")),
    ]);
    const board = await resolveVersion(5, buildRules, deps);
    expect(seen.lookups).toEqual([5]);
    expect(seen.schemas).toEqual(["pickm_v000005"]);
    expect(board).toMatchObject({
      state: "ready",
      scope: { versionId: 5, schema: "pickm_v000005" },
      freshness: { rows: 5 },
      current: { id: 7 },
      requestedV: 5,
      pinned: true,
      pruned: false,
    });
  });

  it("a v that names the current version is not pinned", async () => {
    const { deps } = readers([
      control(currentRow(), requestedRow(7, "published")),
    ]);
    const board = await resolveVersion(7, buildRules, deps);
    expect(board).toMatchObject({ scope: { versionId: 7 }, pinned: false });
  });

  it("v of a dropped version → current, pruned", async () => {
    const { deps, seen } = readers([
      control(currentRow(), requestedRow(3, "dropped", false)),
    ]);
    const board = await resolveVersion(3, buildRules, deps);
    expect(seen.schemas).toEqual(["pickm_v000007"]);
    expect(board).toMatchObject({
      scope: { versionId: 7 },
      requestedV: 3,
      pruned: true,
      ignoredV: false,
      unreadable: false,
    });
  });

  it("v of a published but unreadable version → current, unreadable", async () => {
    const { deps } = readers([
      control(currentRow(), requestedRow(5, "published", false)),
    ]);
    const board = await resolveVersion(5, buildRules, deps);
    expect(board).toMatchObject({
      scope: { versionId: 7 },
      pruned: false,
      unreadable: true,
    });
    expect(errorLog).toHaveBeenCalledWith(
      "[pick-board] mirror version not readable",
      { versionId: 5 },
    );
  });

  it("v of building/failed/missing → current, ignoredV", async () => {
    for (const requested of [
      requestedRow(8, "building"),
      requestedRow(6, "failed"),
      null,
    ]) {
      const { deps } = readers([control(currentRow(), requested)]);
      const board = await resolveVersion(8, buildRules, deps);
      expect(board).toMatchObject({
        scope: { versionId: 7 },
        requestedV: 8,
        ignoredV: true,
        pruned: false,
        pinned: false,
      });
    }
  });

  it("a v out of range is looked up as no v, and flagged ignored", async () => {
    for (const v of [0, 1_000_000, 2.5, Number.NaN]) {
      const { deps, seen } = readers([control(currentRow())]);
      const board = await resolveVersion(v, buildRules, deps);
      expect(seen.lookups).toEqual([-1]);
      expect(board).toMatchObject({ scope: { versionId: 7 }, ignoredV: true });
    }
  });

  it("current null → empty", async () => {
    const { deps, seen } = readers([
      control(null, requestedRow(4, "building")),
    ]);
    const board = await resolveVersion(4, buildRules, deps);
    expect(board).toEqual({
      state: "empty",
      requestedV: 4,
      series: { through: "2026-09-23", trimmedBefore: "2026-06-25" },
    });
    expect(seen.schemas).toEqual([]);
  });

  it("current unreadable → MirrorMisconfigured", async () => {
    const { deps, seen } = readers([
      control(currentRow(7, { readable: false })),
    ]);
    const thrown = await resolveVersion(null, buildRules, deps).catch(
      (error: unknown) => error,
    );
    expect(thrown).toBeInstanceOf(MirrorMisconfigured);
    expect((thrown as MirrorMisconfigured).reason).toBe("current_unreadable");
    expect(seen.schemas).toEqual([]);
  });

  it("a version dropped between the two steps → current, pruned", async () => {
    const gone = (schema: string) =>
      schema === "pickm_v000005"
        ? new MirrorVersionGone("42P01")
        : [{ key: "rules", value: RULES }];
    const { deps, seen } = readers(
      [
        control(currentRow(), requestedRow(5, "published")),
        control(currentRow()),
      ],
      gone,
    );
    const board = await resolveVersion(5, buildRules, deps);
    expect(seen.lookups).toEqual([5, -1]);
    expect(seen.schemas).toEqual(["pickm_v000005", "pickm_v000007"]);
    expect(board).toMatchObject({
      scope: { versionId: 7 },
      requestedV: 5,
      pruned: true,
      pinned: false,
      sources: null,
    });
  });

  it("gives up when the retry finds its version gone too", async () => {
    const { deps } = readers(
      [control(currentRow()), control(currentRow())],
      () => new MirrorVersionGone("3F000"),
    );
    await expect(resolveVersion(null, buildRules, deps)).rejects.toBeInstanceOf(
      MirrorVersionGone,
    );
  });

  it("rules the builder rejects → MirrorMisconfigured, logged by code and field paths", async () => {
    const { deps } = readers([control(currentRow())]);
    const reject = () => {
      // The shape of P3-2's BoardRulesInvalid: a code and field paths only.
      throw Object.assign(new Error("rules do not match"), {
        name: "BoardRulesInvalid",
        code: "board_rules_invalid",
        paths: ["platformRules.<key>.yt"],
      });
    };
    const thrown = await resolveVersion(null, reject, deps).catch(
      (error: unknown) => error,
    );
    expect(thrown).toBeInstanceOf(MirrorMisconfigured);
    expect((thrown as MirrorMisconfigured).reason).toBe("rules");
    expect(errorLog).toHaveBeenCalledWith(
      "[pick-board] version rules rejected",
      {
        versionId: 7,
        code: "board_rules_invalid",
        paths: ["platformRules.<key>.yt"],
      },
    );
  });

  it("a builder's RangeError is a caller bug, not bad data: passed through", async () => {
    const { deps } = readers([control(currentRow())]);
    const bug = new RangeError("versionId out of range");
    const reject = () => {
      throw bug;
    };
    await expect(resolveVersion(null, reject, deps)).rejects.toBe(bug);
  });

  it("a control row of another shape → MirrorMisconfigured", async () => {
    const { deps } = readers([control({ id: "7" })]);
    const thrown = await resolveVersion(null, buildRules, deps).catch(
      (error: unknown) => error,
    );
    expect(thrown).toBeInstanceOf(MirrorMisconfigured);
    expect((thrown as MirrorMisconfigured).reason).toBe("control_shape");
  });

  it("normalizes loose freshness and warnings instead of failing", async () => {
    const { deps } = readers([
      control(currentRow(7, { freshness: null, warnings: [1, { note: "x" }] })),
    ]);
    const board = await resolveVersion(null, buildRules, deps);
    expect(board).toMatchObject({ freshness: null, warnings: [] });
  });

  it("orders current the way the agent pins it", async () => {
    let text = "";
    const deps: VersionReaders = {
      controlDb: () => ({
        execute: async <R>(query: SQL) => {
          text = dialect.sqlToQuery(query).sql.replace(/\s+/g, " ");
          return { rows: [control(null) as R] };
        },
      }),
      versionDb: () => {
        throw new Error("not reached");
      },
    };
    await resolveVersion(null, buildRules, deps);
    expect(text).toContain("WHERE status = 'published'");
    expect(text).toContain("ORDER BY published_at DESC, id DESC LIMIT 1");
    expect(text).toContain("has_schema_privilege(n.oid, 'USAGE')");
    expect(text).toContain("WHERE id = $1::bigint");
  });
});
