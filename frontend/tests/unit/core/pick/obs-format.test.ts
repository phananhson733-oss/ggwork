/**
 * The observation radar's wording and display on the frontend (plan TR-16;
 * premise 1, D8, D10). An unobserved value reads "未观测到", never zero; the
 * wording is the backend's (ggwork_pick/observe/contract.py and wording.py),
 * read here from the Python source so the two cannot drift apart.
 */
import { describe, expect, it } from "@rstest/core";

import {
  FORBIDDEN_TERMS,
  FROM_ZERO_NOTE,
  GSC_STATE_TEXT,
  OBS_CONDITION_LABELS,
  TRENDS_STATE_TEXT,
  UNOBSERVED,
  UNOBSERVED_GSC,
  UNOBSERVED_TRENDS,
  forbiddenIn,
  isObsEvidence,
  obsConditionParts,
  obsEvidenceLine,
  type ObsKind,
} from "@/core/pick/obs-format";
import type { PickConditions, PickEvidence } from "@/core/pick/types";

import { readSource, stripComments } from "../pick-board/ported-source";

import {
  obsFixture,
  repoText,
  validCase,
  type ModelFixture,
} from "./obs-contract-fixtures";

const CONTRACT_PY = repoText(
  "customizations/pick-workbench/ggwork_pick/observe/contract.py",
);
const WORDING_PY = repoText(
  "customizations/pick-workbench/ggwork_pick/observe/wording.py",
);

function pyString(source: string, name: string): string {
  const found = new RegExp(`^${name} = "([^"]*)"$`, "m").exec(source);
  if (found?.[1] === undefined) throw new Error(`no ${name}`);
  return found[1];
}

function pyTuple(source: string, name: string): string[] {
  const found = new RegExp(`^${name} = \\(([^)]*)\\)`, "m").exec(source);
  if (!found?.[1]) throw new Error(`no ${name}`);
  return [...found[1].matchAll(/"([^"]*)"/g)].map((m) => m[1] ?? "");
}

type Known = Readonly<{
  /** module string constants an f-string names */
  strings?: Readonly<Record<string, string>>;
  /** mappings a `**NAME` spread names */
  mappings?: Readonly<Record<string, Readonly<Record<string, string>>>>;
}>;

/** A `NAME = MappingProxyType({...})` of string literals, f-strings over module constants and `**OTHER` spreads. */
function pyMapping(
  source: string,
  name: string,
  known: Known = {},
): Record<string, string> {
  const head = `${name} = MappingProxyType(`;
  const start = source.indexOf(head);
  if (start < 0) throw new Error(`no ${name}`);
  // The texts use full-width brackets, so the first ASCII ")" closes the call.
  const body = source.slice(start, source.indexOf(")", start + head.length));
  const entries = [
    ...body.matchAll(/\*\*(\w+)|"(\w+)":\s*(f?)"([^"]*)"/g),
  ].flatMap((m): [string, string][] => {
    if (m[1]) return Object.entries(known.mappings?.[m[1]] ?? {});
    const text = (m[4] ?? "").replace(/\{(\w+)\}/g, (_, constant: string) =>
      m[3] ? (known.strings?.[constant] ?? "?") : `{${constant}}`,
    );
    return [[m[2] ?? "", text]];
  });
  return Object.fromEntries(entries);
}

type ObsEvidence = PickEvidence & { kind: ObsKind };

function gscEvidence(
  patch: Partial<PickEvidence> & { kind?: ObsKind },
): ObsEvidence {
  const gsc = validCase(
    obsFixture("evidence.json"),
    "gsc_formal",
  ) as ObsEvidence;
  return { ...gsc, ...patch };
}

describe("the wording is the backend's", () => {
  it("the unobserved phrases, from contract.py", () => {
    expect(UNOBSERVED).toBe(pyString(CONTRACT_PY, "UNOBSERVED"));
    expect(UNOBSERVED_TRENDS).toBe(pyString(CONTRACT_PY, "UNOBSERVED_TRENDS"));
    expect(UNOBSERVED_GSC).toBe(pyString(CONTRACT_PY, "UNOBSERVED_GSC"));
  });

  it("the forbidden terms and state texts, from wording.py", () => {
    expect([...FORBIDDEN_TERMS]).toEqual(
      pyTuple(WORDING_PY, "FORBIDDEN_TERMS"),
    );
    const note = pyString(WORDING_PY, "FROM_ZERO_NOTE");
    expect(FROM_ZERO_NOTE).toBe(note);
    const labels = pyMapping(WORDING_PY, "GSC_LABEL_TEXT", {
      strings: { FROM_ZERO_NOTE: note },
    });
    expect(GSC_STATE_TEXT).toEqual(
      pyMapping(WORDING_PY, "GSC_STATE_TEXT", {
        mappings: { GSC_LABEL_TEXT: labels },
      }),
    );
    expect(TRENDS_STATE_TEXT).toEqual(
      pyMapping(WORDING_PY, "TRENDS_STATE_TEXT"),
    );
  });

  it("the condition labels, from contract.py and the shared fixture", () => {
    expect(OBS_CONDITION_LABELS).toEqual(
      pyMapping(CONTRACT_PY, "OBS_CONDITION_LABELS"),
    );
    expect(OBS_CONDITION_LABELS).toEqual(
      obsFixture<{ labels: Record<string, string> }>("unmappable_cases.json")
        .labels,
    );
  });
});

describe("forbiddenIn (premise 1): the cases of test_obs_wording.py", () => {
  it.each([...FORBIDDEN_TERMS])("catches %s", (term) => {
    expect(forbiddenIn(`前一窗口${term}`)).not.toEqual([]);
  });

  it.each([
    "零次曝光",
    "曝光为 0",
    "点击数为0",
    "0 次点击",
    "没有任何曝光",
    "曝光：0 次",
    "W−1 曝光 0",
    "点击 0 次",
    "W0 曝光 0，W−1 曝光 82",
  ])("catches the variant %s", (text) => {
    expect(forbiddenIn(text)).not.toEqual([]);
  });

  it.each([
    "W0 曝光 325，W−1 曝光 82",
    "曝光 0.5 万",
    "点击 0.8%",
    "曝光 0% 以上的变化",
    "曝光 1024",
    "W−1 ≥20 次曝光",
    "从零起量（基线未观测到）",
    "W0 曝光 ≥2000，或 W0 对 W−1 ≥+50%",
    "CTR 为 0.05",
    "非零小时 ≥12",
    UNOBSERVED_GSC,
  ])("leaves %s alone", (text) => {
    expect(forbiddenIn(text)).toEqual([]);
  });
});

describe("obsEvidenceLine", () => {
  it("says the GSC value was not observed when it is null", () => {
    const line = obsEvidenceLine(gscEvidence({ value: null, note: undefined }));
    expect(line).toContain(UNOBSERVED_GSC);
    expect(line).not.toContain("数值未知");
    expect(forbiddenIn(line)).toEqual([]);
  });

  it("says each kind's own phrase, once", () => {
    const empty = (kind: ObsKind, note: string) =>
      obsEvidenceLine({ ...gscEvidence({ value: "" }), kind, note });
    expect(empty("obs_trends", "trend-rules-v1")).toContain(UNOBSERVED_TRENDS);
    expect(empty("obs_discovery", "trend-rules-v1")).toContain(UNOBSERVED);
    const noted = empty("obs_gsc", `gsc-rules-v1；W−1 ${UNOBSERVED_GSC}`);
    expect(noted.split(UNOBSERVED_GSC)).toHaveLength(2);
  });

  it.each([0, "0", -3, 1.5, "325"])(
    "never shows a GSC value %p that is not a positive count",
    (value) => {
      const line = obsEvidenceLine(
        gscEvidence({ value, note: "gsc-rules-v1" }),
      );
      expect(line).toContain("取值不符合约定");
      expect(line).not.toMatch(/(^|[^\dW])(0|-3|1\.5|325)([^\d]|$)/);
      expect(forbiddenIn(line)).toEqual([]);
    },
  );

  it("shows no heat number for Trends, whatever the value says", () => {
    const line = obsEvidenceLine(
      gscEvidence({ kind: "obs_trends", value: 87, note: "trend-rules-v1" }),
    );
    expect(line).not.toContain("87");
  });

  it("renders every valid contract entry with its label and note, and no zero", () => {
    for (const c of obsFixture<ModelFixture>("evidence.json").valid) {
      const entry = c.value as ObsEvidence;
      const line = obsEvidenceLine(entry);
      expect(line.startsWith(entry.label ?? "")).toBe(true);
      expect(line).toContain(entry.note ?? "");
      expect([c.name, forbiddenIn(line)]).toEqual([c.name, []]);
    }
  });

  it("names the discovery term and its identity evidence", () => {
    const discovery = validCase(
      obsFixture("evidence.json"),
      "discovery",
    ) as ObsEvidence;
    expect(obsEvidenceLine(discovery)).toContain(
      "发现词「reelshort the alpha's bride」 · 身份证据 强",
    );
  });

  it("knows the three observation kinds only", () => {
    expect(
      ["obs_trends", "obs_gsc", "obs_discovery"].map((kind) =>
        isObsEvidence({ ...gscEvidence({}), kind }),
      ),
    ).toEqual([true, true, true]);
    expect(isObsEvidence({ ...gscEvidence({}), kind: "obs_link" })).toBe(false);
    expect(isObsEvidence({ ...gscEvidence({}), kind: "gsc" })).toBe(false);
  });
});

describe("obsConditionParts", () => {
  const base: PickConditions = { limit: 5, exclude_selected: true };

  it("says nothing without observation conditions", () => {
    expect(obsConditionParts(base)).toEqual([]);
    expect(
      obsConditionParts({
        ...base,
        trend_state: null,
        trend_geos: [],
        trend_include_first: false,
        gsc_state: null,
        gsc_countries: [],
        link_state: null,
      }),
    ).toEqual([]);
  });

  it("spells out each condition, the site total and the obs order", () => {
    expect(
      obsConditionParts({
        ...base,
        sort: "obs",
        trend_state: "rising",
        trend_geos: ["WW", "US"],
        trend_include_first: true,
        trend_include_presumed: true,
        gsc_state: "from_zero",
        gsc_countries: ["USA", "ALL"],
        link_state: "trends_lead_page",
      }),
    ).toEqual([
      "Google Trends 上升观察",
      "Trends 地区 全球、US",
      OBS_CONDITION_LABELS.trend_include_first,
      OBS_CONDITION_LABELS.trend_include_presumed,
      "站内搜索 从零起量（基线未观测到）",
      "GSC 国家 USA、全站",
      "同国家联动 站外先行·补页",
      "按观测状态排序",
    ]);
  });

  it("never calls anything zero", () => {
    for (const gsc_state of Object.keys(GSC_STATE_TEXT))
      for (const text of obsConditionParts({
        ...base,
        gsc_state: gsc_state as PickConditions["gsc_state"],
      }))
        expect(forbiddenIn(text)).toEqual([]);
    for (const text of Object.values(OBS_CONDITION_LABELS))
      expect(forbiddenIn(text)).toEqual([]);
  });
});

describe("obs-format stays pure (D10)", () => {
  it("never reads the wall clock", () => {
    const code = stripComments(readSource("src/core/pick/obs-format.ts"));
    expect(code).not.toMatch(/Date\.now\(\)|new Date\(\s*\)/);
  });
});
