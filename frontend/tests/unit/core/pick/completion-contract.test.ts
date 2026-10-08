import { describe, expect, it } from "@rstest/core";

import * as completion from "@/core/pick/completion-types";
import {
  checkedPublicationSchema,
  commonQuerySchema,
  planCreateSchema,
} from "@/core/pick/completion-types";
import { pickResultSchema } from "@/core/pick/types";

import oldResult from "./fixtures/backend-result.json";
import wire from "./fixtures/completion-v1.json";

describe("completion boundary", () => {
  it("keeps explicit historical scope and refuses caller authority", () => {
    const query = {
      domain: "rankings",
      scope: "full_catalog",
      signal_kind: "kd",
      period: { kind: "daily", value: "2026-01-01" },
    };
    expect(commonQuerySchema.parse(query).period.value).toBe("2026-01-01");
    expect(
      commonQuerySchema.safeParse({ ...query, owner_id: "other" }).success,
    ).toBe(false);
    expect(
      commonQuerySchema.safeParse({ ...query, budget_ms: 10001 }).success,
    ).toBe(false);
  });
  it("does not alter the historical strict result contract", () => {
    expect(pickResultSchema.safeParse(oldResult).success).toBe(true);
    expect(
      pickResultSchema.safeParse({ ...oldResult, plan_id: "plan1" }).success,
    ).toBe(false);
  });
  it("cannot label unknown facts as a confirmed publication", () => {
    const publication = {
      thread_id: "t",
      run_id: "r",
      message_id: "m",
      status: "confirmed",
      content: "80集",
      facts: [
        {
          claim: "80集",
          status: "unknown",
          evidence_refs: [],
          reason: "缺来源",
        },
      ],
      references: [],
      checker_version: "check-v1",
      correction_count: 0,
      checked_at: "2026-10-08T12:00:00+00:00",
    };
    expect(checkedPublicationSchema.safeParse(publication).success).toBe(false);
    expect(
      checkedPublicationSchema.safeParse({ ...publication, status: "partial" })
        .success,
    ).toBe(true);
  });
  it("can save an incomplete draft but refuses duplicate row identity", () => {
    const row = {
      row_id: "row1",
      identity: '["synthetic","A","en"]',
      source_result_id: "r1",
      source_item_id: "i1",
    };
    const draft = {
      request_id: "cmd1",
      title: "下周",
      timezone: "America/Chicago",
      rows: [row],
    };
    expect(planCreateSchema.parse(draft).rows[0]?.local_time).toBe(null);
    expect(
      planCreateSchema.safeParse({ ...draft, rows: [row, row] }).success,
    ).toBe(false);
  });
});

it("accepts each shared backend wire example without silently dropping fields", () => {
  const schemas = {
    query: completion.commonQuerySchema,
    response: completion.queryResponseSchema,
    publication: completion.checkedPublicationSchema,
    draft: completion.planCreateSchema,
    plan: completion.planSchema,
    preview: completion.planPreviewSchema,
    export: completion.planExportSchema,
    link: completion.planLinkSchema,
    error: completion.completionErrorSchema,
    review: completion.reviewPostsSchema,
  };
  for (const name of Object.keys(schemas) as (keyof typeof schemas)[]) {
    expect(schemas[name].parse(wire[name])).toEqual(wire[name]);
    expect(
      schemas[name].safeParse({ ...wire[name], owner_id: "intruder" }).success,
    ).toBe(false);
  }
});
