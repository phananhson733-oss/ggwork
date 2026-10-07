// 工作台新建（简化版趋势雷达，2026-09-30）：趋势表表头的「这晚的剧从哪来」。三个带名次的榜各自最新一期列了几部，
// ReelShort 收入补了几部（哪个镜像版本、哪天的快照），或者为什么没补（简化范围第 3 节、第 7 节第 9 条）。同步、纯展示。
import type { TrendsTableBatch } from "@/core/pick/trends-table-schema";
import { textOf } from "@/core/pick/trends-table-wording";
import {
  BASIS_TEXT,
  REVENUE_REASON_TEXT,
} from "@/core/pick/trends-table-wording";

import { MUTED } from "./trends-table-parts";

type Sources = TrendsTableBatch["sources"];

function boardLine(board: Sources["boards"][number]): string {
  const name = BASIS_TEXT[board.kind];
  return board.board_date
    ? `${name} ${board.board_date} 期 ${board.listed} 部`
    : `${name}没有可用的一期`;
}

function revenueLine(revenue: Sources["revenue"]): string {
  if (revenue === null) return "ReelShort 收入：这晚没有记录。";
  if (!revenue.available)
    return `ReelShort 收入：${textOf(REVENUE_REASON_TEXT, revenue.reason ?? "")}。`;
  const snapshot = revenue.day ? `${revenue.day} 快照` : "快照日期不详";
  const version =
    revenue.mirror_version !== null
      ? `，镜像版本 ${revenue.mirror_version}`
      : "";
  return `ReelShort 收入补足 ${revenue.filled} 部（滚动 30 天收入，${snapshot}${version}）。`;
}

export function TableSources(
  props: { batch: TrendsTableBatch } | { sources: Sources },
) {
  const { boards, revenue } =
    "batch" in props ? props.batch.sources : props.sources;
  return (
    <p className={MUTED} data-trends-sources="true">
      剧的来源：
      {boards.length === 0 ? "没有榜单记录" : boards.map(boardLine).join("；")}
      。{revenueLine(revenue)}
    </p>
  );
}
