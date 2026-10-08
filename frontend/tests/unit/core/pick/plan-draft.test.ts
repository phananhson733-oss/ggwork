import { expect, it } from "@rstest/core";

import { planSchema, planUpdateSchema } from "@/core/pick/completion-types";
import {
  editablePlan,
  localTimeChoices,
  timezonePreview,
} from "@/core/pick/plan-draft";

import fixture from "./fixtures/completion-v1.json";
it("edits enriched source rows through the strict replacement contract", () => {
  const draft = editablePlan(planSchema.parse(fixture.plan));
  expect(
    planUpdateSchema.safeParse({
      ...draft,
      request_id: "retry",
      expected_version: 1,
    }).success,
  ).toBe(true);
  expect(draft.rows[0]).not.toHaveProperty("source_pin");
  expect(draft.rows[0]?.row_id).toBe(fixture.plan.rows[0]?.row_id);
});
it("distinguishes skipped and both repeated local instants independently of browser time", () => {
  expect(localTimeChoices("2026-03-08T02:30", "America/New_York")).toEqual([]);
  expect(localTimeChoices("2026-11-01T01:30", "America/New_York")).toEqual([
    { instant: "2026-11-01T05:30:00.000Z", offset: "UTC-04:00", fold: 0 },
    { instant: "2026-11-01T06:30:00.000Z", offset: "UTC-05:00", fold: 1 },
  ]);
});
it("previews retaining wall time versus instant explicitly", () => {
  const plan = planSchema.parse(fixture.plan);
  plan.timezone = "America/New_York";
  plan.rows[0]!.local_time = "2026-01-15T09:00";
  plan.rows[0]!.scheduled_at = "2026-01-15T14:00:00Z";
  expect(
    timezonePreview(plan, "America/Chicago", "keep_instant")[0]?.local_time,
  ).toBe("2026-01-15T08:00");
  expect(
    timezonePreview(plan, "America/Chicago", "keep_local_time")[0]?.local_time,
  ).toBe("2026-01-15T09:00");
});
