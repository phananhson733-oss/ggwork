// PORTED_FROM: realshort@816ca2e src/app/admin/(protected)/pick/page.tsx
// 本地改动：DetailView 拆成同步的 DetailBody（取数在页面，reelshortId 的判断也在页面）；now 换成版本的 as_of；
// 规则经 ctx.rules 传入；证据页曲线的清理说明取 series_state.trimmed_before（组件按画出来的 90 天判要不要注明）。
import { Empty } from "@/components/workspace/pick-board/toolbar";
import type { PickRequest } from "@/core/pick-board/request";

import { ReelshortDetailView } from "../reelshort-detail";
import { RowDetailView } from "../row-detail";

import type { BoardContext, DetailData } from "./board-data";

export function DetailBody({
  data,
  req,
  ctx,
}: {
  data: DetailData;
  req: PickRequest;
  ctx: BoardContext;
}) {
  if (data.kind === "no-row") return <Empty>从列表里点剧名进证据页。</Empty>;
  if (data.kind === "reelshort") {
    if (!data.detail)
      return (
        <Empty>
          ReelShort 片库里找不到这部剧（{data.id}
          ）：可能已被上游下架，或还没拉过详情。
        </Empty>
      );
    return (
      <ReelshortDetailView
        detail={data.detail}
        req={req}
        requestedId={data.id}
        asOf={ctx.asOf}
        rules={ctx.rules}
        seriesTrimmedBefore={ctx.seriesState?.trimmedBefore ?? null}
      />
    );
  }
  if (!data.detail)
    return (
      <Empty>
        找不到这一行（{data.rowKey}
        ）。行键会随剧单合并结果变，上一次导入之后它可能已经改名或被剧场下架。
      </Empty>
    );
  return <RowDetailView detail={data.detail} req={req} rules={ctx.rules} />;
}
