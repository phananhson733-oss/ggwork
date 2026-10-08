/** Real HTTP Gateway and original TS SQL read the same committed synthetic PG versions.
 * Only the local non-TLS pool and Next cookie accessor are injected. No query/API output is mocked.
 */
import { readFileSync } from "node:fs";

import { afterAll, beforeAll, describe, expect, it, rs } from "@rstest/core";
import { type Pool } from "pg";

import {
  BASES,
  PLATFORMS,
  RANKS,
  isDailyRank,
  parsePickRequest,
} from "@/core/pick-board/request";
import { isRsRank } from "@/core/pick-board/request";
import { type BoardRules } from "@/core/pick-board/rules";
import * as active from "@/server/pick-board";
import { resolveBoard as legacyBoard } from "@/server/pick-board/cache";
import * as common from "@/server/pick-board/common-loaders";
import { queryPickBoard } from "@/server/pick-board/common-query";
import type * as DbModule from "@/server/pick-board/db";
import {
  createMirrorPool,
  parseReaderUrl,
  withScriptScope,
} from "@/server/pick-board/db";
import {
  MirrorPeriodMissing,
  MirrorVersionGone,
} from "@/server/pick-board/errors";
import * as legacy from "@/server/pick-board/queries";
import * as legacyPosted from "@/server/pick-board/queries-posted";
import * as legacyRank from "@/server/pick-board/queries-rank";
import { type ReadyBoard } from "@/server/pick-board/version";

const fixturePath = process.env.PICK_COMMON_PARITY_FIXTURE;
const fixture = fixturePath
  ? (JSON.parse(readFileSync(fixturePath, "utf8")) as {
      reader_url: string;
      gateway_url: string;
      cookies: Record<string, string>;
      candidate_adapters: boolean;
      versions: Record<string, number>;
    })
  : null;
const readers = fixture?.candidate_adapters
  ? common
  : {
      ...common,
      loadCommonPickRows: active.loadPickRows,
      loadCommonFacets: active.loadFacets,
      loadCommonCandidatePool: active.loadCandidatePool,
      loadCommonRankMeta: active.loadRankMeta,
      loadCommonRankRows: active.loadRankRows,
      loadCommonRsRank: active.loadRsRank,
      loadCommonPostedList: active.loadPostedList,
      loadCommonPostedStats: active.loadPostedStats,
      loadCommonAccounts: active.loadAccounts,
      resolveCommonBoard: active.resolveBoard,
    };
const observedDomains = new Set<string>();
const originalFetch = globalThis.fetch;
const local = rs.hoisted(() => ({ pool: null as Pool | null }));
rs.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) =>
      fixture?.cookies[name] ? { value: fixture.cookies[name] } : undefined,
  }),
}));
rs.mock("@/server/pick-board/db", () => {
  const actual = rs.requireActual<typeof DbModule>("@/server/pick-board/db");
  return {
    ...actual,
    ...actual.makeScope(
      () => ({ scope: null }),
      () => {
        if (!local.pool) throw new Error("local fixture pool missing");
        return local.pool;
      },
    ),
  };
});
const catalogs: Record<string, string>[] = [
  {},
  { q: "c-1" },
  { q: "c-1 的剧名" },
  { q: "不存在" },
  { q: "parity-0111" },
  { q: "中文标题" },
  { q: "%" },
  { lang: "__unknown__" },
  { off: "1" },
  { yt: "1" },
  { inuse: "1" },
  { dated: "1" },
  { w: "1" },
  { sig: "1" },
  { posted: "yes" },
  { posted: "no" },
  { posted: "pool" },
  { page: "2", size: "20" },
  { size: "200" },
  { page: "2", size: "200" },
  { page: "999", size: "20" },
  ...PLATFORMS.map((platform) => ({ platform })),
  ...BASES.map((basis) => ({ basis })),
  ...["evidence", "listed", "title"].map((sort) => ({ sort })),
];
const ranks: Record<string, string>[] = [
  ...RANKS.map((rk) => ({ rk })),
  { rk: "kd", day: "2026-09-01" },
  { rk: "kd", day: "1999-01-01" },
  { rk: "kd", day: "2026-02-31" },
  { rk: "kw", week: "2026-02-31" },
  { rk: "kw", week: "2026-09-07" },
  { rk: "kw", week: "9.7–9.13" },
  { rk: "kw", week: "1.1–1.7" },
  { rk: "sm", grade: "S" },
  ...[
    "rr",
    "d1",
    "d7",
    "dp1",
    "dp7",
    "promoters",
    "publish",
    "bill",
    "eff",
    "gsc",
    "clicks",
  ].map((rs) => ({ rk: "rs_cand", rs })),
  ...["0-7", "8-30", "31-90", "91-365", "366+"].map((bk) => ({
    rk: "rs_rr",
    bk,
  })),
  { rk: "rs_rr", q: "rs0006" },
  { rk: "rs_rr", rl: "en" },
  { rk: "rs_growth", rs: "d1" },
  { rk: "rs_growth", rs: "dp1" },
  { rk: "rs_growth", rs: "dp7" },
  { rk: "rs_rr", page: "2", size: "20" },
];
const posts: Record<string, string>[] = [
  {},
  { q: "不存在" },
  { q: "%" },
  { q: "c-1" },
  ...(["pub", "sched", "none", "nomatch"] as const).map((pst) => ({ pst })),
  { page: "2", size: "20" },
];

describe.runIf(Boolean(fixture))(
  "common-query real Gateway differential",
  () => {
    const boards: ReadyBoard<BoardRules>[] = [];
    beforeAll(async () => {
      if (
        !fixture ||
        !["127.0.0.1", "localhost"].includes(
          new URL(fixture.gateway_url).hostname,
        )
      )
        throw new Error("requires loopback Gateway fixture");
      rs.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        if (
          typeof input === "string" &&
          input.endsWith("/api/pick/query") &&
          typeof init?.body === "string"
        ) {
          const request = JSON.parse(init.body) as {
            domain: string;
            pin: unknown;
            budget_ms: number;
          };
          expect(request.pin).not.toBeNull();
          expect(request.budget_ms).toBeGreaterThan(0);
          expect(request.budget_ms).toBeLessThanOrEqual(5000);
          observedDomains.add(request.domain);
        }
        return originalFetch(input, init);
      });
      local.pool = createMirrorPool({
        connection: parseReaderUrl(fixture.reader_url),
        ssl: false,
      });
      for (const version of [fixture.versions.v1!, fixture.versions.v2!]) {
        const board = await legacyBoard(version);
        if (board.state !== "ready")
          throw new Error("fixture version not ready");
        boards.push(board);
      }
    });
    afterAll(async () => {
      await local.pool?.end();
      rs.restoreAllMocks();
      expect([...observedDomains].sort()).toEqual([
        "candidates",
        "catalog",
        "posted",
        "rankings",
        "rules",
      ]);
    });
    for (const tab of ["pick", "all"] as const)
      it(`${tab}: exact rows/evidence/RS fields/facets/counts across controls and versions`, async () => {
        for (const board of boards)
          await withScriptScope(board.scope, async () => {
            for (const params of catalogs) {
              const req = parsePickRequest({ tab, ...params });
              expect(
                await readers.loadCommonPickRows(req),
                JSON.stringify({
                  version: board.scope.versionId,
                  tab,
                  ...params,
                }),
              ).toEqual(await legacy.loadPickRows(req));
              expect(
                await readers.loadCommonFacets(req),
                JSON.stringify({
                  version: board.scope.versionId,
                  tab,
                  ...params,
                }),
              ).toEqual(await legacy.loadFacets(req));
            }
            expect(await readers.loadCommonCandidatePool()).toBe(
              await legacy.loadCandidatePool(),
            );
          });
      }, 120000);
    it("all21 ranks: exact source rows/period fallback/global facets/legacy caps and ledger", async () => {
      for (const board of boards)
        await withScriptScope(board.scope, async () => {
          for (const params of ranks) {
            const req = parsePickRequest({ tab: "rank", ...params });
            const meta = await legacyRank.loadRankMeta(req);
            // An entirely absent period is an explicit public period_missing error,
            // distinct from a fallback to the latest available period.
            if (
              (isDailyRank(req.rank) && !meta.day) ||
              (req.rank === "kw" && !meta.week)
            ) {
              await expect(
                readers.loadCommonRankMeta(req),
              ).rejects.toBeInstanceOf(MirrorPeriodMissing);
              continue;
            }
            expect(
              await readers.loadCommonRankMeta(req),
              JSON.stringify(params),
            ).toEqual(meta);
            if (isRsRank(req.rank))
              expect(
                await readers.loadCommonRsRank(req, req.rank),
                JSON.stringify(params),
              ).toEqual(await legacyRank.loadRsRank(req, req.rank));
            else
              expect(
                await readers.loadCommonRankRows(req, meta),
                JSON.stringify(params),
              ).toEqual(await legacyRank.loadRankRows(req, meta));
          }
        });
    }, 120000);
    it("posted: archived/state/search/page rows, linked identities, global stats and accounts", async () => {
      for (const board of boards)
        await withScriptScope(board.scope, async () => {
          for (const params of posts) {
            const req = parsePickRequest({ tab: "posted", ...params });
            expect(
              await readers.loadCommonPostedList(req),
              JSON.stringify(params),
            ).toEqual(await legacyPosted.loadPostedList(req));
          }
          expect(await readers.loadCommonPostedStats()).toEqual(
            await legacyPosted.loadPostedStats(),
          );
          expect(await readers.loadCommonAccounts()).toEqual(
            await legacyPosted.loadAccounts(),
          );
        });
    }, 120000);
    it("rules: historic exact catalog/knowledge pair and never latest substitution", async () => {
      for (const board of boards) {
        const commonBoard = await readers.resolveCommonBoard(
          board.scope.versionId,
        );
        expect(commonBoard).toEqual(board);
      }
      await expect(
        readers.resolveCommonBoard(fixture!.versions.dropped!),
      ).rejects.toBeInstanceOf(MirrorVersionGone);
      await withScriptScope(boards[0]!.scope, async () => {
        const pin = await common.pinForVersion(boards[0]!.scope.versionId);
        expect(pin.mirror_version).not.toBe(boards[1]!.scope.versionId);
        const wrong = await queryPickBoard({
          ...common.boardQuery(parsePickRequest({ tab: "all" }), pin),
          pin: { ...pin, mirror_version: boards[1]!.scope.versionId },
        });
        expect(wrong).toEqual({
          ok: false,
          status: 409,
          code: "version_conflict",
        });
      });
    }, 30000);
  },
);
