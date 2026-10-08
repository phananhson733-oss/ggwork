import { afterEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useState } from "react";

let owner = "alice";
rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: owner } }),
}));
rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
import { PlanDraftProvider } from "@/components/workspace/pick/plans/plan-drafts";
import { PlanEditor } from "@/components/workspace/pick/plans/plan-editor";
import { PlansWorkspace } from "@/components/workspace/pick/plans/plans-workspace";
import { fetch as fetcher } from "@/core/api/fetcher";
import { planSchema } from "@/core/pick/completion-types";

import fixture from "../../../core/pick/fixtures/completion-v1.json";
afterEach(() => {
  cleanup();
  owner = "alice";
  rs.mocked(fetcher).mockReset();
});
it("preserves unsaved plan and base version through workspace back/forward but clears them on owner change", () => {
  const plan = planSchema.parse(fixture.plan);
  function Harness() {
    const [show, setShow] = useState(true);
    return (
      <PlanDraftProvider>
        <button onClick={() => setShow(!show)}>切换页面</button>
        {show && <PlanEditor key={owner} initial={plan} />}
      </PlanDraftProvider>
    );
  }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const mounted = render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByLabelText("计划名称"), {
    target: { value: "private draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: "切换页面" }));
  fireEvent.click(screen.getByRole("button", { name: "切换页面" }));
  expect(screen.getByLabelText<HTMLInputElement>("计划名称").value).toBe(
    "private draft",
  );
  owner = "bob";
  mounted.rerender(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
  expect(screen.getByLabelText<HTMLInputElement>("计划名称").value).toBe(
    plan.title,
  );
});
it("does not display a prior owner's late plan read", async () => {
  let finish!: (response: Response) => void;
  rs.mocked(fetcher)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce(
      Response.json({ detail: "not found" }, { status: 404 }),
    );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = () => (
    <QueryClientProvider client={client}>
      <PlansWorkspace planId={fixture.plan.id} />
    </QueryClientProvider>
  );
  const mounted = render(view());
  owner = "bob";
  mounted.rerender(view());
  await act(async () =>
    finish(Response.json({ ...fixture.plan, title: "ALICE ONLY" })),
  );
  await screen.findByText("排期读取失败或无权访问。");
  expect(screen.queryByDisplayValue("ALICE ONLY")).toBeNull();
  client.clear();
});
