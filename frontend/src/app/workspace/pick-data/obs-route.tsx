import type { ReactElement } from "react";

import { BoardTitle } from "@/components/workspace/pick-board/views/board-header";
import { BoardShell } from "@/components/workspace/pick-board/views/board-shell";
import { BoardTabs } from "@/components/workspace/pick-board/views/board-tabs";
import { MirrorNotice } from "@/components/workspace/pick-board/views/notices";
import { ObsView } from "@/components/workspace/pick-board/views/obs-view";
import type { ObsTab, PickRequest } from "@/core/pick-board/request";
import { loadObsTab } from "@/server/pick-board";

import { guarded } from "./notice-of";

/**
 * 趋势雷达的观测 tab（TR-24，D9）：鉴权之后、解析镜像版本之前就分出来。2026-09-30 起 trends tab 改走简化版趋势表
 * （trends-route.tsx），这里只剩先隐藏的 search tab 与它的详情页。观测集合不属于任何镜像版本，
 * 镜像读不了（没有版本、版本被清理、授权缺失）时这两个 tab 照样能看；不钉版本，也不问 gateway 的 /sync
 * （横幅按 pick_obs.run_status 与当前集合现算）。now 是这次请求的时刻：横幅与联动的可行动性都按它算，
 * 视图里不读时钟。读不了观测数据是一条提示，别的错误照常抛给 error.tsx。
 */
export async function obsRoute(
  req: PickRequest & { tab: ObsTab },
): Promise<ReactElement> {
  const now = new Date();
  const loaded = await guarded(() =>
    loadObsTab({ tab: req.tab, obs: req.obs, oid: req.oid }),
  );
  return (
    <BoardShell>
      <BoardTitle />
      <BoardTabs req={req} />
      {loaded.ok ? (
        <ObsView data={loaded.value} req={req} now={now} />
      ) : (
        <MirrorNotice notice={loaded.notice} req={req} />
      )}
    </BoardShell>
  );
}
