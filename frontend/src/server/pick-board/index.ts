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
 * column lists, SQL fragments and row mappers, the per-version cache itself,
 * and the rs-queries loaders that only the other query modules call. Modules
 * inside src/server/pick-board import each other directly.
 *
 * resolveBoard is resolveVersion with the board's rules and the per-version
 * cache of step 2 (meta.rules / meta.sources); freshnessOf and sourcesOf turn
 * what it returns into the header's and the footer's shapes without a query.
 *
 * The replay (P4-2): loadReplay asks the gateway for the agent's list and the
 * result's conditions; loadRowsByKeys and loadMissingKeys read that list's
 * rows from the pinned version.
 */

export {
  type PickPostedTag,
  type PickRow,
  type PickSignal,
  type RowsPage,
  type SameTitleRow,
} from "./queries-shared";
export {
  freshnessOf,
  loadCandidatePool,
  loadFacets,
  loadFreshness,
  loadMissingKeys,
  loadPickRows,
  loadRowDetail,
  loadRowsByKeys,
  type PickFacets,
  type PickFreshness,
  type RowDetail,
  type SiteDrama,
} from "./queries";
export { resolveBoard } from "./cache";
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
export { type ObserveSources, sourcesOf } from "./source-state";

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
  loadReplay,
  REPLAY_LIMIT,
  type ReplayAnswer,
  type ReplayLoad,
} from "./replay";
export {
  MirrorBusy,
  MirrorError,
  type MirrorErrorCode,
  MirrorMisconfigured,
  MirrorUnavailable,
  MirrorVersionGone,
  type MisconfiguredReason,
} from "./errors";

export { loadTrendsTable, type TrendsTableLoad } from "./trends-table";
