/**
 * A link fact's actionability at request time (plan TR-24; D13; contract
 * section 8): the TS twin of ggwork_pick/observe/link_rules.link_actionable.
 * Facts are frozen in pick_obs.links and never recomputed here; only whether
 * one may be acted on now is. obs_link_cases.json's actionable_cases are the
 * shared truth; Python's test_obs_link_rules runs the same cases.
 */
import { describe, expect, it } from "@rstest/core";

import { LINK_ACTIONABILITY_REASONS } from "@/core/pick/obs-contract";
import { forbiddenIn } from "@/core/pick/obs-format";
import {
  ACTIONABILITY_TEXT,
  LINK_RULE_LIMITS,
  linkActionable,
  type LinkFactInput,
} from "@/core/pick/obs-link";

import { repoText } from "./obs-contract-fixtures";

type ActionableCase = Readonly<{
  name: string;
  fact: LinkFactInput;
  trends_published_at: string;
  gsc_published_at: string;
  now: string;
  version?: string;
  expected: { actionable: boolean; reasons: string[] };
}>;

const FIXTURE = JSON.parse(
  repoText(
    "customizations/pick-workbench/tests/fixtures/obs_contract/obs_link_cases.json",
  ),
) as {
  params: {
    link_rules_version: string;
    trends_max_age_hours: number;
    gsc_max_age_hours: number;
  };
  actionable_cases: ActionableCase[];
};

describe("linkActionable", () => {
  it.each(FIXTURE.actionable_cases.map((c) => [c.name, c] as const))(
    "%s",
    (_name, c) => {
      expect(
        linkActionable(
          c.fact,
          c.trends_published_at,
          c.gsc_published_at,
          c.now,
          c.version ?? FIXTURE.params.link_rules_version,
        ),
      ).toEqual(c.expected);
    },
  );

  it("knows link-rules-v1's two age limits", () => {
    expect(LINK_RULE_LIMITS).toEqual({
      [FIXTURE.params.link_rules_version]: {
        trendsMaxAgeHours: FIXTURE.params.trends_max_age_hours,
        gscMaxAgeHours: FIXTURE.params.gsc_max_age_hours,
      },
    });
  });

  it("refuses a version it does not know, never running the newest", () => {
    expect(() =>
      linkActionable(
        { label: "both_rising", timely: true, stale: false },
        "2026-09-25T01:52:10.000000+00:00",
        "2026-09-25T03:31:40.000000+00:00",
        "2026-09-25T04:10:00.000000+00:00",
        "link-rules-v9",
      ),
    ).toThrow("link-rules-v9");
  });

  it("gives reasons in the contract's order, each with a text", () => {
    const result = linkActionable(
      { label: "global_parallel", timely: false, stale: true },
      "2026-09-20T01:52:10.000000+00:00",
      "2026-09-20T03:31:40.000000+00:00",
      new Date("2026-09-25T04:10:00.000Z"),
      "link-rules-v1",
    );
    expect(result.reasons).toEqual([...LINK_ACTIONABILITY_REASONS]);
    expect(Object.keys(ACTIONABILITY_TEXT)).toEqual([
      ...LINK_ACTIONABILITY_REASONS,
    ]);
    for (const text of Object.values(ACTIONABILITY_TEXT))
      expect(forbiddenIn(text)).toEqual([]);
  });
});
