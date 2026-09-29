/**
 * The pick_obs rows the data page reads (plan TR-24; contract sections 9-10;
 * D29). views.json and set_summaries.json are the shared truth: Python's
 * test_contract runs the same cases. The page parses every row it shows and
 * refuses one it cannot read, never guessing: column types, the enums it
 * renders, and the one cross-field rule it relies on (a set's channel agrees
 * with its summary's and its frozen inputs'). The other cross-field rules are
 * the writer's (store refuses them before a set is published) and are listed
 * below by name, so a new case in the fixture is a decision, not an accident.
 * Status codes are the exception on purpose: an unknown code is still shown
 * (obs-banner-rules), so it never makes a row unreadable.
 */
import { describe, expect, it } from "@rstest/core";
import type { z } from "zod";

import {
  OBS_VIEW_SCHEMAS,
  obsSetSchema,
  setSummarySchema,
} from "@/core/pick/obs-rows";

import { obsFixture, type ModelFixture } from "./obs-contract-fixtures";

type ViewCase = Readonly<{
  view: string;
  name: string;
  row: Record<string, unknown>;
  why?: string;
}>;
type ViewsFixture = Readonly<{ valid: ViewCase[]; invalid: ViewCase[] }>;

const VIEWS = obsFixture<ViewsFixture>("views.json");
const SUMMARIES = obsFixture<ModelFixture>("set_summaries.json");

/** The five views the page reads; alias_queue, confirm_queue and alerts belong to TR-25b and the agent. */
const READ = ["sets", "states", "links", "discoveries", "run_status"] as const;

/** Invalid cases the page leaves to the writer, each with why that is safe. */
const WRITER_ONLY: Readonly<Record<string, string>> = {
  sets_frozen_inputs_shape:
    "of the frozen inputs the page reads the channel and the market map's version only",
  sets_target_date_not_frozen: "the page shows the column, not the copy",
  sets_gsc_with_target_date: "the page never reads target_date of a GSC set",
  sets_round_id_not_frozen: "the page shows the column, not the copy",
  sets_copy_not_frozen: "the page shows the columns, not the copies",
  sets_rules_version_not_frozen: "the page shows the column, not the copy",
  sets_gsc_as_of_not_cutoff: "the page shows the summary's cutoff",
  states_informal_state:
    "labels are shown as stored, each with its formal flag",
  links_all_with_us_geo: "the label is shown as stored",
  run_status_unknown_code: "an unknown code is shown in red, never dropped",
};

const schemaOf = (view: string): z.ZodTypeAny | undefined =>
  (OBS_VIEW_SCHEMAS as Readonly<Record<string, z.ZodTypeAny>>)[view];

describe("the views the page reads", () => {
  it.each(
    VIEWS.valid
      .filter((c) => (READ as readonly string[]).includes(c.view))
      .map((c) => [c.name, c] as const),
  )("reads %s", (_name, c) => {
    const parsed = schemaOf(c.view)?.safeParse(c.row);
    expect(parsed?.error?.issues ?? []).toEqual([]);
  });

  it("names exactly the five views", () => {
    expect(Object.keys(OBS_VIEW_SCHEMAS).sort()).toEqual([...READ].sort());
  });

  const refused = VIEWS.invalid.filter(
    (c) => (READ as readonly string[]).includes(c.view) && !WRITER_ONLY[c.name],
  );
  it.each(refused.map((c) => [c.name, c] as const))(
    "refuses %s",
    (_name, c) => {
      expect(schemaOf(c.view)?.safeParse(c.row).success).toBe(false);
    },
  );

  it("every writer-only case is still in the fixture and still read", () => {
    for (const name of Object.keys(WRITER_ONLY)) {
      const c = VIEWS.invalid.find((item) => item.name === name);
      expect(c?.name).toBe(name);
      if (c) expect(schemaOf(c.view)?.safeParse(c.row).success).toBe(true);
    }
  });
});

describe("set summaries", () => {
  it.each(SUMMARIES.valid.map((c) => [c.name, c.value] as const))(
    "reads %s",
    (_name, value) => {
      expect(setSummarySchema.safeParse(value).error?.issues ?? []).toEqual([]);
    },
  );

  it("an unknown status code in a summary is kept, not refused", () => {
    const c = SUMMARIES.invalid.find((i) => i.name === "unknown_status_code");
    const parsed = setSummarySchema.safeParse(c?.value);
    expect(parsed.success).toBe(true);
  });

  it("a set whose summary names the other channel is refused", () => {
    const set = VIEWS.valid.find((c) => c.name === "sets_trends")?.row;
    const gsc = SUMMARIES.valid.find((c) => c.name === "gsc")?.value;
    expect(obsSetSchema.safeParse({ ...set, summary: gsc }).success).toBe(
      false,
    );
  });
});
