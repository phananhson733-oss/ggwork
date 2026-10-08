/** Differential oracle for Python query_rank: real legacy loaders, same synthetic PG version.
 * The Python test supplies a private temporary case file; no service is mocked.
 * Only the pool boundary is injected to allow local PostgreSQL without TLS.
 */
import { readFileSync } from "node:fs";

import { afterAll, describe, expect, it, rs } from "@rstest/core";
import { Pool } from "pg";

import { isRsRank, parsePickRequest } from "@/core/pick-board/request";
import { buildBoardRules } from "@/core/pick-board/rules";
import type * as DbModule from "@/server/pick-board/db";
import { withScriptScope } from "@/server/pick-board/db";
import {
  loadRankMeta,
  loadRankRows,
  loadRsRank,
} from "@/server/pick-board/queries-rank";

const fixture = process.env.PICK_RANK_PARITY_CASES;
const pg = rs.hoisted(() => ({ pool: null as Pool | null }));
rs.mock("@/server/pick-board/db", () => {
  const actual = rs.requireActual<typeof DbModule>("@/server/pick-board/db");
  const scope = actual.makeScope(
    () => ({ scope: null }),
    () => {
      if (!pg.pool) throw new Error("parity pool is not open");
      return pg.pool;
    },
  );
  return { ...actual, ...scope };
});

describe.runIf(Boolean(fixture))(
  "Python rank helper vs existing TypeScript loaders",
  () => {
    afterAll(async () => {
      await pg.pool?.end();
    });
    it("has identical ordered rows, legacy totals, global facets, periods and ledger totals", async () => {
      const data = JSON.parse(readFileSync(fixture!, "utf8")) as {
        url: string;
        asOf: string;
        rules: unknown;
        cases: {
          params: Record<string, string>;
          expected: Record<string, unknown>;
        }[];
      };
      pg.pool = new Pool({ connectionString: data.url });
      await withScriptScope(
        {
          schema: "pickm_v000001",
          asOf: data.asOf,
          versionId: 1,
          rules: buildBoardRules(data.rules, 1),
        },
        async () => {
          for (const test of data.cases) {
            const req = parsePickRequest({ tab: "rank", ...test.params });
            const meta = await loadRankMeta(req);
            const actual: Record<string, unknown> = {
              facets: { ranks: meta.counts, grades: meta.grades },
              period_options: {
                days: meta.days,
                weeks: meta.weeks,
                resolution:
                  meta.dayResolution !== "latest"
                    ? meta.dayResolution
                    : meta.weekResolution,
              },
              actual_period: meta.day
                ? { kind: "daily", value: meta.day }
                : meta.week
                  ? { kind: "weekly", value: meta.week }
                  : { kind: "latest", value: null },
            };
            if (isRsRank(req.rank)) {
              const page = await loadRsRank(req, req.rank);
              if (page.kind === "rows") {
                actual.row_keys = page.rows.map((row) => `reelshort-${row.id}`);
                actual.legacy_total = page.total;
                actual.effective_sort = page.sort;
              } else {
                actual.bill_rows = page.rows.map((row) => ({
                  bill_date: row.billDate,
                  book_id: row.bookId,
                  promotion_type: row.promotionType,
                  canonical_id: row.canonicalId,
                  title: row.title,
                  locale: row.locale,
                  order_cnt: row.orderCnt,
                  source_rows: row.sourceRows,
                  same_day_clicks: row.sameDayClicks,
                }));
                actual.bill_totals = {
                  rows: page.totals.rows,
                  merged_rows: page.totals.mergedRows,
                  orders: page.totals.orders,
                  merged_with_clicks: page.totals.mergedWithClicks,
                  rows_with_clicks: page.totals.rowsWithClicks,
                };
              }
            } else {
              const page = await loadRankRows(req, meta);
              actual.row_keys = page.rows.map((row) => row.rowKey);
              actual.legacy_total = page.total;
              actual.rank_rows = page.rows.map((row) => ({
                row_key: row.rowKey,
                day_rank: row.dayRank,
                day_note: row.dayNote,
                signal: {
                  row_key: row.rowKey,
                  kind: row.signal.kind,
                  ord: row.signal.ord,
                  evidence_on: row.signal.evidenceOn,
                  rank: row.signal.rank,
                  grade: row.signal.grade,
                  note: row.signal.note,
                  payload: row.signal.payload,
                },
              }));
            }
            expect(actual, JSON.stringify(test.params)).toEqual(test.expected);
          }
        },
      );
    });
  },
);
