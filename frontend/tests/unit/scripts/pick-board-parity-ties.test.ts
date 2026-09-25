/**
 * parity after tie completion (snapshot format 3): format 2 snapshots are
 * refused, the report counts tie-completed cases, a boundary tie group too
 * long to complete is named in the failure reason, and an end-to-end run of
 * both sides over one dataset ordered by two collations. Synthetic values
 * only; nothing here opens a database.
 */
import { describe, expect, it } from "@rstest/core";

import {
  compareSnapshot,
  formatReport,
  readSnapshot,
} from "../../../scripts/pick-board-parity";
import {
  compareCase,
  type CompareContext,
} from "../../../scripts/pick-board-parity-compare";
import {
  ROW_LIMIT,
  SNAPSHOT_FORMAT,
  TIE_ROWS_MAX,
  runCase,
  type Json,
} from "../../../scripts/pick-board-snapshot-core.rs";

import {
  C_ORDER,
  EN_US_ORDER,
  PLAIN,
  failures,
  listLoaders,
  pickRow,
  rsRow,
  rules,
} from "./pick-board-parity-fixtures";

describe("snapshot format 3", () => {
  const doc = {
    format: SNAPSHOT_FORMAT,
    asOf: "2026-09-23T03:40:00.000Z",
    fingerprint: "c".repeat(64),
    sourceRevision: "0123456789abcdef0123456789abcdef01234567",
    collation: "C.UTF-8",
    rowLimit: 50,
    startedAt: "2026-09-23T04:00:00.000Z",
    finishedAt: "2026-09-23T04:02:00.000Z",
    elapsedMs: 120000,
    cases: [{ id: "pick", query: "", ms: 12, result: { page: { rows: [] } } }],
  };

  it("refuses a snapshot taken by older scripts and says to retake it", () => {
    expect(SNAPSHOT_FORMAT).toBe("pick-board-snapshot/3");
    const old = readSnapshot(
      JSON.stringify({ ...doc, format: "pick-board-snapshot/2" }),
    );
    expect(old.ok).toBe(false);
    if (old.ok) return;
    expect(old.error).toContain("pick-board-snapshot/2");
    expect(old.error).toContain(SNAPSHOT_FORMAT);
    expect(old.error).toContain("重拍");
    const odd = readSnapshot(
      JSON.stringify({ ...doc, format: "x".repeat(500) }),
    );
    expect(odd.ok).toBe(false);
    if (odd.ok) return;
    expect(odd.error).not.toContain("x".repeat(50));
  });
});

describe("the report's tie counts", () => {
  it("counts full cases, those that got rows and the capped ones, never their rows", () => {
    const tied = (ties: Json): Json => ({
      page: { rows: [], total: 0, hasMore: false, ties },
      facets: {},
    });
    const done = tied({ extra: 3, capped: false });
    const none = tied({ extra: 0, capped: false });
    const capped = tied({ extra: TIE_ROWS_MAX, capped: true });
    const report = compareSnapshot({
      rsCases: [
        { id: "pick", result: done },
        { id: "pick.off", result: none },
        { id: "all", result: capped },
        { id: "posted", result: { list: { rows: [] } } },
      ],
      mirror: new Map([
        ["pick", { ok: true, result: done }],
        ["pick.off", { ok: true, result: none }],
        ["all", { ok: false, error: "MirrorBusy" }],
        ["posted", { ok: true, result: { list: { rows: [] } } }],
      ]),
      ctx: PLAIN,
    });
    expect(report.ties).toEqual({
      rs: { full: 3, appended: 2, rows: 3 + TIE_ROWS_MAX, capped: 1 },
      mirror: { full: 2, appended: 1, rows: 3, capped: 0 },
    });
    const text = formatReport(report).join("\n");
    expect(text).toContain(`第 ${ROW_LIMIT} 行所在的并列组`);
    expect(text).toContain(
      `RealShort 3 个用例取满 ${ROW_LIMIT} 行、查了并列组，其中 2 个补了共 ${3 + TIE_ROWS_MAX} 行，1 个超过`,
    );
    expect(text).toContain(
      `镜像 2 个用例取满 ${ROW_LIMIT} 行、查了并列组，其中 1 个补了共 3 行，0 个超过`,
    );
    expect(text).not.toContain("快照只含每个用例的前 50 行）");
  });
});

describe("compareCase: a boundary tie group that was not completed", () => {
  const page = (rows: Json[], ties: Json): Json => ({
    page: { rows, total: 200, hasMore: true, ties },
    facets: {},
  });
  const CAPPED = { extra: TIE_ROWS_MAX, capped: true };
  const DONE = { extra: 2, capped: false };
  const whats = (rs: Json, mirror: Json) =>
    failures(compareCase("pick", rs, mirror, PLAIN).findings).map(
      (f) => `${f.path} ${f.what}`,
    );

  it("names the cap only on rows of row 50's group, whichever side capped it", () => {
    const two = (n: number) => String(n).padStart(2, "0");
    const row = (rowKey: string, day: string) => ({
      rowKey,
      title: rowKey,
      latestEvidenceOn: `2026-09-${day}`,
      listedOn: null,
    });
    const top = Array.from({ length: 45 }, (_, i) => `kalos-top${two(i)}`);
    const group = (from: number) =>
      Array.from({ length: 5 }, (_, i) => `kalos-g${two(from + i)}`);
    // RealShort: 45 rows above the group, then g00..g09; row 50 is g04.
    const rs = [
      ...top.map((k) => rsRow(row(k, "21"))),
      ...[...group(0), ...group(5)].map((k) => rsRow(row(k, "20"))),
    ];
    // The mirror misses kalos-top07, above the group: a real difference.
    // Inside the group it holds g10..g14 where RealShort has g05..g09; its
    // row 50 is in the group too.
    const mirror = [
      ...top
        .filter((k) => k !== "kalos-top07")
        .map((k) => pickRow(row(k, "21"))),
      ...[...group(0), ...group(10)].map((k) => pickRow(row(k, "20"))),
    ];
    const cap = `超过 ${TIE_ROWS_MAX} 行`;
    for (const [a, b] of [
      [CAPPED, CAPPED],
      [DONE, CAPPED],
      [CAPPED, DONE],
    ] as const) {
      const found = whats(page(rs, a), page(mirror, b));
      const rows = found.filter((w) => w.startsWith("page.rows["));
      expect(rows).toHaveLength(11);
      expect(rows).toContain("page.rows[kalos-top07] 镜像少了这一项");
      const tied = rows.filter((w) => w.startsWith("page.rows[kalos-g"));
      expect(tied).toHaveLength(10);
      for (const w of tied) {
        expect(w).toContain(cap);
        expect(w).toContain("运行手册");
      }
    }
    // Each side against its own row 50: here the mirror's row 50 is in a
    // group of another day, and its rows still get the note.
    const other = [
      ...top.map((k) => pickRow(row(k, "21"))),
      ...[...group(20), ...group(25)].map((k) => pickRow(row(k, "19"))),
    ];
    const split = whats(page(rs, CAPPED), page(other, CAPPED));
    expect(split.filter((w) => w.includes(cap))).toHaveLength(20);
    const plain = whats(page(rs, DONE), page(mirror, DONE));
    expect(plain).toHaveLength(11);
    expect(plain.filter((w) => w.includes(cap))).toEqual([]);
    expect(plain).toContain("page.rows[kalos-g05] 镜像少了这一项");
    expect(plain).toContain("page.rows[kalos-g10] 镜像多了这一项");
  });

  it("takes each side's boundary key from its own row 50 exactly", () => {
    const two = (n: number) => String(n).padStart(3, "0");
    const row = (rowKey: string, day: string) => ({
      rowKey,
      title: rowKey,
      latestEvidenceOn: `2026-09-${day}`,
      listedOn: null,
    });
    const top = Array.from({ length: 49 }, (_, i) => `kalos-top${two(i)}`);
    // RealShort: row 50 opens a group that ends there (row 49 and row 51
    // are on other keys, there is no row 51).
    const rs = [
      ...top.map((k) => rsRow(row(k, "21"))),
      rsRow(row("kalos-g000", "20")),
    ];
    // The mirror misses kalos-top007 and kalos-g000; its own group from
    // row 49 on runs past the cap.
    const group = Array.from({ length: 102 }, (_, i) => `kalos-g${two(i + 1)}`);
    const mirror = [
      ...top
        .filter((k) => k !== "kalos-top007")
        .map((k) => pickRow(row(k, "21"))),
      ...group.map((k) => pickRow(row(k, "20"))),
    ];
    const noted = (found: readonly string[]) =>
      found
        .filter((w) => w.includes(`超过 ${TIE_ROWS_MAX} 行`))
        .map((w) => w.split(" ")[0]);
    const found = whats(
      page(rs, { extra: 0, capped: false }),
      page(mirror, CAPPED),
    );
    expect(found).toContain("page.rows[kalos-top007] 镜像少了这一项");
    expect(noted(found)).toEqual([
      "page.rows[kalos-g000]",
      ...group.map((k) => `page.rows[${k}]`),
    ]);
    // A side that did not fill 50 rows has no row 50 and no boundary.
    const short = whats(page(rs.slice(0, 30), null), page(mirror, CAPPED));
    expect(short).toContain("page.rows[kalos-top007] 镜像少了这一项");
    expect(noted(short)).toHaveLength(group.length);
  });

  it("keeps the plain reason for lists nested inside the rows", () => {
    const signal = (ord: number): Json => ({ kind: "kd", ord, payload: {} });
    const found = whats(
      page([rsRow({ signals: [signal(0), signal(1)] })], CAPPED),
      page([pickRow({ signals: [signal(0)] })], CAPPED),
    );
    expect(found).toEqual(["page.rows[kalos-a].signals[kd#1] 镜像少了这一项"]);
  });
});

describe("tie completion end to end: row 50's tie group under two collations", () => {
  // RealShort's database is C.UTF-8, the mirror's en_US.UTF-8: "Beta" sorts
  // before "alpha" on RealShort and after it on the mirror.
  const CTX: CompareContext = {
    scrub: {},
    collations: { rs: "C.UTF-8", mirror: "en_US.UTF-8" },
  };
  const two = (n: number) => String(n).padStart(2, "0");
  const on = (day: string, n: number, key: (i: number) => [string, string]) =>
    Array.from({ length: n }, (_, i) => {
      const [rowKey, title] = key(i);
      return pickRow({
        rowKey,
        title,
        latestEvidenceOn: `2026-09-${day}`,
        listedOn: null,
      });
    });
  /** 35 rows above the group, the group (half "Beta", half "alpha"), 20 below */
  const dataset = (tied: number) => [
    ...on("21", 35, (i) => [`kalos-top${two(i)}`, `Top ${two(i)}`]),
    ...on("20", tied / 2, (i) => [`kalos-b${i}`, `Beta ${two(i)}`]),
    ...on("20", tied / 2, (i) => [`kalos-a${i}`, `alpha ${two(i)}`]),
    ...on("19", 20, (i) => [`kalos-low${two(i)}`, `Low ${two(i)}`]),
  ];
  const kase = { id: "pick", query: "" };
  const run = async (
    rsRows: readonly Record<string, Json>[],
    mirrorRows: readonly Record<string, Json>[],
  ) => {
    const rs = await runCase(listLoaders(rsRows, C_ORDER), kase);
    const mirror = await runCase(listLoaders(mirrorRows, EN_US_ORDER), kase);
    return {
      rs,
      mirror,
      findings: compareCase("pick", rs, mirror, CTX).findings,
    };
  };
  /** The same result cut at ROW_LIMIT without the tie group, as before /3 */
  const cut = (result: Json): Json => {
    const { page, facets } = result as {
      page: Record<string, Json>;
      facets: Json;
    };
    const rest = Object.entries(page).filter(([key]) => key !== "ties");
    const rows = (page.rows as Json[]).slice(0, ROW_LIMIT);
    return { page: { ...Object.fromEntries(rest), rows }, facets };
  };
  const tiesOf = (result: Json) =>
    (result as Partial<Record<string, Record<string, Json>>>).page?.ties;

  it("keeps the same rows on both sides; only the collation order differs", async () => {
    const rows = dataset(30);
    const { rs, mirror, findings } = await run(rows, rows);
    // Cut at 50 rows, the two sides keep different rows of the group.
    const before = failures(
      compareCase("pick", cut(rs), cut(mirror), CTX).findings,
    );
    expect(before).toHaveLength(30);
    expect(tiesOf(rs)).toEqual({ extra: 15, capped: false });
    expect(tiesOf(mirror)).toEqual({ extra: 15, capped: false });
    expect(failures(findings)).toEqual([]);
    expect(rules(findings)).toEqual(["collation"]);
  });

  it("compares every row of the group field by field", async () => {
    const rows = dataset(30);
    // alpha 03 is past row 50 on RealShort: without completion it was only
    // "extra on the mirror", its fields never compared.
    const changed = rows.map((r) =>
      r.rowKey === "kalos-a3" ? { ...r, clicks: 7 } : r,
    );
    const { findings } = await run(rows, changed);
    expect(failures(findings).map((f) => f.path)).toEqual([
      "page.rows[kalos-a3].clicks",
    ]);
  });

  it("still fails a row that left the group on the mirror", async () => {
    const rows = dataset(30);
    const moved = rows.map((r) =>
      r.rowKey === "kalos-a3" ? { ...r, latestEvidenceOn: "2026-09-19" } : r,
    );
    const { findings } = await run(rows, moved);
    const paths = failures(findings).map((f) => f.path);
    expect(paths).toContain("page.rows[kalos-a3]");
  });

  it("a group longer than TIE_ROWS_MAX stays a failure that names the cap", async () => {
    const rows = dataset(200);
    const { rs, findings } = await run(rows, rows);
    expect(tiesOf(rs)).toEqual({ extra: TIE_ROWS_MAX, capped: true });
    const failed = failures(findings);
    expect(failed.length).toBeGreaterThan(0);
    for (const f of failed) expect(f.what).toContain(`超过 ${TIE_ROWS_MAX} 行`);
  });
});
