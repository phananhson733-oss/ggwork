// 工作台新建：资料页的 tab 栏外壳。有镜像版本时就是 toolbar 的 Tabs（带徽标，imports 不带；徽标来自版本的新鲜度）；
// 镜像读不了时只有不经镜像的三个能点：趋势雷达的两个 tab（TR-24，读 pick_obs）与「同步与导入」；其余 tab 只显示名字
// （点进去也只会看到同一条提示）。顺序与 Tabs 一致。
import Link from "next/link";

import {
  BOARD_PATH,
  TAB_LABELS,
  Tabs,
  type TabCounts,
} from "@/components/workspace/pick-board/toolbar";
import type { PickRequest } from "@/core/pick-board/request";

import { IMPORTS_HREF } from "./banner-rules";

/** Tabs's order; the ones with an href work without a mirror version. */
const MIRRORLESS_TABS: readonly (readonly [
  keyof typeof TAB_LABELS,
  string | null,
])[] = [
  ["pick", null],
  ["all", null],
  ["rank", null],
  ["posted", null],
  ["trends", `${BOARD_PATH}?tab=trends`],
  ["search", `${BOARD_PATH}?tab=search`],
  ["rules", null],
  ["imports", IMPORTS_HREF],
];

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
        {MIRRORLESS_TABS.map(([t, href]) =>
          href ? (
            <Link
              key={t}
              prefetch={false}
              href={href}
              className="text-helper hover:text-ink-1 -mb-px border-b-2 border-transparent px-3.5 py-2.5 text-[14px] whitespace-nowrap"
            >
              {TAB_LABELS[t]}
            </Link>
          ) : (
            <span
              key={t}
              aria-disabled="true"
              className="text-ink-dim -mb-px border-b-2 border-transparent px-3.5 py-2.5 text-[14px] whitespace-nowrap"
            >
              {TAB_LABELS[t]}
            </span>
          ),
        )}
      </div>
    </nav>
  );
}
