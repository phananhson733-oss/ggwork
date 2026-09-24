/**
 * The ported queries against a real mirror, as the reader role (P3-3).
 *
 * Runs only when PICK_BOARD_TEST_PG_URL names a database built by
 * customizations/pick-workbench/tests/mirror/board_fixture.py (its --url-file);
 * skipped otherwise. The loaders read through db.ts's executors; here those
 * are bound to a plain pool (the local cluster has no TLS), everything else
 * is the production code path. Expected values are counted by hand from the
 * fixture (board_fixture.py over gate_world.py), or taken from what RealShort
 * itself computed for the same rows (meta.control, meta.rsCounts).
 */
import { afterAll, beforeAll, describe, expect, it, rs } from "@rstest/core";
import { sql } from "drizzle-orm";
import { type Pool } from "pg";

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
  freshnessOf,
  loadCandidatePool,
  loadFacets,
  loadFreshness,
  loadPickRows,
  loadRowDetail,
} from "@/server/pick-board/queries";
import {
  loadAccounts,
  loadPostedList,
  loadPostedRecord,
  loadPostedStats,
} from "@/server/pick-board/queries-posted";
import {
  loadGrowthDiagnosis,
  loadRankMeta,
  loadRankRows,
  loadRsRank,
} from "@/server/pick-board/queries-rank";
import { loadReelshortDetail } from "@/server/pick-board/queries-reelshort";
import {
  loadDramaDetail,
  loadRowsByIds,
  loadRsCounts,
} from "@/server/pick-board/rs-queries";
import { readSources } from "@/server/pick-board/source-state";
import {
  resolveVersion,
  type BoardVersion,
  type ReadyBoard,
} from "@/server/pick-board/version";

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

type Board = ReadyBoard<BoardRules>;

function ready(board: BoardVersion<BoardRules>): Board {
  if (board.state !== "ready") throw new Error("expected a published version");
  return board;
}

type PickParams = Record<string, string>;

const ask = (params: PickParams = {}) => parsePickRequest(params);

async function column<T>(query: ReturnType<typeof sql>): Promise<T[]> {
  const { rows } = await getDb().execute<{ v: T }>(query);
  return rows.map((row) => row.v);
}

async function metaValue(key: string): Promise<unknown> {
  const [value] = await column<unknown>(
    sql`SELECT value AS v FROM meta WHERE key = ${key}`,
  );
  return value;
}

describe.runIf(READER_URL !== "")("the ported queries on a real mirror", () => {
  let v1: Board;
  let v2: Board;
  const inV1 = <T>(fn: () => Promise<T>) => withScriptScope(v1.scope, fn);
  const inV2 = <T>(fn: () => Promise<T>) => withScriptScope(v2.scope, fn);
  const at =
    (asOf: string) =>
    <T>(fn: () => Promise<T>) =>
      withScriptScope({ ...v2.scope, asOf }, fn);

  beforeAll(async () => {
    pg.pool = createMirrorPool({
      connection: parseReaderUrl(READER_URL),
      ssl: false,
    });
    v2 = ready(await resolveVersion(null, buildBoardRules));
    const [older] = await withScriptScope(v2.scope, async () =>
      column<number>(
        sql`SELECT id::int AS v FROM pick_mirror.versions WHERE status = 'published' AND id <> ${v2.scope.versionId}`,
      ),
    );
    v1 = ready(await resolveVersion(older ?? null, buildBoardRules));
  });

  afterAll(async () => {
    await pg.pool?.end();
  });

  describe("选剧 / 全部剧库", () => {
    it("pick default: has_signal and not delisted, in RealShort's order", async () => {
      const page = await inV2(() => loadPickRows(ask()));
      expect(page.rows.map((r) => r.rowKey)).toEqual([
        "c-2",
        "c-1",
        "reelshort-rs0001",
        "reelshort-rs0002",
        "reelshort-rs0004",
        "reelshort-rs0005",
      ]);
      const expected = await inV2(() =>
        column<string>(
          sql`SELECT row_key AS v FROM catalog_rows WHERE has_signal AND off_on IS NULL
              UNION ALL SELECT row_key FROM rs_rows WHERE has_signal AND off_on IS NULL`,
        ),
      );
      expect([...page.rows.map((r) => r.rowKey)].sort()).toEqual(
        [...expected].sort(),
      );
      expect({ total: page.total, hasMore: page.hasMore }).toEqual({
        total: 6,
        hasMore: false,
      });
    });

    it("decorates a page: signals, posted records and the ReelShort metrics", async () => {
      const { rows } = await inV2(() => loadPickRows(ask()));
      const byKey = new Map(rows.map((r) => [r.rowKey, r]));
      const c1 = byKey.get("c-1");
      expect(c1?.signals.map((s) => s.kind)).toEqual(["kd", "kw", "kd"]);
      expect(c1?.posted.map((p) => p.sd)).toEqual(["SD-1"]);
      expect(c1?.rs).toBeUndefined();
      expect(byKey.get("c-2")?.hasPan).toBe(true);
      const rs1 = byKey.get("reelshort-rs0001");
      expect(rs1?.rs?.id).toBe("rs0001");
      expect(rs1?.posted.map((p) => p.sd)).toEqual(["SD-2"]);
      expect(rs1?.rsFlags).toEqual({ clk: true, bill: true, gsc: true });
      expect(byKey.get("reelshort-rs0005")?.rs?.promotersCnt).toBe(4);
    });

    it("facet counts, each without its own dimension", async () => {
      expect(await inV2(() => loadFacets(ask()))).toEqual({
        platforms: { shortmax: 1, dramabox: 1, reelshort: 4 },
        langs: [{ lang: "英语", n: 6 }],
        bases: { kd: 1, kw: 1, sm: 1, clk: 3, bill: 2, gsc: 1 },
        posted: { pool: 2, yes: 1, no: 5 },
      });
    });

    it("B15: yt=1 and inuse=1 follow the version's rules (shortmax ok in v1, no in v2)", async () => {
      const filtered = ask({ yt: "1", inuse: "1" });
      const keys = async (inVersion: typeof inV1) =>
        (await inVersion(() => loadPickRows(filtered))).rows.map(
          (r) => r.rowKey,
        );
      const [old, now] = [await keys(inV1), await keys(inV2)];
      expect(old.filter((k) => !now.includes(k))).toEqual(["c-1"]);
      expect(now.filter((k) => !old.includes(k))).toEqual([]);
      expect(now).toContain("c-2");
    });

    it("the agent's candidate pool N equals the pick default total", async () => {
      expect(await inV2(() => loadCandidatePool())).toBe(6);
    });

    it("freshness: the version's own, as Dates", async () => {
      const fresh = await inV2(() => loadFreshness());
      expect(fresh).toEqual(freshnessOf(v2.freshness));
      expect(fresh.importedAt?.toISOString()).toBe("2026-09-22T03:10:06.500Z");
      expect(fresh.rsSyncedAt?.toISOString()).toBe("2026-09-23T09:00:00.000Z");
      expect([fresh.rows, fresh.withSignal, fresh.rsCandidates]).toEqual([
        4, 3, 4,
      ]);
    });
  });

  describe("榜单", () => {
    it("rank counts equal what RealShort counted for the same rows (meta.control.rankCounts)", async () => {
      const meta = await inV2(() => loadRankMeta(ask({ tab: "rank" })));
      const control = (await inV2(() => metaValue("control"))) as {
        rankCounts: Record<string, number>;
      };
      expect(meta.counts).toEqual(control.rankCounts);
    });

    it("daily rank: the day's ranks through the LATERAL, newest day first", async () => {
      const req = ask({ tab: "rank", rk: "kd" });
      const meta = await inV2(() => loadRankMeta(req));
      expect(meta.days).toEqual(["2026-09-02", "2026-09-01"]);
      expect([meta.day, meta.dayResolution]).toEqual(["2026-09-02", "latest"]);
      const page = await inV2(() => loadRankRows(req, meta));
      expect(page.rows.map((r) => [r.rowKey, r.dayRank])).toEqual([
        ["c-4", 1],
        ["c-1", 4],
      ]);
      expect(page.total).toBe(2);
    });

    it("weekly rank: weeks by their start day, the board of the chosen week", async () => {
      const latest = await inV2(() =>
        loadRankMeta(ask({ tab: "rank", rk: "kw" })),
      );
      expect(latest.weeks).toEqual([
        { week: "9.14–9.20", start: "2026-09-14" },
        { week: "9.7–9.13", start: "2026-09-07" },
      ]);
      const req = ask({ tab: "rank", rk: "kw", week: "2026-09-07" });
      const meta = await inV2(() => loadRankMeta(req));
      expect([meta.week, meta.weekResolution]).toEqual(["2026-09-07", "exact"]);
      const page = await inV2(() => loadRankRows(req, meta));
      expect(page.rows.map((r) => r.rowKey)).toEqual(["c-1"]);
    });
  });

  describe("ReelShort 榜与订单对账", () => {
    const rankRows = async (params: PickParams, inVersion = inV2) => {
      const result = await inVersion(() =>
        loadRsRank(ask({ tab: "rank", ...params }), "rs_rr"),
      );
      if (result.kind !== "rows") throw new Error("expected rows");
      return result.rows.map((r) => r.id);
    };

    it("seven boards: each keeps its own rows", async () => {
      const ids = async (rank: Parameters<typeof loadRsRank>[1]) => {
        const result = await inV2(() => loadRsRank(ask({ tab: "rank" }), rank));
        if (result.kind !== "rows") throw new Error("expected rows");
        return result.rows.map((r) => r.id);
      };
      expect(await ids("rs_rr")).toHaveLength(5);
      expect(await ids("rs_cand")).toEqual(
        expect.arrayContaining(["rs0001", "rs0002", "rs0004", "rs0005"]),
      );
      expect(await ids("rs_cand")).toHaveLength(4);
      expect(await ids("rs_pc")).toEqual(["rs0005", "rs0001", "rs0002"]);
      expect(await ids("rs_clk")).toEqual(["rs0001", "rs0005", "rs0002"]);
      expect(await ids("rs_gsc")).toEqual(["rs0001"]);
      expect(await ids("rs_bill")).toEqual(["rs0004", "rs0001"]);
      expect(await ids("rs_growth")).toEqual(["rs0001", "rs0002", "rs0003"]);
    });

    it("C35: a d1 tie stays tied under numeric and falls back to drama_id", async () => {
      expect((await rankRows({ rs: "d1" })).slice(0, 2)).toEqual([
        "rs0002",
        "rs0003",
      ]);
      // The same rows in float8 arithmetic come out the other way round.
      const float = await inV2(() =>
        column<string>(
          sql`SELECT drama_id AS v FROM rs_rows ORDER BY (rr - s1_rr) DESC NULLS LAST, drama_id LIMIT 2`,
        ),
      );
      expect(float).toEqual(["rs0003", "rs0002"]);
    });

    it("growth: dp1 compares with yesterday (p1), counts growthDp1, diagnoses a one-day window", async () => {
      expect(
        await inV2(async () => {
          const result = await loadRsRank(
            ask({ tab: "rank", rk: "rs_growth", rs: "dp1" }),
            "rs_growth",
          );
          return result.kind === "rows" ? result.rows.map((r) => r.id) : [];
        }),
      ).toEqual(["rs0001", "rs0002"]);
      const req = ask({ tab: "rank", rk: "rs_growth", rs: "dp1" });
      const meta = await inV2(() => loadRankMeta(req));
      expect(meta.counts.rs_growth).toBe(2);
      const diagnosis = await inV2(() => loadGrowthDiagnosis(req));
      expect([diagnosis.windowDays, diagnosis.baselineDay]).toEqual([
        1,
        "2026-09-22",
      ]);
    });

    it("buckets are frozen at the version's as_of", async () => {
      expect(await rankRows({ bk: "0-7" })).toEqual(["rs0001"]);
      expect(await rankRows({ bk: "8-30" })).toEqual(["rs0002"]);
      const later = at("2026-10-03T22:15:00+00:00");
      expect(await rankRows({ bk: "0-7" }, later)).toEqual([]);
      expect(await rankRows({ bk: "8-30" }, later)).toEqual([
        "rs0002",
        "rs0001",
      ]);
    });

    it("q finds a canonical row by a sibling's book id", async () => {
      expect(await rankRows({ q: "rs0006" })).toEqual(["rs0001"]);
    });

    it("ledger: merged rows, totals by source rows, the bill source of the version", async () => {
      const result = await inV2(() =>
        loadRsRank(ask({ tab: "rank" }), "rs_ledger"),
      );
      if (result.kind !== "ledger") throw new Error("expected the ledger");
      expect(
        result.rows.map((r) => [r.bookId, r.canonicalId, r.title, r.orderCnt]),
      ).toEqual([
        ["book-x", null, "book_title-2-文本", 1],
        ["rs0001", "rs0001", "rs0001 的剧名", 5],
      ]);
      expect(result.totals).toEqual({
        rows: 3,
        mergedRows: 2,
        orders: 6,
        mergedWithClicks: 2,
        rowsWithClicks: 3,
      });
      const control = (await inV2(() => metaValue("control"))) as {
        ledger: { rows: number; orders: number };
      };
      expect([result.totals.rows, result.totals.orders]).toEqual([
        control.ledger.rows,
        control.ledger.orders,
      ]);
      expect(result.source?.status).toBe("success");
      expect(await inV2(() => readSources())).toEqual({ bill: result.source });
    });

    it("rsCounts from meta equal a recount on rs_rows", async () => {
      const recount = await inV2(async () => {
        const { rows } = await getDb().execute<Record<string, number>>(
          sql`SELECT count(*)::int AS "all", count(*) FILTER (WHERE has_signal)::int AS cand,
                count(*) FILTER (WHERE rr1 IS NOT NULL)::int AS "growthD1",
                count(*) FILTER (WHERE rr7 IS NOT NULL)::int AS "growthD7",
                count(*) FILTER (WHERE p1 IS NOT NULL)::int AS "growthDp1",
                count(*) FILTER (WHERE p7 IS NOT NULL)::int AS "growthDp7",
                count(*) FILTER (WHERE promoters_cnt > 0)::int AS pc,
                count(*) FILTER (WHERE clicks7 > 0)::int AS clk,
                count(*) FILTER (WHERE search_impressions > 0)::int AS gsc,
                count(*) FILTER (WHERE bill_orders > 0)::int AS bill,
                (SELECT coalesce(sum(source_rows), 0)::int FROM rs_bill_orders) AS ledger
              FROM rs_rows`,
        );
        return rows[0];
      });
      expect(await inV2(() => loadRsCounts())).toEqual(recount);
    });
  });

  describe("ReelShort 行指标", () => {
    it("metricsValid maps the boolean column: true, false and null", async () => {
      const rows = await inV2(() =>
        loadRowsByIds(["rs0001", "rs0004", "rs0005"]),
      );
      expect(
        ["rs0001", "rs0004", "rs0005"].map((id) => rows.get(id)?.metricsValid),
      ).toEqual([true, false, null]);
      expect(rows.get("rs0001")?.clicks7).toBe(3);
      expect(rows.get("rs0004")?.clicks7).toBe(0);
    });

    it("full tags and description only when the page holds one ReelShort row", async () => {
      const one = await inV2(() => loadRowsByIds(["rs0001"]));
      const two = await inV2(() => loadRowsByIds(["rs0001", "rs0002"]));
      expect(one.get("rs0001")?.tags).toHaveLength(10);
      expect(one.get("rs0001")?.description).not.toBe("");
      expect(two.get("rs0001")?.tags).toHaveLength(8);
      expect(two.get("rs0001")?.description).toBe("");
    });
  });

  describe("证据页", () => {
    it("a theater row: its matched ReelShort drama from rs_ids", async () => {
      const detail = await inV2(() => loadRowDetail("c-2"));
      expect(detail?.row.signals.map((s) => s.kind)).toEqual(["sm"]);
      expect(detail?.siteDramas).toEqual([
        {
          id: "rs0001",
          locale: "en",
          slug: "rs0001-slug",
          title: "rs0001 的剧名",
          chapterCount: 1,
          payStart: 1,
        },
      ]);
      expect(detail?.columnFilled).toEqual({ episodes: true, payStart: true });
      expect(await inV2(() => loadRowDetail("no-such-row"))).toBeNull();
    });

    it("a ReelShort row through a sibling's book id: the canonical row and all of its evidence", async () => {
      const detail = await inV2(() => loadReelshortDetail("rs0006"));
      expect(detail?.row.id).toBe("rs0001");
      expect(detail?.sameTitle.map((s) => s.rowKey)).toEqual(["c-2"]);
      expect(detail?.postedRecords.map((p) => p.sd)).toEqual(["SD-2"]);
      expect(
        detail?.bill.map((b) => [b.bookId, b.canonicalId, b.sourceRows]),
      ).toEqual([["rs0001", "rs0001", 2]]);
      expect(detail?.clicks).toEqual([{ day: "2026-09-20", human: 1, bot: 1 }]);
      expect(detail?.series.map((p) => p.day)).toEqual([
        "2026-09-21",
        "2026-09-22",
        "2026-09-23",
      ]);
    });

    it("B21: a book id with no canonical row, or none at all, finds nothing", async () => {
      expect(await inV2(() => loadReelshortDetail("rs0007"))).toBeNull();
      expect(await inV2(() => loadReelshortDetail("zz9999"))).toBeNull();
    });

    it("the curve stops at the earlier of latest_snapshot and the as_of day; trimmed_before comes with the version", async () => {
      const earlier = at("2026-09-22T12:00:00+00:00");
      const detail = await earlier(() => loadDramaDetail("rs0001"));
      expect(detail?.series.map((p) => [p.day, p.revenueRaw])).toEqual([
        ["2026-09-21", 1250],
        ["2026-09-22", 980.5],
      ]);
      expect(v2.series?.trimmedBefore).toBe("2026-06-25");
    });
  });

  describe("发布记录", () => {
    it("stats and state counts equal what RealShort counted (meta.control)", async () => {
      const control = (await inV2(() => metaValue("control"))) as {
        postedStats: Record<string, unknown>;
        postedStates: Record<string, number>;
      };
      const stats = await inV2(() => loadPostedStats());
      expect({
        ...stats,
        importedAt: stats.importedAt?.toISOString() ?? null,
      }).toEqual(control.postedStats);
      const list = await inV2(() => loadPostedList(ask({ tab: "posted" })));
      const { "": all, ...states } = list.counts;
      expect(all).toBe(4);
      expect(states).toEqual(control.postedStates);
    });

    it("links a record to its theater rows and ReelShort dramas", async () => {
      const detail = await inV2(() => loadPostedRecord("SD-2"));
      expect(detail?.record.dramaIds).toEqual(["rs0001"]);
      expect(detail?.links.dramas.get("rs0001")?.slug).toBe("rs0001-slug");
      const list = await inV2(() =>
        loadPostedList(ask({ tab: "posted", pst: "pub" })),
      );
      expect(list.rows.map((r) => r.sd)).toEqual(["SD-4", "SD-1"]);
      expect(list.links.rows.get("c-1")?.platform).toBe("shortmax");
      expect(await inV2(() => loadPostedRecord("SD-404"))).toBeNull();
    });

    it("accounts in grp, name order", async () => {
      expect((await inV2(() => loadAccounts())).map((a) => a.id)).toEqual([
        "a-1",
        "a-2",
      ]);
    });
  });
});
