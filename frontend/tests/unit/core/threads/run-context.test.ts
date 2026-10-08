import { describe, expect, it } from "@rstest/core";

import type { LocalSettings } from "@/core/settings";
import { buildRunContext } from "@/core/threads/hooks";

const settings = {
  mode: "pro",
  model_name: "gemma4",
  reasoning_effort: undefined,
} as unknown as LocalSettings["context"];

describe("buildRunContext", () => {
  it("uses the submitted pick reference rather than stale local settings and freezes item IDs", () => {
    const stale = {
      ...settings,
      pick_reference: { result_id: "stale", item_ids: ["wrong"] },
    } as unknown as LocalSettings["context"];
    const reference = { result_id: "actual", item_ids: ["item-1"] };
    const context = buildRunContext({
      settings: stale,
      threadId: "t-1",
      extraContext: { pick_reference: reference },
    });
    reference.item_ids.push("later");
    expect(context.pick_reference).toEqual({
      result_id: "actual",
      item_ids: ["item-1"],
    });
    expect(
      buildRunContext({ settings: stale, threadId: "t-1" }).pick_reference,
    ).toBeUndefined();
  });
  it("sends attached references as a plain string[] under context.conversation_references", () => {
    const context = buildRunContext({
      settings,
      threadId: "t-1",
      extraContext: { agent_name: "writer" },
      conversationReferences: ["source-a", "source-b"],
    });
    expect(context.conversation_references).toEqual(["source-a", "source-b"]);
    expect(context.agent_name).toBe("writer");
    expect(context.thread_id).toBe("t-1");
    expect(context.is_plan_mode).toBe(true);
  });

  it("omits the key when nothing is attached, including on the replay path", () => {
    expect(
      "conversation_references" in
        buildRunContext({ settings, threadId: "t-1" }),
    ).toBe(false);
    expect(
      "conversation_references" in
        buildRunContext({
          settings,
          threadId: "t-1",
          conversationReferences: [],
        }),
    ).toBe(false);
  });

  it("never forwards a stray conversation_references key from local settings", () => {
    const stale = {
      ...settings,
      conversation_references: ["stale-source"],
    } as unknown as LocalSettings["context"];
    expect(
      "conversation_references" in
        buildRunContext({ settings: stale, threadId: "t-1" }),
    ).toBe(false);
    expect(
      buildRunContext({
        settings: stale,
        threadId: "t-1",
        conversationReferences: ["source-a"],
      }).conversation_references,
    ).toEqual(["source-a"]);
  });

  it("copies the list so later mutation of the caller's array cannot change the request", () => {
    const references = ["source-a"];
    const context = buildRunContext({
      settings,
      threadId: "t-1",
      conversationReferences: references,
    });
    references.push("source-b");
    expect(context.conversation_references).toEqual(["source-a"]);
  });
});

it("freezes explicit plural references, rejects conflicts and never inherits them from settings", () => {
  const refs = {
    version: "pick-references-v1",
    references: [
      { result_id: "r1", item_ids: ["i1"] },
      { result_id: "r2", item_ids: ["i2"] },
    ],
  };
  const stale = {
    ...settings,
    pick_references: refs,
  } as unknown as LocalSettings["context"];
  expect(
    buildRunContext({ settings: stale, threadId: "t" }),
  ).not.toHaveProperty("pick_references");
  const context = buildRunContext({
    settings,
    threadId: "t",
    extraContext: { pick_references: refs },
  });
  refs.references[0]!.item_ids.push("changed");
  expect(context.pick_references).toEqual({
    version: "pick-references-v1",
    references: [
      { result_id: "r1", item_ids: ["i1"] },
      { result_id: "r2", item_ids: ["i2"] },
    ],
  });
  expect(() =>
    buildRunContext({
      settings,
      threadId: "t",
      extraContext: { pick_references: refs.references },
    }),
  ).toThrow();
  expect(() =>
    buildRunContext({
      settings,
      threadId: "t",
      extraContext: {
        pick_references: refs,
        pick_reference: refs.references[0],
      },
    }),
  ).toThrow();
});
