import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

let owner = "alice";
rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: owner } }),
}));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));

import { CandidateComparison } from "@/components/workspace/pick/candidate-comparison";
import {
  PickProvider,
  usePickContext,
} from "@/components/workspace/pick/pick-context";
import { fetch as fetcher } from "@/core/api/fetcher";
import { type SaveCommand } from "@/core/pick/api";
import { pickResultSchema } from "@/core/pick/types";

import { PickReferenceNotice } from "@/components/workspace/pick/pick-reference-notice";

import payload from "../../../core/pick/fixtures/backend-result.json";

const first = pickResultSchema.parse({
  ...payload,
  id: "first",
  run_status: "success",
  items: payload.items.slice(0, 1),
});
const second = pickResultSchema.parse({
  ...first,
  id: "second",
  catalog_batch_id: "batch-two",
  ranking_version: "ranking-two",
  items: [
    { ...first.items[0], item_id: "second-item", reason: "Second evidence" },
    {
      ...first.items[0],
      identity: '["other", "2", "en"]',
      title: "Other drama",
      item_id: "other-item",
    },
  ],
});
const third = { ...first, id: "third", created_at: "2026-10-08T00:00:00Z" };
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
const clients: QueryClient[] = [];
function Harness({ threadId }: { threadId: string }) {
  const pick = usePickContext()!;
  return (
    <>
      <button onClick={() => pick.observe(third)}>Third arrives</button>
      <button
        onClick={() => {
          document.title = JSON.stringify(pick.referenceFor(threadId));
        }}
      >
        Read reference
      </button>
      <button
        onClick={() => {
          document.title =
            JSON.stringify(pick.contextFor(threadId)) ?? "unbound";
        }}
      >
        Read turn context
      </button>
      <PickReferenceNotice threadId={threadId} />
      <CandidateComparison
        key={`${owner}:${threadId}`}
        threadId={threadId}
        onUseBatch={() => undefined}
      />
    </>
  );
}
function mount(threadId = "t") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const view = (thread = threadId) => (
    <QueryClientProvider client={client}>
      <PickProvider>
        <Harness threadId={thread} />
      </PickProvider>
    </QueryClientProvider>
  );
  return { client, view, ...render(view()) };
}
async function chooseBatches() {
  await screen.findAllByRole("option", { name: /first/ });
  fireEvent.change(screen.getByLabelText("第一批候选"), {
    target: { value: "first" },
  });
  fireEvent.change(screen.getByLabelText("第二批候选"), {
    target: { value: "second" },
  });
  await screen.findByText("Second evidence");
}
beforeEach(() => {
  rs.mocked(fetcher).mockReset();
  rs.mocked(fetcher).mockImplementation(async (url) => {
    const path =
      typeof url === "string" ? url : url instanceof URL ? url.href : url.url;
    if (path.includes("?thread_id="))
      return response({ results: [first, second] });
    return response(path.endsWith("/first") ? first : second);
  });
});
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  owner = "alice";
  sessionStorage.clear();
});

describe("comparison through authenticated HTTP", () => {
  it("requires two explicit batches and source selection; third arrivals do not rewrite evidence or chat context", async () => {
    const { client } = mount();
    expect(screen.getByLabelText<HTMLSelectElement>("第一批候选").value).toBe(
      "",
    );
    await chooseBatches();
    expect(
      screen.getByLabelText<HTMLSelectElement>("采用来源：Feed Drama 1").value,
    ).toBe("");
    fireEvent.change(screen.getByLabelText("采用来源：Feed Drama 1"), {
      target: { value: "second" },
    });
    fireEvent.click(screen.getByText("Third arrives"));
    client.setQueryData(["pick-results", "alice", "t"], [third, first, second]);
    fireEvent.click(screen.getByText("Read reference"));
    expect(document.title).toContain('"result_id":"third"');
    expect(screen.getByLabelText<HTMLSelectElement>("第一批候选").value).toBe(
      "first",
    );
    expect(screen.getByText("Second evidence")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "以第二批继续对话" }));
    fireEvent.click(screen.getByText("Read reference"));
    expect(JSON.parse(document.title)).toEqual({
      result_id: "second",
      item_ids: ["second-item"],
    });
  });
  it("keeps per-batch partial receipts and retries only the unknown command with the original id", async () => {
    const commands: SaveCommand[] = [];
    let secondCommitted = false;
    rs.mocked(fetcher).mockImplementation(async (url, init) => {
      const path =
        typeof url === "string" ? url : url instanceof URL ? url.href : url.url;
      if (init?.method === "POST") {
        const command = JSON.parse(init.body as string) as SaveCommand;
        commands.push(command);
        if (command.result_id === "second" && !secondCommitted)
          throw new TypeError("lost response");
        return response({
          request_id: command.request_id,
          saved: [
            {
              id: command.result_id,
              identity: "fixture",
              status: "created",
              version: 1,
            },
          ],
        });
      }
      if (path.includes("/commands/")) return response({}, 404);
      if (path.includes("?thread_id="))
        return response({ results: [first, second] });
      return response(path.endsWith("/first") ? first : second);
    });
    mount();
    await chooseBatches();
    fireEvent.change(screen.getByLabelText("采用来源：Feed Drama 1"), {
      target: { value: "first" },
    });
    fireEvent.change(screen.getByLabelText("采用来源：Other drama"), {
      target: { value: "second" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "保存所选来源（2 部）" }),
    );
    await screen.findByText(/第二批：保存结果尚未确认/);
    expect(screen.getByText(/第一批：保存完成：新存入 1 部/)).toBeTruthy();
    expect(commands.map((command) => command.result_id)).toEqual([
      "first",
      "second",
    ]);
    secondCommitted = true;
    fireEvent.click(screen.getByRole("button", { name: "重试第二批保存" }));
    await screen.findByText(/第二批：保存完成：新存入 1 部/);
    expect(commands).toHaveLength(3);
    expect(commands[2]).toEqual(commands[1]);
    expect(screen.getByRole("link", { name: "查看我的选剧" })).toBeTruthy();
  });
  it("aborts comparison reads and clears choices after owner or thread changes", async () => {
    const signals: AbortSignal[] = [];
    rs.mocked(fetcher).mockImplementation(async (url, init) => {
      if (
        (typeof url === "string"
          ? url
          : url instanceof URL
            ? url.href
            : url.url
        ).includes("?thread_id=")
      )
        return response({ results: [first, second] });
      signals.push(init!.signal!);
      return new Promise<Response>(() => undefined);
    });
    const mounted = mount();
    await screen.findAllByRole("option", { name: /first/ });
    fireEvent.change(screen.getByLabelText("第一批候选"), {
      target: { value: "first" },
    });
    fireEvent.change(screen.getByLabelText("第二批候选"), {
      target: { value: "second" },
    });
    await waitFor(() => expect(signals).toHaveLength(2));
    owner = "bob";
    mounted.rerender(mounted.view());
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    expect(screen.getByLabelText<HTMLSelectElement>("第一批候选").value).toBe(
      "",
    );
    mounted.rerender(mounted.view("another-thread"));
    await screen.findByText(/当前对话还没有两批/);
    expect(screen.queryByRole("option", { name: /first/ })).toBeNull();
  });
  it("refuses a fetched snapshot belonging to another thread", async () => {
    rs.mocked(fetcher).mockImplementation(async (url) => {
      if (
        (typeof url === "string"
          ? url
          : url instanceof URL
            ? url.href
            : url.url
        ).includes("?thread_id=")
      )
        return response({ results: [first, second] });
      return response(
        (typeof url === "string"
          ? url
          : url instanceof URL
            ? url.href
            : url.url
        ).endsWith("/first")
          ? first
          : { ...second, thread_id: "private-other-thread" },
      );
    });
    mount();
    await screen.findAllByRole("option", { name: /first/ });
    fireEvent.change(screen.getByLabelText("第一批候选"), {
      target: { value: "first" },
    });
    fireEvent.change(screen.getByLabelText("第二批候选"), {
      target: { value: "second" },
    });
    await screen.findByText(/无法核实两批候选/);
    expect(screen.queryByText("Second evidence")).toBeNull();
  });
  it("recovers a committed write from its receipt without reposting and keeps later changed payloads independent", async () => {
    const commands: SaveCommand[] = [];
    let receiptId = "";
    rs.mocked(fetcher).mockImplementation(async (url, init) => {
      const path =
        typeof url === "string" ? url : url instanceof URL ? url.href : url.url;
      if (init?.method === "POST") {
        const command = JSON.parse(init.body as string) as SaveCommand;
        commands.push(command);
        receiptId = command.request_id;
        throw new TypeError("response lost after commit");
      }
      if (path.includes("/commands/"))
        return response({
          request_id: receiptId,
          saved: [
            {
              id: "saved",
              identity: first.items[0]!.identity,
              status: "existing",
              version: 1,
            },
          ],
        });
      if (path.includes("?thread_id="))
        return response({ results: [first, second] });
      return response(path.endsWith("/first") ? first : second);
    });
    mount();
    await chooseBatches();
    fireEvent.change(screen.getByLabelText("采用来源：Feed Drama 1"), {
      target: { value: "second" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "保存所选来源（1 部）" }),
    );
    await screen.findByText(/第二批：保存完成：1 部已在清单/);
    expect(commands[0]?.item_ids).toEqual(["second-item"]);
    fireEvent.click(
      screen.getByRole("button", { name: "保存所选来源（1 部）" }),
    );
    expect(commands).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("比较保存备注"), {
      target: { value: "updated note" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "保存所选来源（1 部）" }),
    );
    await waitFor(() => expect(commands).toHaveLength(2));
    expect(commands[1]?.request_id).not.toBe(commands[0]?.request_id);
    expect(commands[1]?.note).toBe("updated note");
  });
  it("fences a save response that arrives after switching owners", async () => {
    let resolveSave!: (value: Response) => void;
    let saveSignal: AbortSignal | undefined;
    let command: SaveCommand | undefined;
    rs.mocked(fetcher).mockImplementation(async (url, init) => {
      const path =
        typeof url === "string" ? url : url instanceof URL ? url.href : url.url;
      if (init?.method === "POST") {
        command = JSON.parse(init.body as string) as SaveCommand;
        saveSignal = init.signal ?? undefined;
        return new Promise<Response>((resolve) => {
          resolveSave = resolve;
        });
      }
      if (path.includes("?thread_id="))
        return response({ results: [first, second] });
      return response(path.endsWith("/first") ? first : second);
    });
    const mounted = mount();
    await chooseBatches();
    fireEvent.change(screen.getByLabelText("采用来源：Feed Drama 1"), {
      target: { value: "first" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "保存所选来源（1 部）" }),
    );
    await waitFor(() => expect(command).toBeTruthy());
    owner = "bob";
    mounted.rerender(mounted.view());
    expect(saveSignal?.aborted).toBe(true);
    resolveSave(
      response({
        request_id: command!.request_id,
        saved: [
          { id: "saved", identity: "fixture", status: "created", version: 1 },
        ],
      }),
    );
    await screen.findAllByRole("option", { name: /first/ });
    expect(screen.queryByText(/保存完成/)).toBeNull();
  });
});

it("binds both selected groups only on explicit action and freezes that choice", async () => {
  mount();
  await chooseBatches();
  const button = screen.getByRole<HTMLButtonElement>("button", {
    name: "引用所选两批到对话",
  });
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("采用来源：Feed Drama 1"), {
    target: { value: "first" },
  });
  fireEvent.change(screen.getByLabelText("采用来源：Other drama"), {
    target: { value: "second" },
  });
  fireEvent.click(screen.getByText("Read turn context"));
  expect(document.title).toBe("unbound");
  expect(button.disabled).toBe(true);
  fireEvent.click(
    screen.getByRole("checkbox", { name: "引用第一批：Feed Drama 1" }),
  );
  fireEvent.click(
    screen.getByRole("checkbox", { name: "引用第二批：Feed Drama 1" }),
  );
  fireEvent.click(button);
  fireEvent.click(screen.getByText("Read turn context"));
  const bound = JSON.parse(document.title);
  expect(bound.pick_references.version).toBe("pick-references-v1");
  expect(bound.pick_references.references).toEqual([
    { result_id: "first", item_ids: [first.items[0]!.item_id] },
    { result_id: "second", item_ids: ["second-item"] },
  ]);
  fireEvent.change(screen.getByLabelText("采用来源：Feed Drama 1"), {
    target: { value: "second" },
  });
  fireEvent.click(screen.getByText("Read turn context"));
  expect(JSON.parse(document.title)).toEqual(bound);
  expect(screen.getByText("本轮引用 2 批候选")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "取消多批引用" }));
  fireEvent.click(screen.getByText("Read turn context"));
  expect(document.title).toBe("unbound");
  expect(screen.queryByLabelText("本轮候选引用")).toBeNull();
});
