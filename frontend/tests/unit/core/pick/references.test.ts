import { describe, expect, it } from "@rstest/core";

import {
  bindPickReference,
  chooseReference,
  resolvePickOrdinals,
  storablePickReference,
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
  const stored = storablePickReference("t1", reference);
  const messages = [
    { id: "h0", type: "human" },
    { id: "a0", type: "ai" },
    { id: "h1", type: "human", additional_kwargs: { pick_reference: stored } },
    { id: "a1", type: "ai" },
    { id: "t1", type: "tool" },
    { id: "a2", type: "ai" },
  ];
  it("reads an answer's turn from the human message before it", () => {
    expect(turnPickReference(messages, "a2", "t1")).toEqual(reference);
    expect(turnPickReference(messages, "a1", "t1")).toEqual(reference);
  });
  it("reads an edited human message's own reference", () => {
    expect(turnPickReference(messages, "h1", "t1")).toEqual(reference);
    expect(turnPickReference(messages, "h1", "t1")).not.toBe(reference);
  });
  it("finds none for an unbound turn, an unknown id or a malformed value", () => {
    expect(turnPickReference(messages, "a0", "t1")).toBeUndefined();
    expect(turnPickReference(messages, "missing", "t1")).toBeUndefined();
    expect(
      turnPickReference(
        [
          {
            id: "h",
            type: "human",
            additional_kwargs: {
              pick_reference: { result_id: 1, item_ids: [], thread_id: "t1" },
            },
          },
        ],
        "h",
        "t1",
      ),
    ).toBeUndefined();
  });
  // gpt-6-astra review: a branch copies the messages but not the results, so
  // the gateway refuses a parent thread's result and the replay failed outright.
  it("drops a reference sent in another thread, as a branch copies it", () => {
    expect(turnPickReference(messages, "a2", "branch")).toBeUndefined();
    const unscoped = [
      {
        id: "h",
        type: "human",
        additional_kwargs: { pick_reference: reference },
      },
      { id: "a", type: "ai" },
    ];
    expect(turnPickReference(unscoped, "a", "t1")).toBeUndefined();
  });
  // gpt-6-astra review: a goal continuation is a hidden human message the
  // gateway skips when replaying; stopping at it lost the turn's reference.
  it("skips the hidden control messages the gateway skips, but not a card reply", () => {
    const continued = [
      ...messages.slice(0, 4),
      {
        id: "g1",
        type: "human",
        additional_kwargs: {
          hide_from_ui: true,
          deerflow_goal_continuation: true,
        },
      },
      { id: "s1", type: "human", name: "summary" },
      { id: "a3", type: "ai" },
    ];
    expect(turnPickReference(continued, "a3", "t1")).toEqual(reference);
    const answered = [
      ...messages.slice(0, 4),
      {
        id: "c1",
        type: "human",
        additional_kwargs: {
          hide_from_ui: true,
          human_input_response: { value: "x" },
        },
      },
      { id: "a4", type: "ai" },
    ];
    expect(turnPickReference(answered, "a4", "t1")).toBeUndefined();
  });
});

describe("explicit versioned plural references", () => {
  it("freezes two explicit result selections and restores the original turn only in its thread", async () => {
    const { bindPickReferences, storablePickContext, turnPickContext } =
      await import("@/core/pick/references");
    const second = { ...result, id: "result-new" };
    const input = ["item-b"];
    const refs = bindPickReferences("thread-a", [
      { result, item_ids: input },
      { result: second, item_ids: ["item-a"] },
    ]);
    input.push("item-c");
    expect(refs).toEqual({
      version: "pick-references-v1",
      references: [
        { result_id: "result-old", item_ids: ["item-b"] },
        { result_id: "result-new", item_ids: ["item-a"] },
      ],
    });
    const messages = [
      {
        id: "human",
        type: "human",
        additional_kwargs: storablePickContext("thread-a", {
          pick_references: refs,
        }),
      },
      { id: "answer", type: "ai" },
    ];
    expect(turnPickContext(messages, "answer", "thread-a")).toEqual({
      pick_references: refs,
    });
    expect(turnPickContext(messages, "human", "thread-a")).toEqual({
      pick_references: refs,
    });
    expect(turnPickContext(messages, "answer", "branch")).toBeUndefined();
  });
  it("rejects raw arrays, duplicate groups or items, empty groups and conflicting protocols", async () => {
    const { freezePickReferences, storablePickContext } =
      await import("@/core/pick/references");
    const ref = { result_id: "r", item_ids: ["i"] };
    for (const input of [
      [ref],
      { version: "old", references: [ref] },
      { version: "pick-references-v1", references: [ref, ref] },
      { version: "pick-references-v1", references: [{ ...ref, item_ids: [] }] },
      {
        version: "pick-references-v1",
        references: [{ ...ref, item_ids: ["i", "i"] }],
      },
    ])
      expect(() => freezePickReferences(input)).toThrow();
    expect(() =>
      storablePickContext("t", {
        pick_reference: ref,
        pick_references: { version: "pick-references-v1", references: [ref] },
      }),
    ).toThrow();
  });
});
