import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

let owner = "alice";
rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: owner } }),
}));

import {
  PickProvider,
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
