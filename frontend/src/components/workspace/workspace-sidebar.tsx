"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  Sidebar,
  SidebarHeader,
  SidebarContent,
  SidebarFooter,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";

import { WorkspaceChannelsList } from "./channels/workspace-channels-list";
import { ProjectsSection } from "./projects-section";
import { RecentChatList } from "./recent-chat-list";
import { ThreadDeleteDialogProvider } from "./thread-delete-dialog";
import { WorkspaceHeader } from "./workspace-header";
import { WorkspaceNavChatList } from "./workspace-nav-chat-list";
import { WorkspaceNavMenu } from "./workspace-nav-menu";

const PICK_LINK = "hover:bg-accent block rounded-md px-2 py-2 text-sm";
const PICK_DATA_PATH = "/workspace/pick-data";

function onPickData(pathname: string | null): boolean {
  return (
    pathname === PICK_DATA_PATH ||
    (pathname?.startsWith(`${PICK_DATA_PATH}/`) ?? false)
  );
}

/** The two pick links; 选剧资料 is marked current on its own pages. */
export function PickNav() {
  const current = onPickData(usePathname());
  return (
    <nav aria-label="选剧工作台" className="space-y-1 px-3 py-2">
      <Link className={PICK_LINK} href="/workspace/picks">
        我的选剧
      </Link>
      <Link
        className={cn(PICK_LINK, current && "bg-accent")}
        aria-current={current ? "page" : undefined}
        href={PICK_DATA_PATH}
      >
        选剧资料
      </Link>
    </nav>
  );
}

export function WorkspaceSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const { open: isSidebarOpen } = useSidebar();
  return (
    <ThreadDeleteDialogProvider>
      <Sidebar variant="sidebar" collapsible="icon" {...props}>
        <SidebarHeader className="py-0">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent>
          <WorkspaceNavChatList />
          {isSidebarOpen && <PickNav />}
          <WorkspaceChannelsList />
          {isSidebarOpen && (
            <>
              <ProjectsSection />
              <RecentChatList />
            </>
          )}
        </SidebarContent>
        <SidebarFooter>
          <WorkspaceNavMenu />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </ThreadDeleteDialogProvider>
  );
}
