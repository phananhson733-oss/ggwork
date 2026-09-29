/**
 * The radar's loaders against a real database, as the reader role (plan
 * TR-24): the pick_obs views of migration 0007, with board_obs.py's world in
 * them. Runs only when PICK_BOARD_TEST_PG_URL names a database built by
 * customizations/pick-workbench/tests/mirror/board_fixture.py; skipped
 * otherwise, and CI requires it to run. obsDb is bound to a plain pool (the
 * local cluster has no TLS); everything else is the production code path.
 * The expected values are board_obs.py's world, named there.
 */
import { afterAll, beforeAll, describe, expect, it, rs } from "@rstest/core";
import { type Pool } from "pg";

import type * as DbModule from "@/server/pick-board/db";
import {
  createMirrorPool,
  parseReaderUrl,
  type ScopeHolder,
} from "@/server/pick-board/db";
import {
  loadObsTab,
  resetObsCacheForTests,
  type ObsRequest,
  type ObsTabData,
} from "@/server/pick-board/queries-obs";

const READER_URL = process.env.PICK_BOARD_TEST_PG_URL ?? "";

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

// board_obs.py
const T_LIVE = "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c";
const T_SHADOW = "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6d";
const T_PRUNED = "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6e";
const G_LIVE = "3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c";
const G_SHADOW = "3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8d";
const IDENTITY =
  '["realshort","UkVFTFNIT1JUOjY1MGExYjJjM2Q0ZTVmNmE3YjhjOWQwZQ","en"]';

function load(patch: Partial<ObsRequest>): Promise<ObsTabData> {
  return loadObsTab({ tab: "trends", obs: "", oid: "", ...patch });
}

function as<K extends ObsTabData["kind"]>(
  data: ObsTabData,
  kind: K,
): Extract<ObsTabData, { kind: K }> {
  if (data.kind !== kind) throw new Error(`expected ${kind}, got ${data.kind}`);
  return data as Extract<ObsTabData, { kind: K }>;
}

describe.runIf(READER_URL !== "")("the radar, read as the reader", () => {
  beforeAll(() => {
    pg.pool = createMirrorPool({
      connection: parseReaderUrl(READER_URL),
      ssl: false,
    });
    resetObsCacheForTests();
  });

  afterAll(async () => {
    await pg.pool?.end();
  });

  describe("the trends tab", () => {
    it("shows the live set, lists the published ones newest first, names the latest run", async () => {
      const { trends } = as(await load({}), "trends");
      expect(trends.shown?.set_id).toBe(T_LIVE);
      expect(trends.live?.set_id).toBe(T_LIVE);
      expect(trends.recent.map((s) => [s.set_id, s.mode])).toEqual([
        [T_SHADOW, "shadow"],
        [T_LIVE, "live"],
      ]);
      expect(trends.latestRun?.outcome).toBe("running");
      expect(trends.pin).toBe("none");
      expect(trends.shown?.summary.channel).toBe("trends");
    });

    it("reads the discoveries queue first, then what is only shown, with every route's count", async () => {
      const { discoveries } = as(await load({}), "trends");
      expect(discoveries?.rows.map((d) => [d.discovery_id, d.route])).toEqual([
        [12, "queue"],
        [13, "display_only"],
        [14, "a_tier"],
      ]);
      expect(discoveries?.counts).toEqual({
        queue: 1,
        display_only: 1,
        a_tier: 1,
      });
      expect(discoveries?.truncated).toBe(false);
    });

    it("a pinned shadow set is shown; a pruned or the other channel's pin falls back", async () => {
      const pinned = as(await load({ obs: T_SHADOW }), "trends").trends;
      expect([pinned.pin, pinned.shown?.set_id]).toEqual(["shown", T_SHADOW]);
      for (const obs of [T_PRUNED, G_LIVE]) {
        const fallback = as(await load({ obs }), "trends").trends;
        expect([fallback.pin, fallback.shown?.set_id]).toEqual([
          "missing",
          T_LIVE,
        ]);
      }
    });
  });

  describe("the search tab", () => {
    it("shows the live GSC set and lists the rows with a label", async () => {
      const { gsc, states } = as(await load({ tab: "search" }), "search");
      expect(gsc.shown?.set_id).toBe(G_LIVE);
      expect(gsc.recent.map((s) => s.set_id)).toEqual([G_SHADOW, G_LIVE]);
      expect(gsc.latestRun?.published_set_id).toBe(G_LIVE);
      expect(states?.rows.map((s) => [s.row_id, s.scope, s.state])).toEqual([
        [907, "USA", "surge"],
      ]);
      expect([states?.total, states?.labeled, states?.truncated]).toEqual([
        2,
        1,
        false,
      ]);
    });

    it("a set with no rows is an empty page", async () => {
      const { states } = as(
        await load({ tab: "search", obs: G_SHADOW }),
        "search",
      );
      expect(states).toEqual({
        rows: [],
        total: 0,
        labeled: 0,
        truncated: false,
      });
    });
  });

  describe("an identity's detail", () => {
    it("reads both channels' rows and the link facts of exactly the shown pair", async () => {
      const detail = as(await load({ tab: "search", oid: IDENTITY }), "detail");
      expect([detail.trends.shown?.set_id, detail.gsc.shown?.set_id]).toEqual([
        T_LIVE,
        G_LIVE,
      ]);
      expect(detail.states.map((s) => [s.row_id, s.channel, s.scope])).toEqual([
        [41, "trends", "US"],
        [908, "gsc", "GBR"],
        [907, "gsc", "USA"],
      ]);
      expect(detail.discoveries.map((d) => d.discovery_id)).toEqual([12, 14]);
      expect(detail.links.map((l) => [l.id, l.label])).toEqual([
        [71, "both_rising"],
      ]);
    });

    it("pinning the other GSC set reads no link: its facts belong to another pair", async () => {
      const detail = as(
        await load({ tab: "search", obs: G_SHADOW, oid: IDENTITY }),
        "detail",
      );
      expect(detail.gsc.shown?.set_id).toBe(G_SHADOW);
      expect(detail.links).toEqual([]);
      expect(detail.states.map((s) => s.row_id)).toEqual([41]);
    });

    it("an identity with no rows reads as no rows", async () => {
      const detail = as(
        await load({ tab: "trends", oid: '["kalostv","nobody","en"]' }),
        "detail",
      );
      expect([detail.states, detail.discoveries, detail.links]).toEqual([
        [],
        [],
        [],
      ]);
    });
  });
});
