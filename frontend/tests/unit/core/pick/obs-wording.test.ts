/**
 * The data page's radar wording (plan TR-24; premises 1 and 2). The phrases
 * the backend owns equal wording.py's, read from the Python source; every
 * text the page adds passes the forbidden words, and admission texts never
 * claim completeness, verification or independence.
 */
import { describe, expect, it } from "@rstest/core";

import { forbiddenIn, UNOBSERVED } from "@/core/pick/obs-format";
import {
  type ObsLabelHit,
  DISCOVERY_MATCHES,
  DISCOVERY_ROUTES,
  GSC_FLAGS,
  RUN_OUTCOMES,
  SITE_ADMISSIONS,
  TRENDS_FLAGS,
  TRENDS_ROW_STATES,
  UNATTRIBUTED_KINDS,
  UNCOVERED_REASONS,
} from "@/core/pick/obs-rows";
import * as wording from "@/core/pick/obs-wording";

import { repoText } from "./obs-contract-fixtures";
import { pyMapping, pyString } from "./py-source";

const WORDING_PY = repoText(
  "customizations/pick-workbench/ggwork_pick/observe/wording.py",
);
const PLAN = repoText("docs/plans/2026-09-25-trends-radar-impl-plan.md");

const ADMISSION_FORBIDDEN = ["完整", "已核实", "独立"];

describe("the backend's phrases", () => {
  it.each([
    "SMALL_BASE_SURGE",
    "FROM_ZERO_UNCHECKED",
    "NOT_JUDGED_AMBIGUOUS",
    "PRESUMED_CORRESPONDENCE",
    "DATA_SOURCE_TRENDS",
    "LINK_UNTIMELY",
    "ADMISSION_AGREED",
  ] as const)("%s equals wording.py", (name) => {
    expect(wording[name]).toBe(pyString(WORDING_PY, name));
  });

  it("the confirmation and link action texts equal wording.py", () => {
    expect(wording.CONFIRMATION_TEXT).toEqual(
      pyMapping(WORDING_PY, "CONFIRMATION_TEXT"),
    );
    expect(wording.LINK_ACTION_TEXT).toEqual(
      pyMapping(WORDING_PY, "LINK_ACTION_TEXT"),
    );
  });

  it("the b_only sentence is the plan's", () => {
    expect(PLAN).toContain(`「${wording.TRENDS_B_ONLY}」`);
  });
});

describe("the page's own names", () => {
  const tables: [
    string,
    Readonly<Record<string, string>>,
    readonly string[],
  ][] = [
    ["TRENDS_ROW_STATE_TEXT", wording.TRENDS_ROW_STATE_TEXT, TRENDS_ROW_STATES],
    ["TRENDS_FLAG_TEXT", wording.TRENDS_FLAG_TEXT, TRENDS_FLAGS],
    ["GSC_FLAG_TEXT", wording.GSC_FLAG_TEXT, GSC_FLAGS],
    ["UNCOVERED_REASON_TEXT", wording.UNCOVERED_REASON_TEXT, UNCOVERED_REASONS],
    ["UNATTRIBUTED_TEXT", wording.UNATTRIBUTED_TEXT, UNATTRIBUTED_KINDS],
    ["SITE_ADMISSION_TEXT", wording.SITE_ADMISSION_TEXT, SITE_ADMISSIONS],
    ["DISCOVERY_ROUTE_TEXT", wording.DISCOVERY_ROUTE_TEXT, DISCOVERY_ROUTES],
    ["DISCOVERY_MATCH_TEXT", wording.DISCOVERY_MATCH_TEXT, DISCOVERY_MATCHES],
    ["RUN_OUTCOME_TEXT", wording.RUN_OUTCOME_TEXT, RUN_OUTCOMES],
  ];

  it.each(tables)("%s names every value of its enum", (_name, table, keys) => {
    expect(Object.keys(table).sort()).toEqual([...keys].sort());
  });

  it("no text calls an unobserved count zero", () => {
    const texts = [
      ...tables.flatMap(([, table]) => Object.values(table)),
      ...Object.values(wording as Record<string, unknown>).filter(
        (value): value is string => typeof value === "string",
      ),
    ];
    expect(texts.flatMap(forbiddenIn)).toEqual([]);
  });

  it("the admission texts never claim completeness", () => {
    const admission = [
      ...Object.values(wording.SITE_ADMISSION_TEXT),
      wording.ADMISSION_AGREED,
    ];
    for (const text of admission)
      for (const term of ADMISSION_FORBIDDEN) expect(text).not.toContain(term);
  });
});

describe("counts", () => {
  it("a missing count is unobserved, a zero is a zero", () => {
    expect(wording.countText(null)).toBe(UNOBSERVED);
    expect(wording.countText(undefined)).toBe(UNOBSERVED);
    expect(wording.countText(0)).toBe("0");
    expect(wording.countText(325)).toBe("325");
  });

  it("a label's counts name the windows the design's way", () => {
    expect(wording.labelCountsText({ w0: 325, w_minus_1: null })).toBe(
      `W0 325 · W−1 ${UNOBSERVED}`,
    );
    expect(wording.labelCountsText({ x_flt: 12 })).toBe("x_flt 12");
  });

  it("an unknown code is shown as itself, said to be unknown", () => {
    expect(wording.textOf(wording.GSC_FLAG_TEXT, "new_flag")).toBe(
      "new_flag（这个页面版本还没有它的说明）",
    );
    expect(wording.textOf(wording.GSC_FLAG_TEXT, "detail_gap")).toBe(
      "逐剧两份下界不一致",
    );
  });
});

describe("the paste row (design 2.2)", () => {
  const row = {
    title: "The Alpha's Bride",
    url: "https://dramashortstv.com/en/drama/the-alphas-bride-650a1b2c3d4e5f6a7b8c9d0e",
    impressions: 325,
    clicks: null,
    window_kind: "24h" as const,
    top_query: "the alpha's bride full movie",
    verified_on: "2026-09-25",
    note: "gsc-rules-v1",
  };

  it("is the sheet's seven columns, tab-separated, the window in the note", () => {
    expect(wording.pasteRowText(row).split("\t")).toEqual([
      "The Alpha's Bride",
      row.url,
      "325",
      UNOBSERVED,
      "the alpha's bride full movie",
      "2026-09-25",
      "gsc-rules-v1；窗口 24 小时",
    ]);
  });

  it("tabs and line breaks inside a field never break the one line of seven columns", () => {
    const text = wording.pasteRowText({
      ...row,
      title: "A\tB",
      top_query: "q1\r\nq2",
      note: "one\ntwo",
    });
    expect(text).not.toMatch(/[\r\n]/);
    const cells = text.split("\t");
    expect(cells).toHaveLength(7);
    expect([cells[0], cells[4], cells[6]]).toEqual([
      "A B",
      "q1 q2",
      "one two；窗口 24 小时",
    ]);
  });

  it("an empty query stays an empty column, an empty note only names the window", () => {
    const cells = wording
      .pasteRowText({ ...row, top_query: null, note: "" })
      .split("\t");
    expect(cells[4]).toBe("");
    expect(cells[6]).toBe("窗口 24 小时");
  });
});

describe("a GSC label's title (design 5.8; codex P2)", () => {
  const hit = (
    label: ObsLabelHit["label"],
    formal: boolean,
    condition: string,
  ) => ({
    label,
    formal,
    condition,
    counts: {},
  });

  it("a small-base surge is shown only as a small-base rise", () => {
    const small = hit(
      "surge",
      false,
      "W0 对 W−1 ≥ +50%，W−1 < 20（小基数曝光上升，只作描述）",
    );
    expect(wording.gscLabelTitle(small)).toBe("小基数曝光上升");
  });

  it("a surge that is descriptive for another reason keeps its name", () => {
    expect(
      wording.gscLabelTitle(hit("surge", false, "W0 ≥ 2000，W−1 ≥ 20")),
    ).toBe("曝光飙升");
    expect(
      wording.gscLabelTitle(hit("surge", true, "W0 ≥ 2000，W−1 ≥ 20")),
    ).toBe("曝光飙升");
  });

  it("a from-zero hit whose base was not checked is shown as new, base unchecked", () => {
    const unchecked = hit(
      "from_zero",
      false,
      "W−1 明细没有行，W0 ≥ 20（新出现，基线未核对）",
    );
    expect(wording.gscLabelTitle(unchecked)).toBe("新出现，基线未核对");
    const checked = hit(
      "from_zero",
      true,
      "W−1 两边都没有行（基线未观测到），W0 一致且 ≥ 20",
    );
    expect(wording.gscLabelTitle(checked)).toBe("从零起量（基线未观测到）");
  });

  it("the markers are the phrases rules.py writes into the condition", () => {
    const rules = repoText(
      "customizations/pick-workbench/ggwork_pick/observe/gsc/rules.py",
    );
    expect(rules).toContain(`SMALL_BASE = "${wording.SMALL_BASE_SURGE}"`);
    expect(rules).toContain(`"${wording.FROM_ZERO_UNCHECKED}"`);
  });
});
