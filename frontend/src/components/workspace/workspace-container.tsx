"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo } from "react";

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

// Workspace sections that have an index route (/workspace/<section>/page.tsx)
// and can therefore be linked to from the breadcrumb.
const LINKABLE_SECTIONS: Record<string, true> = {
  agents: true,
  chats: true,
  "scheduled-tasks": true,
};

/** A section index linked to itself reads like BreadcrumbPage (ink-1, 500). */
const CURRENT_CRUMB = "text-ink-1 font-medium";

export function WorkspaceContainer({
  className,
  children,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div className={cn("flex h-screen w-full flex-col", className)} {...props}>
      {children}
    </div>
  );
}

export function WorkspaceHeader({
  className,
  children,
  ...props
}: React.ComponentProps<"header">) {
  const { t } = useI18n();
  const pathname = usePathname();
  const segments = useMemo(() => {
    const parts = pathname?.split("/") || [];
    if (parts.length > 0) {
      return parts.slice(1, 3);
    }
  }, [pathname]);
  const sectionHref = `/${segments?.[0]}/${segments?.[1]}`;
  // A section index (e.g. /workspace/chats) is the current page itself.
  const sectionIsCurrent = !children && pathname === sectionHref;
  return (
    <header
      className={cn(
        "border-line bg-background z-20 flex h-14 shrink-0 items-center gap-2 border-b px-5",
        className,
      )}
      {...props}
    >
      <div className="flex min-w-0 items-center gap-2">
        <SidebarTrigger className="md:hidden" />
        <Breadcrumb>
          <BreadcrumbList className="text-helper gap-2 sm:gap-2">
            {segments?.[0] && (
              <BreadcrumbItem className="hidden md:block">
                <BreadcrumbLink asChild>
                  <Link href={`/${segments[0]}`}>
                    {nameOfSegment(segments[0], t)}
                  </Link>
                </BreadcrumbLink>
              </BreadcrumbItem>
            )}
            {segments?.[1] && (
              <>
                <BreadcrumbSeparator className="hidden md:block" />
                <BreadcrumbItem>
                  {segments[1] && LINKABLE_SECTIONS[segments[1]] ? (
                    <BreadcrumbLink asChild>
                      <Link
                        href={sectionHref}
                        aria-current={sectionIsCurrent ? "page" : undefined}
                        className={cn(sectionIsCurrent && CURRENT_CRUMB)}
                      >
                        {nameOfSegment(segments[1], t)}
                      </Link>
                    </BreadcrumbLink>
                  ) : (
                    <BreadcrumbPage>
                      {nameOfSegment(segments[1], t)}
                    </BreadcrumbPage>
                  )}
                </BreadcrumbItem>
              </>
            )}
            {children && (
              <>
                <BreadcrumbSeparator />
                {children}
              </>
            )}
          </BreadcrumbList>
        </Breadcrumb>
      </div>
    </header>
  );
}

export function WorkspaceBody({
  className,
  children,
  ...props
}: React.ComponentProps<"main">) {
  return (
    <main
      className={cn(
        "relative flex min-h-0 w-full flex-1 flex-col items-center",
        className,
      )}
      {...props}
    >
      <div className="flex h-full w-full flex-col items-center">{children}</div>
    </main>
  );
}

function nameOfSegment(
  segment: string | undefined,
  t: ReturnType<typeof useI18n>["t"],
) {
  if (!segment) return t.common.home;
  if (segment === "workspace") return t.breadcrumb.workspace;
  if (segment === "chats") return t.breadcrumb.chats;
  if (segment === "picks") return "我的选剧";
  if (segment === "pick-data") return "选剧资料";
  return segment[0]?.toUpperCase() + segment.slice(1);
}
