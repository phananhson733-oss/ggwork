import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
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

// Network and authenticated session boundaries; all pick components/API parsers are real.
let owner = "alice";
rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: owner } }),
}));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));

import { MySelections } from "@/components/workspace/pick/my-selections";
import { SelectionDraftProvider } from "@/components/workspace/pick/selection-drafts";
import { fetch as fetcher } from "@/core/api/fetcher";
import type { SavedPick } from "@/core/pick/api";

import payload from "../../../core/pick/fixtures/backend-result.json";

const row: SavedPick = {
  id: "s1",
  identity: "fixture",
  source_result_id: payload.id,
  source_item_id: payload.items[0]!.item_id,
  snapshot_json: payload.items[0]! as SavedPick["snapshot_json"],
  note: "original",
  state: "selected",
  version: 1,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
};
const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });
let rows: SavedPick[];
const clients: QueryClient[] = [];
beforeEach(() => {
  rows = [{ ...row }];
  rs.mocked(fetcher)
    .mockReset()
    .mockImplementation(async () => response({ selections: rows }));
});
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  owner = "alice";
});
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  const view = () => (
    <QueryClientProvider client={client}>
      <SelectionDraftProvider>
        <MySelections />
      </SelectionDraftProvider>
    </QueryClientProvider>
  );
  return { client, ...render(view()), view };
}
async function noteInput() {
  return await screen.findByLabelText<HTMLTextAreaElement>("个人备注");
}

describe("personal selections through mock HTTP", () => {
  it("preserves a dirty note across a newer read and explicitly reconciles the conflict", async () => {
    const { client } = mount();
    fireEvent.change(await noteInput(), { target: { value: "my draft" } });
    rows = [{ ...row, version: 2, note: "server edit" }];
    await act(() =>
      client.invalidateQueries({ queryKey: ["pick-selections", "alice"] }),
    );
    expect((await noteInput()).value).toBe("my draft");
    expect(screen.getByText(/服务器备注：server edit/)).toBeTruthy();
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "保存备注" })
        .disabled,
    ).toBe(true);
    fireEvent.click(
      screen.getByRole("button", { name: "保留我的备注并采用新版本" }),
    );
    rs.mocked(fetcher).mockImplementation(async (_url, init) => {
      if (init?.method === "PATCH") {
        expect(JSON.parse(init.body as string)).toMatchObject({
          expected_version: 2,
          note: "my draft",
        });
        rows = [{ ...rows[0]!, version: 3, note: "my draft" }];
        return response({
          request_id: "receipt",
          id: "s1",
          version: 3,
          state: "selected",
        });
      }
      return response({ selections: rows });
    });
    fireEvent.click(screen.getByRole("button", { name: "保存备注" }));
    await screen.findByText("备注已保存");
    expect((await noteInput()).value).toBe("my draft");
  });
  it("keeps the same command after an unknown update and preserves the input", async () => {
    mount();
    fireEvent.change(await noteInput(), { target: { value: "retry draft" } });
    const commands: string[] = [];
    rs.mocked(fetcher).mockImplementation(async (_url, init) => {
      if (init?.method === "PATCH") {
        commands.push(init.body as string);
        throw new TypeError("Failed to fetch");
      }
      return response({ selections: rows });
    });
    fireEvent.click(screen.getByRole("button", { name: "保存备注" }));
    await screen.findByRole("alert");
    expect((await noteInput()).value).toBe("retry draft");
    fireEvent.click(screen.getByRole("button", { name: "保存备注" }));
    await waitFor(() => expect(commands).toHaveLength(2));
    expect(commands[0]).toBe(commands[1]);
  });
  it("shows a removal conflict and does not drop local notes", async () => {
    mount();
    fireEvent.change(await noteInput(), {
      target: { value: "keep this draft" },
    });
    rs.mocked(fetcher).mockImplementation(async (_url, init) => {
      if (init?.method === "PATCH") {
        rows = [{ ...row, version: 2, note: "elsewhere" }];
        return response({ detail: "记录版本已变化，请刷新后重试" }, 409);
      }
      return response({ selections: rows });
    });
    fireEvent.click(screen.getByRole("button", { name: "移出清单" }));
    await screen.findByText(/服务器备注：elsewhere/);
    expect((await noteInput()).value).toBe("keep this draft");
  });
  it("does not lose an edited snapshot if another tab removed it", async () => {
    const { client } = mount();
    fireEvent.change(await noteInput(), {
      target: { value: "keep after removal" },
    });
    rows = [];
    await act(() =>
      client.invalidateQueries({ queryKey: ["pick-selections", "alice"] }),
    );
    expect((await noteInput()).value).toBe("keep after removal");
    expect(screen.getByText(/已在其他位置移出清单/)).toBeTruthy();
  });
  it("clears drafts on owner change and never shows an empty list on read failure", async () => {
    const { rerender, view } = mount();
    fireEvent.change(await noteInput(), {
      target: { value: "private alice note" },
    });
    owner = "bob";
    rs.mocked(fetcher).mockImplementation(async () =>
      response({ detail: "服务暂不可用" }, 503),
    );
    rerender(view());
    await screen.findByRole("alert");
    expect(screen.queryByDisplayValue("private alice note")).toBeNull();
    expect(screen.queryByText("还没有保存的剧目。")).toBeNull();
  });
  it("retains the saved snapshot if its historical source is gone and labels reference export", async () => {
    mount();
    await noteInput();
    expect(screen.getByRole("button", { name: "导出参考清单" })).toBeTruthy();
    expect(screen.getByText("仅作选剧参考，未完成排期执行核对。")).toBeTruthy();
    rs.mocked(fetcher).mockImplementation(async () =>
      response({ detail: "历史来源已清理" }, 410),
    );
    fireEvent.click(screen.getByRole("button", { name: "查看来源候选" }));
    await screen.findByText(/历史来源已清理/);
    expect((await noteInput()).value).toBe("original");
    expect(screen.getByText(row.snapshot_json.title)).toBeTruthy();
  });
});

function RouteSwitch() {
  const [shown, setShown] = useState(true);
  return (
    <>
      <button onClick={() => setShown(!shown)}>toggle route</button>
      {shown && <MySelections />}
    </>
  );
}

describe("workspace lifetime drafts", () => {
  it("recovers original-version edits after route unmount and purges them on an off-route account change", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    clients.push(client);
    const view = () => (
      <QueryClientProvider client={client}>
        <SelectionDraftProvider>
          <RouteSwitch />
        </SelectionDraftProvider>
      </QueryClientProvider>
    );
    const { rerender } = render(view());
    fireEvent.change(await noteInput(), {
      target: { value: "private recovered draft" },
    });
    fireEvent.click(screen.getByText("toggle route"));
    rows = [{ ...row, version: 2, note: "new server revision" }];
    fireEvent.click(screen.getByText("toggle route"));
    await screen.findByText(/服务器备注：new server revision/);
    expect((await noteInput()).value).toBe("private recovered draft");
    expect(screen.getByText(/本地编辑基于版本 1/)).toBeTruthy();
    fireEvent.click(screen.getByText("toggle route"));
    owner = "bob";
    rerender(view());
    owner = "alice";
    rerender(view());
    // The owner-keyed provider resets RouteSwitch too; old Alice draft cannot return.
    expect((await noteInput()).value).toBe("new server revision");
    expect(screen.queryByText(/已恢复本次工作区内/)).toBeNull();
  });
});
