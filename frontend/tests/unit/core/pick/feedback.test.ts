import { describe, expect, it } from "@rstest/core";

import {
  feedbackReplySchema,
  feedbackStatusSchema,
} from "@/core/pick/feedback-schema";
import { pickResultNotesSchema } from "@/core/pick/notes";
const fixture = {
  status: "ok",
  contract_version: "feedback-v1",
  notice: "",
  feedback_version_id: "v1",
  scan_started_at: "2026-10-01T00:00:00Z",
  scan_completed_at: "2026-10-01T00:02:00Z",
  last_verified_at: null,
  freshness: "historical",
  source_quality: "partial",
  query_scope: {},
  items: [],
  coverage: {
    dramas: 0,
    posts: 0,
    measured_posts: 0,
    unmatched_posts: 0,
    missing_posts: 0,
  },
  warnings: [],
  total_groups: 0,
  has_more: false,
};
describe("feedback contract", () => {
  it("supports old notes and retains historical time", () => {
    expect(
      pickResultNotesSchema.parse({ item_facts: {} }).feedback,
    ).toBeUndefined();
    expect(feedbackReplySchema.parse(fixture).scan_completed_at).toBe(
      fixture.scan_completed_at,
    );
  });
  it("rejects malformed success, unknown status shape and malformed optional feedback", () => {
    expect(
      feedbackReplySchema.safeParse({ ...fixture, feedback_version_id: null })
        .success,
    ).toBe(false);
    expect(feedbackStatusSchema.safeParse({ enabled: true }).success).toBe(
      false,
    );
    expect(
      pickResultNotesSchema.safeParse({
        item_facts: {},
        feedback: { status: "ok" },
      }).success,
    ).toBe(false);
  });
});

it("rejects numeric finance and preserves decimal strings or unknown", () => {
  const item = {
    key: "a",
    evidence_kind: "direct",
    metrics: { views_total: "0", likes_total: null },
    coverage: fixture.coverage,
    evidence_refs: [],
    warnings: [],
    revenue: [
      {
        source_lane: "cps_auto",
        grain: "drama",
        currency: "USD",
        metric: "commission",
        amount: "0.00",
        amount_basis: null,
        records: 1,
        missing_records: 0,
      },
    ],
  };
  expect(
    feedbackReplySchema.parse({ ...fixture, items: [item] }).items[0]
      ?.revenue[0]?.amount,
  ).toBe("0.00");
  expect(
    feedbackReplySchema.safeParse({
      ...fixture,
      items: [{ ...item, revenue: [{ ...item.revenue[0], amount: 0 }] }],
    }).success,
  ).toBe(false);
  expect(
    feedbackReplySchema.parse({
      ...fixture,
      items: [
        {
          ...item,
          revenue: [{ ...item.revenue[0], amount: null, missing_records: 1 }],
        },
      ],
    }).items[0]?.revenue[0]?.amount,
  ).toBeNull();
});
