import type { Message } from "@langchain/langgraph-sdk";
import { afterEach, beforeEach, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";

import { I18nContext } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";
import { DEFAULT_LOCAL_SETTINGS } from "@/core/settings/local";

// Every run this hook starts must survive a proxy or edge disconnect: the
// Gateway defaults `on_disconnect` to cancel, and a long replay is exactly the
// run a Vercel or Railway disconnect is most likely to hit.

const streamMockState = rs.hoisted(() => ({
  submit: rs.fn(async () => undefined),
}));

rs.mock("@langchain/langgraph-sdk/react", () => ({
  useStream: () => ({
    isLoading: false,
    joinStream: rs.fn(async () => undefined),
    messages: [],
    stop: rs.fn(async () => undefined),
    submit: streamMockState.submit,
    values: { artifacts: [], messages: [], title: "", todos: [] },
  }),
}));

const THREAD_ID = "thread-1";
const HUMAN_ID = "srv-h1";
const HUMAN = {
  id: HUMAN_ID,
  type: "human",
  content: [{ type: "text", text: "Original question" }],
} as Message;
const ANSWER = { id: "srv-a1", type: "ai", content: "Original answer" };
const CHECKPOINT = {
  checkpoint_ns: "",
  checkpoint_id: "cp-1",
  checkpoint_map: null,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function stubBackend() {
  rs.stubGlobal("fetch", async (input: unknown) => {
    const url = String(input);
    if (url.includes("/runs/regenerate/prepare")) {
      return jsonResponse({
        input: { messages: [HUMAN] },
        checkpoint: CHECKPOINT,
        metadata: { regenerate_from_run_id: "run-1" },
        target_run_id: "run-1",
      });
    }
    if (url.includes("/runs/edit-regenerate/prepare")) {
      return jsonResponse({
        input: {
          messages: [{ ...HUMAN, id: "repl-h1" }],
        },
        checkpoint: CHECKPOINT,
        metadata: { replay_kind: "edit", regenerate_from_run_id: "run-1" },
        target_run_id: "run-1",
        replacement_human_message_id: "repl-h1",
        source_message_ids: [HUMAN_ID, ANSWER.id],
      });
    }
    if (url.includes("/messages/page")) {
      return jsonResponse({ data: [], has_more: false, next_before_seq: null });
    }
    // Runs list, token usage and other background reads are irrelevant here.
    return jsonResponse([]);
  });
}

function createWrapper(queryClient: QueryClient) {
  return function RunOptionsTestWrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(
        I18nContext.Provider,
        { value: { locale: "en-US", setLocale: () => undefined, t: enUS } },
        children,
      ),
    );
  };
}

async function renderThread() {
  const { useThreadStream } = await import("@/core/threads/hooks");
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderHook(
    () =>
      useThreadStream({
        context: DEFAULT_LOCAL_SETTINGS.context,
        isMock: false,
        threadId: THREAD_ID,
      }),
    { wrapper: createWrapper(queryClient) },
  );
}

type Rendered = Awaited<ReturnType<typeof renderThread>>;

const SUBMIT_PATHS = {
  send: (current: Rendered["result"]["current"]) =>
    current.sendMessage(THREAD_ID, { files: [], text: "Follow up" }),
  regenerate: (current: Rendered["result"]["current"]) =>
    current.regenerateMessage(THREAD_ID, ANSWER.id),
  edit: (current: Rendered["result"]["current"]) =>
    current.editAndRegenerateMessage(THREAD_ID, HUMAN_ID, "Edited question"),
} as const;

type SubmitPath = keyof typeof SUBMIT_PATHS;

/** Submit through one path and return the options passed to the SDK. */
async function submitOptionsFor(
  path: SubmitPath,
): Promise<Record<string, unknown>> {
  const { result, unmount } = await renderThread();
  await act(async () => {
    await SUBMIT_PATHS[path](result.current);
  });
  unmount();
  // The submit mock is an untyped rs.fn(); cast the call list once so the
  // tuple indexing below typechecks.
  const calls = streamMockState.submit.mock.calls as unknown as Array<
    [unknown, Record<string, unknown>]
  >;
  expect(calls).toHaveLength(1);
  return calls[0]![1];
}

// The per-turn half of the options; everything else is the transport contract.
const TURN_FIELDS = new Set(["threadId", "checkpoint", "metadata", "context"]);

function withoutTurnFields(options: Record<string, unknown>) {
  return Object.fromEntries(
    Object.entries(options).filter(([key]) => !TURN_FIELDS.has(key)),
  );
}

beforeEach(() => {
  streamMockState.submit.mockClear();
  stubBackend();
});

afterEach(() => {
  rs.unstubAllGlobals();
});

test.each(Object.keys(SUBMIT_PATHS) as SubmitPath[])(
  "the %s path keeps its run going when the stream disconnects",
  async (path) => {
    const options = await submitOptionsFor(path);

    expect(options).toMatchObject({
      threadId: THREAD_ID,
      streamResumable: true,
      onDisconnect: "continue",
    });
  },
);

test("the replay paths submit with the same transport options as a send", async () => {
  const send = withoutTurnFields(await submitOptionsFor("send"));
  streamMockState.submit.mockClear();
  const regenerate = withoutTurnFields(await submitOptionsFor("regenerate"));
  streamMockState.submit.mockClear();
  const edit = withoutTurnFields(await submitOptionsFor("edit"));

  expect(regenerate).toEqual(send);
  expect(edit).toEqual(send);
});

test("replays still resume from the prepared checkpoint", async () => {
  const options = await submitOptionsFor("regenerate");

  expect(options).toMatchObject({
    checkpoint: CHECKPOINT,
    metadata: { regenerate_from_run_id: "run-1" },
  });
});

// Evaluation batch 2 (2026-10-05): a regenerate or edit replay ran without the
// pick reference its turn was sent with, so "save the 2nd one" lost its card.
// The caller passes the turn's stored reference; a replay without one sends none.
test("a replay carries only the pick reference its caller passes", async () => {
  const reference = { result_id: "r1", item_ids: ["i1"] };
  const { result, unmount } = await renderThread();
  await act(async () => {
    await result.current.regenerateMessage(THREAD_ID, ANSWER.id, [ANSWER.id], {
      pick_reference: reference,
    });
  });
  await act(async () => {
    await result.current.editAndRegenerateMessage(
      THREAD_ID,
      HUMAN_ID,
      "Edited question",
      { pick_reference: reference },
      { pick_reference: reference },
    );
  });
  await act(async () => {
    await result.current.regenerateMessage(THREAD_ID, ANSWER.id);
  });
  unmount();
  const calls = streamMockState.submit.mock.calls as unknown as Array<
    [{ messages: Message[] }, { context: Record<string, unknown> }]
  >;
  expect(calls).toHaveLength(3);
  expect(calls[0]![1].context.pick_reference).toEqual(reference);
  expect(calls[1]![1].context.pick_reference).toEqual(reference);
  expect(calls[1]![0].messages[0]!.additional_kwargs?.pick_reference).toEqual(
    reference,
  );
  expect(calls[2]![1].context).not.toHaveProperty("pick_reference");
});

test("plural edit and regenerate keep original frozen turn refs while an unbound replay has none", async () => {
  const refs = {
    version: "pick-references-v1",
    references: [
      { result_id: "old", item_ids: ["a"] },
      { result_id: "new", item_ids: ["b"] },
    ],
  };
  const stored = { ...refs, thread_id: THREAD_ID };
  const { result, unmount } = await renderThread();
  await act(async () => {
    await result.current.regenerateMessage(THREAD_ID, ANSWER.id, [ANSWER.id], {
      pick_references: refs,
    });
  });
  await act(async () => {
    await result.current.editAndRegenerateMessage(
      THREAD_ID,
      HUMAN_ID,
      "Compare original snapshots",
      { pick_references: stored },
      { pick_references: refs },
    );
  });
  await act(async () => {
    await result.current.regenerateMessage(THREAD_ID, ANSWER.id);
  });
  refs.references[0]!.item_ids.push("later-change");
  unmount();
  const calls = streamMockState.submit.mock.calls as unknown as Array<
    [{ messages: Message[] }, { context: Record<string, unknown> }]
  >;
  expect(calls[0]![1].context.pick_references).toEqual({
    version: "pick-references-v1",
    references: [
      { result_id: "old", item_ids: ["a"] },
      { result_id: "new", item_ids: ["b"] },
    ],
  });
  expect(calls[1]![1].context.pick_references).toEqual(
    calls[0]![1].context.pick_references,
  );
  expect(
    calls[1]![0].messages[0]!.additional_kwargs?.pick_references,
  ).toMatchObject({ thread_id: THREAD_ID });
  expect(calls[2]![1].context).not.toHaveProperty("pick_references");
});
