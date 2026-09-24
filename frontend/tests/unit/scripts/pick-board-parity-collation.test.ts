/**
 * The collation allowance of scripts/pick-board-parity.ts (P4-3): a swapped
 * pair is forgiven only when both sides tie on the primary sort keys and each
 * side ordered it by its own collation. Synthetic values only.
 */
import { describe, expect, it } from "@rstest/core";

import { differentCollations } from "../../../scripts/pick-board-parity";
import {
  codePointCompare,
  collator,
} from "../../../scripts/pick-board-parity-collation";
import {
  compareCase,
  type CompareContext,
} from "../../../scripts/pick-board-parity-compare";
import {
  STRIPPED,
  type Json,
} from "../../../scripts/pick-board-snapshot-core.rs";

import {
  PLAIN,
  failures,
  list,
  pickRow,
  rules,
} from "./pick-board-parity-fixtures";

describe("compareCase: collation (only what the two collations explain)", () => {
  // RealShort on C (byte order), the mirror on en_US: "Beta" < "alpha" in C,
  // "alpha" < "Beta" in en_US.
  const C_EN: CompareContext = {
    scrub: {},
    collations: { rs: "C", mirror: "en_US.UTF-8" },
  };
  const EN_C: CompareContext = {
    scrub: {},
    collations: { rs: "en_US.UTF-8", mirror: "C" },
  };
  const rs = (row: Record<string, Json>) =>
    "rowKey" in row ? { ...row, panUrl: STRIPPED, panPw: STRIPPED } : row;
  /** RealShort lists [a, b], the mirror [b, a] */
  const swapped = (
    a: Record<string, Json>,
    b: Record<string, Json>,
    wrap: (rows: Json[]) => Json = list,
  ): [Json, Json] => [wrap([rs(a), rs(b)]), wrap([b, a])];
  const verdict = (pair: [Json, Json], ctx: CompareContext) => {
    const { findings } = compareCase("pick", pair[0], pair[1], ctx);
    return failures(findings).length === 0 &&
      rules(findings).includes("collation")
      ? "forgiven"
      : "failed";
  };

  it("forgives a pair each side ordered by its own collation, and only then", () => {
    const pair = swapped(
      pickRow({ rowKey: "kalos-a", title: "Beta" }),
      pickRow({ rowKey: "kalos-b", title: "alpha" }),
    );
    expect(verdict(pair, C_EN)).toBe("forgiven");
    // Each side's order contradicts its own collation: not a collation effect.
    expect(verdict(pair, EN_C)).toBe("failed");
    expect(verdict(pair, PLAIN)).toBe("failed");
    // One side's order contradicts its collation: the mirror's (both byte
    // order), then RealShort's (both ICU).
    const bytes = { rs: "C", mirror: "C.UTF-8" };
    expect(verdict(pair, { scrub: {}, collations: bytes })).toBe("failed");
    const icu = { rs: "en_US.UTF-8", mirror: "de_DE.UTF-8" };
    expect(verdict(pair, { scrub: {}, collations: icu })).toBe("failed");
  });

  it("fails a swap both collations order the same way (a primary sort error)", () => {
    const pair = swapped(
      pickRow({ rowKey: "kalos-c5", title: "Alpha" }),
      pickRow({ rowKey: "kalos-c6", title: "Beta" }),
    );
    expect(verdict(pair, C_EN)).toBe("failed");
    const sameTitle = swapped(
      pickRow({ rowKey: "kalos-a", title: "x" }),
      pickRow({ rowKey: "kalos-b", title: "x" }),
    );
    expect(verdict(sameTitle, C_EN)).toBe("failed");
  });

  it("fails a swap of rows whose primary sort keys differ", () => {
    const at = (latestEvidenceOn: string, listedOn: string | null = null) => ({
      latestEvidenceOn,
      listedOn,
    });
    const evidence = swapped(
      pickRow({ rowKey: "kalos-a", title: "Beta", ...at("2026-09-20") }),
      pickRow({ rowKey: "kalos-b", title: "alpha", ...at("2026-09-19") }),
    );
    expect(verdict(evidence, C_EN)).toBe("failed");
    const listed = swapped(
      pickRow({ rowKey: "kalos-a", title: "Beta", ...at("d", "2026-09-02") }),
      pickRow({ rowKey: "kalos-b", title: "alpha", ...at("d", "2026-09-01") }),
    );
    expect(verdict(listed, C_EN)).toBe("failed");
    const tied = swapped(
      pickRow({ rowKey: "kalos-a", title: "Beta", ...at("d", "2026-09-02") }),
      pickRow({ rowKey: "kalos-b", title: "alpha", ...at("d", "2026-09-02") }),
    );
    expect(verdict(tied, C_EN)).toBe("forgiven");
  });

  it("rank rows: the rank's own primary keys, then title and row key", () => {
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
    const row = (key: string, title: string, over: Record<string, Json>) =>
      pickRow({ rowKey: key, title, dayNote: "", dayRank: null, ...over });
    const ranked = (rows: Json[]): Json => ({ meta: {}, page: { rows } });
    const pair = (a: Record<string, Json>, b: Record<string, Json>) =>
      swapped(row("kalos-a", "Beta", a), row("kalos-b", "alpha", b), ranked);
    const daily = (dayRank: number) => ({ signal: signal("kd"), dayRank });
    expect(verdict(pair(daily(3), daily(3)), C_EN)).toBe("forgiven");
    expect(verdict(pair(daily(3), daily(4)), C_EN)).toBe("failed");
    const weekly = (weeks: number) => ({
      signal: signal("kw", { payload: { weeks } }),
    });
    expect(verdict(pair(weekly(5), weekly(5)), C_EN)).toBe("forgiven");
    expect(verdict(pair(weekly(5), weekly(4)), C_EN)).toBe("failed");
    const graded = (grade: string) => ({ signal: signal("sm", { grade }) });
    expect(verdict(pair(graded("S"), graded("S")), C_EN)).toBe("forgiven");
    expect(verdict(pair(graded("S"), graded("A")), C_EN)).toBe("failed");
    const listed = (evidenceOn: string) => ({
      signal: signal("fh", { evidenceOn }),
    });
    expect(
      verdict(pair(listed("2026-09-01"), listed("2026-09-01")), C_EN),
    ).toBe("forgiven");
    expect(
      verdict(pair(listed("2026-09-02"), listed("2026-09-01")), C_EN),
    ).toBe("failed");
  });

  it("language counts: only between languages with the same count", () => {
    const langs = (rows: Json[]): Json => ({
      page: { rows: [] },
      facets: { langs: rows },
    });
    // "EN" < "de" in C, "de" < "EN" in en_US
    const tied = swapped({ lang: "EN", n: 2 }, { lang: "de", n: 2 }, langs);
    expect(verdict(tied, C_EN)).toBe("forgiven");
    const counted = swapped({ lang: "EN", n: 3 }, { lang: "de", n: 2 }, langs);
    expect(verdict(counted, C_EN)).toBe("failed");
  });
});

describe("collation names", () => {
  it("compares only when both sides are known and differ", () => {
    expect(differentCollations("C", "C")).toBeNull();
    expect(differentCollations("C", null)).toBeNull();
    expect(differentCollations(null, "en_US.UTF-8")).toBeNull();
    expect(differentCollations("C.UTF-8", "en_US.UTF-8")).toEqual({
      rs: "C.UTF-8",
      mirror: "en_US.UTF-8",
    });
  });

  it("models C-like collations as code point order and the rest as ICU", () => {
    for (const name of ["C", "C.UTF-8", "POSIX", "ucs_basic"])
      expect(collator(name)("Beta", "alpha")).toBeLessThan(0);
    expect(collator("en_US.UTF-8")("Beta", "alpha")).toBeGreaterThan(0);
    // Code points, not UTF-16 units: U+FFFF sorts before U+1F600 in UTF-8.
    expect(codePointCompare("\uffff", "\u{1f600}")).toBeLessThan(0);
    // ICU calls canonically equivalent strings equal; PG then compares bytes.
    expect(collator("en_US.UTF-8")("e\u0301", "\u00e9")).not.toBe(0);
    expect(collator("not a locale!")("b", "a")).toBeGreaterThan(0);
  });
});
