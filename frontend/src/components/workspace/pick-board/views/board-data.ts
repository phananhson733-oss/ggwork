// 工作台新建：资料页各 tab 的数据形状。取数在页面（app/workspace/pick-data/page.tsx）里做——组件对 @/server/pick-board
// 只能整句 import type（契约测试），视图因此都是同步的：页面先把这一 tab 要的数据都取回来，再交给这里定义的形状渲染。
import type { RankKey, RsRank, TheaterBasis } from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import type {
  CatalogAccount,
  GrowthDiagnosis,
  PickFacets,
  PickFreshness,
  PickRow,
  PostedList,
  PostedRecordDetail,
  PostedStats,
  RankMeta,
  RankRow,
  ReelshortDetail,
  RowDetail,
  RowsPage,
  RsRankResult,
  SeriesState,
} from "@/server/pick-board";

/** 每个视图都知道的：版本规则、版本的 as_of（本页的「现在」）与新鲜度 */
export type BoardContext = Readonly<{
  rules: BoardRules;
  asOf: Date;
  versionId: number;
  freshness: PickFreshness;
  latestSnapshot: string | null;
  seriesState: SeriesState | null;
}>;

export type RsRows = Extract<RsRankResult, { kind: "rows" }>;
export type Ledger = Extract<RsRankResult, { kind: "ledger" }>;

export type RankBoard =
  | Readonly<{ kind: "theater"; rank: TheaterBasis; page: RowsPage<RankRow> }>
  | Readonly<RsRows & { rank: RsRank; diagnosis: GrowthDiagnosis | null }>
  | Readonly<Ledger & { rank: RankKey }>;

export type ListData = Readonly<{
  kind: "list";
  page: RowsPage<PickRow>;
  facets: PickFacets;
}>;
export type RankData = Readonly<{
  kind: "rank";
  meta: RankMeta;
  board: RankBoard;
}>;
export type PostedData = Readonly<{
  kind: "posted";
  list: PostedList;
  stats: PostedStats;
  accounts: CatalogAccount[];
}>;
export type PostedRecordData = Readonly<{
  kind: "posted-record";
  found: PostedRecordDetail | null;
}>;
export type DetailData =
  | Readonly<{ kind: "no-row" }>
  | Readonly<{ kind: "reelshort"; id: string; detail: ReelshortDetail | null }>
  | Readonly<{ kind: "row"; rowKey: string; detail: RowDetail | null }>;

export type TabData =
  | ListData
  | RankData
  | PostedData
  | PostedRecordData
  | DetailData
  | Readonly<{ kind: "rules" }>;
