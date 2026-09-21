import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: "alice" } }),
}));
rs.mock("@/core/pick/api", () => ({
  getPickResult: rs.fn(),
  savePickSelection: rs.fn(),
}));

import { PickToolCard } from "@/components/workspace/pick/pick-tool-card";
import { getPickResult, savePickSelection } from "@/core/pick/api";

afterEach(() => {
  cleanup();
  rs.mocked(getPickResult).mockReset();
  rs.mocked(savePickSelection).mockReset();
});
describe("chat save confirmation", () => {
  it("saves only after one explicit confirmation and preserves the command on retry", async () => {
    rs.mocked(getPickResult).mockResolvedValue({
      id: "r1",
      thread_id: "t1",
      run_id: "run1",
      run_status: "success",
      catalog_batch_id: "b1",
      knowledge_batch_id: null,
      rule_version: "v1",
      ranking_version: "v1",
      conditions: { limit: 1, exclude_selected: true },
      created_at: "2026-09-21T00:00:00Z",
      items: [
        {
          item_id: "i1",
          identity: "source/1",
          title: "待保存的剧",
          theater: "Example",
          language: "en",
          availability: "active",
          reason: "匹配",
          warnings: [],
          evidence: [],
        },
      ],
    });
    rs.mocked(savePickSelection).mockRejectedValueOnce(new Error("offline"));
    rs.mocked(savePickSelection).mockResolvedValueOnce({
      request_id: "receipt",
      saved: [
        { id: "s1", identity: "source/1", status: "created", version: 1 },
      ],
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <PickToolCard
          threadId="t1"
          result={{
            result_id: "r1",
            item_ids: ["i1"],
            requires_confirmation: true,
            note: "下周准备剪辑",
          }}
        />
      </QueryClientProvider>,
    );
    await screen.findByText("待保存的剧");
    expect(screen.getByText(/下周准备剪辑/)).toBeTruthy();
    expect(savePickSelection).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认保存（1）" }));
    await screen.findByText("offline");
    fireEvent.click(screen.getByRole("button", { name: "确认保存（1）" }));
    await waitFor(() => expect(savePickSelection).toHaveBeenCalledTimes(2));
    expect(rs.mocked(savePickSelection).mock.calls[0]?.[0]).toEqual(
      rs.mocked(savePickSelection).mock.calls[1]?.[0],
    );
    expect(rs.mocked(savePickSelection).mock.calls[0]?.[0]).toMatchObject({
      result_id: "r1",
      item_ids: ["i1"],
      note: "下周准备剪辑",
    });
  });
  it("does not present a failed tool response as permanently running", () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <PickToolCard result="Tool failed" threadId="t1" />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("alert").textContent).toContain("未完成");
  });
});
