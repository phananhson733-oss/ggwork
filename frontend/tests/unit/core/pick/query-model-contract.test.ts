import { describe, expect, it } from "@rstest/core";

import { queryModelProjectionSchema } from "@/core/pick/completion-types";

import examples from "./fixtures/query-model-v1.json";

describe("bounded query model projection", () => {
  it("round trips all five typed read-only variants", () => {
    for (const value of Object.values(examples))
      expect(queryModelProjectionSchema.parse(value)).toEqual(value);
  });
  it("rejects raw source blobs, authority and an invented candidate identity", () => {
    expect(
      queryModelProjectionSchema.safeParse({ ...examples.drama, board: {} })
        .success,
    ).toBe(false);
    expect(
      queryModelProjectionSchema.safeParse({
        ...examples.drama,
        owner_id: "other",
      }).success,
    ).toBe(false);
    const raw = examples.catalog_record;
    expect(
      queryModelProjectionSchema.safeParse({
        ...raw,
        rows: [{ ...raw.rows[0], identity: "invented" }],
      }).success,
    ).toBe(false);
  });
});

it("accepts old projections and distinguishes nullable episodes from measured zero", () => {
  expect(queryModelProjectionSchema.parse(examples.drama)).toEqual(
    examples.drama,
  );
  for (const episodes of [null, 0, 80]) {
    const payload = {
      ...examples.drama,
      rows: [
        {
          ...examples.drama.rows[0],
          episodes,
          episodes_source_ref: "mirror:1:catalog_rows:synthetic-A",
        },
      ],
    };
    expect(queryModelProjectionSchema.parse(payload).rows[0]).toMatchObject({
      episodes,
    });
  }
  for (const episodes of [true, -1, "80"]) {
    expect(
      queryModelProjectionSchema.safeParse({
        ...examples.drama,
        rows: [{ ...examples.drama.rows[0], episodes }],
      }).success,
    ).toBe(false);
  }
});
