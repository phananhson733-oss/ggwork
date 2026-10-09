/**
 * The pick data board's page (P3-5): which branch each request takes, in
 * which order it checks the visitor, resolves the version and reads, and
 * what it tells the reader when the mirror cannot answer. The data layer
 * (@/server/pick-board) is replaced by doubles that record their calls; the
 * views and the ported components render for real. The page is awaited
 * first and its element rendered after, as projects-page.dom.test does.
 * The replay branch (P4-2) is in pick-data-page-replay.dom.test.tsx; both
 * files share pick-data-page.support.tsx.
 */
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, screen, within } from "@testing-library/react";

import * as pageModule from "@/app/workspace/pick-data/page";
import * as errors from "@/server/pick-board/errors";

import { boardRules } from "../components/workspace/pick-board/fixtures";
import candidatesFixture from "../core/pick/fixtures/backend-trends-candidates.json";
import trendsFixture from "../core/pick/fixtures/backend-trends-table.json";

import type * as Support from "./pick-data-page.support";
import {
  AS_OF,
  MIRROR,
  NOW,
  RESULT,
  dataCallsOf,
  readyBoard,
  renderWith,
  resetState,
  syncWith,
  tabLinks,
  type Board,
  type PageState,
} from "./pick-data-page.support";

const state = rs.hoisted(
  (): PageState => ({
    calls: [],
    nextPath: "",
    access: null,
    resolved: null,
    sync: null,
    scope: null,
    loaders: {},
  }),
);

// Factories run while the imports above are evaluated, before this module's
// own constants exist: each one reaches the support module itself.
rs.mock("@/server/pick-board", () =>
  rs
    .requireActual<typeof Support>("./pick-data-page.support")
    .pickBoardMock(state),
);
rs.mock("next/navigation", () =>
  rs.requireActual<typeof Support>("./pick-data-page.support").navigationMock(),
);
rs.mock("next/link", () =>
  rs.requireActual<typeof Support>("./pick-data-page.support").linkMock(),
);
rs.mock("@/components/workspace/workspace-container", () =>
  rs
    .requireActual<typeof Support>("./pick-data-page.support")
    .workspaceContainerMock(),
);
rs.mock("@/components/ui/scroll-area", () =>
  rs.requireActual<typeof Support>("./pick-data-page.support").scrollAreaMock(),
);
rs.mock("@/components/workspace/pick/data-imports", () =>
  rs
    .requireActual<typeof Support>("./pick-data-page.support")
    .dataImportsMock(),
);

const PickDataPage = pageModule.default;
const renderPage = (params: Record<string, string> = {}) =>
  renderWith(PickDataPage, params);
const dataCalls = () => dataCallsOf(state);

beforeEach(() => {
  rs.useFakeTimers({ toFake: ["Date"] });
  rs.setSystemTime(NOW);
  resetState(state);
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
    const replay = await pageModule.generateMetadata({
      searchParams: Promise.resolve({ result: RESULT }),
    });
    expect(replay.title).toBe("选剧资料 · 回放候选");
    expect(state.calls).toEqual([]);
  });

  it("titles the default tab when called without searchParams", async () => {
    // A caller without request context (the removed Nextra docs build used
    // to call generateMetadata({})) still gets the default tab's title.
    const meta = await pageModule.generateMetadata({});
    expect(meta.title).toBe("选剧资料 · 选剧");
    expect(state.calls).toEqual([]);
  });

  it("renders per request, for up to a minute", () => {
    expect(pageModule.dynamic).toBe("force-dynamic");
    expect(pageModule.maxDuration).toBe(60);
  });
});

describe("each tab takes its branch", () => {
  it("warns when the latest available daily ranking is old even though the mirror is current", async () => {
    state.loaders.loadRankMeta = () => ({
      counts: { qc: 1 },
      days: ["2026-09-20"],
      day: "2026-09-20",
      dayResolution: "latest",
      weeks: [],
      week: "",
      weekResolution: "latest",
      grades: [],
      grade: "",
    });
    await renderPage({ tab: "rank", rk: "qc" });
    expect(screen.getByText(/最新榜期为 2026-09-20/)).toBeTruthy();
    expect(screen.getByText(/不能作为当前热门依据/)).toBeTruthy();
  });
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

  it("a result on another tab is ignored, and no link carries it", async () => {
    const root = await renderPage({ tab: "all", result: RESULT });
    expect(dataCalls()).not.toContain("loadReplay");
    expect(dataCalls()).toContain("loadPickRows");
    const hrefs = Array.from(root.querySelectorAll("a")).map(
      (a) => a.getAttribute("href") ?? "",
    );
    expect(hrefs.some((h) => h.includes("result="))).toBe(false);
  });

  it("a replay detail keeps its result on the return link through server rendering", async () => {
    await renderPage({
      tab: "row",
      row: "kalos-demo-1",
      from: "pick",
      result: RESULT,
      page: "2",
      v: "7",
    });
    const href = screen.getByText("← 返回选剧").getAttribute("href")!;
    const query = new URLSearchParams(href.split("?")[1]);
    expect(query.get("result")).toBe(RESULT);
    expect(query.get("page")).toBe("2");
    expect(query.get("v")).toBe("7");
    expect(dataCalls()).not.toContain("loadReplay");
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
      "Google 趋势",
    ]);
    expect(links[0]?.getAttribute("href")).toBe("/workspace/pick-data");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "选剧资料",
    );
  });
});

describe("the resolved version", () => {
  it("evidence links and forms carry the resolved v; current resources are explicit", async () => {
    const root = await renderPage({ tab: "pick" });
    const internal = Array.from(root.querySelectorAll("a"))
      .map((a) => a.getAttribute("href") ?? "")
      .filter((h) => h.startsWith("/workspace/pick-data"));
    expect(internal.length).toBeGreaterThan(5);
    for (const href of internal)
      expect(href).toMatch(/^\/workspace\/pick-data\?(?:.*&)?v=7(?:&|$)/);
    const resources = root.querySelectorAll(
      'a[href^="/workspace/pick-resources?"]',
    );
    expect(resources.length).toBeGreaterThan(0);
    for (const link of resources) {
      expect(link.textContent).toMatch(/当前(?:资源|取货资料)/);
      expect(link.getAttribute("href")).not.toContain("v=");
    }
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
      "Google 趋势",
    ]);
  });

  it("the footer explains owner-only resources and the retired website", async () => {
    await renderPage();
    expect(
      screen.getByText(/网盘与官方取货链接按资料所有者权限读取/),
    ).toBeTruthy();
    expect(screen.getByText(/原公开网站已停止服务/)).toBeTruthy();
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

  it("no reader configured: a notice, and imports and trends remain accessible", async () => {
    state.resolved = new errors.MirrorUnavailable();
    const root = await renderPage({ tab: "pick" });
    expect(screen.getByText(/此部署未连接镜像库/)).toBeTruthy();
    expect(onlyImportsLinks(root)).toEqual([
      "/workspace/pick-data?tab=trends",
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
      "/workspace/pick-data?tab=trends",
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

  it("gone while resolving: the link to the current version drops v and result", async () => {
    state.resolved = new errors.MirrorVersionGone("3F000");
    const root = await renderPage({
      tab: "pick",
      v: "5",
      result: "0123456789abcdef0123456789abcdef",
    });
    expect(screen.getByText(/该版本刚被清理/)).toBeTruthy();
    const current = within(root).getByText("打开当前版本");
    expect(current.getAttribute("href")).toBe("/workspace/pick-data");
    expect(state.calls).not.toContain("setBoardScope");
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

describe("simplified trends entry", () => {
  it("redirects when the session expires before candidate preview", async () => {
    state.loaders.loadTrendsTable = () => ({
      kind: "ok",
      table: { ...trendsFixture, batch: null, rows: [] },
    });
    state.loaders.loadTrendsCandidates = () => ({ kind: "unauthenticated" });
    await expect(renderPage({ tab: "trends", rv: "daily" })).rejects.toThrow(
      "NEXT_REDIRECT",
    );
    expect(state.calls).toEqual([
      "requireBoardUser",
      "loadTrendsTable",
      "loadTrendsCandidates",
    ]);
  });

  it("shows an empty selection without calling it a database failure", async () => {
    state.loaders.loadTrendsTable = () => ({
      kind: "ok",
      table: { ...trendsFixture, batch: null, rows: [] },
    });
    state.loaders.loadTrendsCandidates = () => ({
      kind: "ok",
      candidates: {
        ...candidatesFixture,
        selection_state: "empty",
        selected: 0,
        rows: [],
      },
    });
    await renderPage({ tab: "trends", rv: "daily" });
    expect(
      screen.getByText(/目前没有可用于趋势查询的榜单或收入候选/),
    ).toBeTruthy();
    expect(screen.queryByText(/待采集剧集暂时读不了/)).toBeNull();
  });
  it("loads only the authenticated gateway table, even without a mirror", async () => {
    state.resolved = new errors.MirrorUnavailable();
    state.loaders.loadTrendsTable = () => ({
      kind: "ok",
      table: {
        checked_at: "2026-10-06T03:00:00.000000+00:00",
        banners: [],
        batch: null,
        rows: [],
        row_limit: 200,
        truncated: false,
      },
    });
    state.loaders.loadTrendsCandidates = () => ({
      kind: "ok",
      candidates: candidatesFixture,
    });
    await renderPage({ tab: "trends", rv: "daily", ts: "order" });
    expect(state.calls).toEqual([
      "requireBoardUser",
      "loadTrendsTable",
      "loadTrendsCandidates",
    ]);
    expect(screen.getByRole("heading", { name: /待采集剧集/ })).toBeTruthy();
    expect(screen.getAllByText("First Choice")).toHaveLength(2);
    expect(screen.getByText(/尚未形成采集批次/)).toBeTruthy();
  });

  it("never loads current candidates over an existing observation batch", async () => {
    state.loaders.loadTrendsTable = () => ({
      kind: "ok",
      table: trendsFixture,
    });
    await renderPage({ tab: "trends", rv: "daily" });
    expect(state.calls).toEqual(["requireBoardUser", "loadTrendsTable"]);
    expect(screen.queryByRole("heading", { name: /待采集剧集/ })).toBeNull();
  });

  it("retains stop banners and the no-plan state when preview fails", async () => {
    state.loaders.loadTrendsTable = () => ({
      kind: "ok",
      table: { ...trendsFixture, batch: null, rows: [] },
    });
    state.loaders.loadTrendsCandidates = () => ({ kind: "unavailable" });
    await renderPage({ tab: "trends", rv: "daily" });
    expect(screen.getByText(/待采集剧集暂时读不了/)).toBeTruthy();
    expect(document.querySelector("[data-trends-banners]")).not.toBeNull();
    expect(
      document.querySelector("[data-trends-empty='batch']"),
    ).not.toBeNull();
  });

  it("retains a fixed notice when the table cannot be read", async () => {
    state.loaders.loadTrendsTable = () => ({ kind: "unavailable" });
    await renderPage({ tab: "trends", rv: "daily" });
    expect(state.calls).toEqual(["requireBoardUser", "loadTrendsTable"]);
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
