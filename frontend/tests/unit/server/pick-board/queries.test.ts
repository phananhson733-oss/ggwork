import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { beforeEach, describe, expect, it, rs } from "@rstest/core";

import { parsePickRequest } from "@/core/pick-board/request";
import { buildBoardRules, type BoardRules } from "@/core/pick-board/rules";
import { resetVersionCacheForTests } from "@/server/pick-board/cache";
import type * as DbModule from "@/server/pick-board/db";
import { type ScopeHolder, type VersionScope } from "@/server/pick-board/db";
import {
  loadCandidatePool,
  loadFacets,
  loadPickRows,
} from "@/server/pick-board/queries";
import { loadReelshortDetail } from "@/server/pick-board/queries-reelshort";
import {
  loadDramaDetail,
  loadRows,
  loadRowsByIds,
} from "@/server/pick-board/rs-queries";
import { readSources } from "@/server/pick-board/source-state";

import rulesFixture from "../../core/pick-board/fixtures/rules.json";

/**
 * The query layer against a fake reader: what each loader sends (text and
 * bound values) and how it maps what comes back. The real SQL runs in
 * queries.integration.test.ts against board_fixture's database.
 */

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

const AS_OF = "2026-09-23T22:15:00+00:00";

function rulesWith(shortmaxYt: string): BoardRules {
  const raw = structuredClone(rulesFixture) as {
    platformRules: Record<string, { yt: string }>;
  };
  const shortmax = raw.platformRules.shortmax;
  if (!shortmax) throw new Error("fixture has no shortmax rule");
  shortmax.yt = shortmaxYt;
  return buildBoardRules(raw, 7);
}

function scopeOf(rules: BoardRules, asOf = AS_OF): VersionScope<BoardRules> {
  return { schema: "pickm_v000007", asOf, versionId: 7, rules };
}

function pin(scope: VersionScope<BoardRules>) {
  fake.holder.scope = scope;
}

function sent(pattern: RegExp): Statement[] {
  return fake.statements.filter((s) => pattern.test(s.text));
}

function only(pattern: RegExp): Statement {
  const found = sent(pattern);
  expect(found).toHaveLength(1);
  const [statement] = found;
  if (!statement) throw new Error("no statement");
  return statement;
}

/** An rs_rows result row as loadRows selects it. */
function observeRaw(over: Record<string, unknown> = {}) {
  return {
    id: "rs0001",
    title: "rs0001 的剧名",
    locale: "en",
    slug: "rs0001-slug",
    publish_at: "2026-09-18 22:15:00+00",
    chapter_count: 60,
    pay_start: 8,
    rr: 1250.5,
    promoters_cnt: 3,
    metrics_valid: true,
    synced_at: "2026-09-23 09:00:00+00",
    baseline1_at: null,
    baseline7_at: null,
    baseline15_at: null,
    search_impressions: 0,
    search_data_at: null,
    detail_synced_at: null,
    tags: ["甜宠"],
    description: "",
    rr1: null,
    p1: null,
    rr7: null,
    p7: null,
    rr15: null,
    p15: null,
    bill_orders: 0,
    clicks: 0,
    last_click_on: null,
    last_bill_on: null,
    ...over,
  };
}

const EMPTY_RS = {
  tab: "all",
  sort: "rr",
  locale: "",
  bucket: null,
  page: 1,
  size: 10,
  q: "",
  dramaId: "",
} as const;

beforeEach(() => {
  fake.statements.length = 0;
  fake.respond = () => [];
  fake.holder.scope = null;
  resetVersionCacheForTests();
});

describe("rs-queries: ReelShort rows from rs_rows", () => {
  it("maps metrics_valid as a boolean and keeps clicks7 0 as 0 (B16)", async () => {
    pin(scopeOf(rulesWith("ok")));
    fake.respond = (s) =>
      s.text.includes("count(*)")
        ? [{ n: 3 }]
        : [
            observeRaw({ id: "a", metrics_valid: true, clicks: 0 }),
            observeRaw({ id: "b", metrics_valid: false, clicks: 4 }),
            observeRaw({ id: "c", metrics_valid: null }),
          ];
    const page = await loadRows(EMPTY_RS);
    expect(page.rows.map((r) => [r.id, r.metricsValid, r.clicks7])).toEqual([
      ["a", true, 0],
      ["b", false, 4],
      ["c", null, 0],
    ]);
    expect(page.total).toBe(3);
    expect(page.rows[0]?.publishAt?.toISOString()).toBe(
      "2026-09-18T22:15:00.000Z",
    );
  });

  it("d1 / d7 / eff compare as numeric, bill by the exported rank, each ending on drama_id (C35)", async () => {
    pin(scopeOf(rulesWith("ok")));
    for (const sort of [
      "d1",
      "d7",
      "dp1",
      "dp7",
      "eff",
      "bill",
      "clicks",
      "rr",
    ] as const)
      await loadRows({ ...EMPTY_RS, sort }, { limit: 5 });
    const orders = fake.statements.map(
      (s) => /ORDER BY ([\s\S]*?) LIMIT/.exec(s.text)?.[1]?.trim() ?? "",
    );
    expect(orders).toEqual([
      "(rr::numeric - s1_rr::numeric) DESC NULLS LAST, drama_id ASC",
      "(rr::numeric - s7_rr::numeric) DESC NULLS LAST, drama_id ASC",
      "(promoters_cnt - s1_p) DESC NULLS LAST, drama_id ASC",
      "(promoters_cnt - s7_p) DESC NULLS LAST, drama_id ASC",
      "(round(rr::numeric, 2) / NULLIF(promoters_cnt, 0)) DESC NULLS LAST, drama_id ASC",
      "bill_rank ASC NULLS LAST, drama_id ASC",
      "clicks7 DESC NULLS LAST, drama_id ASC",
      "rr DESC NULLS LAST, drama_id ASC",
    ]);
  });

  it("buckets count days from the pinned as_of, bound as a parameter, never now()", async () => {
    pin(scopeOf(rulesWith("ok"), "2026-10-03T22:15:00+00:00"));
    await loadRows({ ...EMPTY_RS, bucket: "8-30" });
    const [page, count] = fake.statements;
    for (const s of [page, count]) {
      expect(s?.text).not.toMatch(/now\(\)/i);
      expect(s?.values).toContain("2026-10-03T22:15:00+00:00");
      expect(s?.text).toMatch(/AT TIME ZONE 'UTC'/);
    }
  });

  it("loadRowsByIds takes the full text when the page holds one ReelShort row", async () => {
    pin(scopeOf(rulesWith("ok")));
    await loadRowsByIds(["rs0001"]);
    await loadRowsByIds(["rs0001", "rs0002"]);
    const [one, two] = fake.statements;
    expect(one?.text).toMatch(/tag_list AS tags, description AS description/);
    expect(two?.text).toMatch(/tag_list\[1:8\] AS tags, '' AS description/);
    expect(sent(/count\(\*\)/)).toHaveLength(0);
  });

  it("loadDramaDetail asks for exactly the canonical id (B21)", async () => {
    pin(scopeOf(rulesWith("ok")));
    fake.respond = (s) =>
      s.text.includes("FROM rs_rows") ? [observeRaw({ id: "rs0001" })] : [];
    const detail = await loadDramaDetail("rs0001");
    expect(detail?.row.id).toBe("rs0001");
    const metrics = only(/FROM rs_rows/);
    expect(metrics.text).toMatch(/drama_id = ANY\(ARRAY\[\$\d+\]::text\[\]\)/);
    expect(metrics.text).not.toMatch(/ILIKE/);
    expect(metrics.values).toContain("rs0001");
    const series = only(/pick_mirror\.series/);
    expect(series.text).toMatch(/LEAST\(/);
    expect(series.values).toContain(AS_OF);
  });

  it("loadReelshortDetail: a book id whose canonical_id is NULL finds nothing (B21)", async () => {
    pin(scopeOf(rulesWith("ok")));
    fake.respond = (s) =>
      s.text.includes("FROM rs_ids") ? [{ canonical_id: null }] : [];
    expect(await loadReelshortDetail("rs0007")).toBeNull();
    expect(fake.statements).toHaveLength(1);
  });
});

describe("queries: the rules come from the pinned version", () => {
  const boundFor = async (
    params: Record<string, string>,
    rules: BoardRules,
  ) => {
    fake.statements.length = 0;
    pin(scopeOf(rules));
    await loadPickRows(parsePickRequest(params));
    return only(/LIMIT/).values;
  };

  it("yt=1 binds the version's YouTube lists: shortmax ok in one version, no in the next", async () => {
    const ok = await boundFor({ yt: "1" }, rulesWith("ok"));
    const no = await boundFor({ yt: "1" }, rulesWith("no"));
    expect(ok).toEqual(
      expect.arrayContaining(["moboreels", "flareflow", "kalos", "starshort"]),
    );
    expect(ok).not.toContain("shortmax");
    expect(no).toContain("shortmax");
  });

  it("inuse=1 binds the version's inUse, not a static list", async () => {
    const raw = { ...structuredClone(rulesFixture), inUse: ["dramabox"] };
    const bound = await boundFor({ inuse: "1" }, buildBoardRules(raw, 7));
    expect(bound).toContain("dramabox");
    expect(bound).not.toContain("shortmax");
    expect(await boundFor({ inuse: "1" }, rulesWith("ok"))).toEqual(
      expect.arrayContaining(["reelshort", "dramabox", "shortmax"]),
    );
  });

  it("the five facet counts go out together", async () => {
    pin(scopeOf(rulesWith("ok")));
    await loadFacets(parsePickRequest({}));
    expect(fake.statements).toHaveLength(5);
  });
});

describe("A3: what never changes within a version is read once", () => {
  it("the candidate pool N and meta.sources, per version", async () => {
    fake.respond = (s) =>
      s.text.includes("FROM meta")
        ? [
            {
              value: {
                bill: {
                  source: "bill",
                  status: "success",
                  attemptedAt: "2026-09-23T00:00:00.000Z",
                  completedAt: "2026-09-23T00:01:00.000Z",
                  details: {},
                },
              },
            },
          ]
        : [{ n: 6 }];
    pin(scopeOf(rulesWith("ok")));
    expect(await loadCandidatePool()).toBe(6);
    expect(await loadCandidatePool()).toBe(6);
    expect((await readSources()).bill?.status).toBe("success");
    await readSources();
    expect(fake.statements).toHaveLength(2);
    pin({ ...scopeOf(rulesWith("ok")), schema: "pickm_v000008", versionId: 8 });
    await loadCandidatePool();
    expect(fake.statements).toHaveLength(3);
  });
});

describe("source scan", () => {
  const dir = path.resolve(__dirname, "../../../../src/server/pick-board");
  const files = readdirSync(dir).filter((name) => name.endsWith(".ts"));

  it("no query reads the wall clock: no now(), no argument-less new Date(), no Date.now()", () => {
    for (const name of files) {
      const text = readFileSync(path.join(dir, name), "utf8");
      expect(text, name).not.toMatch(/\bnow\(\)/i);
      expect(text, name).not.toMatch(/new Date\(\)/);
      expect(text, name).not.toMatch(/Date\.now\(/);
    }
  });

  it("the query files read the version's rules, never the static rule lists", () => {
    for (const name of files) {
      const text = readFileSync(path.join(dir, name), "utf8");
      expect(text, name).not.toMatch(
        /\b(IN_USE|PLATFORM_RULES|YOUTUBE_LABEL|GLOSSARY|RULE_HINTS)\b/,
      );
    }
  });
});
