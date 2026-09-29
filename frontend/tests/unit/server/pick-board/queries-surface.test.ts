import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

import { type ObserveRequest } from "@/core/pick-board/metrics";
import { type PickRequest, type RsRank } from "@/core/pick-board/request";
import * as board from "@/server/pick-board";
import {
  type BillRow,
  type BillTotals,
  type CatalogAccount,
  type DramaDetail,
  type GrowthDiagnosis,
  type ObserveRow,
  type ObserveSources,
  type PickFacets,
  type PickFreshness,
  type PickRow,
  type PostedList,
  type PostedRecordDetail,
  type PostedStats,
  type RankMeta,
  type RankRow,
  type ReelshortDetail,
  type RowDetail,
  type RowsPage,
  type RsRankResult,
} from "@/server/pick-board";
import {
  ROW_COLUMN_NAMES,
  type RawRow,
} from "@/server/pick-board/queries-shared";
import type {
  GrowthBaseline,
  GrowthWindowDays,
  LoadRowsOptions,
  ObserveRowsPage,
  RsCounts,
  loadBillRows,
  loadBillTotals,
  loadDramaDetail,
  loadGrowthBaseline,
  loadRows,
  loadRowsByIds,
  loadRsCounts,
} from "@/server/pick-board/rs-queries";

/**
 * P3-3a: the shapes the queries (P3-3) and the components (P3-4) share. The
 * type pins below are checked by `pnpm typecheck`; the runtime cases pin the
 * column list against the mirror DDL and the barrel's export surface.
 */

type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
    ? true
    : false;
type KnownKeys<T> = keyof {
  [K in keyof T as string extends K
    ? never
    : number extends K
      ? never
      : K]: T[K];
};
type Signature<F extends (...args: never[]) => unknown> = [
  Parameters<F>,
  Awaited<ReturnType<F>>,
];

const DDL_FILE = path.resolve(
  __dirname,
  "../../../../../customizations/pick-workbench/ggwork_pick/mirror/ddl.sql",
);

const EXPECTED_ROW_COLUMNS = [
  "row_key",
  "platform",
  "source_table",
  "title",
  "title_cn",
  "lang",
  "kind",
  "origin",
  "tags",
  "listed_on",
  "has_pan",
  "episodes",
  "pay_start",
  "youtube",
  "merged_rows",
  "off_on",
  "reoff_note",
  "in_site_ids",
  "legacy_only",
  "site_other",
  "has_signal",
  "latest_evidence_on",
];

type RsUnionColumn =
  | "rs_clk"
  | "rs_bill"
  | "rs_gsc"
  | "rs_clk_on"
  | "rs_bill_on"
  | "rs_gsc_on"
  | "drama_id";

type PickRowKey =
  | "rowKey"
  | "platform"
  | "sourceTable"
  | "title"
  | "titleCn"
  | "lang"
  | "kind"
  | "origin"
  | "tags"
  | "listedOn"
  | "hasPan"
  | "episodes"
  | "payStart"
  | "youtube"
  | "mergedRows"
  | "offOn"
  | "reoffNote"
  | "inSiteIds"
  | "legacyOnly"
  | "siteOther"
  | "hasSignal"
  | "latestEvidenceOn"
  | "signals"
  | "posted"
  | "rsFlags"
  | "rsFlagDates"
  | "rs";

type ObserveRowKey =
  | "id"
  | "title"
  | "locale"
  | "slug"
  | "publishAt"
  | "chapterCount"
  | "payStart"
  | "revenueCents"
  | "promotersCnt"
  | "searchImpressions"
  | "searchDataAt"
  | "detailSyncedAt"
  | "tags"
  | "metricsValid"
  | "syncedAt"
  | "baseline1At"
  | "baseline7At"
  | "revenueCents1"
  | "promotersCnt1"
  | "revenueCents7"
  | "promotersCnt7"
  | "baseline15At"
  | "revenueCents15"
  | "promotersCnt15"
  | "billOrders"
  | "description"
  | "clicks7"
  | "lastClickOn"
  | "lastBillOn";

type BillRowKey =
  | "billDate"
  | "bookId"
  | "canonicalId"
  | "title"
  | "locale"
  | "promotionType"
  | "orderCnt"
  | "sourceRows"
  | "sameDayClicks";

type Ledger = Extract<RsRankResult, { kind: "ledger" }>;

const ROW_SHAPES: readonly true[] = [
  true satisfies Equal<keyof PickRow, PickRowKey>,
  true satisfies Equal<PickRow["hasPan"], boolean>,
  true satisfies Equal<PickRow["rs"], ObserveRow | undefined>,
  true satisfies Equal<
    Exclude<KnownKeys<RawRow>, RsUnionColumn>,
    (typeof ROW_COLUMN_NAMES)[number]
  >,
  true satisfies Equal<RawRow["has_pan"], boolean>,
  true satisfies Equal<keyof ObserveRow, ObserveRowKey>,
  true satisfies Equal<ObserveRow["metricsValid"], boolean | null>,
  true satisfies Equal<keyof BillRow, BillRowKey>,
  true satisfies Equal<BillRow["canonicalId"], string | null>,
  true satisfies Equal<
    BillTotals,
    {
      rows: number;
      mergedRows: number;
      orders: number;
      mergedWithClicks: number;
      rowsWithClicks: number;
    }
  >,
  true satisfies Equal<
    keyof DramaDetail,
    "row" | "series" | "bill" | "billTruncated" | "clicks"
  >,
  true satisfies Equal<DramaDetail["row"], ObserveRow>,
  true satisfies Equal<DramaDetail["bill"], BillRow[]>,
  true satisfies ReelshortDetail extends DramaDetail ? true : false,
  true satisfies Equal<Ledger["totals"], BillTotals>,
  true satisfies Equal<Ledger["rows"], BillRow[]>,
  true satisfies Equal<RankRow extends PickRow ? true : false, true>,
  true satisfies Equal<RowDetail["row"], PickRow>,
];

// Loader signatures: the version comes from the request's scope, so none of
// them takes an as-of time any more.
const LOADER_SIGNATURES: readonly true[] = [
  true satisfies Equal<
    Signature<typeof loadRows>,
    [[req: ObserveRequest, opts?: LoadRowsOptions], ObserveRowsPage]
  >,
  true satisfies Equal<
    Signature<typeof loadRowsByIds>,
    [
      [ids: readonly string[], opts?: { fullText?: boolean }],
      Map<string, ObserveRow>,
    ]
  >,
  true satisfies Equal<Signature<typeof loadRsCounts>, [[], RsCounts]>,
  true satisfies Equal<
    Signature<typeof loadGrowthBaseline>,
    [[windowDays: GrowthWindowDays], GrowthBaseline]
  >,
  true satisfies Equal<
    Signature<typeof loadBillRows>,
    [[limit?: number], BillRow[]]
  >,
  true satisfies Equal<Signature<typeof loadBillTotals>, [[], BillTotals]>,
  true satisfies Equal<
    Signature<typeof loadDramaDetail>,
    [[canonicalId: string], DramaDetail | null]
  >,
  true satisfies Equal<
    Signature<typeof board.loadRankMeta>,
    [[req: PickRequest], RankMeta]
  >,
  true satisfies Equal<
    Signature<typeof board.loadRankRows>,
    [[req: PickRequest, meta: RankMeta], RowsPage<RankRow>]
  >,
  true satisfies Equal<
    Signature<typeof board.loadGrowthDiagnosis>,
    [[req: PickRequest], GrowthDiagnosis]
  >,
  true satisfies Equal<
    Signature<typeof board.loadRsRank>,
    [[req: PickRequest, rank: RsRank], RsRankResult]
  >,
  true satisfies Equal<
    Signature<typeof board.loadPostedList>,
    [[req: PickRequest], PostedList]
  >,
  true satisfies Equal<
    Signature<typeof board.loadPostedRecord>,
    [[sd: string], PostedRecordDetail | null]
  >,
  true satisfies Equal<
    Signature<typeof board.loadPostedStats>,
    [[], PostedStats]
  >,
  true satisfies Equal<
    Signature<typeof board.loadAccounts>,
    [[], CatalogAccount[]]
  >,
  true satisfies Equal<
    Signature<typeof board.loadReelshortDetail>,
    [[id: string], ReelshortDetail | null]
  >,
  true satisfies Equal<
    Signature<typeof board.loadObserveSources>,
    [[], ObserveSources]
  >,
];

// The loaders of queries.ts (P3-3), and the per-version cached reads the page
// uses in place of a query: the candidate pool N, the version's freshness and
// sources as resolveBoard returned them.
const PAGE_LOADERS: readonly true[] = [
  true satisfies Equal<
    Signature<typeof board.loadPickRows>,
    [[req: PickRequest], RowsPage<PickRow>]
  >,
  true satisfies Equal<
    Signature<typeof board.loadFacets>,
    [[req: PickRequest], PickFacets]
  >,
  true satisfies Equal<
    Signature<typeof board.loadFreshness>,
    [[], PickFreshness]
  >,
  true satisfies Equal<
    Signature<typeof board.loadRowDetail>,
    [[rowKey: string], RowDetail | null]
  >,
  true satisfies Equal<Signature<typeof board.loadCandidatePool>, [[], number]>,
  true satisfies Equal<Parameters<typeof board.freshnessOf>, [raw: unknown]>,
  true satisfies Equal<ReturnType<typeof board.freshnessOf>, PickFreshness>,
  true satisfies Equal<ReturnType<typeof board.sourcesOf>, ObserveSources>,
  true satisfies Equal<
    Parameters<typeof board.resolveBoard>,
    [v: number | null]
  >,
];

const PAGE_TYPES: readonly true[] = [
  true satisfies Equal<
    RowsPage<PickRow>,
    { rows: PickRow[]; total: number; hasMore: boolean }
  >,
  true satisfies Equal<
    keyof PickFacets,
    "platforms" | "langs" | "bases" | "posted"
  >,
  true satisfies Equal<
    keyof PickFreshness,
    | "importedAt"
    | "rows"
    | "withSignal"
    | "signals"
    | "posted"
    | "rsCanonical"
    | "rsCandidates"
    | "rsSyncedAt"
  >,
];

function tableColumns(ddl: string, table: string): Map<string, string> {
  const start = ddl.indexOf(`CREATE TABLE __SCHEMA__.${table} (`);
  expect(start).toBeGreaterThanOrEqual(0);
  const body = ddl.slice(start, ddl.indexOf(");", start));
  const lines = body.split("\n").slice(1);
  return new Map(
    lines
      .map((line) => /^\s+([a-z0-9_]+)\s+(.+?),?$/.exec(line))
      .filter((m): m is RegExpExecArray => m !== null)
      .map((m) => [m[1] ?? "", (m[2] ?? "").replace(/ NOT NULL$/, "")]),
  );
}

describe("P3-3a 类型与签名", () => {
  it("行与对账的类型形状由 tsc 钉住（去掉金额与网盘字段）", () => {
    expect(
      [
        ...ROW_SHAPES,
        ...LOADER_SIGNATURES,
        ...PAGE_LOADERS,
        ...PAGE_TYPES,
      ].every(Boolean),
    ).toBe(true);
  });

  it("ROW_COLUMN_NAMES：has_pan 顶替原来两列网盘的位置，列序固定", () => {
    expect([...ROW_COLUMN_NAMES]).toEqual(EXPECTED_ROW_COLUMNS);
  });

  it("ROW_COLUMN_NAMES 的每一列在 catalog_rows 与 rs_rows 都有、类型逐位一致", () => {
    const ddl = readFileSync(DDL_FILE, "utf8");
    const catalog = tableColumns(ddl, "catalog_rows");
    const rs = tableColumns(ddl, "rs_rows");
    for (const column of ROW_COLUMN_NAMES) {
      expect(catalog.get(column), column).toBeDefined();
      expect(rs.get(column), column).toBe(catalog.get(column));
    }
  });
});

const PAGE_ENTRY = [
  "loadPickRows",
  "loadFacets",
  "loadFreshness",
  "loadRowDetail",
  "loadCandidatePool",
  "freshnessOf",
  "sourcesOf",
  "resolveBoard",
  "loadRankMeta",
  "loadRankRows",
  "loadGrowthDiagnosis",
  "loadRsRank",
  "loadPostedList",
  "loadPostedRecord",
  "loadPostedStats",
  "loadAccounts",
  "loadReelshortDetail",
  "loadObserveSources",
  "resolveVersion",
  "setBoardScope",
  "requireBoardUser",
  "pickDataNextPath",
  "getPickSync",
  "loadReplay",
  "loadRowsByKeys",
  "loadMissingKeys",
  "MirrorError",
  "MirrorUnavailable",
  "MirrorMisconfigured",
  "MirrorVersionGone",
  "MirrorBusy",
  // the radar's two tabs (TR-24)
  "loadObsTab",
  "ObsError",
  "ObsNotReady",
  "ObsUnreadable",
  "ObsRowInvalid",
] as const;

const INTERNAL = [
  "getPool",
  "resetMirrorPoolForTests",
  "createMirrorPool",
  "parseReaderUrl",
  "makeScope",
  "mirrorTypes",
  "checkVersionSchema",
  "getDb",
  "controlDb",
  "versionDb",
  "withScriptScope",
  "translateMirrorError",
  "errorCode",
  "isConnectionFailure",
  "ROW_COLUMN_NAMES",
  "loadRows",
  "loadRowsByIds",
  "loadDramaDetail",
  "loadBillRows",
  "loadBillTotals",
  "loadRsCounts",
  "loadGrowthBaseline",
  "readSources",
  "readVersionMeta",
  "cachedVersionDb",
  "resetVersionCacheForTests",
  "unionRows",
  "filtersFor",
  // the radar's executor, error translation and cache (TR-24)
  "obsDb",
  "translateObsError",
  "resetObsCacheForTests",
] as const;

describe("桶文件 @/server/pick-board", () => {
  it("转出页面要用的 loader、版本与鉴权入口、错误类", () => {
    const entry = board as Record<string, unknown>;
    for (const name of PAGE_ENTRY)
      expect(typeof entry[name], name).toBe("function");
    expect(board.PICK_DATA_PATH).toBe("/workspace/pick-data");
  });

  it("不转出连接池、执行器、SQL 片段与只给查询层用的 loader", () => {
    const exported = new Set(Object.keys(board));
    for (const name of INTERNAL) expect(exported.has(name), name).toBe(false);
  });
});
