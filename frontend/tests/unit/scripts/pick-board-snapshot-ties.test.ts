/**
 * Tie completion in scripts/pick-board-snapshot-core.rs.ts (snapshot format
 * 3): after the first ROW_LIMIT rows, a list goes on through the end of row
 * 50's tie group, the rows sharing its collation-independent sort keys
 * (tiePrimary), so the two databases' collations cannot change which rows
 * each side keeps. Fake loaders only; nothing here opens a database.
 */
import { describe, expect, it } from "@rstest/core";

import type { PickRequest } from "@/core/pick-board/request";

import {
  ROW_LIMIT,
  TIE_ROWS_MAX,
  runCase,
  tiePrimary,
  tieStats,
  tieSummary,
  type BoardLoaders,
  type Json,
} from "../../../scripts/pick-board-snapshot-core.rs";

import { unusedLoaders } from "./pick-board-parity-fixtures";

describe("tiePrimary", () => {
  const signal = (kind: string, over: Record<string, Json> = {}): Json => ({
    kind,
    ord: 0,
    evidenceOn: null,
    rank: null,
    grade: "",
    note: "",
    payload: {},
    ...over,
  });
  const rankRow = (over: Record<string, Json>): Json => ({
    rowKey: "kalos-a",
    sourceTable: "catalog_rows",
    title: "t",
    listedOn: "2026-09-01",
    dayRank: null,
    ...over,
  });
  const key = (value: Json[]) => JSON.stringify(value);

  it("daily ranks tie on the day's rank", () => {
    for (const kind of ["kd", "qc", "qr"])
      expect(tiePrimary(rankRow({ signal: signal(kind), dayRank: 3 }))).toBe(
        key([3]),
      );
  });

  it("the weekly rank ties on the weeks count", () => {
    const weekly = signal("kw", { payload: { weeks: 5, h: [] } });
    expect(tiePrimary(rankRow({ signal: weekly }))).toBe(key([5]));
    expect(tiePrimary(rankRow({ signal: { kind: "kw" } }))).toBe(key([null]));
  });

  it("graded ranks tie on the grade's position (unknown grades together), then the listing date", () => {
    const graded = (kind: string, grade: Json) =>
      tiePrimary(rankRow({ signal: signal(kind, { grade }) }));
    expect(graded("sm", "SSS")).toBe(key([0, "2026-09-01"]));
    expect(graded("mg", "S")).toBe(key([2, "2026-09-01"]));
    // array_position(GRADES, grade) NULLS LAST: every grade outside GRADES
    // is NULL, so "", an unknown grade and a missing one all tie.
    expect(graded("sm", "")).toBe(key([null, "2026-09-01"]));
    expect(graded("sm", "X")).toBe(graded("sm", ""));
    expect(graded("sm", null)).toBe(graded("sm", ""));
  });

  it("other theater ranks tie on the evidence date, then the listing date", () => {
    const row = rankRow({ signal: signal("fh", { evidenceOn: "2026-09-02" }) });
    expect(tiePrimary(row)).toBe(key(["2026-09-02", "2026-09-01"]));
    expect(tiePrimary(rankRow({ signal: signal("gn"), listedOn: null }))).toBe(
      key([null, null]),
    );
  });

  it("pick rows tie on the latest evidence date, then the listing date", () => {
    const row = {
      rowKey: "kalos-a",
      sourceTable: "kalos_rows",
      latestEvidenceOn: "2026-09-02",
      listedOn: "2026-09-01",
    };
    expect(tiePrimary(row)).toBe(key(["2026-09-02", "2026-09-01"]));
    expect(tiePrimary({ rowKey: "kalos-a", sourceTable: "t" })).toBe(
      key([null, null]),
    );
  });

  it("is null for anything that is not a list row", () => {
    for (const value of [
      null,
      "x",
      3,
      [],
      { rowKey: "kalos-a" },
      { lang: "英语", n: 2 },
      { id: "rs0001", title: "t" },
    ] as Json[])
      expect(tiePrimary(value)).toBeNull();
  });
});

/* Tie completion: a full list is extended through the end of row 50's tie
 * group (rows sharing its collation-independent sort keys), paging on with
 * the same loader. The fake loaders serve a fixed, already ordered list. */

/** Pick rows in list order, n per evidence day: [[45, "20"], [13, "19"]] */
function pickList(groups: readonly (readonly [number, string])[]): Json[] {
  return groups.flatMap(([n, day], g) =>
    Array.from({ length: n }, (_, i) => ({
      rowKey: `kalos-${g}-${i}`,
      sourceTable: "kalos_rows",
      title: `t${g}-${i}`,
      latestEvidenceOn: `2026-09-${day}`,
      listedOn: null,
    })),
  );
}

/** Daily rank rows in list order, n per day rank: [[48, 1], [5, 2]] */
function dailyList(groups: readonly (readonly [number, number])[]): Json[] {
  return groups.flatMap(([n, dayRank], g) =>
    Array.from({ length: n }, (_, i) => ({
      rowKey: `kalos-${g}-${i}`,
      sourceTable: "catalog_rows",
      title: `t${g}-${i}`,
      listedOn: null,
      signal: { kind: "kd", ord: 0, grade: "", payload: {} },
      dayRank,
    })),
  );
}

function paginate(rows: readonly Json[], req: PickRequest) {
  const offset = (req.page - 1) * req.size;
  const page = rows.slice(offset, offset + req.size);
  return {
    rows: page,
    total: rows.length,
    hasMore: offset + page.length < rows.length,
  };
}

type Meta = { kind: string };

function pagedLoaders(rows: readonly Json[]) {
  const pick: PickRequest[] = [];
  const rank: [PickRequest, Meta][] = [];
  const metas: Meta[] = [];
  const loaders: BoardLoaders<PickRequest, Meta> = {
    ...unusedLoaders<Meta>(),
    facets: async () => ({ platforms: {} }),
    pickRows: async (req) => {
      pick.push(req);
      return paginate(rows, req);
    },
    rankMeta: async () => {
      const meta = { kind: "meta" };
      metas.push(meta);
      return meta;
    },
    rankRows: async (req, meta) => {
      rank.push([req, meta]);
      return paginate(rows, req);
    },
  };
  return { loaders, pick, rank, metas };
}

async function completed(rows: readonly Json[], query: string) {
  const paged = pagedLoaders(rows);
  const result = await runCase(paged.loaders, { id: "case", query });
  const outer = result as Record<string, Json>;
  const page = outer.page as Record<string, Json>;
  const kept = page.rows as readonly Json[];
  const keys = kept.map((r) => (r as Record<string, Json>).rowKey);
  return { ...paged, page, keys, pages: paged.pick.map((r) => r.page) };
}

describe("runCase: completing the tie group at row 50", () => {
  it("leaves a list that is not full alone", async () => {
    for (const n of [30, ROW_LIMIT - 1]) {
      const { page, pages } = await completed(pickList([[n, "20"]]), "");
      expect(pages).toEqual([1]);
      expect((page.rows as Json[]).length).toBe(n);
      expect(page.ties).toBeUndefined();
    }
  });

  it("reads the next page once and stops when it opens on another key", async () => {
    const rows = pickList([
      [50, "20"],
      [60, "19"],
    ]);
    const { page, pages, keys } = await completed(rows, "");
    expect(pages).toEqual([1, 2]);
    expect(keys).toHaveLength(ROW_LIMIT);
    expect(page.ties).toEqual({ extra: 0, capped: false });
    expect(page.total).toBe(110);
    expect(page.hasMore).toBe(true);
  });

  it("stops at row 50 when row 51 of a bigger page has another key", async () => {
    const rows = pickList([
      [50, "20"],
      [80, "19"],
    ]);
    const { page, pages, keys } = await completed(rows, "size=100");
    expect(pages).toEqual([1]);
    expect(page.ties).toEqual({ extra: 0, capped: false });
    expect(keys).toHaveLength(ROW_LIMIT);
  });

  it("pages on only while the page says hasMore: true", async () => {
    const rows = pickList([[80, "20"]]);
    const pages: number[] = [];
    const loaders: BoardLoaders<PickRequest, Meta> = {
      ...pagedLoaders(rows).loaders,
      pickRows: async (req) => {
        pages.push(req.page);
        const { rows: served, total } = paginate(rows, req);
        return { rows: served, total };
      },
    };
    const result = await runCase(loaders, { id: "case", query: "" });
    const page = (result as Record<string, Record<string, Json>>).page;
    expect(pages).toEqual([1]);
    expect(page?.ties).toEqual({ extra: 0, capped: false });
  });

  it("completes the tie group that row 50 opens (row 49 on another key)", async () => {
    const rows = pickList([
      [49, "20"],
      [11, "19"],
    ]);
    const { page, pages, keys } = await completed(rows, "");
    expect(pages).toEqual([1, 2]);
    expect(page.ties).toEqual({ extra: 10, capped: false });
    expect(keys).toHaveLength(60);
  });

  it("appends the rest of a tie group that runs into the next page", async () => {
    const rows = pickList([
      [45, "20"],
      [13, "19"],
      [20, "18"],
    ]);
    const { page, pages, keys } = await completed(rows, "tab=all");
    expect(pages).toEqual([1, 2]);
    expect(page.ties).toEqual({ extra: 8, capped: false });
    expect(keys).toEqual(
      rows.slice(0, 58).map((r) => (r as Record<string, Json>).rowKey),
    );
  });

  it("follows a tie group across more than one page", async () => {
    const rows = pickList([
      [40, "20"],
      [90, "19"],
      [20, "18"],
    ]);
    const { page, pages, keys } = await completed(rows, "");
    expect(pages).toEqual([1, 2, 3]);
    expect(page.ties).toEqual({ extra: 80, capped: false });
    expect(keys).toHaveLength(130);
  });

  it("stops at TIE_ROWS_MAX appended rows while the tie goes on (capped)", async () => {
    const rows = pickList([
      [40, "20"],
      [300, "19"],
    ]);
    const { page, pages, keys } = await completed(rows, "");
    // Pages 2 and 3 fill the cap and the tie runs to their last row: capped
    // without reading page 4, which could not change the verdict.
    expect(pages).toEqual([1, 2, 3]);
    expect(page.ties).toEqual({ extra: TIE_ROWS_MAX, capped: true });
    expect(keys).toHaveLength(ROW_LIMIT + TIE_ROWS_MAX);
  });

  it("a group of exactly TIE_ROWS_MAX rows is complete only when its end was read", async () => {
    const exact = pickList([
      [40, "20"],
      [10 + TIE_ROWS_MAX, "19"],
      [5, "18"],
    ]);
    // Size 50: the group ends on page 3's last row, the next key is on page
    // 4, which is not read; capped is the conservative answer.
    const unseen = await completed(exact, "");
    expect(unseen.pages).toEqual([1, 2, 3]);
    expect(unseen.page.ties).toEqual({ extra: TIE_ROWS_MAX, capped: true });
    // Size 100: page 2 shows the next key right after the group.
    const seen = await completed(exact, "size=100");
    expect(seen.pages).toEqual([1, 2]);
    expect(seen.page.ties).toEqual({ extra: TIE_ROWS_MAX, capped: false });
  });

  it("caps a group one row past TIE_ROWS_MAX even when a bigger page shows its end", async () => {
    // Size 100: rows 51-100 are page 1's, page 2 holds the other 51 group
    // rows and then the next key. The cap still holds: 100 rows, capped.
    const over = pickList([
      [40, "20"],
      [10 + TIE_ROWS_MAX + 1, "19"],
      [5, "18"],
    ]);
    const shown = await completed(over, "size=100");
    expect(shown.pages).toEqual([1, 2]);
    expect(shown.page.ties).toEqual({ extra: TIE_ROWS_MAX, capped: true });
    expect(shown.keys).toHaveLength(ROW_LIMIT + TIE_ROWS_MAX);
    // A page that fills the cap halfway through: capped there, no page 3.
    const long = await completed(
      pickList([
        [40, "20"],
        [300, "19"],
      ]),
      "size=100",
    );
    expect(long.pages).toEqual([1, 2]);
    expect(long.page.ties).toEqual({ extra: TIE_ROWS_MAX, capped: true });
  });

  it("ends, not capped, when a page hasMore promised comes back empty", async () => {
    // RealShort counts non-daily ranks without the page query's join, so an
    // orphan signal can keep hasMore true past the last row.
    const rows = pickList([[60, "20"]]);
    const pages: number[] = [];
    const loaders: BoardLoaders<PickRequest, Meta> = {
      ...pagedLoaders(rows).loaders,
      pickRows: async (req) => {
        pages.push(req.page);
        if (req.page > 2)
          throw new Error(`page ${req.page} read past an empty page`);
        const served = req.page === 1 ? rows.slice(0, req.size) : [];
        return { rows: served, total: 999, hasMore: true };
      },
    };
    const result = await runCase(loaders, { id: "case", query: "" });
    const page = (result as Record<string, Record<string, Json>>).page;
    expect(pages).toEqual([1, 2]);
    expect(page?.ties).toEqual({ extra: 0, capped: false });
    expect((page?.rows as Json[]).length).toBe(ROW_LIMIT);
  });

  it("completes only page 1 at a page size of at least ROW_LIMIT", async () => {
    // Page 2 has an open boundary at its head too, and at size 20 the LIMIT
    // is row 20, not row 50: those stay on plain trimming (no case sets
    // page or size, see the case list test in pick-board-snapshot.test.ts).
    const rows = pickList([
      [120, "20"],
      [60, "19"],
    ]);
    for (const [query, first, length] of [
      ["page=2", 2, ROW_LIMIT],
      ["tab=all&page=3", 3, ROW_LIMIT],
      ["size=20", 1, 20],
    ] as const) {
      const { page, pages } = await completed(rows, query);
      expect([query, pages]).toEqual([query, [first]]);
      expect((page.rows as Json[]).length).toBe(length);
      expect(page.ties).toBeUndefined();
    }
    // Page 2 of the rank is full and tied to its end: page 3 would be read.
    const daily = await completed(
      dailyList([[200, 1]]),
      "tab=rank&rk=kd&page=2",
    );
    expect(daily.rank.map(([req]) => req.page)).toEqual([2]);
    expect(daily.page.ties).toBeUndefined();
    // parsePickRequest reads a bad or missing page as page 1: completed.
    for (const query of ["page=1", "page=0", "page=x", "size=50"]) {
      const { page } = await completed(rows, query);
      expect([query, page.ties]).toEqual([query, { extra: 70, capped: false }]);
    }
  });

  it("keeps plain trimming for any sort but the default one", async () => {
    const rows = pickList([[60, "20"]]);
    for (const sort of ["title", "listed"]) {
      const { page, pages } = await completed(rows, `tab=all&sort=${sort}`);
      expect(pages).toEqual([1]);
      expect((page.rows as Json[]).length).toBe(ROW_LIMIT);
      expect(page.ties).toBeUndefined();
    }
    const explicit = await completed(rows, "tab=all&sort=evidence");
    expect(explicit.page.ties).toEqual({ extra: 10, capped: false });
  });

  it("keeps the other query parameters on the next page request", async () => {
    const rows = pickList([[80, "20"]]);
    const { pick, pages } = await completed(
      rows,
      "tab=all&platform=kalos&off=1&page=1",
    );
    expect(pages).toEqual([1, 2]);
    const next = pick[1];
    expect([next?.tab, next?.platform, next?.withOff]).toEqual([
      "all",
      "kalos",
      true,
    ]);
  });

  it("uses the rows a bigger page already holds before fetching", async () => {
    const rows = pickList([
      [70, "20"],
      [60, "19"],
    ]);
    const inPage = await completed(rows, "size=100");
    expect(inPage.pages).toEqual([1]);
    expect(inPage.page.ties).toEqual({ extra: 20, capped: false });
    const beyond = await completed(
      pickList([
        [120, "20"],
        [60, "19"],
      ]),
      "size=100",
    );
    expect(beyond.pages).toEqual([1, 2]);
    expect(beyond.pick.map((r) => r.size)).toEqual([100, 100]);
    expect(beyond.page.ties).toEqual({ extra: 70, capped: false });
  });

  it("theater ranks page on with rankRows, the same meta and page + 1", async () => {
    const rows = dailyList([
      [48, 1],
      [5, 2],
      [7, 3],
    ]);
    const { page, rank, metas, keys } = await completed(rows, "tab=rank&rk=kd");
    expect(metas).toHaveLength(1);
    expect(rank.map(([req]) => [req.rank, req.page])).toEqual([
      ["kd", 1],
      ["kd", 2],
    ]);
    for (const [, meta] of rank) expect(meta).toBe(metas[0]);
    expect(page.ties).toEqual({ extra: 3, capped: false });
    expect(keys).toHaveLength(53);
  });

  it("leaves rs ranks and posted lists on plain trimming", async () => {
    const rows = pickList([[80, "20"]]);
    const paged = pagedLoaders(rows);
    const loaders: BoardLoaders<PickRequest, Meta> = {
      ...paged.loaders,
      rsRank: async (req) => ({ kind: "rows", ...paginate(rows, req) }),
      postedList: async (req) => paginate(rows, req),
    };
    const rr = (await runCase(loaders, {
      id: "rank.rs_rr",
      query: "tab=rank&rk=rs_rr",
    })) as Record<string, Record<string, Json>>;
    expect((rr.result?.rows as Json[]).length).toBe(ROW_LIMIT);
    expect(rr.result?.ties).toBeUndefined();
    const posted = (await runCase(loaders, {
      id: "posted",
      query: "tab=posted",
    })) as Record<string, Record<string, Json>>;
    expect((posted.list?.rows as Json[]).length).toBe(ROW_LIMIT);
    expect(posted.list?.ties).toBeUndefined();
  });
});

describe("tieStats", () => {
  it("counts full cases, the cases that got rows, the rows and the capped groups", () => {
    const page = (ties: Json) => ({ page: { rows: [], ties } });
    expect(
      tieStats([
        page({ extra: 3, capped: false }),
        page({ extra: 0, capped: false }),
        { meta: {}, ...page({ extra: 100, capped: true }) },
        page({ extra: 0, capped: false }),
        page(null),
        { page: { rows: [] } },
        null,
        { result: { rows: [], ties: { extra: 9, capped: true } } },
      ]),
    ).toEqual({ full: 4, appended: 2, rows: 103, capped: 1 });
    expect(tieStats([])).toEqual({ full: 0, appended: 0, rows: 0, capped: 0 });
  });

  it("tieSummary words them once, for the snapshot and for parity", () => {
    expect(tieSummary({ full: 4, appended: 2, rows: 103, capped: 1 })).toBe(
      `4 个用例取满 ${ROW_LIMIT} 行、查了并列组，其中 2 个补了共 103 行，1 个超过 ${TIE_ROWS_MAX} 行没补全`,
    );
  });
});
