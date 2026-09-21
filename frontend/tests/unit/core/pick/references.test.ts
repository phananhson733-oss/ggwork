import { describe, expect, it } from "@rstest/core";

import { bindPickReference, resolvePickOrdinals } from "@/core/pick/references";

const result = {
  id: "result-old",
  thread_id: "thread-a",
  items: [{ item_id: "item-a" }, { item_id: "item-b" }, { item_id: "item-c" }],
};

describe("pick result references", () => {
  it("binds ordinals to the explicitly displayed immutable result", () => {
    expect(resolvePickOrdinals(result, [1, 3])).toEqual({
      result_id: "result-old",
      item_ids: ["item-a", "item-c"],
    });
  });

  it("rejects zero, fractional and out-of-range ordinals", () => {
    for (const ordinals of [[0], [4], [1.5], [Number.NaN]]) {
      expect(() => resolvePickOrdinals(result, ordinals)).toThrow();
    }
  });

  it("does not guess the latest result when no result is bound", () => {
    expect(() => resolvePickOrdinals(null, [1])).toThrow("请先选择");
  });

  it("deduplicates selected IDs in snapshot order", () => {
    expect(
      bindPickReference("thread-a", result, ["item-c", "item-a", "item-c"]),
    ).toEqual({
      result_id: "result-old",
      item_ids: ["item-a", "item-c"],
    });
  });

  it("rejects references from a different conversation", () => {
    expect(() => bindPickReference("thread-b", result, ["item-a"])).toThrow(
      "当前对话",
    );
  });

  it("rejects unknown IDs instead of silently selecting a subset", () => {
    expect(() =>
      bindPickReference("thread-a", result, ["item-a", "injected"]),
    ).toThrow("候选");
  });

  it("copies references so a later checkbox change cannot retarget a sent message", () => {
    const ids = ["item-a"];
    const ref = bindPickReference("thread-a", result, ids);
    ids.push("item-b");
    expect(ref.item_ids).toEqual(["item-a"]);
  });
});
