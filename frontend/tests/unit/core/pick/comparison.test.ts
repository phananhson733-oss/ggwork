import { describe, expect, it } from "@rstest/core";

import { alignCandidates, selectionGroups } from "@/core/pick/comparison";
import { pickResultSchema } from "@/core/pick/types";

import payload from "./fixtures/backend-result.json";

const first = pickResultSchema.parse({
  ...payload,
  id: "first",
  run_status: "success",
});
const second = pickResultSchema.parse({
  ...first,
  id: "second",
  items: first.items.map((item) => ({
    ...item,
    item_id: `second-${item.item_id}`,
    reason: "Different evidence",
  })),
});

describe("explicit candidate comparison", () => {
  it("aligns stable identities while preserving both immutable evidence sources", () => {
    const rows = alignCandidates(first.thread_id, first, second);
    expect(rows).toHaveLength(first.items.length);
    expect(rows[0]?.first?.item).toEqual(first.items[0]);
    expect(rows[0]?.second?.item.reason).toBe("Different evidence");
    const selection = selectionGroups(rows, {
      [first.items[0]!.identity]: "second",
    });
    expect(selection).toEqual([
      { result_id: "second", item_ids: [second.items[0]!.item_id] },
    ]);
    expect(selectionGroups(rows, {})).toEqual([]);
  });
  it("keeps same-title different-source identities separate and marked for verification", () => {
    const other = {
      ...second,
      items: [{ ...second.items[0]!, identity: '["other-source", "1", "en"]' }],
    };
    const rows = alignCandidates(first.thread_id, first, other);
    expect(rows.filter((row) => row.ambiguousTitle)).toHaveLength(2);
    expect(
      rows.find((row) => row.identity === other.items[0]!.identity)?.first,
    ).toBeUndefined();
  });
  it("refuses same-batch, other-thread and unfinished comparisons", () => {
    expect(() => alignCandidates(first.thread_id, first, first)).toThrow();
    expect(() => alignCandidates("another-thread", first, second)).toThrow();
    expect(() =>
      alignCandidates(first.thread_id, first, {
        ...second,
        run_status: "running",
      }),
    ).toThrow();
  });
});
