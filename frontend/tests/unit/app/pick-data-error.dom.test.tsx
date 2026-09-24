/**
 * The pick data board's error boundary (P3-5): a fixed sentence, the digest
 * the server logged under, and a retry. The error's own message never shows:
 * it can carry SQL or values.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { PropsWithChildren } from "react";

import PickDataError from "@/app/workspace/pick-data/error";

rs.mock("@/components/workspace/workspace-container", () => ({
  WorkspaceContainer: ({ children }: PropsWithChildren) => (
    <div>{children}</div>
  ),
  WorkspaceHeader: () => <div />,
  WorkspaceBody: ({ children }: PropsWithChildren) => <main>{children}</main>,
}));

afterEach(() => {
  cleanup();
});

function failure(
  message: string,
  digest?: string,
): Error & { digest?: string } {
  return Object.assign(new Error(message), digest ? { digest } : {});
}

describe("pick-data error.tsx", () => {
  it("shows the fixed text and the digest, never the message", () => {
    const { container } = render(
      <PickDataError
        error={failure("SELECT secret FROM pickm_v000007.rs_rows", "d-4242")}
        reset={() => undefined}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain(
      "选剧资料暂时打不开",
    );
    expect(container.textContent).toContain("d-4242");
    expect(container.textContent).not.toContain("secret");
    expect(container.textContent).not.toContain("pickm_v");
  });

  it("leaves the digest line out when there is none", () => {
    const { container } = render(
      <PickDataError error={failure("boom")} reset={() => undefined} />,
    );
    expect(container.textContent).not.toContain("错误编号：");
    expect(container.textContent).not.toContain("boom");
  });

  it("retries with reset", () => {
    const reset = rs.fn();
    render(<PickDataError error={failure("boom", "d-1")} reset={reset} />);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
