import { expect, test } from "@rstest/core";

import { trendsCandidatesSchema } from "@/core/pick/trends-candidates-schema";

import fixture from "./fixtures/backend-trends-candidates.json";

test("parses a real backend preview without observation claims", () => {
  const data = trendsCandidatesSchema.parse(fixture);
  expect(data.kind).toBe("candidate_preview");
  expect(data.selected).toBe(3);
  expect(data.rows[0]?.title).toBe("First Choice");
  expect(data.rows[1]?.basis).toHaveLength(2);
  expect(data).not.toHaveProperty("batch_id");
  expect(data.rows[0]).not.toHaveProperty("series");
});

test("rejects inconsistent counts, identities, order and selection state", () => {
  for (const value of [
    { ...fixture, selected: 100 },
    { ...fixture, selection_state: "empty" },
    { ...fixture, rows: [fixture.rows[0], fixture.rows[0], fixture.rows[2]] },
    { ...fixture, rows: [...fixture.rows].reverse() },
    { ...fixture, rows: fixture.rows.map((row) => ({ ...row, geo: "US" })) },
  ])
    expect(trendsCandidatesSchema.safeParse(value).success).toBe(false);
});
