import { describe, expect, it } from "@rstest/core";

import {
  bindPickReference,
  chooseReference,
  resolvePickOrdinals,
  turnPickReference,
} from "@/core/pick/references";

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

describe("chooseReference", () => {
  const make = (id: string, created: string, thread = "t") => ({
    id,
    thread_id: thread,
    created_at: created,
    items: [{ item_id: `${id}-1` }, { item_id: `${id}-2` }],
  });
  const old = make("old", "2026-09-23T01:00:00Z");
  const fresh = make("new", "2026-09-23T02:00:00Z");
  it("binds the newest finished card when no panel is open", () => {
    expect(
      chooseReference("t", { result: null, open: false, selected: [] }, fresh),
    ).toEqual({ result_id: "new", item_ids: [] });
  });
  it("keeps an explicitly opened older panel and its ticks", () => {
    expect(
      chooseReference(
        "t",
        { result: old, open: true, selected: ["old-2"] },
        fresh,
      ),
    ).toEqual({ result_id: "old", item_ids: ["old-2"] });
  });
  it("lets a newer card replace a closed older panel", () => {
    expect(
      chooseReference(
        "t",
        { result: old, open: false, selected: ["old-2"] },
        fresh,
      )?.result_id,
    ).toBe("new");
  });
  it("never binds another thread's result", () => {
    expect(
      chooseReference(
        "t",
        {
          result: make("x", "2026-09-24T00:00:00Z", "other"),
          open: true,
          selected: [],
        },
        make("y", "2026-09-24T00:00:00Z", "other"),
      ),
    ).toBeUndefined();
  });
});

// Evaluation batch 2 (2026-10-05): a regenerate or edit replays its turn with
// the pick reference that turn was sent with, read back from its human message.
describe("turnPickReference", () => {
  const reference = { result_id: "r1", item_ids: ["i2", "i1"] };
  const messages = [
    { id: "h0", type: "human" },
    { id: "a0", type: "ai" },
    {
      id: "h1",
      type: "human",
      additional_kwargs: { pick_reference: reference },
    },
    { id: "a1", type: "ai" },
    { id: "t1", type: "tool" },
    { id: "a2", type: "ai" },
  ];
  it("reads an answer's turn from the human message before it", () => {
    expect(turnPickReference(messages, "a2")).toEqual(reference);
    expect(turnPickReference(messages, "a1")).toEqual(reference);
  });
  it("reads an edited human message's own reference", () => {
    expect(turnPickReference(messages, "h1")).toEqual(reference);
    expect(turnPickReference(messages, "h1")).not.toBe(reference);
  });
  it("finds none for an unbound turn, an unknown id or a malformed value", () => {
    expect(turnPickReference(messages, "a0")).toBeUndefined();
    expect(turnPickReference(messages, "missing")).toBeUndefined();
    expect(
      turnPickReference(
        [
          {
            id: "h",
            type: "human",
            additional_kwargs: {
              pick_reference: { result_id: 1, item_ids: [] },
            },
          },
        ],
        "h",
      ),
    ).toBeUndefined();
  });
});
