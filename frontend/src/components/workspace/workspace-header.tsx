"use client";

import { SquarePen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { GGWorkMark, GGWorkWordmark } from "@/components/brand/ggwork-logo";
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { APP_NAME } from "@/core/brand";
import { useI18n } from "@/core/i18n/hooks";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export function WorkspaceHeader({ className }: { className?: string }) {
  const { t } = useI18n();
  const { state } = useSidebar();
  const pathname = usePathname();
  return (
    <>
      <div
        className={cn(
          "group/workspace-header flex h-12 flex-col justify-center",
          className,
        )}
      >
        {state === "collapsed" ? (
          <div className="flex w-full cursor-pointer items-center justify-center">
            <GGWorkMark
              size={22}
              alt={APP_NAME}
              className="group-hover/workspace-header:hidden"
            />
            <SidebarTrigger className="hidden group-hover/workspace-header:inline-flex" />
          </div>
        ) : (
          <div className="flex items-center justify-between gap-2 pl-1">
            {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ? (
              <Link href="/" className="rounded-md">
                <GGWorkWordmark />
              </Link>
            ) : (
              <GGWorkWordmark className="cursor-default" />
            )}
            <SidebarTrigger />
          </div>
        )}
      </div>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname === "/workspace/chats/new"}
            className="border-line bg-surface border font-medium"
            asChild
          >
            <Link href="/workspace/chats/new">
              <SquarePen size={16} />
              <span>{t.sidebar.newChat}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </>
  );
}
