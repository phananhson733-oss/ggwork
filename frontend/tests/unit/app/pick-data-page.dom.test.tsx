/**
 * The pick data board's page (P3-5): which branch each request takes, in
 * which order it checks the visitor, resolves the version and reads, and
 * what it tells the reader when the mirror cannot answer. The data layer
 * (@/server/pick-board) is replaced by doubles that record their calls; the
 * views and the ported components render for real. The page is awaited
 * first and its element rendered after, as projects-page.dom.test does.
 */
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { PropsWithChildren, ReactElement, ReactNode } from "react";

import * as pageModule from "@/app/workspace/pick-data/page";
import * as errors from "@/server/pick-board/errors";
import type * as ErrorsModule from "@/server/pick-board/errors";

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

const state = rs.hoisted(() => ({
  calls: [] as string[],
  nextPath: "",
  access: { kind: "ok", user: { id: "u-1" } } as unknown,
  resolved: null as unknown,
  sync: null as unknown,
  scope: null as unknown,
  loaders: {} as Record<string, (...args: unknown[]) => unknown>,
}));

rs.mock("@/server/pick-board", () => {
  const errors = rs.requireActual<typeof ErrorsModule>(
    "@/server/pick-board/errors",
  );
  const recorded =
    (name: string) =>
    async (...args: unknown[]) => {
      state.calls.push(name);
      const loader = state.loaders[name];
      if (!loader) throw new Error(`unexpected call: ${name}`);
      return loader(...args);
    };
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
    loadCandidatePool: recorded("loadCandidatePool"),
    loadPickRows: recorded("loadPickRows"),
    loadFacets: recorded("loadFacets"),
    loadRowDetail: recorded("loadRowDetail"),
    loadRankMeta: recorded("loadRankMeta"),
    loadRankRows: recorded("loadRankRows"),
    loadRsRank: recorded("loadRsRank"),
    loadGrowthDiagnosis: recorded("loadGrowthDiagnosis"),
    loadPostedList: recorded("loadPostedList"),
    loadPostedRecord: recorded("loadPostedRecord"),
    loadPostedStats: recorded("loadPostedStats"),
    loadAccounts: recorded("loadAccounts"),
    loadReelshortDetail: recorded("loadReelshortDetail"),
  };
});

rs.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: PropsWithChildren<{ href: string }>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

rs.mock("@/components/workspace/workspace-container", () => ({
  WorkspaceContainer: ({ children }: PropsWithChildren) => (
    <div>{children}</div>
  ),
  WorkspaceHeader: () => <div />,
  WorkspaceBody: ({ children }: PropsWithChildren) => <main>{children}</main>,
}));

rs.mock("@/components/ui/scroll-area", () => ({
  ScrollArea: ({ children }: PropsWithChildren) => (
    <div data-testid="scroll">{children}</div>
  ),
}));

rs.mock("@/components/workspace/pick/data-imports", () => ({
  DataImports: () => <div data-testid="data-imports" />,
}));

const PickDataPage = pageModule.default;

const AS_OF = "2026-09-24T03:40:00+00:00";
const NOW = new Date("2026-09-24T06:00:00Z");

const FRESHNESS = {
  importedAt: new Date("2026-09-24T01:00:00Z"),
  rows: 120,
  withSignal: 30,
  signals: 44,
  posted: 9,
  rsCanonical: 500,
  rsCandidates: 12,
  rsSyncedAt: new Date("2026-09-24T02:00:00Z"),
};

type Board = Record<string, unknown>;

function readyBoard(patch: Board = {}): Board {
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

const MIRROR = {
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

function syncWith(patch: Record<string, unknown> = {}) {
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

const FACETS = {
  platforms: { kalos: 1 },
  langs: [{ lang: "英语", n: 1 }],
  bases: { kd: 1 },
  posted: { no: 1, yes: 0, pool: 0 },
};

function defaultLoaders(): Record<string, (...args: unknown[]) => unknown> {
  return {
    loadCandidatePool: () => 42,
    loadPickRows: () => ({ rows: [pickRow()], total: 1, hasMore: false }),
    loadFacets: () => FACETS,
    loadRowDetail: () => rowDetail(),
    loadRankMeta: () => rankMeta(),
    loadRankRows: () => ({ rows: [rankRow()], total: 1, hasMore: false }),
    loadRsRank: (_req: unknown, rank: unknown) =>
      rank === "rs_ledger"
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
          },
    loadGrowthDiagnosis: () => null,
    loadPostedList: () => ({
      rows: [postedRecord()],
      total: 1,
      hasMore: false,
      counts: { "": 1, pub: 1, sched: 0, none: 0, nomatch: 0 },
      links: postedLinks(),
    }),
    loadPostedRecord: () => ({
      record: postedRecord(),
      links: postedLinks(),
    }),
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
  };
}

async function renderPage(
  params: Record<string, string> = {},
): Promise<HTMLElement> {
  const element: ReactElement = await PickDataPage({
    searchParams: Promise.resolve(params),
  });
  return render(element as ReactNode as ReactElement).container;
}

const dataCalls = () =>
  state.calls.filter(
    (name) =>
      name.startsWith("load") ||
      name.startsWith("resolveBoard") ||
      name === "getPickSync",
  );

function tabLinks(root: HTMLElement): HTMLAnchorElement[] {
  const nav = root.querySelector('[data-board-tabs="true"]');
  if (!nav) throw new Error("no tab bar");
  return Array.from(nav.querySelectorAll("a"));
}

beforeEach(() => {
  rs.useFakeTimers({ toFake: ["Date"] });
  rs.setSystemTime(NOW);
  state.calls = [];
  state.nextPath = "";
  state.access = { kind: "ok", user: { id: "u-1" } };
  state.resolved = readyBoard();
  state.sync = syncWith();
  state.scope = null;
  state.loaders = defaultLoaders();
});

afterEach(() => {
  cleanup();
  rs.useRealTimers();
});

describe("generateMetadata", () => {
  it("titles each tab without a query", async () => {
    const meta = await pageModule.generateMetadata({
      searchParams: Promise.resolve({ tab: "rank" }),
    });
    expect(meta.title).toBe("选剧资料 · 榜单");
    const imports = await pageModule.generateMetadata({
      searchParams: Promise.resolve({ tab: "imports" }),
    });
    expect(imports.title).toBe("选剧资料 · 同步与导入");
    expect(state.calls).toEqual([]);
  });

  it("renders per request, for up to a minute", () => {
    expect(pageModule.dynamic).toBe("force-dynamic");
    expect(pageModule.maxDuration).toBe(60);
  });
});

describe("each tab takes its branch", () => {
  it("pick and all read rows and facets", async () => {
    for (const tab of ["pick", "all"]) {
      state.calls = [];
      await renderPage({ tab });
      expect(dataCalls()).toEqual([
        "resolveBoard:null",
        "getPickSync",
        "loadCandidatePool",
        "loadPickRows",
        "loadFacets",
      ]);
      expect(screen.getAllByText("Demo Bride").length).toBeGreaterThan(0);
      cleanup();
    }
  });

  it("a theater rank reads its meta and rows", async () => {
    await renderPage({ tab: "rank", rk: "kd" });
    expect(dataCalls()).toContain("loadRankRows");
    expect(dataCalls()).not.toContain("loadRsRank");
    expect(screen.getByText(/榜单 tab 一律含已下架的行/)).toBeTruthy();
  });

  it("a ReelShort rank reads loadRsRank and shows its rows", async () => {
    await renderPage({ tab: "rank", rk: "rs_rr" });
    expect(dataCalls()).toContain("loadRsRank");
    expect(dataCalls()).not.toContain("loadRankRows");
    expect(screen.getAllByText("Demo Heir").length).toBeGreaterThan(0);
  });

  it("the orders ledger renders its table", async () => {
    await renderPage({ tab: "rank", rk: "rs_ledger" });
    expect(screen.getAllByText(/订单对账/).length).toBeGreaterThan(0);
  });

  it("an empty growth rank asks for its diagnosis", async () => {
    state.loaders.loadRsRank = () => ({
      kind: "rows",
      rows: [],
      total: null,
      hasMore: false,
      sort: "d7",
    });
    state.loaders.loadGrowthDiagnosis = () => ({
      reason: "baseline_snapshot_missing",
      baselineDay: "2026-09-17",
      earliestVerifiedOn: null,
      earliestPossibleOn: null,
      filtered: false,
    });
    await renderPage({ tab: "rank", rk: "rs_growth" });
    expect(dataCalls()).toContain("loadGrowthDiagnosis");
    expect(screen.getByText(/那天没有快照/)).toBeTruthy();
  });

  it("posted reads list, stats and accounts; sd reads one record", async () => {
    await renderPage({ tab: "posted" });
    expect(dataCalls()).toEqual(
      expect.arrayContaining([
        "loadPostedList",
        "loadPostedStats",
        "loadAccounts",
      ]),
    );
    cleanup();
    state.calls = [];
    await renderPage({ tab: "posted", sd: "SD-000001" });
    expect(dataCalls()).toContain("loadPostedRecord");
    expect(dataCalls()).not.toContain("loadPostedList");
    expect(screen.getByText("← 返回发布记录")).toBeTruthy();
  });

  it("rules reads nothing beyond the version", async () => {
    await renderPage({ tab: "rules" });
    expect(dataCalls()).toEqual([
      "resolveBoard:null",
      "getPickSync",
      "loadCandidatePool",
    ]);
    expect(screen.getAllByText("TouchShort").length).toBeGreaterThan(0);
  });

  it("a row page reads a theater row or a ReelShort drama by its key", async () => {
    await renderPage({ tab: "row", row: "kalos-demo-1" });
    expect(dataCalls()).toContain("loadRowDetail");
    cleanup();
    state.calls = [];
    await renderPage({ tab: "row", row: "reelshort-demo0006" });
    expect(dataCalls()).toContain("loadReelshortDetail");
    expect(dataCalls()).not.toContain("loadRowDetail");
  });

  it("a row page without a key or with a missing row says so", async () => {
    await renderPage({ tab: "row" });
    expect(screen.getByText("从列表里点剧名进证据页。")).toBeTruthy();
    cleanup();
    state.loaders.loadReelshortDetail = () => null;
    await renderPage({ tab: "row", row: "reelshort-zz9999" });
    expect(
      screen.getByText(/ReelShort 片库里找不到这部剧（zz9999）/),
    ).toBeTruthy();
  });

  it("pick with a result id still shows the list, and no link carries result", async () => {
    const root = await renderPage({
      tab: "pick",
      result: "0123456789abcdef0123456789abcdef",
    });
    expect(dataCalls()).toContain("loadPickRows");
    const hrefs = Array.from(root.querySelectorAll("a")).map(
      (a) => a.getAttribute("href") ?? "",
    );
    expect(hrefs.some((h) => h.includes("result="))).toBe(false);
  });

  it("imports: tabs without badges, the imports panel, no mirror and no gateway", async () => {
    const root = await renderPage({
      tab: "imports",
      result: "0123456789abcdef0123456789abcdef",
    });
    expect(dataCalls()).toEqual([]);
    expect(state.calls).toEqual(["requireBoardUser"]);
    expect(screen.getByTestId("data-imports")).toBeTruthy();
    const links = tabLinks(root);
    expect(links.map((a) => a.textContent)).toEqual([
      "选剧",
      "全部剧库",
      "榜单",
      "发布记录",
      "剧场规则",
      "同步与导入",
    ]);
    expect(links[0]?.getAttribute("href")).toBe("/workspace/pick-data");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "选剧资料",
    );
  });
});

describe("the resolved version", () => {
  it("every internal link and form carries the resolved v", async () => {
    const root = await renderPage({ tab: "pick" });
    const internal = Array.from(root.querySelectorAll("a"))
      .map((a) => a.getAttribute("href") ?? "")
      .filter((h) => h.startsWith("/"));
    expect(internal.length).toBeGreaterThan(5);
    for (const href of internal)
      expect(href).toMatch(/^\/workspace\/pick-data\?(?:.*&)?v=7(?:&|$)/);
    const forms = Array.from(root.querySelectorAll("form"));
    expect(forms.length).toBeGreaterThan(0);
    for (const form of forms)
      expect(
        form
          .querySelector('input[type="hidden"][name="v"]')
          ?.getAttribute("value"),
      ).toBe("7");
  });

  it("asks for the URL's v and sets the scope before any loader", async () => {
    await renderPage({ tab: "pick", v: "5" });
    expect(state.calls[0]).toBe("requireBoardUser");
    expect(state.calls).toContain("resolveBoard:5");
    const scopeAt = state.calls.indexOf("setBoardScope");
    const firstLoader = state.calls.findIndex((c) => c.startsWith("load"));
    expect(scopeAt).toBeGreaterThan(-1);
    expect(scopeAt).toBeLessThan(firstLoader);
    expect(state.scope).toBe((state.resolved as Board).scope);
  });

  it("filters follow the version's rules (yt / inuse)", async () => {
    const rules = boardRules((raw) => {
      raw.inUse = ["kalos"];
    });
    state.resolved = readyBoard({
      scope: { schema: "pickm_v000007", asOf: AS_OF, versionId: 7, rules },
    });
    const root = await renderPage({ tab: "pick", inuse: "1" });
    expect((state.scope as { rules: unknown }).rules).toBe(rules);
    expect(within(root).getByText(/其他剧场 9 个/)).toBeTruthy();
  });

  it("the header: freshness, curve end min(latest_snapshot, through), pool N", async () => {
    state.resolved = readyBoard({
      latestSnapshot: "2026-09-23",
      series: { through: "2026-09-21", trimmedBefore: null },
    });
    await renderPage();
    const header = screen.getByTestId("board-header").textContent ?? "";
    expect(header).toContain("剧单导入于 2026-09-24 01:00 UTC");
    expect(header).toContain("ReelShort 指标采集 2026-09-24 02:00 UTC");
    expect(header).toContain("镜像 v7 采集于 2026-09-24 03:40 UTC");
    expect(header).toContain("曲线截至 2026-09-21");
    expect(header).toContain("截至 2026-09-24 03:40 UTC，不是实时数据");
    expect(header).toContain("智能体候选池 42 部");
    cleanup();
    state.resolved = readyBoard({
      latestSnapshot: "2026-09-22",
      series: { through: "2026-09-23", trimmedBefore: null },
    });
    await renderPage();
    expect(screen.getByTestId("board-header").textContent).toContain(
      "曲线截至 2026-09-22",
    );
  });

  it("tab badges come from the version's freshness", async () => {
    const root = await renderPage();
    const labels = tabLinks(root).map((a) => a.textContent);
    expect(labels).toEqual([
      "选剧42",
      "全部剧库620",
      "榜单",
      "发布记录9",
      "剧场规则",
      "同步与导入",
    ]);
  });

  it("the footer: sources and the differences from RealShort", async () => {
    await renderPage();
    expect(screen.getByText("与 RealShort 选剧台的差异")).toBeTruthy();
    expect(screen.getByText(/网盘只显示有没有/)).toBeTruthy();
  });

  it("the trimmed-curve note reaches the ReelShort evidence page", async () => {
    state.resolved = readyBoard({
      series: { through: "2026-09-23", trimmedBefore: "2026-09-01" },
    });
    await renderPage({ tab: "row", row: "reelshort-demo0001" });
    expect(
      screen.getAllByText(/早于 2026-09-01 的曲线点已清理/).length,
    ).toBeGreaterThan(0);
  });
});

describe("banners", () => {
  it("pinned, pruned and ignored v", async () => {
    state.resolved = readyBoard({
      pinned: true,
      requestedV: 5,
      scope: {
        schema: "pickm_v000005",
        asOf: AS_OF,
        versionId: 5,
        rules: boardRules(),
      },
    });
    await renderPage({ v: "5" });
    expect(screen.getByText(/正在看智能体当时用的版本 v5/)).toBeTruthy();
    expect(screen.getByText("切换到最新").getAttribute("href")).toBe(
      "/workspace/pick-data",
    );
    cleanup();
    state.resolved = readyBoard({ pruned: true, requestedV: 3 });
    await renderPage({ v: "3" });
    expect(
      screen.getByText("链接里的版本 v3 已清理，已显示当前版本 v7"),
    ).toBeTruthy();
    cleanup();
    state.resolved = readyBoard({ ignoredV: true, requestedV: 9 });
    await renderPage({ v: "9" });
    expect(
      screen.getByText("链接里的版本 v9 不存在或未发布，已显示当前版本 v7"),
    ).toBeTruthy();
  });

  it("personal import, behind, alert and catalog_import_incomplete", async () => {
    state.sync = syncWith({
      current: {
        id: "b-mine",
        shared: false,
        source_as_of: null,
        published_at: null,
      },
      mirror: { ...MIRROR, behind: true, alert: true, consecutive_failures: 3 },
    });
    state.resolved = readyBoard({
      warnings: [
        {
          code: "catalog_import_incomplete",
          source: "pick_catalog",
          status: "failed",
          attemptedAt: "2026-09-24T01:00:00.000Z",
        },
      ],
    });
    await renderPage();
    const statuses = screen.getAllByRole("status").map((n) => n.textContent);
    const alerts = screen.getAllByRole("alert").map((n) => n.textContent);
    expect(statuses.join("\n")).toContain("智能体当前用的是你手动导入的剧库");
    expect(statuses.join("\n")).toContain("资料页落后于智能体");
    expect(alerts.join("\n")).toContain("镜像同步已连续失败 3 次");
    expect(alerts.join("\n")).toContain("剧单导入不完整");
  });

  it("no mirror field: no mirror banner and no crash", async () => {
    state.sync = syncWith({ mirror: undefined });
    await renderPage();
    expect(screen.queryAllByRole("status")).toEqual([]);
    expect(screen.getAllByText("Demo Bride").length).toBeGreaterThan(0);
  });

  it("no /sync: one line says so and the data still shows", async () => {
    state.sync = { ok: false, status: "unavailable" };
    await renderPage();
    expect(screen.getByText("暂时拿不到同步状态")).toBeTruthy();
    expect(screen.getAllByText("Demo Bride").length).toBeGreaterThan(0);
  });

  it("rule drift", async () => {
    const rules = boardRules((raw) => {
      raw.platformRules.newtv = {
        ...raw.platformRules.touchshort,
        key: "newtv",
        name: "NewTV",
      };
    });
    state.resolved = readyBoard({
      scope: { schema: "pickm_v000007", asOf: AS_OF, versionId: 7, rules },
    });
    await renderPage();
    expect(
      screen.getByText("RealShort 新增了剧场（newtv），部分标签可能过时"),
    ).toBeTruthy();
  });
});

describe("when the mirror cannot answer", () => {
  const onlyImportsLinks = (root: HTMLElement) =>
    tabLinks(root).map((a) => a.getAttribute("href"));

  it("no reader configured: a notice, and only imports is a link", async () => {
    state.resolved = new errors.MirrorUnavailable();
    const root = await renderPage({ tab: "pick" });
    expect(screen.getByText(/此部署未连接镜像库/)).toBeTruthy();
    expect(onlyImportsLinks(root)).toEqual([
      "/workspace/pick-data?tab=imports",
    ]);
    expect(state.calls).not.toContain("setBoardScope");
    expect(dataCalls().filter((c) => c.startsWith("load"))).toEqual([]);
  });

  it("no version published yet", async () => {
    state.resolved = { state: "empty", requestedV: null, series: null };
    const root = await renderPage({ tab: "all" });
    expect(screen.getByText(/镜像还没有发布任何版本/)).toBeTruthy();
    expect(onlyImportsLinks(root)).toEqual([
      "/workspace/pick-data?tab=imports",
    ]);
  });

  it("a missing grant (42501) is a notice, not the error page (A2)", async () => {
    state.resolved = new errors.MirrorMisconfigured("permission", "42501");
    await renderPage();
    expect(
      screen.getByText(/镜像版本不可读（授权缺失），请联系管理员/),
    ).toBeTruthy();
    cleanup();
    state.resolved = new errors.MirrorMisconfigured("current_unreadable");
    await renderPage();
    expect(
      screen.getByText(/镜像版本不可读（授权缺失），请联系管理员/),
    ).toBeTruthy();
  });

  it("control_missing, a bad connection and bad rules each have words", async () => {
    state.resolved = new errors.MirrorMisconfigured("control_missing", "42P01");
    await renderPage();
    expect(screen.getByText(/库里没有镜像表/)).toBeTruthy();
    cleanup();
    state.resolved = new errors.MirrorMisconfigured("tls");
    await renderPage();
    expect(screen.getByText(/读连接配置有误（tls）/)).toBeTruthy();
    cleanup();
    state.resolved = new errors.MirrorMisconfigured("rules");
    await renderPage();
    expect(screen.getByText(/镜像版本的规则形状本页不认识/)).toBeTruthy();
  });

  it("busy while resolving", async () => {
    state.resolved = new errors.MirrorBusy("57014");
    await renderPage();
    expect(screen.getByText(/镜像库繁忙，请稍后刷新/)).toBeTruthy();
  });

  it("busy or gone while reading a tab: header and tabs stay, a notice replaces the table", async () => {
    state.loaders.loadPickRows = () => {
      throw new errors.MirrorBusy("57014");
    };
    await renderPage();
    expect(screen.getByText(/镜像库繁忙，请稍后刷新/)).toBeTruthy();
    expect(screen.getByTestId("board-header")).toBeTruthy();
    cleanup();
    state.loaders.loadPickRows = () => {
      throw new errors.MirrorVersionGone("42P01");
    };
    const root = await renderPage({ tab: "all", v: "7" });
    expect(screen.getByText(/该版本刚被清理/)).toBeTruthy();
    const current = within(root).getByText("打开当前版本");
    expect(current.getAttribute("href")).toBe("/workspace/pick-data?tab=all");
  });

  it("a grant missing on a table while reading a tab is a notice too", async () => {
    state.loaders.loadFacets = () => {
      throw new errors.MirrorMisconfigured("permission", "42501");
    };
    await renderPage();
    expect(
      screen.getByText(/镜像版本不可读（授权缺失），请联系管理员/),
    ).toBeTruthy();
  });

  it("any other error goes on to error.tsx", async () => {
    state.loaders.loadPickRows = () => {
      throw new Error("boom");
    };
    await expect(
      PickDataPage({ searchParams: Promise.resolve({}) }),
    ).rejects.toThrow("boom");
    state.resolved = new Error("resolve boom");
    await expect(
      PickDataPage({ searchParams: Promise.resolve({}) }),
    ).rejects.toThrow("resolve boom");
  });
});

describe("the visitor", () => {
  it("is checked first, with the query re-serialized as next", async () => {
    await renderPage({ tab: "rank", day: "2026-09-20" });
    expect(state.calls[0]).toBe("requireBoardUser");
    expect(state.nextPath).toBe("/workspace/pick-data?tab=rank&day=2026-09-20");
  });

  it("a forbidden user sees a notice and nothing is read", async () => {
    for (const reason of ["forbidden", "gateway_unavailable", "config_error"]) {
      state.calls = [];
      state.access = { kind: "notice", reason };
      await renderPage({ tab: "pick" });
      expect(state.calls).toEqual(["requireBoardUser"]);
      expect(screen.getByRole("alert").textContent).toBeTruthy();
      cleanup();
    }
  });

  it("a signed-out visitor: requireBoardUser's redirect goes through, nothing is read", async () => {
    // At runtime the workspace layout usually redirects to /login first
    // (without next); whichever runs first, nothing is read.
    state.access = new Error("NEXT_REDIRECT");
    await expect(
      PickDataPage({ searchParams: Promise.resolve({ tab: "pick" }) }),
    ).rejects.toThrow("NEXT_REDIRECT");
    expect(state.calls).toEqual(["requireBoardUser"]);
  });
});
