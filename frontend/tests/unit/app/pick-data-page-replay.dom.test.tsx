/**
 * The pick data board's page on a replay link, tab=pick&result= (P4-2;
 * critique B10, B11, C27): the gateway's list first, then the result's
 * paired version, then only this page's rows. The doubles, boards and
 * answers are pick-data-page.support.tsx's, shared with
 * pick-data-page.dom.test.tsx.
 */
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, screen, within } from "@testing-library/react";

import * as pageModule from "@/app/workspace/pick-data/page";
import * as errors from "@/server/pick-board/errors";

import type * as Support from "./pick-data-page.support";
import {
  CONDITIONS,
  NOW,
  RESULT,
  boardAt,
  dataCallsOf,
  readyBoard,
  renderWith,
  replayAnswer,
  resetState,
  tabLinks,
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

describe("replay: tab=pick&result= (P4-2)", () => {
  const replayOf =
    (patch: Record<string, unknown> = {}, conditions = CONDITIONS) =>
    () => ({ kind: "ok", answer: replayAnswer(patch), conditions });
  const texts = (role: "status" | "alert") =>
    screen.queryAllByRole(role).map((n) => n.textContent ?? "");

  it("asks the gateway first, then pins the result's version, then reads only this page's rows", async () => {
    state.loaders.loadReplay = replayOf({ mirrorVersion: 5 });
    state.resolved = boardAt(5, { pinned: true, requestedV: 5 });
    await renderPage({ result: RESULT });
    expect(dataCalls()).toEqual([
      "loadReplay",
      "getPickSync",
      "resolveBoard:5",
      "loadCandidatePool",
      "loadRowsByKeys",
      "loadMissingKeys",
    ]);
    const scopeAt = state.calls.indexOf("setBoardScope");
    expect(scopeAt).toBeGreaterThan(state.calls.indexOf("resolveBoard:5"));
    expect(scopeAt).toBeLessThan(state.calls.indexOf("loadRowsByKeys"));
    expect(screen.getByText("回放智能体候选")).toBeTruthy();
    expect(screen.getAllByText("Demo Bride").length).toBeGreaterThan(0);
    expect(texts("status").join("\n")).toContain(
      "按这份候选配对的镜像版本 v5 回放",
    );
    // The page's own pinned banner (and its "switch" link back into the replay) is not shown.
    expect(texts("status").join("\n")).not.toContain(
      "正在看智能体当时用的版本",
    );
  });

  it("B10: the page's keys to loadRowsByKeys, the whole list to loadMissingKeys", async () => {
    const list = Array.from({ length: 30 }, (_, n) =>
      JSON.stringify([
        "realshort-pick",
        Buffer.from(`k-${n + 1}`, "utf8").toString("base64url"),
        "英语",
      ]),
    );
    const seen: unknown[][] = [];
    state.loaders.loadReplay = replayOf({ identities: list, total: 30 });
    state.loaders.loadRowsByKeys = (keys: unknown) => {
      seen.push(["rows", keys]);
      return [];
    };
    state.loaders.loadMissingKeys = (keys: unknown) => {
      seen.push(["missing", keys]);
      return [];
    };
    await renderPage({ result: RESULT, page: "2", size: "20" });
    const keys = (from: number, to: number) =>
      Array.from({ length: to - from + 1 }, (_, n) => `k-${n + from}`);
    expect(seen).toEqual([
      ["rows", keys(21, 30)],
      ["missing", keys(1, 30)],
    ]);
  });

  it("B11: the result's paired version wins over the link's v, with a banner", async () => {
    state.loaders.loadReplay = replayOf({ mirrorVersion: 5 });
    state.resolved = boardAt(5, { pinned: true, requestedV: 5 });
    await renderPage({ result: RESULT, v: "9" });
    expect(dataCalls()).toContain("resolveBoard:5");
    expect(texts("status")).toContain(
      "链接里的版本 v9 与这份候选配对的版本 v5 不一致，已按配对的版本回放",
    );
  });

  it("B11: no paired version reads the current version and says so", async () => {
    state.loaders.loadReplay = replayOf({ mirrorVersion: null });
    await renderPage({ result: RESULT });
    expect(dataCalls()).toContain("resolveBoard:null");
    expect(texts("status")).toContain(
      "这份候选当时没有配对的镜像版本，行数据取自当前版本 v7",
    );
  });

  it("the paired version pruned: current rows, the fallback banner, missing rows listed", async () => {
    state.loaders.loadReplay = replayOf({ mirrorVersion: 5 });
    state.resolved = readyBoard({ pruned: true, requestedV: 5 });
    state.loaders.loadMissingKeys = () => ["kalos-demo-1"];
    state.loaders.loadRowsByKeys = () => [];
    await renderPage({ result: RESULT });
    expect(texts("status")).toContain(
      "镜像 v5 已清理：名单与顺序按智能体当时的批次，行数据取自当前版本 v7，可能与当时不同",
    );
    expect(texts("status").join("\n")).not.toContain("链接里的版本 v5 已清理");
    expect(texts("alert").join("\n")).toContain(
      "第 1 位 · kalos-demo-1：当前版本已无此行",
    );
  });

  it("C27: the 选剧 tab leaves the replay; every link carries the shown v", async () => {
    state.loaders.loadReplay = replayOf({ mirrorVersion: 5 });
    state.resolved = boardAt(5, { pinned: true, requestedV: 5 });
    const root = await renderPage({ result: RESULT });
    expect(tabLinks(root)[0]?.getAttribute("href")).toBe(
      "/workspace/pick-data?v=5",
    );
    for (const a of tabLinks(root))
      expect(a.getAttribute("href")).not.toContain("result=");
    // The one way out to the latest version is the banner's, and it drops the result.
    const latest = screen.getByText("看当前版本的选剧列表");
    expect(latest.getAttribute("href")).toBe("/workspace/pick-data?v=7");
    const internal = Array.from(root.querySelectorAll("a"))
      .filter((a) => a !== latest)
      .map((a) => a.getAttribute("href") ?? "")
      .filter((h) => h.startsWith("/workspace/pick-data?"));
    expect(internal.length).toBeGreaterThan(5);
    for (const href of internal) expect(href).toMatch(/[?&]v=5(?:&|$)/);
  });

  it("410, 404 and 409: a notice on the URL's version, no rows read", async () => {
    const cases = [
      ["gone", "这份候选用的剧库批次已过保留期被清理，无法回放。"],
      ["notFound", "找不到这份候选：链接不完整，或它不是你的结果。"],
      ["conflict", "这份候选的条件已不能按当前规则重跑，无法回放。"],
      [
        "unavailable",
        "暂时拿不到这份候选的回放（工作台后端没有回应），稍后刷新再试。",
      ],
    ] as const;
    for (const [kind, text] of cases) {
      state.calls = [];
      state.loaders.loadReplay = () =>
        kind === "notFound" || kind === "unavailable"
          ? { kind }
          : { kind, conditions: CONDITIONS };
      const root = await renderPage({ result: RESULT, v: "7" });
      expect(dataCalls()).toEqual([
        "loadReplay",
        "getPickSync",
        "resolveBoard:7",
        "loadCandidatePool",
      ]);
      expect(texts("alert")).toContain(text);
      const near = root.querySelector('[data-replay-near="true"]');
      expect(near === null).toBe(kind === "notFound" || kind === "unavailable");
      cleanup();
    }
  });

  it("a notice on a pinned version: the page's own banners leave the replay too", async () => {
    // A card link ?result=X&v=5 whose replay fails, v5 readable but not the latest:
    // 切换到最新 must not carry the result back into the same failed replay.
    const answers = [
      { kind: "gone", conditions: CONDITIONS },
      { kind: "conflict", conditions: CONDITIONS },
      { kind: "notFound" },
      { kind: "unavailable" },
    ];
    state.resolved = boardAt(5, { pinned: true, requestedV: 5 });
    for (const answer of answers) {
      state.loaders.loadReplay = () => answer;
      const root = await renderPage({ result: RESULT, v: "5" });
      expect(texts("status").join("\n")).toContain(
        "正在看智能体当时用的版本 v5",
      );
      const latest = within(root).getByText("切换到最新");
      expect(latest.getAttribute("href")).toBe("/workspace/pick-data");
      cleanup();
    }
  });

  it("a gateway 401 on the replay sends the visitor to login with next", async () => {
    state.loaders.loadReplay = () => ({ kind: "unauthenticated" });
    await expect(
      PickDataPage({ searchParams: Promise.resolve({ result: RESULT }) }),
    ).rejects.toThrow(
      `NEXT_REDIRECT /login?next=${encodeURIComponent(`/workspace/pick-data?result=${RESULT}`)}`,
    );
    expect(dataCalls()).not.toContain("loadRowsByKeys");
  });

  it("the version pruned while reading the replay's rows: 打开当前版本 goes on replaying", async () => {
    state.loaders.loadReplay = replayOf({ mirrorVersion: 5 });
    state.resolved = boardAt(5, { pinned: true, requestedV: 5 });
    state.loaders.loadMissingKeys = () => {
      throw new errors.MirrorVersionGone("3F000");
    };
    const root = await renderPage({ result: RESULT, v: "5" });
    expect(screen.getByText(/该版本刚被清理/)).toBeTruthy();
    expect(within(root).getByText("打开当前版本").getAttribute("href")).toBe(
      `/workspace/pick-data?result=${RESULT}`,
    );
  });

  it("busy while reading the replay's rows: header and tabs stay, a notice replaces the list", async () => {
    state.loaders.loadRowsByKeys = () => {
      throw new errors.MirrorBusy("57014");
    };
    const root = await renderPage({ result: RESULT });
    expect(screen.getByText(/镜像库繁忙，请稍后刷新/)).toBeTruthy();
    expect(screen.getByTestId("board-header")).toBeTruthy();
    expect(tabLinks(root).length).toBe(6);
  });
});
