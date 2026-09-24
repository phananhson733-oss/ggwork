// 工作台新建：资料页的外壳。WorkspaceBody 不滚动，照工作台惯例放 <ScrollArea className="size-full">（批判 C28）；
// Radix 的视口里还包着一层 display:table 的 div，它会按最宽的表撑开整页，把它改成 block，宽表才在自己的
// overflow-x-auto 里横向滚动。.pick-board 挂在内容根上，资料页的配色变量只在这里生效（pick-data/layout.tsx 引入）。
// 外层已有 <main>（WorkspaceBody），这里不再用 main。
import type { ReactNode } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";

const VIEWPORT_BLOCK = "[&_[data-radix-scroll-area-viewport]>div]:block!";

export function BoardShell({ children }: { children: ReactNode }) {
  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody>
        <ScrollArea className={`size-full ${VIEWPORT_BLOCK}`}>
          <div className="pick-board text-ink-1 mx-auto w-full max-w-[1680px] px-7 pt-6 pb-15">
            {children}
          </div>
        </ScrollArea>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
