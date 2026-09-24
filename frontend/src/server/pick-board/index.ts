import "server-only";

/**
 * The pick data board's server entry for the app: the page and its views
 * import loaders, version resolution, access checks and error classes from
 * here; components import only types, with a whole-statement `import type`
 * (an inline `{ type X }` leaves an empty import behind under
 * verbatimModuleSyntax and would pull this server-only module into a client
 * bundle).
 *
 * Not re-exported: the pool and executors (db.ts), error translation, the
 * column lists, SQL fragments and row mappers, and the rs-queries loaders that
 * only the other query modules call. Modules inside src/server/pick-board
 * import each other directly.
 *
 * The four loaders of queries.ts (loadPickRows, loadFacets, loadFreshness,
 * loadRowDetail) join this list when P3-3 lands queries.ts.
 */

export {
  type PickFacets,
  type PickFreshness,
  type PickPostedTag,
  type PickRow,
  type PickSignal,
  type RowDetail,
  type RowsPage,
  type SameTitleRow,
  type SiteDrama,
} from "./queries-shared";
export {
  type BillRow,
  type BillTotals,
  type DramaDetail,
  type GrowthBaseline,
  type ObserveRow,
  type RsCounts,
  type SeriesPoint,
} from "./rs-queries";
export {
  type GrowthDiagnosis,
  type GrowthEmptyReason,
  loadGrowthDiagnosis,
  loadRankMeta,
  loadRankRows,
  loadRsRank,
  type RankMeta,
  type RankRow,
  type RsRankResult,
} from "./queries-rank";
export {
  type CatalogAccount,
  type LinkedDrama,
  type LinkedRow,
  loadAccounts,
  loadPostedList,
  loadPostedRecord,
  loadPostedStats,
  type PostedLinks,
  type PostedList,
  type PostedRecord,
  type PostedRecordDetail,
  type PostedStats,
} from "./queries-posted";
export {
  loadObserveSources,
  loadReelshortDetail,
  type ReelshortDetail,
} from "./queries-reelshort";
export { type ObserveSources } from "./source-state";

export { setBoardScope, type VersionScope } from "./db";
export {
  type BoardVersion,
  type CurrentVersion,
  type EmptyBoard,
  type ReadyBoard,
  resolveVersion,
  type SeriesState,
  type VersionWarning,
} from "./version";
export {
  type BoardAccess,
  type BoardNoticeReason,
  PICK_DATA_PATH,
  pickDataNextPath,
  requireBoardUser,
  type SearchParamsRecord,
} from "./auth";
export { type GatewayResult, getPickSync } from "./gateway";
export {
  MirrorBusy,
  MirrorError,
  type MirrorErrorCode,
  MirrorMisconfigured,
  MirrorUnavailable,
  MirrorVersionGone,
  type MisconfiguredReason,
} from "./errors";
