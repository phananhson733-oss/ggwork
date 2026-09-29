/**
 * The radar's loaders against a fake reader (plan TR-24; D9, D13): what each
 * one sends and how it maps what comes back. Which set a channel shows is
 * read live on every request (the channel's head, then the shown set with
 * status = 'published'); what belongs to one published set, or to one pair of
 * them, is immutable and remembered by set id. The real SQL runs as the
 * reader in queries-obs.integration.test.ts against board_fixture's database.
 */
import { describe, expect, it } from "@rstest/core";
import type { SQL } from "drizzle-orm";
import { PgDialect } from "drizzle-orm/pg-core";

import { makeCacheCell } from "@/server/pick-board/cache";
import type { Executor } from "@/server/pick-board/db";
import { ObsRowInvalid } from "@/server/pick-board/errors";
import {
  DISCOVERY_LIMIT,
  loadObsTab,
  STATE_LIMIT,
  type ObsDeps,
} from "@/server/pick-board/queries-obs";

import { obsFixture } from "../../core/pick/obs-contract-fixtures";

type Row = Record<string, unknown>;
type Statement = Readonly<{ text: string; params: unknown[] }>;

const VIEWS = obsFixture<{ valid: { name: string; row: Row }[] }>("views.json");
const row = (name: string): Row => {
  const found = VIEWS.valid.find((c) => c.name === name)?.row;
  if (!found) throw new Error(`no view case ${name}`);
  return structuredClone(found);
};

const TRENDS_SET = row("sets_trends");
const GSC_SET = row("sets_gsc");
const T_ID = TRENDS_SET.set_id as string;
const G_ID = GSC_SET.set_id as string;
const OTHER_ID = "0123456789abcdef0123456789abcdef";
const IDENTITY = row("states_gsc").identity as string;

const brief = (set: Row) => ({
  set_id: set.set_id,
  mode: set.mode,
  published_at: set.published_at,
});

const dialect = new PgDialect();

type Responder = (statement: Statement) => Row[];

function fakeDeps(respond: Responder) {
  const statements: Statement[] = [];
  const db: Executor = Object.freeze({
    execute: async <R>(query: SQL) => {
      const { sql: text, params } = dialect.sqlToQuery(query);
      const statement = { text, params };
      statements.push(statement);
      return { rows: respond(statement) as R[] };
    },
  });
  const deps: ObsDeps = { db: () => db, cell: makeCacheCell(32) };
  return { deps, statements };
}

type Heads = Partial<Record<"trends" | "gsc", Row>>;

/** A reader holding one trends and one gsc set, answering each statement by its shape. */
function world(
  heads: Heads,
  sets: readonly Row[],
  extra: Responder = () => [],
): Responder {
  return (statement) => {
    const { text, params } = statement;
    if (text.includes("FROM run_status")) {
      const channel = params[0] as "trends" | "gsc";
      return [heads[channel] ?? { recent: null, live: null, run: null }];
    }
    if (/from sets where set_id = \$1/i.test(text)) {
      const found = sets.find(
        (s) => s.set_id === params[0] && s.channel === params[1],
      );
      return found?.status === "published" ? [found] : [];
    }
    return extra(statement);
  };
}

const KINDS: readonly (readonly [string, RegExp])[] = [
  ["head", /from run_status/i],
  ["identity", /from links/i],
  ["set", /from sets where set_id/i],
  ["discovery-counts", /from discoveries[\s\S]*group by route/i],
  ["discoveries", /from discoveries/i],
  ["state-counts", /count\(\*\)[\s\S]*from states/i],
  ["states", /from states/i],
];

const kinds = (statements: readonly Statement[]) =>
  statements.map(
    ({ text }) =>
      KINDS.find(([, pattern]) => pattern.test(text))?.[0] ?? "other",
  );

const REQ = { obs: "", oid: "" } as const;

describe("a channel's head", () => {
  it("an empty radar is one statement per channel and no set", async () => {
    const { deps, statements } = fakeDeps(world({}, []));
    const data = await loadObsTab({ ...REQ, tab: "trends" }, deps);
    expect(data).toEqual({
      kind: "trends",
      trends: {
        channel: "trends",
        shown: null,
        live: null,
        latestRun: null,
        pin: "none",
        recent: [],
      },
      discoveries: null,
    });
    expect(kinds(statements)).toEqual(["head"]);
    expect(statements[0]?.params.filter((p) => typeof p === "string")).toEqual([
      "trends",
      "trends",
      "trends",
    ]);
    expect(statements[0]?.text).toMatch(/status = 'published'/);
  });

  it("shows the live set, and says which run came last", async () => {
    const run = row("run_status_trends");
    const shadow = { ...TRENDS_SET, set_id: OTHER_ID, mode: "shadow" };
    const { deps, statements } = fakeDeps(
      world(
        {
          trends: {
            recent: [brief(shadow), brief(TRENDS_SET)],
            live: brief(TRENDS_SET),
            run,
          },
        },
        [TRENDS_SET, shadow],
        () => [],
      ),
    );
    const data = await loadObsTab({ ...REQ, tab: "trends" }, deps);
    if (data.kind !== "trends") throw new Error(data.kind);
    expect(data.trends.shown?.set_id).toBe(T_ID);
    expect(data.trends.live).toEqual(brief(TRENDS_SET));
    expect(data.trends.latestRun).toEqual(run);
    expect(data.trends.recent.map((s) => s.set_id)).toEqual([OTHER_ID, T_ID]);
    expect(kinds(statements)).toEqual([
      "head",
      "set",
      "discoveries",
      "discovery-counts",
    ]);
    expect(statements[1]?.params).toEqual([T_ID, "trends"]);
  });

  it("with no live set, shows the newest published one (a shadow)", async () => {
    const shadow = { ...GSC_SET };
    const { deps } = fakeDeps(
      world(
        { gsc: { recent: [brief(shadow)], live: null, run: null } },
        [shadow],
        (s) => (/count\(\*\)/i.test(s.text) ? [{ total: 0, labeled: 0 }] : []),
      ),
    );
    const data = await loadObsTab({ ...REQ, tab: "search" }, deps);
    if (data.kind !== "search") throw new Error(data.kind);
    expect(data.gsc.shown?.mode).toBe("shadow");
    expect(data.gsc.pin).toBe("none");
  });

  it("a pinned set is shown when it is this channel's and still published", async () => {
    const older = { ...TRENDS_SET, set_id: OTHER_ID };
    const { deps, statements } = fakeDeps(
      world(
        {
          trends: {
            recent: [brief(TRENDS_SET)],
            live: brief(TRENDS_SET),
            run: null,
          },
        },
        [TRENDS_SET, older],
      ),
    );
    const data = await loadObsTab(
      { ...REQ, tab: "trends", obs: OTHER_ID },
      deps,
    );
    if (data.kind !== "trends") throw new Error(data.kind);
    expect(data.trends.pin).toBe("shown");
    expect(data.trends.shown?.set_id).toBe(OTHER_ID);
    expect(statements[1]?.params).toEqual([OTHER_ID, "trends"]);
  });

  it("a pin that is gone, pruned or the other channel's falls back and says so", async () => {
    const pruned = { ...TRENDS_SET, set_id: OTHER_ID, status: "pruned" };
    for (const obs of [OTHER_ID, G_ID, "f".repeat(32)]) {
      const { deps, statements } = fakeDeps(
        world(
          {
            trends: {
              recent: [brief(TRENDS_SET)],
              live: brief(TRENDS_SET),
              run: null,
            },
          },
          [TRENDS_SET, pruned, GSC_SET],
        ),
      );
      const data = await loadObsTab({ ...REQ, tab: "trends", obs }, deps);
      if (data.kind !== "trends") throw new Error(data.kind);
      expect(data.trends.pin).toBe("missing");
      expect(data.trends.shown?.set_id).toBe(T_ID);
      expect(kinds(statements).slice(0, 3)).toEqual(["head", "set", "set"]);
    }
  });
});

describe("the trends tab (b_only)", () => {
  const unique = row("discoveries_unique");
  const outside = { ...row("discoveries_out_of_pool"), discovery_id: 13 };
  const queued = { ...unique, route: "queue", discovery_id: 14 };
  const heads = {
    trends: { recent: [brief(TRENDS_SET)], live: brief(TRENDS_SET), run: null },
  };

  function routeCounts(discoveries: readonly Row[]): Row[] {
    const counts = new Map<unknown, number>();
    for (const d of discoveries)
      counts.set(d.route, (counts.get(d.route) ?? 0) + 1);
    return [...counts].map(([route, n]) => ({ route, n }));
  }

  function trendsWorld(discoveries: Row[]) {
    return world(heads, [TRENDS_SET], (s) =>
      /group by route/i.test(s.text)
        ? routeCounts(discoveries)
        : /from discoveries/i.test(s.text)
          ? discoveries
          : [],
    );
  }

  it("reads the shown set's discoveries, with each route's count", async () => {
    const rows = [queued, outside, unique];
    const { deps, statements } = fakeDeps(trendsWorld(rows));
    const data = await loadObsTab({ ...REQ, tab: "trends" }, deps);
    if (data.kind !== "trends") throw new Error(data.kind);
    expect(data.discoveries?.rows.map((d) => d.discovery_id)).toEqual([
      14, 13, 12,
    ]);
    expect(data.discoveries?.counts).toEqual({
      queue: 1,
      display_only: 1,
      a_tier: 1,
    });
    expect(data.discoveries?.truncated).toBe(false);
    const read = statements.find((s) => /from discoveries/i.test(s.text));
    expect(read?.params).toContain(T_ID);
    expect(read?.params).toContain(DISCOVERY_LIMIT + 1);
  });

  it("an empty queue is an empty list with no counts, not a zero", async () => {
    const { deps } = fakeDeps(trendsWorld([]));
    const data = await loadObsTab({ ...REQ, tab: "trends" }, deps);
    if (data.kind !== "trends") throw new Error(data.kind);
    expect(data.discoveries).toEqual({
      rows: [],
      counts: {},
      truncated: false,
    });
  });

  it("more than the limit is cut to the limit and marked", async () => {
    const rows = Array.from({ length: DISCOVERY_LIMIT + 1 }, (_, i) => ({
      ...queued,
      discovery_id: i + 1,
    }));
    const { deps } = fakeDeps(trendsWorld(rows));
    const data = await loadObsTab({ ...REQ, tab: "trends" }, deps);
    if (data.kind !== "trends") throw new Error(data.kind);
    expect(data.discoveries?.rows).toHaveLength(DISCOVERY_LIMIT);
    expect(data.discoveries?.truncated).toBe(true);
    expect(data.discoveries?.counts.queue).toBe(DISCOVERY_LIMIT + 1);
  });

  it("a published set's discoveries are read once; the head and the set every time", async () => {
    const { deps, statements } = fakeDeps(trendsWorld([queued]));
    await loadObsTab({ ...REQ, tab: "trends" }, deps);
    await loadObsTab({ ...REQ, tab: "trends" }, deps);
    expect(kinds(statements)).toEqual([
      "head",
      "set",
      "discoveries",
      "discovery-counts",
      "head",
      "set",
    ]);
  });

  it("a failed read is not remembered", async () => {
    let fail = true;
    const { deps, statements } = fakeDeps(
      world(heads, [TRENDS_SET], (s) => {
        if (!/from discoveries/i.test(s.text)) return [];
        if (fail && !/group by/i.test(s.text)) throw new Error("boom");
        return [];
      }),
    );
    await expect(loadObsTab({ ...REQ, tab: "trends" }, deps)).rejects.toThrow(
      "boom",
    );
    fail = false;
    await loadObsTab({ ...REQ, tab: "trends" }, deps);
    expect(kinds(statements).filter((k) => k === "discoveries")).toHaveLength(
      2,
    );
  });

  it("a row the page cannot read is refused by view name, never echoed", async () => {
    const bad = { ...queued, route: "somewhere", term: "secret-term" };
    const { deps } = fakeDeps(trendsWorld([bad]));
    const failure = await loadObsTab({ ...REQ, tab: "trends" }, deps).catch(
      (error: unknown) => error,
    );
    expect(failure).toBeInstanceOf(ObsRowInvalid);
    expect((failure as ObsRowInvalid).view).toBe("discoveries");
    expect(String((failure as Error).message)).not.toContain("secret-term");
  });

  it("a bad head is refused as run_status or sets", async () => {
    const { deps } = fakeDeps(
      world(
        { trends: { recent: null, live: null, run: { channel: "trends" } } },
        [],
      ),
    );
    const failure = await loadObsTab({ ...REQ, tab: "trends" }, deps).catch(
      (error: unknown) => error,
    );
    expect((failure as ObsRowInvalid).view).toBe("run_status");
  });
});

describe("the search tab", () => {
  const surge = row("states_gsc");
  const heads = {
    gsc: { recent: [brief(GSC_SET)], live: null, run: row("run_status_gsc") },
  };

  function searchWorld(states: Row[], counts: Row) {
    return world(heads, [GSC_SET], (s) =>
      /count\(\*\)/i.test(s.text)
        ? [counts]
        : /from states/i.test(s.text)
          ? states
          : [],
    );
  }

  it("lists the rows with a label, and counts every row of the set", async () => {
    const { deps, statements } = fakeDeps(
      searchWorld([surge], { total: 7, labeled: 1 }),
    );
    const data = await loadObsTab({ ...REQ, tab: "search" }, deps);
    if (data.kind !== "search") throw new Error(data.kind);
    expect(data.states).toEqual({
      rows: [surge],
      total: 7,
      labeled: 1,
      truncated: false,
    });
    const list = statements.find(
      (s) => /from states/i.test(s.text) && !/count\(\*\)/i.test(s.text),
    );
    expect(list?.text).toMatch(/json_array_length\(labels\) > 0/);
    expect(list?.params).toEqual([G_ID, STATE_LIMIT + 1]);
  });

  it("a set with no rows reads as no rows", async () => {
    const { deps } = fakeDeps(searchWorld([], { total: 0, labeled: 0 }));
    const data = await loadObsTab({ ...REQ, tab: "search" }, deps);
    if (data.kind !== "search") throw new Error(data.kind);
    expect(data.states).toEqual({
      rows: [],
      total: 0,
      labeled: 0,
      truncated: false,
    });
  });

  it("with no set there is nothing to list", async () => {
    const { deps, statements } = fakeDeps(world({}, []));
    const data = await loadObsTab({ ...REQ, tab: "search" }, deps);
    expect(data).toMatchObject({ kind: "search", states: null });
    expect(kinds(statements)).toEqual(["head"]);
  });
});

describe("an identity's detail", () => {
  const heads = {
    trends: { recent: [brief(TRENDS_SET)], live: brief(TRENDS_SET), run: null },
    gsc: { recent: [brief(GSC_SET)], live: null, run: null },
  };
  const detailRows = {
    states: [row("states_trends"), row("states_gsc")],
    discoveries: [row("discoveries_unique")],
    links: [row("links")],
  };

  it("reads both channels, then the identity's rows of the exact pair in one statement", async () => {
    const { deps, statements } = fakeDeps(
      world(heads, [TRENDS_SET, GSC_SET], (s) =>
        /from links/i.test(s.text) ? [detailRows] : [],
      ),
    );
    const data = await loadObsTab(
      { tab: "search", obs: "", oid: IDENTITY },
      deps,
    );
    if (data.kind !== "detail") throw new Error(data.kind);
    expect(data.tab).toBe("search");
    expect(data.identity).toBe(IDENTITY);
    expect(data.trends.shown?.set_id).toBe(T_ID);
    expect(data.gsc.shown?.set_id).toBe(G_ID);
    expect(data.states).toHaveLength(2);
    expect(data.discoveries).toHaveLength(1);
    expect(data.links).toHaveLength(1);
    const read = statements.find((s) => /from links/i.test(s.text));
    expect(read?.params).toEqual(
      expect.arrayContaining([T_ID, G_ID, IDENTITY]),
    );
  });

  it("is remembered per pair and identity", async () => {
    const { deps, statements } = fakeDeps(
      world(heads, [TRENDS_SET, GSC_SET], (s) =>
        /from links/i.test(s.text) ? [detailRows] : [],
      ),
    );
    await loadObsTab({ tab: "trends", obs: "", oid: IDENTITY }, deps);
    await loadObsTab({ tab: "trends", obs: "", oid: IDENTITY }, deps);
    expect(kinds(statements).filter((k) => k === "identity")).toHaveLength(1);
  });

  it("with one channel empty, reads that channel's side as nothing and no links", async () => {
    const { deps, statements } = fakeDeps(
      world({ gsc: heads.gsc }, [GSC_SET], (s) =>
        /from links/i.test(s.text)
          ? [{ states: [row("states_gsc")], discoveries: [], links: [] }]
          : [],
      ),
    );
    const data = await loadObsTab(
      { tab: "search", obs: "", oid: IDENTITY },
      deps,
    );
    if (data.kind !== "detail") throw new Error(data.kind);
    expect(data.trends.shown).toBeNull();
    expect(data.links).toEqual([]);
    const read = statements.find((s) => /from links/i.test(s.text));
    expect(read?.params).toEqual(expect.arrayContaining([null, G_ID]));
  });
});
