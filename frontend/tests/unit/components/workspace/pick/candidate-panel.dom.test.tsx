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

import { CandidatePanel } from "@/components/workspace/pick/candidate-panel";
import {
  PickProvider,
  usePickContext,
} from "@/components/workspace/pick/pick-context";
import { fetch as fetcher } from "@/core/api/fetcher";
import { pickResultSchema } from "@/core/pick/types";

import payload from "../../../core/pick/fixtures/backend-result.json";

const result = pickResultSchema.parse({ ...payload, run_status: "success" });
const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });
function OpenPanel() {
  const pick = usePickContext()!;
  return (
    <>
      <button
        onClick={() =>
          pick.show(
            result,
            result.items.map((item) => item.item_id),
          )
        }
      >
        open fixture
      </button>
      <CandidatePanel />
    </>
  );
}
const clients: QueryClient[] = [];
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const view = () => (
    <QueryClientProvider client={client}>
      <PickProvider>
        <OpenPanel />
      </PickProvider>
    </QueryClientProvider>
  );
  return { client, view, ...render(view()) };
}
beforeEach(() => {
  rs.mocked(fetcher).mockReset();
});
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  owner = "alice";
  sessionStorage.clear();
});

describe("candidate save through mock HTTP", () => {
  it("recovers a committed receipt after a lost response and links to the list", async () => {
    rs.mocked(fetcher).mockImplementation(async (url, init) => {
      if (init?.method === "POST") throw new TypeError("lost response");
      if (
        (typeof url === "string"
          ? url
          : url instanceof URL
            ? url.href
            : url.url
        ).includes("/commands/")
      )
        return response({
          request_id: (typeof url === "string"
            ? url
            : url instanceof URL
              ? url.href
              : url.url
          )
            .split("/")
            .at(-1),
          saved: [
            { id: "s1", identity: "fixture", status: "created", version: 1 },
          ],
        });
      if (
        (typeof url === "string"
          ? url
          : url instanceof URL
            ? url.href
            : url.url
        ).endsWith("/notes")
      )
        return response({}, 404);
      return response(result);
    });
    mount();
    fireEvent.click(screen.getByText("open fixture"));
    fireEvent.click(await screen.findByRole("button", { name: /保存选中/ }));
    await screen.findByRole("link", { name: "查看我的选剧" });
    expect(screen.getByText(/新存入 1 部/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
  it("retries unknown outcomes with the same command, and uses a new command for changed input", async () => {
    const commands: { request_id: string; note: string }[] = [];
    rs.mocked(fetcher).mockImplementation(async (url, init) => {
      if (init?.method === "POST") {
        commands.push(JSON.parse(init.body as string));
        throw new TypeError("lost response");
      }
      if (
        (typeof url === "string"
          ? url
          : url instanceof URL
            ? url.href
            : url.url
        ).includes("/commands/") ||
        (typeof url === "string"
          ? url
          : url instanceof URL
            ? url.href
            : url.url
        ).endsWith("/notes")
      )
        return response({}, 404);
      return response(result);
    });
    mount();
    fireEvent.click(screen.getByText("open fixture"));
    fireEvent.click(await screen.findByRole("button", { name: /保存选中/ }));
    await screen.findByText(/保存结果尚未确认/);
    fireEvent.click(screen.getByRole("button", { name: /保存选中/ }));
    await waitFor(() => expect(commands).toHaveLength(2));
    await screen.findByText(/保存结果尚未确认/);
    expect(commands[0]).toEqual(commands[1]);
    fireEvent.change(screen.getByLabelText("保存备注"), {
      target: { value: "new note" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保存选中/ }));
    await waitFor(() => expect(commands).toHaveLength(3));
    expect(commands[2]?.request_id).not.toBe(commands[0]?.request_id);
    expect(commands[2]?.note).toBe("new note");
  });
  it("scopes result polling by owner and aborts old reads", async () => {
    const signals: AbortSignal[] = [];
    rs.mocked(fetcher).mockImplementation(async (url, init) => {
      if (
        (typeof url === "string"
          ? url
          : url instanceof URL
            ? url.href
            : url.url
        ).endsWith("/notes")
      )
        return response({}, 404);
      signals.push(init!.signal!);
      return new Promise<Response>(() => undefined);
    });
    const { client, rerender, view } = mount();
    fireEvent.click(screen.getByText("open fixture"));
    await waitFor(() => expect(signals).toHaveLength(1));
    owner = "bob";
    rerender(view());
    expect(signals[0]?.aborted).toBe(true);
    fireEvent.click(screen.getByText("open fixture"));
    await waitFor(() => expect(signals).toHaveLength(2));
    expect(
      client.getQueryCache().find({
        queryKey: ["pick-panel-result", "bob", result.thread_id, result.id],
      }),
    ).toBeTruthy();
  });
  it("opens comparison from the candidate panel with explicit empty batch selectors", async () => {
    rs.mocked(fetcher).mockImplementation(async (url) => {
      const path =
        typeof url === "string" ? url : url instanceof URL ? url.href : url.url;
      if (path.includes("?thread_id="))
        return response({
          results: [result, { ...result, id: "other-batch" }],
        });
      if (path.endsWith("/notes")) return response({}, 404);
      return response(result);
    });
    mount();
    fireEvent.click(screen.getByText("open fixture"));
    fireEvent.click(screen.getByRole("button", { name: "比较两批候选" }));
    await screen.findByRole("dialog", { name: "比较两批候选" });
    expect(screen.getByLabelText<HTMLSelectElement>("第一批候选").value).toBe(
      "",
    );
    expect(screen.getByLabelText<HTMLSelectElement>("第二批候选").value).toBe(
      "",
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭比较" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
