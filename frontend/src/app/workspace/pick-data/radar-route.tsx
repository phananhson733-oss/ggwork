import { BoardTitle } from "@/components/workspace/pick-board/views/board-header";
import { BoardShell } from "@/components/workspace/pick-board/views/board-shell";
import { BoardTabs } from "@/components/workspace/pick-board/views/board-tabs";
import { HistoricalRadar } from "@/components/workspace/radar/historical-radar";
import type { PickRequest } from "@/core/pick-board/request";

/** Called only after requireBoardUser; all data reads independently authenticate at the Gateway. */
export function radarPage(req: PickRequest) {
  const params = new URLSearchParams({ tab: "trends", rv: "daily" });
  if (req.v !== null) params.set("v", String(req.v));
  return (
    <BoardShell>
      <BoardTitle />
      <BoardTabs req={req} />
      <HistoricalRadar dailyHref={`?${params}`} />
    </BoardShell>
  );
}
