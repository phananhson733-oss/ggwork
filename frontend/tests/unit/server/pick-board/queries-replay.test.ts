/**
 * The replay's two row reads (P4-2, not in RealShort) against a fake reader:
 * what they send and how they order what comes back. The real SQL runs in
 * replay.integration.test.ts against board_fixture's database.
 */
import { beforeEach, describe, expect, it, rs } from "@rstest/core";

import { buildBoardRules } from "@/core/pick-board/rules";
import type * as DbModule from "@/server/pick-board/db";
import type { ScopeHolder } from "@/server/pick-board/db";
import { loadMissingKeys, loadRowsByKeys } from "@/server/pick-board/queries";

import rulesFixture from "../../core/pick-board/fixtures/rules.json";

type Statement = { text: string; values: unknown[] };

const fake = rs.hoisted(() => ({
  statements: [] as Statement[],
  respond: (_statement: Statement): unknown[] => [],
  holder: { scope: null } as ScopeHolder,
}));

rs.mock("@/server/pick-board/db", () => {
  const actual = rs.requireActual<typeof DbModule>("@/server/pick-board/db");
  const client = {
    query: async (statement: string | Statement) => {
      if (typeof statement === "string") return { rows: [] };
      fake.statements.push(statement);
      return { rows: fake.respond(statement) };
    },
    release: () => undefined,
  };
  const scope = actual.makeScope(
    () => fake.holder,
    () => ({ connect: async () => client }),
  );
  return { ...actual, ...scope };
});

/** A union row as `SELECT rows.*` returns it, plus the ordinality column. */
function unionRaw(rowKey: string, ord: number, platform = "kalos") {
  return {
    row_key: rowKey,
    platform,
    source_table: "KalosTV 剧单",
    title: `${rowKey} 的剧名`,
    title_cn: "",
    lang: "英语",
    kind: "",
    origin: "",
    tags: "",
    listed_on: null,
    has_pan: false,
    episodes: null,
    pay_start: null,
    youtube: false,
    merged_rows: 1,
    off_on: null,
    reoff_note: "",
    in_site_ids: null,
    legacy_only: false,
    site_other: false,
    has_signal: true,
    latest_evidence_on: null,
    rs_clk: false,
    rs_bill: false,
    rs_gsc: false,
    rs_clk_on: null,
    rs_bill_on: null,
    rs_gsc_on: null,
    drama_id: null,
    title_key: "",
    ord: String(ord),
  };
}

const sent = (pattern: RegExp) =>
  fake.statements.filter((s) => pattern.test(s.text));

beforeEach(() => {
  fake.statements = [];
  fake.respond = () => [];
  fake.holder.scope = {
    schema: "pickm_v000007",
    asOf: "2026-09-23T22:15:00+00:00",
    versionId: 7,
    rules: buildBoardRules(structuredClone(rulesFixture), 7),
  };
});

describe("loadRowsByKeys", () => {
  it("asks nothing for no keys", async () => {
    await expect(loadRowsByKeys([])).resolves.toEqual([]);
    expect(fake.statements).toEqual([]);
  });

  it("joins the keys, once each, as one bound array, in the order given", async () => {
    fake.respond = (s) =>
      s.text.includes("WITH ORDINALITY")
        ? [unionRaw("c-2", 1), unionRaw("c-1", 3)]
        : [];
    const rows = await loadRowsByKeys(["c-2", "gone", "c-2", "c-1"]);
    const [main] = sent(/WITH ORDINALITY/);
    expect(main?.text).toMatch(/unnest\(\$1::text\[\]\) WITH ORDINALITY/);
    // The list's own order, ascending: nothing may follow k.ord (a DESC would reverse the page).
    expect(main?.text).toMatch(/ORDER BY k\.ord\s*$/);
    expect(main?.values).toEqual([["c-2", "gone", "c-1"]]);
    expect(rows.map((r) => r.rowKey)).toEqual(["c-2", "c-1"]);
    // decorate: signals and posted tags for the page's keys, ReelShort metrics for none.
    expect(sent(/catalog_signals/)).toHaveLength(1);
    expect(sent(/catalog_posted/)).toHaveLength(1);
  });

  it("the rows carry the union's flags and the decorations", async () => {
    fake.respond = (s) =>
      s.text.includes("WITH ORDINALITY") ? [unionRaw("c-1", 1)] : [];
    const [row] = await loadRowsByKeys(["c-1"]);
    expect(row?.rsFlags).toEqual({ clk: false, bill: false, gsc: false });
    expect(row?.signals).toEqual([]);
    expect(row?.posted).toEqual([]);
  });
});

describe("loadMissingKeys", () => {
  it("asks nothing for no keys", async () => {
    await expect(loadMissingKeys([])).resolves.toEqual([]);
    expect(fake.statements).toEqual([]);
  });

  it("one EXCEPT over both row tables for the whole list, answered in list order", async () => {
    fake.respond = () => [{ row_key: "z-9" }, { row_key: "a-1" }];
    const keys = ["a-1", "c-1", "z-9", "a-1"];
    await expect(loadMissingKeys(keys)).resolves.toEqual(["a-1", "z-9"]);
    expect(fake.statements).toHaveLength(1);
    const [statement] = fake.statements;
    expect(statement?.text).toMatch(/unnest\(\$1::text\[\]\)/);
    expect(statement?.text).toMatch(
      /EXCEPT\s*\(\s*SELECT row_key FROM catalog_rows\s+UNION ALL\s+SELECT row_key FROM rs_rows\s*\)/,
    );
    expect(statement?.values).toEqual([["a-1", "c-1", "z-9"]]);
  });

  it("binds a full replay list as one parameter", async () => {
    const keys = Array.from({ length: 2000 }, (_, n) => `k-${n}`);
    await loadMissingKeys(keys);
    expect(fake.statements[0]?.values).toEqual([keys]);
  });
});
