/**
 * The replay view (P4-2): the agent's list in its own order with the card's
 * first `limit` highlighted, rows missing from the version and identities the
 * mirror does not hold listed one by one, the near filter and what it cannot
 * express, and the notices for 404 / 409 / 410. The page reads everything
 * first; the view is synchronous.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import type { BoardContext } from "@/components/workspace/pick-board/views/board-data";
import { replayPage } from "@/components/workspace/pick-board/views/replay-rules";
import {
  ReplayBody,
  type ReplayData,
} from "@/components/workspace/pick-board/views/replay-view";
import type { PickConditions } from "@/core/pick/types";
import type { PickRow, ReplayAnswer } from "@/server/pick-board";

import { AS_OF, VERSION, boardRules, pickRow, request } from "../fixtures";
import { hrefs } from "../support";

rs.mock("next/link", () => ({
  default: ({
    href,
    prefetch,
    children,
    ...rest
  }: {
    href: string;
    prefetch?: boolean;
    children: ReactNode;
  }) => (
    <a href={href} data-prefetch={String(prefetch)} {...rest}>
      {children}
    </a>
  ),
}));

afterEach(cleanup);

const REPO_ROOT = path.resolve(__dirname, "../../../../../../..");
const CASES = JSON.parse(
  readFileSync(
    path.join(
      REPO_ROOT,
      "customizations/pick-workbench/tests/fixtures/board_replay_cases.json",
    ),
    "utf8",
  ),
) as {
  replay: { identities: string[]; unmappable: string[]; limit: number };
  conditions: PickConditions;
  row_keys: string[];
};

const RESULT = "0123456789abcdef0123456789abcdef";

const ctx: BoardContext = {
  rules: boardRules(),
  asOf: AS_OF,
  versionId: VERSION,
  freshness: {
    importedAt: null,
    rows: 0,
    withSignal: 0,
    signals: 0,
    posted: 0,
    rsCanonical: 0,
    rsCandidates: 0,
    rsSyncedAt: null,
  },
  latestSnapshot: null,
  seriesState: null,
};

function b64url(text: string): string {
  return Buffer.from(text, "utf8").toString("base64url");
}

const shared = (key: string) =>
  JSON.stringify(["realshort-pick", b64url(key), "英语"]);

function answer(patch: Partial<ReplayAnswer> = {}): ReplayAnswer {
  return {
    resultId: RESULT,
    mirrorVersion: VERSION,
    limit: CASES.replay.limit,
    total: CASES.replay.identities.length,
    identities: CASES.replay.identities,
    truncated: false,
    excludedReproducible: true,
    rankingReproducible: true,
    unmappable: CASES.replay.unmappable,
    sourceAsOf: "2026-09-23T22:15:00.000Z",
    ...patch,
  };
}

/** A synthetic row per key, titled by its place in `keys`. */
function rowsFor(keys: readonly string[]): PickRow[] {
  return keys.map((rowKey) =>
    pickRow({ rowKey, title: `剧 ${rowKey}`, titleCn: "" }),
  );
}

function rowsData(
  a: ReplayAnswer,
  opts: {
    params?: Record<string, string>;
    missing?: string[];
    conditions?: PickConditions | null;
  } = {},
): { data: ReplayData; req: ReturnType<typeof request> } {
  const req = request({ result: RESULT, ...opts.params });
  const plan = replayPage(a, req.page, req.size);
  const missing = new Set(opts.missing ?? []);
  const rows = rowsFor(plan.pageKeys.filter((k) => !missing.has(k)));
  const data: ReplayData = {
    kind: "rows",
    answer: a,
    conditions:
      opts.conditions === undefined ? CASES.conditions : opts.conditions,
    plan,
    rows,
    missing: [...missing],
  };
  return { data, req };
}

function show(data: ReplayData, req = request({ result: RESULT })) {
  return render(<ReplayBody data={data} req={req} ctx={ctx} />).container;
}

function titlesIn(root: Element, selector: string): string[] {
  return Array.from(root.querySelectorAll(`${selector} tbody tr`)).map((tr) =>
    (tr.querySelector("td a")?.textContent ?? "").replace(/^剧 /, ""),
  );
}

describe("the list", () => {
  it("keeps the agent's order and highlights the card's first limit", () => {
    const { data, req } = rowsData(answer());
    const root = show(data, req);
    const shown = titlesIn(root, '[data-replay-group="shown"]');
    const rest = titlesIn(root, '[data-replay-group="rest"]');
    expect(shown).toEqual(CASES.row_keys.slice(0, 5));
    expect(rest).toEqual(CASES.row_keys.slice(5));
    expect(screen.getByText(/卡片展示的前 5 部/)).toBeTruthy();
    expect(screen.getByText(/名单 8 部/)).toBeTruthy();
  });

  it("the highlight follows the global ordinal across pages", () => {
    const list = Array.from({ length: 45 }, (_, n) => shared(`k-${n + 1}`));
    const second = rowsData(answer({ identities: list, total: 45, limit: 5 }), {
      params: { page: "2", size: "20" },
    });
    const root = show(second.data, second.req);
    expect(root.querySelector('[data-replay-group="shown"]')).toBeNull();
    expect(titlesIn(root, '[data-replay-group="rest"]')).toEqual(
      Array.from({ length: 20 }, (_, n) => `k-${n + 21}`),
    );
    expect(screen.getByText(/本页是名单第 21–40 位/)).toBeTruthy();
    const pager = hrefs(root).filter(
      (h) => h.includes("page=") && !h.includes("tab="),
    );
    expect(pager.length).toBeGreaterThan(0);
    for (const href of pager) {
      expect(href).toContain(`result=${RESULT}`);
      expect(href).toContain(`v=${VERSION}`);
    }
  });

  it("rows the version no longer has are listed one by one, with their place", () => {
    const { data, req } = rowsData(answer(), {
      missing: ["c-1", "reelshort-rs0005"],
    });
    const root = show(data, req);
    const box = root.querySelector('[data-replay-missing="true"]');
    expect(box?.getAttribute("role")).toBe("alert");
    expect(box?.textContent).toContain("第 3 位 · c-1：当前版本已无此行");
    expect(box?.textContent).toContain(
      "第 8 位 · reelshort-rs0005：当前版本已无此行",
    );
    expect(titlesIn(root, "[data-replay-group]")).not.toContain("c-1");
  });

  it("identities the mirror does not hold are listed, never looked up", () => {
    const list = [
      shared("c-1"),
      JSON.stringify(["sheet-upload", "Yy0x", "en"]),
      shared("c-2"),
    ];
    const { data, req } = rowsData(answer({ identities: list, total: 3 }));
    expect(data.kind === "rows" && data.plan.pageKeys).toEqual(["c-1", "c-2"]);
    const root = show(data, req);
    const box = root.querySelector('[data-replay-offsite="true"]');
    expect(box?.textContent).toContain("第 2 位 · sheet-upload · Yy0x · en");
    expect(titlesIn(root, "[data-replay-group]")).toEqual(["c-1", "c-2"]);
  });

  it("the off-mirror box covers this page only, by global place", () => {
    const personal = JSON.stringify(["sheet-upload", "Yy0x", "en"]);
    const list = Array.from({ length: 25 }, (_, n) =>
      n === 22 ? personal : shared(`k-${n + 1}`),
    );
    const a = answer({ identities: list, total: 25, limit: 5 });
    const first = rowsData(a, { params: { page: "1", size: "20" } });
    const root = show(first.data, first.req);
    expect(root.querySelector('[data-replay-offsite="true"]')).toBeNull();
    cleanup();
    const second = rowsData(a, { params: { page: "2", size: "20" } });
    const box = show(second.data, second.req).querySelector(
      '[data-replay-offsite="true"]',
    );
    expect(box?.textContent).toContain("本页有 1 部不在镜像里");
    expect(box?.textContent).toContain("第 23 位 · sheet-upload · Yy0x · en");
  });

  it("an empty list and a page past the end say so", () => {
    const empty = rowsData(answer({ identities: [], total: 0 }));
    show(empty.data, empty.req);
    expect(screen.getByText("这份候选当时没有符合条件的剧。")).toBeTruthy();
    cleanup();
    const beyond = rowsData(answer(), { params: { page: "3" } });
    show(beyond.data, beyond.req);
    expect(screen.getByText(/第 3 页不存在/)).toBeTruthy();
  });

  it("explicit navigation leaves the replay; details and pagination preserve it", () => {
    const { data, req } = rowsData(answer(), {
      conditions: {
        ...CASES.conditions,
        theater: "ShortMax",
        sort: "rank",
        signal_kind: "kd",
      },
    });
    const root = show(data, req);
    const internal = hrefs(root).filter((h) =>
      h.startsWith("/workspace/pick-data"),
    );
    for (const href of hrefs(root).filter((h) =>
      h.startsWith("/workspace/pick-resources"),
    ))
      expect(href).toMatch(/^\/workspace\/pick-resources\?row=/);
    expect(internal.length).toBeGreaterThan(5);
    for (const href of internal) {
      expect(href).toMatch(/^\/workspace\/pick-data\?(?:.*&)?v=7(?:&|$)/);
      if (href.includes("result=") && href.includes("tab="))
        expect(href).toContain("tab=row");
    }
    const rowLinks = internal.filter((h) => h.includes("tab=row"));
    expect(rowLinks.length).toBeGreaterThan(0);
    for (const href of rowLinks) expect(href).toContain("result=");
    for (const link of Array.from(root.querySelectorAll("[data-prefetch]")))
      expect(link.getAttribute("data-prefetch")).toBe("false");
  });
});

describe("the near filter", () => {
  it("links the closest 选剧 view and the rank board, and lists what it cannot say", () => {
    const { data, req } = rowsData(
      answer({ unmappable: ["tags", "exclude_selected"] }),
      {
        conditions: {
          ...CASES.conditions,
          theater: "ShortMax",
          language: "und",
          tags: ["复仇"],
          sort: "rank",
          signal_kind: "kd",
        },
      },
    );
    const root = show(data, req);
    const near = root.querySelector('[data-replay-near="true"]');
    const links = Array.from(near?.querySelectorAll("a") ?? []);
    expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      [
        "在选剧 tab 打开近似筛选",
        "/workspace/pick-data?platform=shortmax&basis=kd&v=7",
      ],
      ["看这张榜最新一期", "/workspace/pick-data?tab=rank&v=7"],
    ]);
    const text = near?.textContent ?? "";
    expect(text).toContain("语种：反查不到剧单里的语种名：und");
    expect(text).toContain("标签：复仇");
    expect(text).toContain("排除个人清单里已保存的剧");
    expect(text).toContain("同名次时智能体按编号排、榜单按剧名排");
  });

  it("without the result's conditions there is no link, only the backend's list", () => {
    const { data, req } = rowsData(answer(), { conditions: null });
    const root = show(data, req);
    const near = root.querySelector('[data-replay-near="true"]');
    expect(near?.querySelectorAll("a")).toHaveLength(0);
    expect(near?.textContent).toContain("暂时拿不到这份候选的条件");
    expect(near?.textContent).toContain("排除个人清单里已保存的剧");
  });
});

describe("notices", () => {
  const notice = (
    reason: "notFound" | "gone" | "conflict" | "unavailable",
    conditions: PickConditions | null = CASES.conditions,
  ): ReplayData => ({ kind: "notice", reason, conditions });

  it("410, 404 and 409 each say what happened", () => {
    const texts = {
      gone: "这份候选用的剧库批次已过保留期被清理，无法回放。",
      notFound: "找不到这份候选：链接不完整，或它不是你的结果。",
      conflict: "这份候选的条件已不能按当前规则重跑，无法回放。",
      unavailable:
        "暂时拿不到这份候选的回放（工作台后端没有回应），稍后刷新再试。",
    } as const;
    for (const [reason, text] of Object.entries(texts)) {
      const root = show(notice(reason as keyof typeof texts));
      expect(screen.getByRole("alert").textContent).toBe(text);
      const back = screen.getByRole("link", { name: "回到选剧列表" });
      expect(back.getAttribute("href")).toBe("/workspace/pick-data?v=7");
      expect(hrefs(root).some((h) => h.includes("result="))).toBe(false);
      cleanup();
    }
  });

  it("409 and 410 still give the near filter; 404 does not", () => {
    for (const reason of ["conflict", "gone"] as const) {
      const root = show(
        notice(reason, { ...CASES.conditions, tags: ["复仇"] }),
      );
      const near = root.querySelector('[data-replay-near="true"]');
      expect(near?.textContent).toContain("标签：复仇");
      expect(near?.textContent).toContain("排除个人清单里已保存的剧");
      cleanup();
    }
    const root = show(notice("notFound"));
    expect(root.querySelector('[data-replay-near="true"]')).toBeNull();
  });
});
