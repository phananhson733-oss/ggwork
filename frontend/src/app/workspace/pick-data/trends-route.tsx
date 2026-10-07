import { redirect } from "next/navigation";
import type { ReactElement } from "react";


import { BoardTitle } from "@/components/workspace/pick-board/views/board-header";
import { BoardShell } from "@/components/workspace/pick-board/views/board-shell";
import { BoardTabs } from "@/components/workspace/pick-board/views/board-tabs";
import { TrendsCandidatesView } from "@/components/workspace/pick-board/views/trends-candidates-view";
import {
  TrendsTableUnavailable,
  TrendsTableView,
} from "@/components/workspace/pick-board/views/trends-table-view";
import { validateAuthNextPath } from "@/core/auth/next-path";
import { buildLoginUrl } from "@/core/auth/types";
import type { PickRequest } from "@/core/pick-board/request";
import {
  loadTrendsCandidates,
  loadTrendsTable,
  PICK_DATA_PATH,
} from "@/server/pick-board";

/**
 * 「Google 趋势」tab（2026-09-30 简化范围第 6 节第 7 项）：鉴权之后就分出来，只问 gateway 的趋势表接口，不解析镜像
 * 版本、不读 pick_obs 视图，镜像读不了也能看。横幅是 gateway 按批次判断的（采集漏跑、数据过期）。gateway 说没登录
 * （会话在两次请求之间过期）就去登录页，回来还是这一页；别的读不了是一条提示，页签外壳照留。
 */
export async function trendsRoute(
  req: PickRequest,
  nextPath: string,
): Promise<ReactElement> {
  const loaded = await loadTrendsTable();
  if (loaded.kind === "unauthenticated")
    redirect(buildLoginUrl(validateAuthNextPath(nextPath) ?? PICK_DATA_PATH));
  const preview =
    loaded.kind === "ok" && loaded.table.batch === null
      ? await loadTrendsCandidates()
      : null;
  if (preview?.kind === "unauthenticated")
    redirect(buildLoginUrl(validateAuthNextPath(nextPath) ?? PICK_DATA_PATH));
  return (
    <BoardShell>
      <BoardTitle />
      <BoardTabs req={req} />
      {loaded.kind === "ok" ? (
        <>
          <TrendsTableView
            table={loaded.table}
            req={req}
            preview={
              preview?.kind === "ok" ? (
                <TrendsCandidatesView candidates={preview.candidates} />
              ) : undefined
            }
          />
          {preview?.kind === "unavailable" ? (
            <p role="alert">待采集剧集暂时读不了，稍后刷新再试。</p>
          ) : null}
        </>
      ) : (
        <TrendsTableUnavailable />
      )}
    </BoardShell>
  );
}
