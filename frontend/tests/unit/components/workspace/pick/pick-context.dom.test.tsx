import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState } from "react";

let owner = "alice";
rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: owner } }),
}));
const api = rs.hoisted(() => ({
  listPickResults: rs.fn<(threadId: string) => Promise<PickResult[]>>(),
}));
rs.mock("@/core/pick/api", () => ({
  getPickResult: rs.fn(),
  listPickResults: api.listPickResults,
}));

import {
  PickProvider,
  useObservePickThread,
  usePickContext,
} from "@/components/workspace/pick/pick-context";
import type { PickResult } from "@/core/pick/types";

const result: PickResult = {
  id: "r1",
  thread_id: "t1",
  run_id: "run",
  run_status: "success",
  catalog_batch_id: "b1",
  knowledge_batch_id: null,
  rule_version: "v1",
  ranking_version: "v1",
  created_at: "2026-09-21T00:00:00Z",
  conditions: { limit: 5, exclude_selected: true },
  items: [],
};
function Probe() {
  const pick = usePickContext()!;
  return (
    <>
      <button onClick={() => pick.show(result)}>show</button>
      <button onClick={pick.close}>close</button>
      <output>{pick.result?.id ?? "none"}</output>
    </>
  );
}
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  owner = "alice";
  api.listPickResults.mockReset();
});

function ThreadProbe({
  threadId,
  isLoading,
}: {
  threadId: string;
  isLoading: boolean;
}) {
  const pick = usePickContext()!;
  useObservePickThread(threadId, isLoading);
  // The reference is read when a message is sent, not during render.
  const [bound, setBound] = useState("none");
  return (
    <>
      <button
        onClick={() =>
          setBound(pick.referenceFor(threadId)?.result_id ?? "none")
        }
      >
        bind
      </button>
      <output>{bound}</output>
    </>
  );
}

async function expectBound(id: string) {
  await waitFor(() => {
    fireEvent.click(screen.getByText("bind"));
    expect(screen.getByText(id)).toBeTruthy();
  });
}

function renderThread(threadId: string, isLoading: boolean) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const tree = (loading: boolean) => (
    <QueryClientProvider client={client}>
      <PickProvider>
        <ThreadProbe threadId={threadId} isLoading={loading} />
      </PickProvider>
    </QueryClientProvider>
  );
  const view = render(tree(isLoading));
  return {
    ...view,
    rerender: (loading: boolean) => view.rerender(tree(loading)),
  };
}

// 2026-09-30: the latest candidate was only known from cards that mounted; a card collapsed or scrolled out of a
// virtualized list never registered, so the next question bound the wrong card or none.
describe("latest candidate per thread", () => {
  it("reads the thread's results and binds the newest finished one", async () => {
    api.listPickResults.mockResolvedValue([
      { ...result, id: "r-old", created_at: "2026-09-21T00:00:00Z" },
      { ...result, id: "r-new", created_at: "2026-09-22T00:00:00Z" },
      {
        ...result,
        id: "r-failed",
        run_status: "error",
        created_at: "2026-09-23T00:00:00Z",
      },
    ]);
    renderThread("t1", false);
    await expectBound("r-new");
    expect(api.listPickResults).toHaveBeenCalledWith("t1", expect.anything());
  });

  it("waits for the answer to finish, then reads again", async () => {
    api.listPickResults.mockResolvedValueOnce([result]);
    const view = renderThread("t1", true);
    fireEvent.click(screen.getByText("bind"));
    expect(screen.getByText("none")).toBeTruthy();
    expect(api.listPickResults).not.toHaveBeenCalled();
    view.rerender(false);
    await expectBound("r1");
    api.listPickResults.mockResolvedValueOnce([
      result,
      { ...result, id: "r2", created_at: "2026-09-22T00:00:00Z" },
    ]);
    view.rerender(true);
    view.rerender(false);
    await expectBound("r2");
  });
});
describe("personal candidate scope", () => {
  it("persists only a scoped reference for browser refresh", () => {
    render(
      <PickProvider>
        <Probe />
      </PickProvider>,
    );
    fireEvent.click(screen.getByText("show"));
    expect(sessionStorage.getItem('ggwork-pick:["alice","t1"]')).toContain(
      '"result_id":"r1"',
    );
    fireEvent.click(screen.getByText("close"));
    expect(sessionStorage.getItem('ggwork-pick:["alice","t1"]')).toContain(
      '"open":false',
    );
  });
  it("clears visible data when the authenticated user changes", () => {
    const view = render(
      <PickProvider>
        <Probe />
      </PickProvider>,
    );
    fireEvent.click(screen.getByText("show"));
    expect(screen.getByText("r1")).toBeTruthy();
    owner = "bob";
    view.rerender(
      <PickProvider>
        <Probe />
      </PickProvider>,
    );
    expect(screen.getByText("none")).toBeTruthy();
  });
});

it("fails closed visibly when a stored plural reference cannot be re-authorized", async () => {
  const { getPickResult } = await import("@/core/pick/api");
  const { useRestorePick } =
    await import("@/components/workspace/pick/pick-context");
  const { PickReferenceNotice } =
    await import("@/components/workspace/pick/pick-reference-notice");
  sessionStorage.setItem(
    `ggwork-pick:${JSON.stringify(["alice", "t1"])}`,
    JSON.stringify({
      pick_references: {
        version: "pick-references-v1",
        references: [
          { result_id: "gone", item_ids: ["i"] },
          { result_id: "other", item_ids: ["j"] },
        ],
      },
    }),
  );
  rs.mocked(getPickResult).mockRejectedValue(new Error("private source error"));
  function RestoreProbe() {
    useRestorePick("t1");
    const pick = usePickContext()!;
    return (
      <>
        <PickReferenceNotice threadId="t1" />
        <button
          onClick={() => {
            try {
              document.title =
                JSON.stringify(pick.contextFor("t1")) ?? "unbound";
            } catch {
              document.title = "blocked";
            }
          }}
        >
          Read restored context
        </button>
      </>
    );
  }
  render(
    <PickProvider>
      <RestoreProbe />
    </PickProvider>,
  );
  await screen.findByRole("alert");
  expect(screen.getByRole("alert").textContent).not.toContain("private source");
  fireEvent.click(screen.getByText("Read restored context"));
  expect(document.title).toBe("blocked");
  fireEvent.click(screen.getByRole("button", { name: "取消多批引用" }));
  fireEvent.click(screen.getByText("Read restored context"));
  expect(document.title).toBe("unbound");
});

it("blocks dispatch during plural restore and cancellation fences a late response", async () => {
  const { getPickResult } = await import("@/core/pick/api");
  const { useRestorePick } =
    await import("@/components/workspace/pick/pick-context");
  const { PickReferenceNotice } =
    await import("@/components/workspace/pick/pick-reference-notice");
  const refs = {
    version: "pick-references-v1",
    references: [
      { result_id: "a", item_ids: ["i"] },
      { result_id: "b", item_ids: ["j"] },
    ],
  };
  sessionStorage.setItem(
    `ggwork-pick:${JSON.stringify(["alice", "t1"])}`,
    JSON.stringify({ pick_references: refs }),
  );
  const completions: Array<(r: PickResult) => void> = [];
  rs.mocked(getPickResult).mockImplementation(
    () => new Promise<PickResult>((resolve) => completions.push(resolve)),
  );
  function Probe() {
    useRestorePick("t1");
    const pick = usePickContext()!;
    return (
      <>
        <PickReferenceNotice threadId="t1" />
        <button
          onClick={() => {
            try {
              document.title =
                JSON.stringify(pick.contextFor("t1")) ?? "unbound";
            } catch {
              document.title = "blocked";
            }
          }}
        >
          Dispatch restore
        </button>
      </>
    );
  }
  render(
    <PickProvider>
      <Probe />
    </PickProvider>,
  );
  await waitFor(() => expect(completions).toHaveLength(2));
  fireEvent.click(screen.getByText("Dispatch restore"));
  expect(document.title).toBe("blocked");
  fireEvent.click(screen.getByRole("button", { name: "取消多批引用" }));
  await act(async () => {
    completions.forEach((resolve, index) =>
      resolve({
        ...result,
        id: index === 0 ? "a" : "b",
        items: [
          { item_id: index === 0 ? "i" : "j" } as PickResult["items"][number],
        ],
      }),
    );
  });
  fireEvent.click(screen.getByText("Dispatch restore"));
  expect(document.title).toBe("unbound");
});
