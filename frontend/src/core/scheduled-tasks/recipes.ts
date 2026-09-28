import type { ScheduleValue } from "@/components/workspace/scheduled-task-schedule-input";

export type RecipeTitleKey =
  | "dailyCandidates"
  | "kdDaily"
  | "kwWeekly"
  | "poolWeekly";

export type Recipe = {
  id: string;
  titleKey: RecipeTitleKey;
  prompt: string;
  schedule: ScheduleValue;
};

// Front-end-only starter recipes for the pick workbench. Only the title is
// localized: a scheduled run is unattended (clarification auto-proceeds), so
// each prompt is one Chinese string that uses the phrases the pick tools and
// gate instructions are written in and pins the tool arguments in brackets.
// Prompts carry no placeholders, because nothing on the scheduled-task path
// checks for unreplaced ones. Times are Asia/Shanghai so the runs land after
// the 11:40 Beijing catalog sync, and they are staggered so two runs never
// start together.
export const RECIPES: Recipe[] = [
  {
    id: "dailyCandidates",
    titleKey: "dailyCandidates",
    prompt:
      "找10部英语剧（language=en，limit=10），排除我已经选过的（exclude_selected=true），也排除团队发布记录里已发过的（exclude_posted=true）。条件已完整，直接查询一次，不追问。按候选卡顺序列出（顺序按最新依据日期，不代表热度排名），说明符合条件的总数和数据时点（data_as_of）；不足10部按实际数量说明，不凑数；发布记录不可用时如实说明，不要去掉这个条件重查。",
    schedule: {
      schedule_type: "cron",
      schedule_spec: { cron: "0 12 * * *" },
      timezone: "Asia/Shanghai",
    },
  },
  {
    id: "kdDaily",
    titleKey: "kdDaily",
    prompt:
      "按名次列出KalosTV日榜最新一期上榜的英语剧，最多10部（signal_kind=kd，sort=rank，language=en，limit=10），不排除我已经选过的（exclude_selected=false）。条件已完整，直接查询一次，不追问。按候选卡顺序列出名次和榜单日期，说明数据时点（data_as_of）；不足10部按实际数量说明；名次只在这张榜的这一期内有效，不与其他榜单或日期比较。",
    schedule: {
      schedule_type: "cron",
      schedule_spec: { cron: "10 12 * * *" },
      timezone: "Asia/Shanghai",
    },
  },
  {
    id: "kwWeekly",
    titleKey: "kwWeekly",
    prompt:
      "找10部有KalosTV周热门依据的英语剧（signal_kind=kw，language=en，limit=10），排除我已经选过的（exclude_selected=true）和团队发布记录里已发过的（exclude_posted=true）。kw没有名次，不按名次排序。条件已完整，直接查询一次，不追问。按候选卡顺序列出，每部写出它的kw依据日期（周起始日）；kw依据可能来自较早的周，最新一期不是本周时要说明。说明符合条件的总数和数据时点（data_as_of）；不足10部按实际数量说明；发布记录不可用时如实说明，不要去掉这个条件重查。",
    schedule: {
      schedule_type: "cron",
      schedule_spec: { cron: "20 12 * * 1" },
      timezone: "Asia/Shanghai",
    },
  },
  {
    id: "poolWeekly",
    titleKey: "poolWeekly",
    prompt:
      "统计候选池里的剧目数量：不限语种和剧场，不排除我已经选过的（exclude_selected=false），只调用一次计数工具（pick_count_candidates），不生成候选卡。列出总数、各剧场数量和各语种代码数量，说明数据时点（data_as_of）；候选池只含有来源信号且未标下架的剧，不等于全部剧库。",
    schedule: {
      schedule_type: "cron",
      schedule_spec: { cron: "30 12 * * 1" },
      timezone: "Asia/Shanghai",
    },
  },
];
