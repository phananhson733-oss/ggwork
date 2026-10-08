import { afterEach, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import { CompletionStatus } from "@/components/workspace/pick/completion-status";
afterEach(cleanup);
it("renders only validated public status without audit claims", () => {
  render(
    <CompletionStatus
      metadata={{
        status: "partial",
        checker_version: "v1",
        checked_at: "2026-10-08T10:00:00Z",
        correction_count: 0,
      }}
    />,
  );
  expect(screen.getByText("部分完成：未确认内容请保留为待核对。")).toBeTruthy();
});
it("does not infer a checked status from legacy success or malformed metadata", () => {
  const { container } = render(
    <CompletionStatus
      metadata={{
        run_status: "success",
        status: "confirmed",
        facts: [{ claim: "secret internal assertion" }],
      }}
    />,
  );
  expect(container.textContent).toBe("");
});
