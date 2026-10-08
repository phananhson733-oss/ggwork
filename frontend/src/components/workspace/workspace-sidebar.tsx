"use client";

import { DatabaseIcon, ListChecksIcon, ScissorsIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useId } from "react";
import { toast } from "sonner";

import {
  Sidebar,
  SidebarHeader,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";

import { WorkspaceChannelsList } from "./channels/workspace-channels-list";
import { ProjectsSection } from "./projects-section";
import { RecentChatList } from "./recent-chat-list";
import { ThreadDeleteDialogProvider } from "./thread-delete-dialog";
import { WorkspaceHeader } from "./workspace-header";
import { WorkspaceNavChatList } from "./workspace-nav-chat-list";
import { WorkspaceNavMenu } from "./workspace-nav-menu";

const PICKS_PATH = "/workspace/picks";
const PICK_DATA_PATH = "/workspace/pick-data";

/** True on `base` and on every page below it (not on `/workspace/picksX`). */
function isUnder(pathname: string | null, base: string): boolean {
  return pathname === base || (pathname?.startsWith(`${base}/`) ?? false);
}

/**
 * The 选剧工作台 group: 我的选剧 shows an availability notice;
 * 选剧资料 is marked current on its own pages.
 */
export function PickNav() {
  const { t } = useI18n();
  const pathname = usePathname();
  const labelId = useId();
  const links = [
    {
      href: "/workspace/editing",
      label: "剪辑",
      icon: ScissorsIcon,
      current: isUnder(pathname, "/workspace/editing"),
    },
    {
      href: PICKS_PATH,
      label: "我的选剧",
      icon: ListChecksIcon,
      current: false,
    },
    {
      href: PICK_DATA_PATH,
      label: "选剧资料",
      icon: DatabaseIcon,
      current: isUnder(pathname, PICK_DATA_PATH),
    },
  ];
  return (
    <SidebarGroup>
      <SidebarGroupLabel id={labelId}>选剧工作台</SidebarGroupLabel>
      <nav aria-labelledby={labelId}>
        <SidebarMenu>
          {links.map(({ href, label, icon: Icon, current }) => (
            <SidebarMenuItem key={href}>
              {href === PICKS_PATH ? (
                <SidebarMenuButton
                  type="button"
                  onClick={() => toast.info(t.sidebar.notOpenYet)}
                >
                  <Icon />
                  <span>{label}</span>
                </SidebarMenuButton>
              ) : (
                <SidebarMenuButton isActive={current} asChild>
                  <Link href={href} aria-current={current ? "page" : undefined}>
                    <Icon />
                    <span>{label}</span>
                  </Link>
                </SidebarMenuButton>
              )}
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </nav>
    </SidebarGroup>
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
        <SidebarFooter className="border-line border-t">
          <WorkspaceNavMenu />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </ThreadDeleteDialogProvider>
  );
}
