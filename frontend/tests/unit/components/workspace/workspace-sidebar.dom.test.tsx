/**
 * The sidebar's pick links (P3-5): 选剧资料 is marked current on every
 * /workspace/pick-data page (any tab, any query), and nowhere else.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import { PickNav } from "@/components/workspace/workspace-sidebar";

const nav = rs.hoisted(() => ({ pathname: "/workspace/chats" }));

rs.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: rs.fn(), replace: rs.fn(), refresh: rs.fn() }),
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams(),
}));

rs.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: ReactNode;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

afterEach(() => {
  cleanup();
});

function link(name: string): HTMLElement {
  return screen.getByRole("link", { name });
}

describe("PickNav", () => {
  it("marks 选剧资料 current on the pick data page", () => {
    nav.pathname = "/workspace/pick-data";
    render(<PickNav />);
    expect(link("选剧资料").getAttribute("aria-current")).toBe("page");
    expect(link("选剧资料").classList.contains("bg-accent")).toBe(true);
    expect(link("我的选剧").getAttribute("aria-current")).toBeNull();
  });

  it("keeps the mark below the page path", () => {
    nav.pathname = "/workspace/pick-data/anything";
    render(<PickNav />);
    expect(link("选剧资料").getAttribute("aria-current")).toBe("page");
  });

  it("marks nothing elsewhere", () => {
    for (const pathname of [
      "/workspace/chats",
      "/workspace/picks",
      "/workspace/pick-database",
    ]) {
      nav.pathname = pathname;
      render(<PickNav />);
      expect(link("选剧资料").getAttribute("aria-current")).toBeNull();
      expect(link("选剧资料").classList.contains("bg-accent")).toBe(false);
      cleanup();
    }
  });

  it("links to both pick pages", () => {
    render(<PickNav />);
    expect(link("我的选剧").getAttribute("href")).toBe("/workspace/picks");
    expect(link("选剧资料").getAttribute("href")).toBe("/workspace/pick-data");
  });
});
