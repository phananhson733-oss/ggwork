/**
 * The replay view's pure rules (P4-2; critique B10, B11): the agent's list cut
 * into pages with global ordinals, which version the rows come from, the
 * near filter on the board, and the banners that say what differs from the
 * card. The backend's own replay answer for board_fixture's v2 pull
 * (board_replay_cases.json) is the default case.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

import type { BannerBoard } from "@/components/workspace/pick-board/views/banner-rules";
import {
  agentOnlyConditions,
  nearFilter,
  replayBannerBoard,
  replayBanners,
  replayEntries,
  replayPage,
  replayVersion,
} from "@/components/workspace/pick-board/views/replay-rules";
import type { PickConditions } from "@/core/pick/types";
import { parsePickRequest } from "@/core/pick-board/request";
import type { ReplayAnswer } from "@/server/pick-board";

import { boardRules } from "../fixtures";

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

/** The shared contract's cases for the observation fields (plan TR-33, TR-16). */
const OBS_CASES = JSON.parse(
  readFileSync(
    path.join(
      REPO_ROOT,
      "customizations/pick-workbench/tests/fixtures/obs_contract/unmappable_cases.json",
    ),
    "utf8",
  ),
) as {
  labels: Record<string, string>;
  cases: { name: string; conditions: PickConditions; expected: string[] }[];
  base_order: string[];
};

const AS_OF = "2026-09-24T03:40:00+00:00";

function b64url(text: string): string {
  return Buffer.from(text, "utf8").toString("base64url");
}

function shared(rowKey: string): string {
  return JSON.stringify(["realshort-pick", b64url(rowKey), "英语"]);
}

function answer(patch: Partial<ReplayAnswer> = {}): ReplayAnswer {
  return {
    resultId: "0123456789abcdef0123456789abcdef",
    mirrorVersion: 7,
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

function listOf(count: number): string[] {
  return Array.from({ length: count }, (_, n) => shared(`k-${n + 1}`));
}

function board(patch: Partial<BannerBoard> = {}): BannerBoard {
  return {
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
    freshness: null,
    postedImportedAt: null,
    warnings: [],
    requestedV: 7,
    pinned: false,
    pruned: false,
    ignoredV: false,
    unreadable: false,
    ...patch,
  };
}

function showing(id: number, patch: Partial<BannerBoard> = {}): BannerBoard {
  const base = board();
  return {
    ...base,
    scope: {
      ...base.scope,
      schema: `pickm_v${String(id).padStart(6, "0")}`,
      versionId: id,
    },
    ...patch,
  };
}

const RESULT = "0123456789abcdef0123456789abcdef";
const replayReq = (params: Record<string, string> = {}) =>
  parsePickRequest({ result: RESULT, v: "7", ...params });

function conditions(patch: Partial<PickConditions> = {}): PickConditions {
  return { ...CASES.conditions, ...patch };
}

describe("the list and its pages (B10)", () => {
  it("numbers the whole list and marks the card's first limit", () => {
    const entries = replayEntries(answer());
    expect(entries.map((e) => e.ordinal)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(entries.map((e) => e.rowKey)).toEqual(CASES.row_keys);
    expect(entries.filter((e) => e.shown).map((e) => e.ordinal)).toEqual([
      1, 2, 3, 4, 5,
    ]);
  });

  it("a page queries only its own keys; the missing check covers the whole list", () => {
    const list = listOf(45);
    const first = replayPage(answer({ identities: list, limit: 5 }), 1, 20);
    expect(first.pageKeys).toEqual(
      Array.from({ length: 20 }, (_, n) => `k-${n + 1}`),
    );
    expect(first.allKeys).toHaveLength(45);
    expect([first.first, first.last, first.hasMore]).toEqual([1, 20, true]);
    const last = replayPage(answer({ identities: list, limit: 5 }), 3, 20);
    expect(last.pageKeys).toEqual(["k-41", "k-42", "k-43", "k-44", "k-45"]);
    expect([last.first, last.last, last.hasMore]).toEqual([41, 45, false]);
    const beyond = replayPage(answer({ identities: list }), 4, 20);
    expect(beyond.page).toEqual([]);
    expect(beyond.pageKeys).toEqual([]);
  });

  it("highlights by the global ordinal, not the place on the page", () => {
    const list = listOf(45);
    const second = replayPage(answer({ identities: list, limit: 20 }), 2, 20);
    expect(second.page.some((e) => e.shown)).toBe(false);
    const straddling = replayPage(
      answer({ identities: list, limit: 20 }),
      1,
      50,
    );
    expect(straddling.page.filter((e) => e.shown)).toHaveLength(20);
  });

  it("personal and undecodable identities have no row key and are not looked up", () => {
    const list = [
      shared("c-1"),
      JSON.stringify(["sheet-upload", "Yy0x", "en"]),
      JSON.stringify(["realshort-pick", "Yy1", "en"]),
      "not json",
      shared("c-1"),
    ];
    const page = replayPage(answer({ identities: list }), 1, 50);
    expect(page.page.map((e) => e.rowKey)).toEqual([
      "c-1",
      null,
      null,
      null,
      "c-1",
    ]);
    expect(page.pageKeys).toEqual(["c-1"]);
    expect(page.allKeys).toEqual(["c-1"]);
  });
});

describe("which version the rows come from (B11)", () => {
  it("the result's paired version wins over the link's v", () => {
    expect(replayVersion(answer({ mirrorVersion: 5 }), 9)).toBe(5);
    expect(replayVersion(answer({ mirrorVersion: 5 }), null)).toBe(5);
  });

  it("a result without a paired version reads the link's v, else the current one", () => {
    expect(replayVersion(answer({ mirrorVersion: null }), 9)).toBe(9);
    expect(replayVersion(answer({ mirrorVersion: null }), null)).toBeNull();
  });
});

describe("the replay's banners", () => {
  const texts = (b: ReturnType<typeof replayBanners>) => b.map((x) => x.text);

  it("the paired version, current: nothing to say", () => {
    expect(
      replayBanners({
        board: board(),
        answer: answer(),
        urlV: 7,
        req: replayReq(),
      }),
    ).toEqual([]);
  });

  it("the paired version, not the latest: says so, and links to the current list without the result", () => {
    const b = replayBanners({
      board: showing(5, { pinned: true, requestedV: 5 }),
      answer: answer({ mirrorVersion: 5 }),
      urlV: 5,
      req: replayReq({ v: "5" }),
    });
    expect(texts(b)).toEqual([
      "按这份候选配对的镜像版本 v5 回放（采集于 2026-09-24 03:40 UTC），当前最新 v7",
    ]);
    expect(b[0]?.link?.href).toBe("/workspace/pick-data?v=7");
  });

  it("pruned: the fallback of plan 2.5 item 4, in its words", () => {
    const b = replayBanners({
      board: board({ pruned: true, requestedV: 5 }),
      answer: answer({ mirrorVersion: 5 }),
      urlV: 7,
      req: replayReq(),
    });
    expect(texts(b)).toEqual([
      "镜像 v5 已清理：名单与顺序按智能体当时的批次，行数据取自当前版本 v7，可能与当时不同",
    ]);
  });

  it("unreadable and not published get their own words", () => {
    const unreadable = replayBanners({
      board: board({ unreadable: true, requestedV: 5 }),
      answer: answer({ mirrorVersion: 5 }),
      urlV: null,
      req: replayReq(),
    });
    expect(unreadable[0]?.role).toBe("alert");
    expect(unreadable[0]?.text).toContain("授权缺失");
    expect(unreadable[0]?.text).toContain("行数据取自当前版本 v7");
    const ignored = replayBanners({
      board: board({ ignoredV: true, requestedV: 5 }),
      answer: answer({ mirrorVersion: 5 }),
      urlV: null,
      req: replayReq(),
    });
    expect(texts(ignored)).toEqual([
      "这份候选配对的镜像 v5 不存在或未发布：名单与顺序按智能体当时的批次，行数据取自当前版本 v7，可能与当时不同",
    ]);
  });

  it("a link's v that is neither the paired version nor the one shown is flagged", () => {
    const b = replayBanners({
      board: showing(5, { pinned: true, requestedV: 5 }),
      answer: answer({ mirrorVersion: 5 }),
      urlV: 3,
      req: replayReq({ v: "5" }),
    });
    expect(texts(b)).toContain(
      "链接里的版本 v3 与这份候选配对的版本 v5 不一致，已按配对的版本回放",
    );
  });

  it("no paired version: its own words, whatever the link says", () => {
    const current = replayBanners({
      board: board({ requestedV: null }),
      answer: answer({ mirrorVersion: null }),
      urlV: null,
      req: replayReq(),
    });
    expect(texts(current)).toEqual([
      "这份候选当时没有配对的镜像版本，行数据取自当前版本 v7",
    ]);
    const older = replayBanners({
      board: showing(5, { pinned: true, requestedV: 5 }),
      answer: answer({ mirrorVersion: null }),
      urlV: 5,
      req: replayReq({ v: "5" }),
    });
    expect(texts(older)).toEqual([
      "这份候选当时没有配对的镜像版本，行数据取自镜像 v5（当前最新 v7）",
    ]);
    expect(older[0]?.link?.href).toBe(`/workspace/pick-data?result=${RESULT}`);
  });

  it("truncated, exclusions and rule changes each get a line", () => {
    const b = replayBanners({
      board: board(),
      answer: answer({
        total: 2345,
        identities: listOf(2000),
        truncated: true,
        excludedReproducible: false,
        rankingReproducible: false,
      }),
      urlV: 7,
      req: replayReq(),
    });
    expect(texts(b)).toEqual([
      "名单共 2,345 部，只列出前 2,000 部",
      "这份候选早于排除记录：「排除已选」与「换一批」无法复现，名单里可能有卡片当时排除掉的剧",
      "规则或排序版本已变：名单按现在的规则重跑，顺序可能与卡片当时不同",
    ]);
  });

  it("the page's own version banners are left to the replay's", () => {
    const flagged = board({
      pinned: true,
      pruned: true,
      ignoredV: true,
      unreadable: true,
    });
    const paired = replayBannerBoard(flagged, 5);
    expect([
      paired.pinned,
      paired.pruned,
      paired.ignoredV,
      paired.unreadable,
    ]).toEqual([false, false, false, false]);
    const unpaired = replayBannerBoard(flagged, null);
    expect([
      unpaired.pinned,
      unpaired.pruned,
      unpaired.ignoredV,
      unpaired.unreadable,
    ]).toEqual([false, true, true, true]);
  });
});

describe("the near filter", () => {
  const rules = boardRules();
  const near = (patch: Partial<PickConditions>, agentOnly: string[] = []) =>
    nearFilter(conditions(patch), rules, 7, agentOnly);

  it("default conditions: the 选剧 tab's default view, pinned, never the result", () => {
    const filter = near({});
    expect(filter.href).toBe("/workspace/pick-data?v=7");
    expect(filter.rankHref).toBeNull();
    expect(filter.unmapped).toEqual([]);
  });

  it("theater: the display name back to its key, in any case; a bare key as the feed falls back to it", () => {
    expect(near({ theater: "ShortMax" }).href).toBe(
      "/workspace/pick-data?platform=shortmax&v=7",
    );
    expect(near({ theater: "shortMAX" }).href).toContain("platform=shortmax");
    expect(near({ theater: "flareflow" }).href).toContain("platform=flareflow");
    const unknown = near({ theater: "NoSuchTheater" });
    expect(unknown.href).toBe("/workspace/pick-data?v=7");
    expect(unknown.unmapped).toEqual([
      {
        key: "theater",
        label: "剧场：本页没有这个剧场",
        value: "NoSuchTheater",
      },
    ]);
  });

  it("a theater the version knows but the board does not is listed, not guessed", () => {
    const withNew = boardRules((raw) => {
      const base = raw.platformRules.touchshort;
      if (!base) throw new Error("the fixture has no touchshort rule");
      raw.platformRules.newshort = { ...base, name: "NewShort" };
    });
    const filter = nearFilter(
      conditions({ theater: "NewShort" }),
      withNew,
      7,
      [],
    );
    expect(filter.href).toBe("/workspace/pick-data?v=7");
    expect(filter.unmapped.map((u) => u.key)).toEqual(["theater"]);
  });

  it("a display name two theaters share is listed, not given to the first", () => {
    const twins = boardRules((raw) => {
      for (const key of ["dramabox", "flareflow"] as const) {
        const base = raw.platformRules[key];
        if (!base) throw new Error(`the fixture has no ${key} rule`);
        raw.platformRules[key] = { ...base, name: "TwinShort" };
      }
    });
    const filter = nearFilter(
      conditions({ theater: "TwinShort" }),
      twins,
      7,
      [],
    );
    expect(filter.href).toBe("/workspace/pick-data?v=7");
    expect(filter.unmapped).toEqual([
      { key: "theater", label: "剧场：本页没有这个剧场", value: "TwinShort" },
    ]);
  });

  it("language: the locale back to the sheet's name; a name the feed kept as is; und is listed", () => {
    expect(near({ language: "en" }).href).toBe(
      `/workspace/pick-data?lang=${encodeURIComponent("英语")}&v=7`,
    );
    expect(near({ language: "ZH-HANT" }).href).toContain(
      `lang=${encodeURIComponent("繁体中文")}`,
    );
    expect(near({ language: "粤语" }).href).toContain(
      `lang=${encodeURIComponent("粤语")}`,
    );
    for (const language of ["und", "xx"]) {
      const filter = near({ language });
      expect(filter.href).toBe("/workspace/pick-data?v=7");
      expect(filter.unmapped.map((u) => [u.key, u.value])).toEqual([
        ["language", language],
      ]);
    }
  });

  it("signal kind to basis, exclude_posted to posted=no", () => {
    expect(near({ signal_kind: "kd", exclude_posted: true }).href).toBe(
      "/workspace/pick-data?basis=kd&posted=no&v=7",
    );
    expect(near({ signal_kind: "clk" }).href).toContain("basis=clk");
    const unknown = near({ signal_kind: "zz" });
    expect(unknown.href).toBe("/workspace/pick-data?v=7");
    expect(unknown.unmapped.map((u) => [u.key, u.value])).toEqual([
      ["signal_kind", "zz"],
    ]);
  });

  it("sort=rank on a board's rank: its own link to the rank tab", () => {
    const filter = near({ sort: "rank", signal_kind: "kd" });
    expect(filter.rankHref).toBe("/workspace/pick-data?tab=rank&v=7");
    expect(near({ sort: "rank", signal_kind: "qc" }).rankHref).toBe(
      "/workspace/pick-data?tab=rank&rk=qc&v=7",
    );
    const noBoard = near({ sort: "rank", signal_kind: "clk" });
    expect(noBoard.rankHref).toBeNull();
    expect(noBoard.unmapped.map((u) => [u.key, u.value])).toEqual([
      ["sort", "clk"],
    ]);
  });

  it("the agent-only conditions, with the values the result stored", () => {
    const filter = near(
      {
        tags: ["复仇", "都市"],
        posted_account: "acc-1",
        channel: "youtube",
        query: "合成",
      },
      [
        "tags",
        "posted_account",
        "channel",
        "confirmed_eligible_only",
        "query",
        "exclude_selected",
        "brand_new",
      ],
    );
    expect(filter.unmapped.map((u) => [u.key, u.value])).toEqual([
      ["tags", "复仇、都市"],
      ["posted_account", "acc-1"],
      ["channel", "youtube"],
      ["confirmed_eligible_only", ""],
      ["query", "合成"],
      ["exclude_selected", ""],
      ["brand_new", ""],
    ]);
    expect(filter.unmapped.at(-1)?.label).toBe("brand_new");
  });

  it("without the result's conditions: no link, the backend's list by name", () => {
    const filter = nearFilter(null, rules, 7, ["exclude_selected"]);
    expect(filter.href).toBeNull();
    expect(filter.unmapped).toEqual([
      { key: "exclude_selected", label: "排除个人清单里已保存的剧", value: "" },
    ]);
  });

  it("agentOnlyConditions says what selection.unmappable_conditions says", () => {
    expect(agentOnlyConditions(CASES.conditions)).toEqual(
      CASES.replay.unmappable,
    );
    // The cases of test_mirror_replay.py's test_replay_lists_the_conditions_the_data_page_cannot_express.
    expect(
      agentOnlyConditions(
        conditions({
          tags: ["复仇"],
          posted_account: "acc-1",
          channel: "youtube",
          query: "合成",
        }),
      ),
    ).toEqual([
      "tags",
      "posted_account",
      "channel",
      "confirmed_eligible_only",
      "query",
      "exclude_selected",
    ]);
    expect(
      agentOnlyConditions(
        conditions({
          exclude_selected: false,
          theater: "Example",
          language: "en",
          signal_kind: "kd",
          exclude_posted: true,
        }),
      ),
    ).toEqual([]);
    expect(
      agentOnlyConditions(
        conditions({
          channel: "youtube",
          confirmed_eligible_only: false,
          exclude_selected: false,
        }),
      ),
    ).toEqual(["channel"]);
    expect(
      agentOnlyConditions(
        conditions({ exclude_previous: true, exclude_selected: false }),
      ),
    ).toEqual(["exclude_previous"]);
  });

  it("a stored result without confirmed_eligible_only took the backend's default, true", () => {
    // contracts.py: confirmed_eligible_only: bool = True; pickConditionsSchema keeps the key optional.
    const full = conditions({ channel: "youtube", exclude_selected: false });
    const stored = Object.fromEntries(
      Object.entries(full).filter(([key]) => key !== "confirmed_eligible_only"),
    ) as PickConditions;
    expect("confirmed_eligible_only" in stored).toBe(false);
    expect(agentOnlyConditions(stored)).toEqual([
      "channel",
      "confirmed_eligible_only",
    ]);
  });
});

// Plan TR-16: the seven observation fields are conditions only the agent can
// express. They follow the existing seven in the backend's order
// (UNMAPPABLE_OBS_ORDER), each when truthy, with the contract's labels.
describe("observation conditions in the near filter (TR-16)", () => {
  const rules = boardRules();

  it.each(OBS_CASES.cases.map((c) => [c.name, c] as const))(
    "%s: agentOnlyConditions says what selection.unmappable_conditions says",
    (_name, c) => {
      expect(agentOnlyConditions(c.conditions)).toEqual(c.expected);
    },
  );

  it("keeps the existing seven first, in the backend's order", () => {
    const all = agentOnlyConditions({
      ...conditions(),
      tags: ["x"],
      posted_account: "a",
      channel: "youtube",
      query: "q",
      exclude_selected: true,
      exclude_previous: true,
      trend_state: "rising",
      trend_geos: ["US"],
      trend_include_first: true,
      trend_include_presumed: true,
      gsc_state: "surge",
      gsc_countries: ["USA"],
      link_state: "both_rising",
    });
    expect(all.slice(0, OBS_CASES.base_order.length)).toEqual(
      OBS_CASES.base_order,
    );
    expect(all.slice(OBS_CASES.base_order.length)).toEqual(
      Object.keys(OBS_CASES.labels),
    );
  });

  it("lists them with the contract's labels and the values the result stored", () => {
    const stored = conditions({
      exclude_selected: false,
      trend_state: "emerging",
      trend_geos: ["WW", "GB"],
      trend_include_presumed: true,
      gsc_state: "from_zero",
      gsc_countries: ["GBR", "ALL"],
      link_state: "site_only",
    });
    const filter = nearFilter(stored, rules, 7, agentOnlyConditions(stored));
    expect(filter.unmapped).toEqual([
      {
        key: "trend_state",
        label: OBS_CASES.labels.trend_state,
        value: "emerging",
      },
      {
        key: "trend_geos",
        label: OBS_CASES.labels.trend_geos,
        value: "WW、GB",
      },
      {
        key: "trend_include_presumed",
        label: OBS_CASES.labels.trend_include_presumed,
        value: "",
      },
      {
        key: "gsc_state",
        label: OBS_CASES.labels.gsc_state,
        value: "from_zero",
      },
      {
        key: "gsc_countries",
        label: OBS_CASES.labels.gsc_countries,
        value: "GBR、ALL",
      },
      {
        key: "link_state",
        label: OBS_CASES.labels.link_state,
        value: "site_only",
      },
    ]);
    expect(filter.href).toBe("/workspace/pick-data?v=7");
  });
});
