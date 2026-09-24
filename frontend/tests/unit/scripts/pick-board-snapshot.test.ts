/**
 * scripts/pick-board-snapshot.rs.ts is copied into a RealShort checkout and run
 * there (P4-3, critique A5); here only its pure parts are tested: arguments,
 * the case list, JSON normalization, what is stripped before anything is
 * written, the case dispatcher over fake loaders, and the guards. Nothing here
 * talks to RealShort or to a database.
 */
import { describe, expect, it } from "@rstest/core";

import {
  GRADES as CORE_GRADES,
  PLATFORMS as CORE_PLATFORMS,
  POSTED_FILTERS as CORE_POSTED_FILTERS,
  POSTED_STATES as CORE_POSTED_STATES,
  RS_BASES as CORE_RS_BASES,
  RS_RANKS as CORE_RS_RANKS,
  THEATER_BASES as CORE_THEATER_BASES,
  isDailyRank,
  isRsRank,
  parsePickRequest,
  reelshortId,
} from "@/core/pick-board/request";

import {
  GLOBALS_CASE_ID,
  GRADES,
  PLATFORMS,
  POSTED_FILTERS,
  POSTED_STATES,
  ROW_LIMIT,
  RS_BASES,
  RS_RANKS,
  STRIPPED,
  THEATER_BASES,
  deriveCases,
  parseSnapshotArgs,
  runCase,
  selectCases,
  staticCases,
  stripSensitive,
  toJson,
  type BoardLoaders,
  type Json,
} from "../../../scripts/pick-board-snapshot-core.rs";
import {
  busyWindow,
  checkHead,
  checkSourceProblem,
} from "../../../scripts/pick-board-snapshot.rs";

const FP = "a".repeat(64);
const AS_OF = "2026-09-23T03:40:00.000Z";

describe("parseSnapshotArgs", () => {
  const base = ["--as-of", AS_OF, "--fp", FP, "--out", "/tmp/snap.json"];

  it("reads the three required flags", () => {
    const parsed = parseSnapshotArgs(base);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.args).toEqual({
      asOf: AS_OF,
      fp: FP,
      out: "/tmp/snap.json",
      only: null,
      ignoreWindow: false,
    });
  });

  it("takes --only as a regular expression and --ignore-window as a switch", () => {
    const parsed = parseSnapshotArgs([
      ...base,
      "--only",
      "^rank\\.",
      "--ignore-window",
    ]);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.args.only?.test("rank.kd")).toBe(true);
    expect(parsed.args.only?.test("pick")).toBe(false);
    expect(parsed.args.ignoreWindow).toBe(true);
  });

  it.each([
    [["--fp", FP, "--out", "x"], "--as-of"],
    [["--as-of", AS_OF, "--out", "x"], "--fp"],
    [["--as-of", AS_OF, "--fp", FP], "--out"],
    [["--as-of", "2026-09-23", "--fp", FP, "--out", "x"], "--as-of"],
    [["--as-of", "2026-09-23T03:40:00Z", "--fp", FP, "--out", "x"], "--as-of"],
    [["--as-of", AS_OF, "--fp", "A".repeat(64), "--out", "x"], "--fp"],
    [["--as-of", AS_OF, "--fp", "a".repeat(63), "--out", "x"], "--fp"],
    [
      [...["--as-of", AS_OF, "--fp", FP, "--out", "x"], "--only", "("],
      "--only",
    ],
    [[...["--as-of", AS_OF, "--fp", FP, "--out", "x"], "--what"], "--what"],
    [["--as-of"], "--as-of"],
  ])("refuses %j (names %s)", (argv, flag) => {
    const parsed = parseSnapshotArgs(argv);
    expect(parsed.ok).toBe(false);
    if (parsed.ok) return;
    expect(parsed.error).toContain(flag);
  });

  it("never echoes the fingerprint or other values back in an error", () => {
    const parsed = parseSnapshotArgs(["--as-of", AS_OF, "--fp", "secret"]);
    expect(parsed.ok).toBe(false);
    if (parsed.ok) return;
    expect(parsed.error).not.toContain("secret");
  });
});

describe("the constants copied into the self-contained script", () => {
  it("equal the ported request module (itself RealShort's request.ts)", () => {
    expect(PLATFORMS).toEqual(CORE_PLATFORMS);
    expect(THEATER_BASES).toEqual(CORE_THEATER_BASES);
    expect(RS_BASES).toEqual(CORE_RS_BASES);
    expect(RS_RANKS).toEqual(CORE_RS_RANKS);
    expect(GRADES).toEqual(CORE_GRADES);
    expect(POSTED_FILTERS).toEqual(CORE_POSTED_FILTERS.filter((f) => f !== ""));
    expect(POSTED_STATES).toEqual(CORE_POSTED_STATES.filter((s) => s !== ""));
  });
});

describe("staticCases", () => {
  const cases = staticCases();
  const byId = new Map(cases.map((c) => [c.id, c.query]));
  const parse = (id: string) =>
    parsePickRequest(new URLSearchParams(byId.get(id) ?? "?missing"));

  it("has unique ids, globals first, and canonically encoded URL queries", () => {
    expect(new Set(cases.map((c) => c.id)).size).toBe(cases.length);
    expect(cases[0]).toEqual({ id: GLOBALS_CASE_ID, query: "" });
    for (const kase of cases.slice(1)) {
      expect(new URLSearchParams(kase.query).toString()).toBe(kase.query);
      expect(parsePickRequest(new URLSearchParams(kase.query)).tab).not.toBe(
        "rules",
      );
    }
  });

  it("covers both list defaults, every theater, basis and filter (plan P4-3)", () => {
    expect(parse("pick").tab).toBe("pick");
    expect(parse("all").tab).toBe("all");
    for (const p of CORE_PLATFORMS)
      expect(parse(`pick.platform.${p}`).platform).toBe(p);
    for (const b of [...CORE_THEATER_BASES, ...CORE_RS_BASES])
      expect(parse(`pick.basis.${b}`).basis).toBe(b);
    for (const f of ["no", "yes", "pool"] as const)
      expect(parse(`pick.posted.${f}`).posted).toBe(f);
    expect(parse("pick.yt").youtubeOk).toBe(true);
    expect(parse("pick.inuse").inUseOnly).toBe(true);
    expect(parse("pick.dated").datedOnly).toBe(true);
    expect(parse("pick.off").withOff).toBe(true);
  });

  it("covers every rank, the growth sorts and the posted states", () => {
    for (const r of [...CORE_THEATER_BASES, ...CORE_RS_RANKS]) {
      const req = parse(`rank.${r}`);
      expect([req.tab, req.rank]).toEqual(["rank", r]);
    }
    for (const s of ["d1", "dp1", "dp7"] as const) {
      const req = parse(`rank.rs_growth.${s}`);
      expect([req.rank, req.rsSort]).toEqual(["rs_growth", s]);
    }
    expect(parse("posted").tab).toBe("posted");
    for (const s of ["pub", "sched", "none", "nomatch"] as const)
      expect(parse(`posted.${s}`).postedState).toBe(s);
  });
});

function rankResult(meta: Record<string, Json>): Json {
  return { meta, page: { rows: [], total: 0, hasMore: false } };
}

describe("deriveCases", () => {
  const pickRows: Json[] = [
    { rowKey: "kalos-A+b/c==", platform: "kalos" },
    { rowKey: "reelshort-rs0001", platform: "reelshort" },
    { rowKey: "dramabox-x", platform: "dramabox" },
    { rowKey: "reelshort-rs0002", platform: "reelshort" },
    { rowKey: "shortmax-y", platform: "shortmax" },
  ];
  const results = new Map<string, Json>([
    ["pick", { page: { rows: pickRows, total: 5, hasMore: false } }],
    ["rank.kd", rankResult({ days: ["2026-09-22", "2026-09-21"] })],
    ["rank.qc", rankResult({ days: ["2026-09-20"] })],
    ["rank.qr", rankResult({ days: [] })],
    [
      "rank.kw",
      rankResult({
        weeks: [
          { week: "9.14-9.20", start: "2026-09-14" },
          { week: "9.7-9.13", start: "2026-09-07" },
        ],
      }),
    ],
    ["rank.sm", rankResult({ grades: { S: 3, SSS: 1, A: 0, B: 2 } })],
    ["rank.mg", rankResult({ grades: { C: 1 } })],
    [
      "posted",
      {
        list: {
          rows: [{ sd: "SD-000002" }, { sd: "SD-000001" }, { sd: "SD-9" }],
        },
      },
    ],
  ]);
  const derived = deriveCases(results);
  const byId = new Map(derived.map((c) => [c.id, c.query]));
  const parse = (id: string) =>
    parsePickRequest(new URLSearchParams(byId.get(id) ?? "?missing"));

  it("takes the second day and week, and the first two graded tiers", () => {
    expect(parse("rank.kd.day2").day).toBe("2026-09-21");
    expect(byId.has("rank.qc.day2")).toBe(false);
    expect(byId.has("rank.qr.day2")).toBe(false);
    expect(parse("rank.kw.week2").week).toBe("2026-09-07");
    expect([
      parse("rank.sm.grade1").grade,
      parse("rank.sm.grade2").grade,
    ]).toEqual(["SSS", "S"]);
    expect(parse("rank.mg.grade1").grade).toBe("C");
    expect(byId.has("rank.mg.grade2")).toBe(false);
  });

  it("opens two posted records and two evidence pages of each kind", () => {
    expect(parse("posted.record1").sd).toBe("SD-000002");
    expect(parse("posted.record2").sd).toBe("SD-000001");
    expect(byId.has("posted.record3")).toBe(false);
    expect(parse("row.theater1").rowKey).toBe("kalos-A+b/c==");
    expect(parse("row.theater2").rowKey).toBe("dramabox-x");
    expect(reelshortId(parse("row.reelshort1").rowKey)).toBe("rs0001");
    expect(reelshortId(parse("row.reelshort2").rowKey)).toBe("rs0002");
    for (const id of ["row.theater1", "row.reelshort1"])
      expect(parse(id).tab).toBe("row");
  });

  it("is empty when nothing it derives from is there", () => {
    expect(deriveCases(new Map())).toEqual([]);
  });

  it("selectCases keeps globals and what --only matches", () => {
    const all = [...staticCases(), ...derived];
    const picked = selectCases(all, /^rank\.kd/);
    expect(picked.map((c) => c.id)).toEqual([
      GLOBALS_CASE_ID,
      "rank.kd",
      "rank.kd.day2",
    ]);
    expect(selectCases(all, null)).toEqual(all);
  });
});

describe("toJson", () => {
  it("writes dates as ISO, maps as objects, and drops undefined", () => {
    const value = {
      at: new Date("2026-09-23T03:40:00.123Z"),
      links: new Map([["k", { title: "t", gone: undefined }]]),
      list: [1, undefined, "x"],
      big: 2n ** 60n,
      odd: Number.NaN,
      nested: { deeper: [new Date(0)] },
    };
    expect(toJson(value)).toEqual({
      at: "2026-09-23T03:40:00.123Z",
      links: { k: { title: "t" } },
      list: [1, null, "x"],
      big: "1152921504606846976",
      odd: "NaN",
      nested: { deeper: ["1970-01-01T00:00:00.000Z"] },
    });
  });
});

describe("stripSensitive", () => {
  it("keeps only whether a pan link exists, never the link or the code", () => {
    const rows: Json = [
      { rowKey: "a", panUrl: "https://pan.example/s/1", panPw: "ab12" },
      { rowKey: "b", panUrl: "HTTP://pan.example/s/2", panPw: "" },
      { rowKey: "c", panUrl: "", panPw: "cd34" },
      { rowKey: "d", panUrl: "ftp://pan.example/3", panPw: "" },
    ];
    const out = stripSensitive({ rows });
    expect(out).toEqual({
      rows: [
        { rowKey: "a", panUrl: STRIPPED, panPw: STRIPPED, hasPan: true },
        { rowKey: "b", panUrl: STRIPPED, panPw: STRIPPED, hasPan: true },
        { rowKey: "c", panUrl: STRIPPED, panPw: STRIPPED, hasPan: false },
        { rowKey: "d", panUrl: STRIPPED, panPw: STRIPPED, hasPan: false },
      ],
    });
    expect(JSON.stringify(out)).not.toMatch(/pan\.example|ab12|cd34/);
  });

  it("drops every money field and the promotion value wherever it is", () => {
    const input: Json = {
      rs: { id: "1", billUsd: 1.5, billUsd1: 2, billUsd7: 3, billUsd15: 4 },
      result: {
        rows: [{ billDate: "d", revenueUsd: 9.99, promotionValue: "code" }],
        totals: { usd: 12.5, matchedUsd: 3, orders: 2, rows: 2 },
      },
      other: { billUsd30: 7, revenueCents: 100 },
    };
    const out = stripSensitive(input);
    expect(out).toEqual({
      rs: {
        id: "1",
        billUsd: STRIPPED,
        billUsd1: STRIPPED,
        billUsd7: STRIPPED,
        billUsd15: STRIPPED,
      },
      result: {
        rows: [
          { billDate: "d", revenueUsd: STRIPPED, promotionValue: STRIPPED },
        ],
        totals: { usd: STRIPPED, matchedUsd: STRIPPED, orders: 2, rows: 2 },
      },
      other: { billUsd30: STRIPPED, revenueCents: 100 },
    });
    expect(JSON.stringify(input)).toContain("9.99");
  });
});

type FakeReq = ReturnType<typeof parsePickRequest>;

function fakeLoaders(calls: string[]): BoardLoaders<FakeReq, { kind: string }> {
  const rows = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ rowKey: `k${i}` }));
  const log =
    <T>(name: string, value: T) =>
    async () => {
      calls.push(name);
      return value;
    };
  return {
    parse: parsePickRequest,
    isRsRank,
    reelshortId,
    pickRows: log("pickRows", { rows: rows(60), total: 60, hasMore: true }),
    facets: log("facets", { platforms: {} }),
    rankMeta: log("rankMeta", { kind: "meta" }),
    rankRows: log("rankRows", { rows: rows(55), total: 55, hasMore: true }),
    rsRank: async (req) => {
      calls.push(`rsRank:${req.rank}`);
      if (req.rank === "rs_ledger")
        return { kind: "ledger", rows: rows(120), totals: {}, source: null };
      if (req.rank === "rs_growth")
        return { kind: "rows", rows: [], total: null, hasMore: false };
      return { kind: "rows", rows: rows(70), total: 70, hasMore: true };
    },
    growthDiagnosis: log("growthDiagnosis", { reason: "filters_empty" }),
    postedList: log("postedList", { rows: rows(80), total: 80 }),
    postedRecord: async (sd) => {
      calls.push(`postedRecord:${sd}`);
      return null;
    },
    postedStats: log("postedStats", { total: 1 }),
    accounts: log("accounts", []),
    rowDetail: async (rowKey) => {
      calls.push(`rowDetail:${rowKey}`);
      return { row: { rowKey } };
    },
    reelshortDetail: async (id) => {
      calls.push(`reelshortDetail:${id}`);
      return { row: { id }, at: new Date(0) };
    },
    freshness: log("freshness", { rows: 1 }),
    sources: log("sources", {}),
  };
}

describe("runCase", () => {
  const run = async (query: string, id = "case") => {
    const calls: string[] = [];
    const result = await runCase(fakeLoaders(calls), { id, query });
    return { calls, result: result as Record<string, Json> };
  };
  const length = (value: Json | undefined) =>
    Array.isArray(value) ? value.length : -1;

  it("lists: the page (first ROW_LIMIT rows) and the facets", async () => {
    const { calls, result } = await run("tab=all");
    expect(calls.sort()).toEqual(["facets", "pickRows"]);
    const page = result.page as Record<string, Json>;
    expect(length(page.rows)).toBe(ROW_LIMIT);
    expect(page.total).toBe(60);
    expect(result.facets).toEqual({ platforms: {} });
  });

  it("theater ranks: meta, then the rows with that meta", async () => {
    const { calls, result } = await run("tab=rank&rk=kd");
    expect(calls).toEqual(["rankMeta", "rankRows"]);
    expect(result.meta).toEqual({ kind: "meta" });
    expect(length((result.page as Record<string, Json>).rows)).toBe(ROW_LIMIT);
  });

  it("rs ranks: rows trimmed, the ledger whole, growth diagnosed only when empty", async () => {
    const rr = await run("tab=rank&rk=rs_rr");
    expect(rr.calls).toEqual(["rankMeta", "rsRank:rs_rr"]);
    expect(length((rr.result.result as Record<string, Json>).rows)).toBe(
      ROW_LIMIT,
    );
    expect(rr.result.diagnosis).toBeUndefined();
    const ledger = await run("tab=rank&rk=rs_ledger");
    expect(length((ledger.result.result as Record<string, Json>).rows)).toBe(
      120,
    );
    const growth = await run("tab=rank&rk=rs_growth&rs=d1");
    expect(growth.calls).toEqual([
      "rankMeta",
      "rsRank:rs_growth",
      "growthDiagnosis",
    ]);
    expect(growth.result.diagnosis).toEqual({ reason: "filters_empty" });
  });

  it("posted: the list page, or one record by sd", async () => {
    const list = await run("tab=posted&pst=pub");
    expect(list.calls).toEqual(["postedList"]);
    expect(length((list.result.list as Record<string, Json>).rows)).toBe(
      ROW_LIMIT,
    );
    const one = await run("tab=posted&sd=SD-000001");
    expect(one.calls).toEqual(["postedRecord:SD-000001"]);
    expect(one.result).toBeNull();
  });

  it("evidence pages: ReelShort rows by book id, theater rows by row key", async () => {
    const rs = await run("tab=row&row=reelshort-rs0001");
    expect(rs.calls).toEqual(["reelshortDetail:rs0001"]);
    expect(rs.result).toEqual({
      row: { id: "rs0001" },
      at: "1970-01-01T00:00:00.000Z",
    });
    const theater = await run("tab=row&row=kalos-abc");
    expect(theater.calls).toEqual(["rowDetail:kalos-abc"]);
  });

  it("globals: freshness, sources, posted stats and accounts", async () => {
    const { calls, result } = await run("", GLOBALS_CASE_ID);
    expect(calls.sort()).toEqual([
      "accounts",
      "freshness",
      "postedStats",
      "sources",
    ]);
    expect(Object.keys(result).sort()).toEqual([
      "accounts",
      "freshness",
      "postedStats",
      "sources",
    ]);
  });

  it("refuses a tab without data", async () => {
    await expect(run("tab=rules")).rejects.toThrow(/rules/);
  });
});

describe("guards", () => {
  it.each([
    ["2026-09-25T05:52:00Z", "sync"],
    ["2026-09-25T06:30:00Z", "sync"],
    ["2026-09-25T23:55:00Z", "sync"],
    ["2026-09-25T05:20:00Z", "GSC"],
    ["2026-09-25T03:45:00Z", "镜像"],
    ["2026-09-25T15:35:00Z", "镜像"],
  ])("busyWindow(%s) names the %s run", (at, name) => {
    expect(busyWindow(new Date(at))).toContain(name);
  });

  it.each([
    "2026-09-25T07:00:00Z",
    "2026-09-25T10:00:00Z",
    "2026-09-25T20:30:00Z",
  ])("busyWindow(%s) is clear", (at) => {
    expect(busyWindow(new Date(at))).toBeNull();
  });

  it("checkSourceProblem passes only the same fingerprint with no problem", () => {
    expect(
      checkSourceProblem({ fingerprint: FP, problem: null }, FP),
    ).toBeNull();
    expect(
      checkSourceProblem(
        { fingerprint: "b".repeat(64), problem: { status: 409 } },
        FP,
      ),
    ).toContain("409");
    expect(
      checkSourceProblem(
        { fingerprint: FP, problem: { status: 503, error: "source_busy" } },
        FP,
      ),
    ).toContain("503");
    expect(
      checkSourceProblem({ fingerprint: "b".repeat(64), problem: null }, FP),
    ).not.toBeNull();
    expect(checkSourceProblem("nonsense", FP)).not.toBeNull();
  });

  it("checkHead wants the checkout at VERCEL_GIT_COMMIT_SHA when it is set", () => {
    const sha = "0123456789abcdef0123456789abcdef01234567";
    expect(checkHead(`${sha}\n`, sha)).toBeNull();
    expect(checkHead(sha, "f".repeat(40))).toContain("HEAD");
    // Unset only matches a version exported with no revision (sourceRevision
    // null); the fingerprint check decides, the script prints a notice.
    expect(checkHead(sha, undefined)).toBeNull();
    expect(checkHead(sha, " ")).toBeNull();
  });
});

it("isDailyRank agrees with the daily ranks the script derives second days for", () => {
  expect(["kd", "qc", "qr"].every(isDailyRank)).toBe(true);
});
