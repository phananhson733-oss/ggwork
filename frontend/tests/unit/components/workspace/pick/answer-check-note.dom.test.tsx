import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: "alice" } }),
}));
rs.mock("@/core/pick/api", () => ({ listPickAnswerChecks: rs.fn() }));
rs.mock("@/components/workspace/pick/pick-context", () => ({
  usePickContext: () => ({}),
}));

import { PickAnswerCheckNote } from "@/components/workspace/pick/answer-check-note";
import { listPickAnswerChecks } from "@/core/pick/api";

afterEach(() => {
  cleanup();
  rs.mocked(listPickAnswerChecks).mockReset();
});

const check = (message_id: string, notes: string[]) => ({
  message_id,
  run_id: "r1",
  notes,
  created_at: "2026-10-05T00:00:00+00:00",
});

function renderNote(messageId: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PickAnswerCheckNote threadId="t1" messageId={messageId} runId="r1" />
    </QueryClientProvider>,
  );
}

// Evaluation batch 2 (2026-10-05): a clean answer now has an empty check, so
// the card tells "checked, nothing found" from "not checked" and "read failed".
describe("answer check note states", () => {
  it("warns with the notes of a flagged answer", async () => {
    rs.mocked(listPickAnswerChecks).mockResolvedValue([
      check("m1", ["《Fake》不在本轮查询结果中"]),
    ]);
    renderNote("m1");
    expect(
      (await screen.findByTestId("pick-answer-check")).textContent,
    ).toContain("《Fake》不在本轮查询结果中");
  });
  it("says a clean answer was checked, and only what was checked", async () => {
    rs.mocked(listPickAnswerChecks).mockResolvedValue([check("m1", [])]);
    renderNote("m1");
    const clean = await screen.findByTestId("pick-answer-check-clean");
    expect(clean.textContent).toContain("剧名、保存与发布说法");
    expect(screen.queryByTestId("pick-answer-check")).toBeNull();
  });
  it("shows nothing for an answer without a check", async () => {
    rs.mocked(listPickAnswerChecks).mockResolvedValue([check("m1", [])]);
    renderNote("m2");
    await waitFor(() => expect(listPickAnswerChecks).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByTestId("pick-answer-check-clean")).toBeNull();
    expect(screen.queryByTestId("pick-answer-check")).toBeNull();
    expect(screen.queryByTestId("pick-answer-check-unavailable")).toBeNull();
  });
  it("says the check could not be read instead of looking clean", async () => {
    rs.mocked(listPickAnswerChecks).mockRejectedValue(new Error("502"));
    renderNote("m1");
    expect(
      (await screen.findByTestId("pick-answer-check-unavailable")).textContent,
    ).toContain("回答核对暂不可用");
  });
});
