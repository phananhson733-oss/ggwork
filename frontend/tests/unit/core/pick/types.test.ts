import { describe, expect, it } from "@rstest/core";

import { pickObservationsSchema, pickResultSchema } from "@/core/pick/types";

import obsPayload from "./fixtures/backend-result-obs.json";
import oldPayload from "./fixtures/backend-result.json";

const item = {
  item_id: "item-1",
  identity: "source/123/en",
  title: "Sample drama",
  theater: "Example",
  language: "en",
  availability: "unknown",
  reason: "符合英语条件",
  warnings: ["上下架状态待核实"],
  evidence: [
    {
      citation_id: "e1",
      kind: "rank",
      source_ref: "sheet:1",
      observed_at: null,
      value: 2,
    },
  ],
};
const result = {
  id: "r1",
  thread_id: "t1",
  run_id: "run1",
  run_status: "success",
  catalog_batch_id: "b1",
  knowledge_batch_id: null,
  rule_version: "v1",
  ranking_version: "v1",
  conditions: { language: "en", limit: 5, exclude_selected: true },
  items: [item],
  created_at: "2026-09-21T00:00:00Z",
};

describe("pick candidate payload", () => {
  it("preserves unknown status and missing evidence dates", () => {
    const parsed = pickResultSchema.parse(result);
    expect(parsed.items[0]?.availability).toBe("unknown");
    expect(parsed.items[0]?.evidence[0]?.observed_at).toBeNull();
  });
  it("accepts zero matches but requires the source batch", () => {
    expect(pickResultSchema.safeParse({ ...result, items: [] }).success).toBe(
      true,
    );
    expect(
      pickResultSchema.safeParse({ ...result, catalog_batch_id: undefined })
        .success,
    ).toBe(false);
  });
  it("rejects incomplete identity and duplicate item IDs", () => {
    expect(
      pickResultSchema.safeParse({
        ...result,
        items: [{ ...item, identity: "" }],
      }).success,
    ).toBe(false);
    expect(
      pickResultSchema.safeParse({ ...result, items: [item, item] }).success,
    ).toBe(false);
  });
  it("accepts data_as_of with, without, or with a null mirror_version", () => {
    const asOf = {
      source_as_of: "2026-09-23T03:00:00.000Z",
      published_at: "2026-09-23T03:17:34Z",
      shared: true,
    };
    for (const data_as_of of [
      asOf,
      { ...asOf, mirror_version: 7 },
      { ...asOf, mirror_version: null },
    ])
      expect(
        pickResultSchema.safeParse({ ...result, data_as_of }).success,
      ).toBe(true);
    expect(
      pickResultSchema.parse({
        ...result,
        data_as_of: { ...asOf, mirror_version: 7 },
      }).data_as_of?.mirror_version,
    ).toBe(7);
  });
  it("rejects a mirror_version that is no version id, and stays strict", () => {
    const asOf = { source_as_of: null, published_at: null, shared: true };
    for (const mirror_version of [0, -1, 1.5, "7", true])
      expect(
        pickResultSchema.safeParse({
          ...result,
          data_as_of: { ...asOf, mirror_version },
        }).success,
      ).toBe(false);
    expect(
      pickResultSchema.safeParse({
        ...result,
        data_as_of: {
          ...asOf,
          mirror_version: 7,
          mirror_schema: "pickm_v000007",
        },
      }).success,
    ).toBe(false);
  });
  it("rejects a model-invented status and executable markup payload", () => {
    expect(
      pickResultSchema.safeParse({
        ...result,
        items: [{ ...item, availability: "guaranteed" }],
      }).success,
    ).toBe(false);
    expect(
      pickResultSchema.safeParse({
        ...result,
        jsx: "<script>saveAll()</script>",
      }).success,
    ).toBe(false);
  });
});

// Plan TR-16 (design 7.5 step 1): the frontend lets the observation fields
// through before any gateway sends them, and stays strict about everything else.
describe("observation fields (TR-16)", () => {
  it("parses the old fixture and the obs fixture", () => {
    expect(pickResultSchema.parse(oldPayload).observations).toBeUndefined();
    const parsed = pickResultSchema.parse(obsPayload);
    expect(parsed.conditions.sort).toBe("obs");
    expect(parsed.conditions.trend_geos).toEqual(["US"]);
    expect(parsed.observations?.coverage.pool_identities).toBe(4416);
    expect(parsed.observations?.excluded).toEqual({
      trend_first_only: 3,
      shared_title: 2,
      gsc_descriptive_only: 7,
    });
  });
  it("accepts sort=obs and the seven optional condition keys on an old-style result", () => {
    const conditions = {
      ...result.conditions,
      sort: "obs",
      trend_state: "emerging",
      trend_geos: ["WW", "GB"],
      trend_include_first: true,
      trend_include_presumed: false,
      gsc_state: "present",
      gsc_countries: ["ALL"],
      link_state: "site_only",
    };
    expect(pickResultSchema.safeParse({ ...result, conditions }).success).toBe(
      true,
    );
    for (const sort of ["heat", "trends", "OBS"])
      expect(
        pickResultSchema.safeParse({
          ...result,
          conditions: { ...result.conditions, sort },
        }).success,
      ).toBe(false);
  });
  it("still refuses unknown keys at every level", () => {
    const obs = pickResultSchema.parse(obsPayload);
    const refused = [
      { ...obsPayload, obs_as_of: {} },
      { ...obsPayload, conditions: { ...obs.conditions, trend_score: 1 } },
      {
        ...obsPayload,
        observations: { ...obsPayload.observations, status: "ok" },
      },
      {
        ...obsPayload,
        data_as_of: {
          ...obsPayload.data_as_of,
          observations: obsPayload.observations,
        },
      },
      {
        ...obsPayload,
        items: [
          {
            ...obsPayload.items[0],
            evidence: [{ ...obsPayload.items[0]?.evidence[2], payload: {} }],
          },
        ],
      },
    ];
    for (const value of refused)
      expect(pickResultSchema.safeParse(value).success).toBe(false);
  });
  it("keeps observations optional but never null, and never partial", () => {
    expect(
      pickResultSchema.safeParse({ ...obsPayload, observations: null }).success,
    ).toBe(false);
    const partial = Object.fromEntries(
      Object.entries(obsPayload.observations).filter(
        ([key]) => key !== "coverage",
      ),
    );
    expect(pickObservationsSchema.safeParse(partial).success).toBe(false);
  });
});
