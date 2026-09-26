/**
 * The sidebar's pick links (P3-5): 选剧资料 is marked current on every
 * /workspace/pick-data page (any tab, any query), and nowhere else;
 * 我的选剧 the same on /workspace/picks. "Current" means both the selected
 * style (data-active, which the sidebar primitive paints) and aria-current.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import { SidebarProvider } from "@/components/ui/sidebar";
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

function renderNav() {
  return render(
    <SidebarProvider>
      <PickNav />
    </SidebarProvider>,
  );
}

function isCurrent(name: string): boolean {
  const element = link(name);
  const current = element.getAttribute("aria-current") === "page";
  // The selected style and the accessible state must never disagree.
  expect(element.getAttribute("data-active")).toBe(String(current));
  return current;
}

describe("PickNav", () => {
  it("shows the 选剧工作台 group label and names the navigation by it", () => {
    renderNav();
    expect(screen.getByText("选剧工作台")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "选剧工作台" })).toBeTruthy();
  });

  it("marks 选剧资料 current on the pick data page", () => {
    nav.pathname = "/workspace/pick-data";
    renderNav();
    expect(isCurrent("选剧资料")).toBe(true);
    expect(isCurrent("我的选剧")).toBe(false);
  });

  it("keeps the mark below the page path", () => {
    nav.pathname = "/workspace/pick-data/anything";
    renderNav();
    expect(isCurrent("选剧资料")).toBe(true);
  });

  it("marks 我的选剧 current on the picks page only", () => {
    nav.pathname = "/workspace/picks";
    renderNav();
    expect(isCurrent("我的选剧")).toBe(true);
    expect(isCurrent("选剧资料")).toBe(false);
  });

  it("marks nothing elsewhere", () => {
    for (const pathname of [
      "/workspace/chats",
      "/workspace/pick-database",
      "/workspace/picksmith",
    ]) {
      nav.pathname = pathname;
      renderNav();
      expect(isCurrent("选剧资料")).toBe(false);
      expect(isCurrent("我的选剧")).toBe(false);
      cleanup();
    }
  });

  it("links to both pick pages", () => {
    renderNav();
    expect(link("我的选剧").getAttribute("href")).toBe("/workspace/picks");
    expect(link("选剧资料").getAttribute("href")).toBe("/workspace/pick-data");
  });
});
