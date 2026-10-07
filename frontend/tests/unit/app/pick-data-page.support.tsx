/**
 * Shared by the pick data board's page tests (pick-data-page.dom.test.tsx and
 * pick-data-page-replay.dom.test.tsx): the doubles each file installs with
 * rs.mock, the boards, sync answers and replay answers they read, and the DOM
 * helpers. Each test file keeps its own rs.hoisted state and passes it in; a
 * mock factory reaches this module with rs.requireActual, so nothing here may
 * import the page or @/server/pick-board (only its types and errors).
 */
import { render } from "@testing-library/react";
import type { PropsWithChildren, ReactElement, ReactNode } from "react";

import * as errors from "@/server/pick-board/errors";

import {
  accounts,
  billRow,
  billTotals,
  boardRules,
  observeRow,
  pickRow,
  postedLinks,
  postedRecord,
  rankMeta,
  rankRow,
  reelshortDetail,
  rowDetail,
} from "../components/workspace/pick-board/fixtures";

export type Loader = (...args: unknown[]) => unknown;

/** What the doubles record and answer; each test file owns one (rs.hoisted). */
export interface PageState {
  calls: string[];
  nextPath: string;
  access: unknown;
  resolved: unknown;
  sync: unknown;
  scope: unknown;
  loaders: Record<string, Loader>;
}

const LOADERS = [
  "loadTrendsTable",
  "loadTrendsCandidates",
  "loadCandidatePool",
  "loadPickRows",
  "loadFacets",
  "loadRowDetail",
  "loadRankMeta",
  "loadRankRows",
  "loadRsRank",
  "loadGrowthDiagnosis",
  "loadPostedList",
  "loadPostedRecord",
  "loadPostedStats",
  "loadAccounts",
  "loadReelshortDetail",
  "loadReplay",
  "loadRowsByKeys",
  "loadMissingKeys",
] as const;

function recorded(state: PageState, name: string): Loader {
  return async (...args: unknown[]) => {
    state.calls.push(name);
    const loader = state.loaders[name];
    if (!loader) throw new Error(`unexpected call: ${name}`);
    return loader(...args);
  };
}

/** @/server/pick-board as the page sees it: the real errors, every read a recorded double. */
export function pickBoardMock(state: PageState) {
  return {
    ...errors,
    PICK_DATA_PATH: "/workspace/pick-data",
    pickDataNextPath: (raw: Record<string, string>) =>
      `/workspace/pick-data?${new URLSearchParams(raw).toString()}`,
    requireBoardUser: async (nextPath: string) => {
      state.calls.push("requireBoardUser");
      state.nextPath = nextPath;
      if (state.access instanceof Error) throw state.access;
      return state.access;
    },
    resolveBoard: async (v: number | null) => {
      state.calls.push(`resolveBoard:${String(v)}`);
      if (state.resolved instanceof Error) throw state.resolved;
      return state.resolved;
    },
    getPickSync: async () => {
      state.calls.push("getPickSync");
      return state.sync;
    },
    setBoardScope: (scope: unknown) => {
      state.calls.push("setBoardScope");
      state.scope = scope;
    },
    freshnessOf: (raw: unknown) => raw,
    sourcesOf: () => ({}),
    ...Object.fromEntries(LOADERS.map((name) => [name, recorded(state, name)])),
  };
}

export const navigationMock = () => ({
  redirect: (url: string) => {
    throw new Error(`NEXT_REDIRECT ${url}`);
  },
});

export const linkMock = () => ({
  default: ({
    href,
    children,
    ...rest
  }: PropsWithChildren<{ href: string }>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
});

export const workspaceContainerMock = () => ({
  WorkspaceContainer: ({ children }: PropsWithChildren) => (
    <div>{children}</div>
  ),
  WorkspaceHeader: () => <div />,
  WorkspaceBody: ({ children }: PropsWithChildren) => <main>{children}</main>,
});

export const scrollAreaMock = () => ({
  ScrollArea: ({ children }: PropsWithChildren) => (
    <div data-testid="scroll">{children}</div>
  ),
});

export const dataImportsMock = () => ({
  DataImports: () => <div data-testid="data-imports" />,
});

export const AS_OF = "2026-09-24T03:40:00+00:00";
export const NOW = new Date("2026-09-24T06:00:00Z");

export const FRESHNESS = {
  importedAt: new Date("2026-09-24T01:00:00Z"),
  rows: 120,
  withSignal: 30,
  signals: 44,
  posted: 9,
  rsCanonical: 500,
  rsCandidates: 12,
  rsSyncedAt: new Date("2026-09-24T02:00:00Z"),
};

export type Board = Record<string, unknown>;

export function readyBoard(patch: Board = {}): Board {
  return {
    state: "ready",
    scope: {
      schema: "pickm_v000007",
      asOf: AS_OF,
      versionId: 7,
      rules: boardRules(),
    },
    current: {
      id: 7,
      asOf: AS_OF,
      publishedAt: "2026-09-24T03:52:00+00:00",
      agentCatalogBatchId: "b-cat",
      agentKnowledgeBatchId: "b-kn",
    },
    latestSnapshot: "2026-09-23",
    freshness: FRESHNESS,
    warnings: [],
    sources: {},
    series: { through: "2026-09-23", trimmedBefore: "2026-06-01" },
    requestedV: null,
    pinned: false,
    pruned: false,
    ignoredV: false,
    unreadable: false,
    ...patch,
  };
}

/** A ready board showing version `id` (the latest stays v7). */
export function boardAt(id: number, patch: Board = {}): Board {
  return readyBoard({
    scope: {
      schema: `pickm_v${String(id).padStart(6, "0")}`,
      asOf: AS_OF,
      versionId: id,
      rules: boardRules(),
    },
    ...patch,
  });
}

export const MIRROR = {
  enabled: true,
  current: {
    id: 7,
    as_of: "2026-09-24T03:40:00.000Z",
    latest_snapshot: "2026-09-23",
    published_at: "2026-09-24T03:52:00.000000+00:00",
  },
  series_through: "2026-09-23",
  trimmed_before: "2026-06-01",
  behind: false,
  consecutive_failures: 0,
  last_failure_at: null,
  last_failure: null,
  alert: false,
  warnings: [],
  lock_stuck: null,
  shared_source_as_of: "2026-09-24T03:40:00.000Z",
};

export function syncWith(patch: Record<string, unknown> = {}) {
  return {
    ok: true,
    data: {
      configured: true,
      current: {
        id: "b-cat",
        shared: true,
        source_as_of: "2026-09-24T03:40:00.000Z",
        published_at: null,
      },
      runs: [],
      mirror: MIRROR,
      ...patch,
    },
  };
}

export const RESULT = "0123456789abcdef0123456789abcdef";
const DEMO_IDENTITY = JSON.stringify([
  "realshort-pick",
  Buffer.from("kalos-demo-1", "utf8").toString("base64url"),
  "英语",
]);
export const CONDITIONS = {
  theater: null,
  language: null,
  channel: null,
  query: null,
  tags: [],
  limit: 5,
  exclude_selected: true,
  confirmed_eligible_only: true,
  exclude_previous: false,
  signal_kind: null,
  sort: "evidence_date",
  exclude_posted: false,
  posted_account: null,
};

export function replayAnswer(patch: Record<string, unknown> = {}) {
  return {
    resultId: RESULT,
    mirrorVersion: 7,
    limit: 5,
    total: 1,
    identities: [DEMO_IDENTITY],
    truncated: false,
    excludedReproducible: true,
    rankingReproducible: true,
    unmappable: ["exclude_selected"],
    sourceAsOf: "2026-09-24T03:40:00.000Z",
    ...patch,
  };
}

const FACETS = {
  platforms: { kalos: 1 },
  langs: [{ lang: "英语", n: 1 }],
  bases: { kd: 1 },
  posted: { no: 1, yes: 0, pool: 0 },
};

function rsRankLoader(_req: unknown, rank: unknown) {
  return rank === "rs_ledger"
    ? {
        kind: "ledger",
        rows: [billRow()],
        totals: billTotals(),
        source: undefined,
      }
    : {
        kind: "rows",
        rows: [observeRow()],
        total: 1,
        hasMore: false,
        sort: "rr",
      };
}

export function defaultLoaders(): Record<string, Loader> {
  return {
    loadCandidatePool: () => 42,
    loadPickRows: () => ({ rows: [pickRow()], total: 1, hasMore: false }),
    loadFacets: () => FACETS,
    loadRowDetail: () => rowDetail(),
    loadRankMeta: () => rankMeta(),
    loadRankRows: () => ({ rows: [rankRow()], total: 1, hasMore: false }),
    loadRsRank: rsRankLoader,
    loadGrowthDiagnosis: () => null,
    loadPostedList: () => ({
      rows: [postedRecord()],
      total: 1,
      hasMore: false,
      counts: { "": 1, pub: 1, sched: 0, none: 0, nomatch: 0 },
      links: postedLinks(),
    }),
    loadPostedRecord: () => ({ record: postedRecord(), links: postedLinks() }),
    loadPostedStats: () => ({
      total: 1,
      pubCount: 1,
      postsSum: 2,
      viewsSum: 30,
      metricAt: "2026-09-22",
      importedAt: new Date("2026-09-23T00:00:00Z"),
      accountCount: 2,
    }),
    loadAccounts: () => accounts(),
    loadReelshortDetail: () => reelshortDetail(),
    loadReplay: () => ({
      kind: "ok",
      answer: replayAnswer(),
      conditions: CONDITIONS,
    }),
    loadRowsByKeys: () => [pickRow()],
    loadMissingKeys: () => [],
  };
}

/** Back to a signed-in visitor on the ready v7 board, every read answering. */
export function resetState(state: PageState): void {
  state.calls = [];
  state.nextPath = "";
  state.access = { kind: "ok", user: { id: "u-1" } };
  state.resolved = readyBoard();
  state.sync = syncWith();
  state.scope = null;
  state.loaders = defaultLoaders();
}

type Page = (props: {
  searchParams: Promise<Record<string, string>>;
}) => Promise<ReactElement>;

/** The page is awaited first and its element rendered after, as projects-page.dom.test does. */
export async function renderWith(
  page: Page,
  params: Record<string, string> = {},
): Promise<HTMLElement> {
  const element: ReactElement = await page({
    searchParams: Promise.resolve(params),
  });
  return render(element as ReactNode as ReactElement).container;
}

/** The reads, in order: gateway, version and mirror calls, not the scope or auth steps. */
export function dataCallsOf(state: PageState): string[] {
  return state.calls.filter(
    (name) =>
      name.startsWith("load") ||
      name.startsWith("resolveBoard") ||
      name === "getPickSync",
  );
}

export function tabLinks(root: HTMLElement): HTMLAnchorElement[] {
  const nav = root.querySelector('[data-board-tabs="true"]');
  if (!nav) throw new Error("no tab bar");
  return Array.from(nav.querySelectorAll("a"));
}
