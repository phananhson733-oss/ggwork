// 工作台新建（TR-24）：趋势雷达两个 tab 与详情页的分发。数据由 app/workspace/pick-data/obs-route.tsx 取好传进来，
// now 是这次请求的时刻（横幅与联动的可行动性都按它算）；这里只按 kind 选视图，同步、纯展示。
import type { PickRequest } from "@/core/pick-board/request";
import type { ObsTabData } from "@/server/pick-board";

import { ObsDetailView } from "./obs-detail-view";
import { ObsSearchView } from "./obs-search-view";
import { ObsTrendsView } from "./obs-trends-view";

export function ObsView({
  data,
  req,
  now,
}: {
  data: ObsTabData;
  req: PickRequest;
  now: Date;
}) {
  switch (data.kind) {
    case "trends":
      return <ObsTrendsView data={data} req={req} now={now} />;
    case "search":
      return <ObsSearchView data={data} req={req} now={now} />;
    case "detail":
      return <ObsDetailView data={data} req={req} now={now} />;
  }
}
