/**
 * The sidebar's pick links (P3-5): 选剧资料 is marked current on every
 * /workspace/pick-data page (any tab, any query), and nowhere else.
 * 我的选剧 and 定时任务 remain visible but only show a notice. "Current" means both the selected
 * style (data-active, which the sidebar primitive paints) and aria-current.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { toast } from "sonner";

import { SidebarProvider } from "@/components/ui/sidebar";
import { WorkspaceNavChatList } from "@/components/workspace/workspace-nav-chat-list";
import { PickNav } from "@/components/workspace/workspace-sidebar";
import { I18nProvider } from "@/core/i18n/context";

const nav = rs.hoisted(() => ({ pathname: "/workspace/chats" }));

rs.mock("sonner", () => ({ toast: { info: rs.fn() } }));
rs.mock("@/core/agents", () => ({
  useAgentsApiEnabled: () => ({ enabled: true, isLoading: false }),
}));

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
  rs.clearAllMocks();
});

function link(name: string): HTMLElement {
  return screen.getByRole("link", { name });
}

function renderNav() {
  document.cookie = "locale=zh-CN; path=/";
  return render(
    <I18nProvider initialLocale="zh-CN">
      <SidebarProvider>
        <WorkspaceNavChatList />
        <PickNav />
      </SidebarProvider>
    </I18nProvider>,
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
  });

  it("keeps the mark below the page path", () => {
    nav.pathname = "/workspace/pick-data/anything";
    renderNav();
    expect(isCurrent("选剧资料")).toBe(true);
  });

  it("opens 我的选剧 and marks its route current", () => {
    nav.pathname = "/workspace/picks";
    renderNav();
    expect(
      screen.getByRole("link", { name: "我的选剧" }).getAttribute("href"),
    ).toBe("/workspace/picks");
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
      cleanup();
    }
  });

  it("keeps 选剧资料 linked to its page", () => {
    renderNav();
    expect(link("选剧资料").getAttribute("href")).toBe("/workspace/pick-data");
  });

  it.each(["定时任务"])(
    "%s shows 暂未开放 without providing a destination",
    (name) => {
      renderNav();
      const entry = screen.getByRole("button", { name });
      expect(screen.queryByRole("link", { name })).toBeNull();
      expect(entry.getAttribute("href")).toBeNull();
      expect((entry as HTMLButtonElement).disabled).toBe(false);
      fireEvent.click(entry);
      expect(toast.info).toHaveBeenCalledWith("暂未开放");
    },
  );
});
