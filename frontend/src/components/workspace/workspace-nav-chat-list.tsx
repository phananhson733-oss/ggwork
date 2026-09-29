"use client";

import {
  BotIcon,
  CalendarClock,
  MessagesSquare,
  BlocksIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useAgentsApiEnabled } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";

export function WorkspaceNavChatList() {
  const { t } = useI18n();
  const pathname = usePathname();
  const { enabled: agentsEnabled, isLoading: agentsLoading } =
    useAgentsApiEnabled();
  return (
    <SidebarGroup className="pt-1">
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton isActive={pathname === "/workspace/chats"} asChild>
            <Link href="/workspace/chats">
              <MessagesSquare />
              <span>{t.sidebar.chats}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        {/* Hidden, not greyed out, while agents_api is off: the entry has
            nothing to offer then. Waiting for the live answer keeps a
            disabled deployment from flashing the link on first paint. */}
        {agentsEnabled && !agentsLoading && (
          <SidebarMenuItem>
            <SidebarMenuButton
              isActive={pathname.startsWith("/workspace/agents")}
              asChild
            >
              <Link href="/workspace/agents">
                <BotIcon />
                <span>{t.sidebar.agents}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        )}
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/scheduled-tasks")}
            asChild
          >
            <Link href="/workspace/scheduled-tasks">
              <CalendarClock />
              <span>{t.sidebar.scheduledTasks}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/capabilities")}
            asChild
          >
            <Link href="/workspace/capabilities">
              <BlocksIcon />
              <span>{t.capabilities.title}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  );
}
