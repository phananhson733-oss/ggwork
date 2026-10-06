// 工作台新建：资料页的 tab 栏外壳。有镜像版本时就是 toolbar 的 Tabs（带徽标，imports 不带；徽标来自版本的新鲜度）；
// 镜像读不了时只有「同步与导入」能点，其余 tab 只显示名字（点进去也只会看到同一条提示）。
import Link from "next/link";

import {
  TAB_LABELS,
  Tabs,
  type TabCounts,
} from "@/components/workspace/pick-board/toolbar";
import type { PickRequest } from "@/core/pick-board/request";

import { IMPORTS_HREF } from "./banner-rules";

const DATA_TABS = ["pick", "all", "rank", "posted", "rules"] as const;

export function BoardTabs({
  req,
  counts,
}: {
  req: PickRequest;
  counts?: TabCounts;
}) {
  return (
    <nav aria-label="选剧资料分页" data-board-tabs="true">
      <Tabs req={req} counts={counts} />
    </nav>
  );
}

export function ImportsOnlyTabs() {
  return (
    <nav aria-label="选剧资料分页" data-board-tabs="true">
      <div className="border-line mb-4 flex flex-wrap gap-1 border-b">
        {DATA_TABS.map((t) => (
          <span
            key={t}
            aria-disabled="true"
            className="text-ink-dim -mb-px border-b-2 border-transparent px-3.5 py-2.5 text-[14px] whitespace-nowrap"
          >
            {TAB_LABELS[t]}
          </span>
        ))}
        <Link
          prefetch={false}
          href="/workspace/pick-data?tab=trends"
          className="text-helper px-3.5 py-2.5 text-[14px]"
        >
          {TAB_LABELS.trends}
        </Link>
        <Link
          prefetch={false}
          href={IMPORTS_HREF}
          className="text-helper hover:text-ink-1 -mb-px border-b-2 border-transparent px-3.5 py-2.5 text-[14px] whitespace-nowrap"
        >
          {TAB_LABELS.imports}
        </Link>
      </div>
    </nav>
  );
}
