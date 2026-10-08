import { describe, expect, it } from "@rstest/core";
import examples from "./fixtures/query-model-v1.json";
import { queryModelProjectionSchema } from "@/core/pick/completion-types";

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
