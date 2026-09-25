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

  it("names the first swapped pair the collations cannot explain, by row key only", () => {
    // RealShort (C) [Beta, alpha, cat, dog, egg, fig], the mirror (en_US)
    // [alpha, Beta, dog, cat, fig, egg]: the first swap is the collations',
    // the other two are not; the reason names the first of those.
    const row = (rowKey: string, title: string) => pickRow({ rowKey, title });
    const [b, a, c, d, e, f] = [
      row("kalos-B", "Beta"),
      row("kalos-a", "alpha"),
      row("kalos-c", "cat"),
      row("kalos-d", "dog"),
      row("kalos-e", "egg"),
      row("kalos-f", "fig"),
    ] as const;
    const reason = (ctx: CompareContext) => {
      const { findings } = compareCase(
        "pick",
        list([b, a, c, d, e, f].map(rs)),
        list([a, b, d, c, f, e]),
        ctx,
      );
      const failed = failures(findings);
      expect(failed.map((x) => x.path)).toEqual(["page.rows"]);
      expect(failed[0]?.rs).toBeUndefined();
      expect(failed[0]?.mirror).toBeUndefined();
      return failed[0]?.what ?? "";
    };
    const what = reason(C_EN);
    expect(what).toContain("第 1 项 RealShort 是 kalos-B，镜像是 kalos-a");
    expect(what).toContain("RealShort 把 kalos-c 排在 kalos-d 前面");
    expect(what).not.toContain("kalos-e 排在");
    expect(what).not.toMatch(/Beta|alpha|cat|dog|egg|fig/);
    // Collations unknown or the same: no pair is explained, the first is named.
    expect(reason(PLAIN)).toContain("RealShort 把 kalos-B 排在 kalos-a 前面");
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
    // ORDER BY array_position(GRADES, grade) NULLS LAST: every grade outside
    // GRADES sorts as NULL, so an empty and an unknown grade tie.
    expect(verdict(pair(graded(""), graded("X")), C_EN)).toBe("forgiven");
    expect(verdict(pair(graded("S"), graded("")), C_EN)).toBe("failed");
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

  it("accounts: group, then name, then id, each by its collation", () => {
    const account = (id: string, grp: string, name: string) => ({
      id,
      name,
      url: "",
      grp,
      form: "",
      niche: "",
      status: "",
      fans: null,
      asOf: null,
    });
    const globals = (rows: Json[]): Json => ({ accounts: rows });
    const byGroup = swapped(
      account("a1", "Beta", "x"),
      account("a2", "alpha", "x"),
      globals,
    );
    expect(verdict(byGroup, C_EN)).toBe("forgiven");
    expect(verdict(byGroup, EN_C)).toBe("failed");
    const byName = swapped(
      account("a1", "g", "Beta"),
      account("a2", "g", "alpha"),
      globals,
    );
    expect(verdict(byName, C_EN)).toBe("forgiven");
    // Both collations put "alpha" first: the swap is a sort error.
    const plain = swapped(
      account("a1", "g", "alpha"),
      account("a2", "g", "beta"),
      globals,
    );
    expect(verdict(plain, C_EN)).toBe("failed");
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
