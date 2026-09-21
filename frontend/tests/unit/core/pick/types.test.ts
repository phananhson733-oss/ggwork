import { describe, expect, it } from "@rstest/core";

import { pickResultSchema } from "@/core/pick/types";

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
