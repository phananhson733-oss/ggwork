/**
 * The observation radar's shared contract from the frontend's side (plan TR-16,
 * TR-33): the obs_contract fixtures the frontend reads parse where they are
 * valid and are refused, for the reason their "error" names, where they are
 * not. Python's tests/observe/test_contract.py checks the same files against
 * the pydantic models, and the contract document against both.
 *
 * Who refuses what: the strict schemas the frontend parses with refuse bad
 * conditions and observations. Evidence keeps its generic schema (D8: an old
 * frontend parses new entries, and one malformed entry never takes the panel
 * down), so the flattening rules are checked by obs-contract's
 * obsEvidenceSchema, which these tests and TR-36's validator use.
 */
import { describe, expect, it } from "@rstest/core";
import type { z } from "zod";

import {
  ADMISSIONS,
  CONFIRMATIONS,
  ID_EVIDENCE_LEVELS,
  LINK_ACTIONABILITY_REASONS,
  obsEvidenceSchema,
  obsResultIssues,
} from "@/core/pick/obs-contract";
import {
  EVIDENCE_KINDS,
  LINK_LABEL_TEXT,
  LINK_LABELS,
  OBS_CONDITION_FIELDS,
} from "@/core/pick/obs-format";
import {
  EXCLUSION_REASONS,
  GSC_STATES,
  LINK_STATES,
  PICK_SORTS,
  TREND_STATES,
  pickConditionsSchema,
  pickEvidenceSchema,
  pickObservationsSchema,
  pickResultSchema,
} from "@/core/pick/types";

import obsPayload from "./fixtures/backend-result-obs.json";
import {
  docEnum,
  docFields,
  obsFixture,
  repoText,
  unmatchedReason,
  validCase,
  type ContractCase,
} from "./obs-contract-fixtures";

const DOC = repoText("docs/pick-workbench/observe-contract.md");
/** What a stored result's conditions always carry besides the seven observation keys. */
const BASE_CONDITIONS = { limit: 5, exclude_selected: true };

const conditions = obsFixture("conditions.json");
const observations = obsFixture("observations.json");
const evidence = obsFixture("evidence.json");
const resultObs = obsFixture("result_obs.json");

function refusals(
  cases: readonly ContractCase[],
  issuesOf: (value: unknown) => readonly z.ZodIssue[],
) {
  return cases
    .map((c) => [c.name, unmatchedReason(issuesOf(c.value), c.error)])
    .filter(([, reason]) => reason !== null);
}

const issuesOf =
  (schema: z.ZodTypeAny) =>
  (value: unknown): readonly z.ZodIssue[] => {
    const parsed = schema.safeParse(value);
    return parsed.success ? [] : parsed.error.issues;
  };

describe("the obs_contract fixtures TR-16 reads", () => {
  it("each has valid and invalid cases, and each case a unique name", () => {
    for (const fixture of [conditions, observations, evidence, resultObs]) {
      expect(fixture.valid.length).toBeGreaterThan(0);
      expect(fixture.invalid.length).toBeGreaterThan(0);
      const names = [...fixture.valid, ...fixture.invalid].map((c) => c.name);
      expect(new Set(names).size).toBe(names.length);
    }
  });

  it("conditions.json: the seven optional keys of pickConditionsSchema", () => {
    const withBase = (value: unknown) => ({
      ...BASE_CONDITIONS,
      ...(value as object),
    });
    for (const c of conditions.valid)
      expect(pickConditionsSchema.safeParse(withBase(c.value)).success).toBe(
        true,
      );
    const refused = refusals(conditions.invalid, (value) =>
      issuesOf(pickConditionsSchema)(withBase(value)),
    );
    expect(refused).toEqual([]);
  });

  it("observations.json: pickObservationsSchema, strict", () => {
    for (const c of observations.valid)
      expect(pickObservationsSchema.safeParse(c.value).success).toBe(true);
    expect(
      refusals(observations.invalid, issuesOf(pickObservationsSchema)),
    ).toEqual([]);
  });

  it("evidence.json: every valid entry parses as evidence and keeps the flattening rules", () => {
    for (const c of evidence.valid) {
      expect(pickEvidenceSchema.safeParse(c.value).success).toBe(true);
      expect(obsEvidenceSchema.safeParse(c.value).success).toBe(true);
    }
  });

  it("evidence.json: every invalid entry is refused by the flattening rules, for its reason", () => {
    expect(refusals(evidence.invalid, issuesOf(obsEvidenceSchema))).toEqual([]);
  });

  it("result_obs.json: the whole result parses; every broken one is refused for its reason", () => {
    for (const c of resultObs.valid) {
      expect(pickResultSchema.safeParse(c.value).success).toBe(true);
      expect(obsResultIssues(c.value)).toEqual([]);
    }
    const asserted = resultObs.invalid.filter(
      (c) => c.error?.type !== "assertion",
    );
    expect(refusals(asserted, obsResultIssues)).toEqual([]);
  });

  it("result_obs.json: observations never ride in data_as_of (D28)", () => {
    const moved = resultObs.invalid.find(
      (c) => c.name === "observations_in_data_as_of",
    );
    const issues = obsResultIssues(moved?.value);
    expect(
      issues.some(
        (issue) =>
          issue.code === "unrecognized_keys" &&
          issue.keys.includes("observations") &&
          issue.path.join(".") === "data_as_of",
      ),
    ).toBe(true);
  });

  it("the obs payload fixture is result_obs.json's valid case until TR-27 regenerates it from a real run", () => {
    expect(obsPayload).toEqual(validCase(resultObs, "obs_result"));
  });
});

describe("the contract document says what the frontend implements", () => {
  it("names the same enum values, in the same order", () => {
    const enums: [string, readonly string[]][] = [
      ["TREND_STATES", TREND_STATES],
      ["GSC_STATES", GSC_STATES],
      ["LINK_STATES", LINK_STATES],
      ["SORTS", PICK_SORTS],
      ["EXCLUSION_REASONS", EXCLUSION_REASONS],
      ["EVIDENCE_KINDS", EVIDENCE_KINDS],
      ["LINK_LABELS", LINK_LABELS],
      ["LINK_ACTIONABILITY_REASONS", LINK_ACTIONABILITY_REASONS],
      ["CONFIRMATIONS", CONFIRMATIONS],
      ["ADMISSIONS", ADMISSIONS],
      ["ID_EVIDENCE_LEVELS", ID_EVIDENCE_LEVELS],
    ];
    for (const [name, values] of enums)
      expect([name, ...values]).toEqual([name, ...docEnum(DOC, name)]);
  });

  it("lists the same fields as the schemas", () => {
    const shape = pickObservationsSchema.shape;
    expect(docFields(DOC, "Observations")).toEqual(Object.keys(shape));
    expect(docFields(DOC, "TrendsSetRef")).toEqual(
      Object.keys(shape.trends.unwrap().shape),
    );
    expect(docFields(DOC, "GscSetRef")).toEqual(
      Object.keys(shape.gsc.unwrap().shape),
    );
    expect(docFields(DOC, "ObsCoverage")).toEqual(
      Object.keys(shape.coverage.shape),
    );
    expect(docFields(DOC, "ObsConditionFields")).toEqual([
      ...OBS_CONDITION_FIELDS,
    ]);
    for (const field of OBS_CONDITION_FIELDS)
      expect(Object.keys(pickConditionsSchema.shape)).toContain(field);
  });

  it("flattens evidence into the nine keys the generic evidence schema already has (D8)", () => {
    const keys = docFields(DOC, "ObsEvidence");
    expect(keys).toEqual(Object.keys(pickEvidenceSchema.shape));
    expect(keys).toEqual(Object.keys(obsEvidenceSchema.innerType().shape));
  });

  it("gives each link label the same Chinese text", () => {
    const line = /中文标签依次是：(.+)/.exec(DOC)?.[1] ?? "";
    const pairs = [...line.matchAll(/`(\w+)`（([^）]+)）/g)].map((m) => [
      m[1],
      m[2],
    ]);
    expect(pairs).toEqual(Object.entries(LINK_LABEL_TEXT));
  });
});
