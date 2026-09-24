// 工作台新建：页面取回的一 tab 数据交给对应视图（RealShort page.tsx 的 view() 分发，数据先取好再分）。
import type { PickRequest } from "@/core/pick-board/request";

import { RulesTab } from "../rules-tab";

import type { BoardContext, TabData } from "./board-data";
import { DetailBody } from "./detail-view";
import { ListBody } from "./list-view";
import { PostedBody, PostedRecordBody } from "./posted-view";
import { RankBody } from "./rank-view";

export function TabView({
  data,
  req,
  ctx,
}: {
  data: TabData;
  req: PickRequest;
  ctx: BoardContext;
}) {
  switch (data.kind) {
    case "list":
      return <ListBody data={data} req={req} ctx={ctx} />;
    case "rank":
      return <RankBody data={data} req={req} ctx={ctx} />;
    case "posted":
      return <PostedBody data={data} req={req} ctx={ctx} />;
    case "posted-record":
      return <PostedRecordBody data={data} req={req} />;
    case "rules":
      return <RulesTab rules={ctx.rules} />;
    default:
      return <DetailBody data={data} req={req} ctx={ctx} />;
  }
}
