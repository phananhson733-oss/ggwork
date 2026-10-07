import { z } from "zod";

import { trendsTableSchema } from "./trends-table-schema";

const candidate = trendsTableSchema.shape.rows.element
  .omit({ unit: true, result: true, status: true, series: true })
  .extend({
    order: z.number().int().min(1).max(100),
    geo: z.literal("WW"),
    time_range: z.literal("today 1-m"),
  });

export const trendsCandidatesSchema = z
  .object({
    checked_at: trendsTableSchema.shape.checked_at,
    kind: z.literal("candidate_preview"),
    selection_state: z.enum(["ready", "empty"]),
    catalog_batch_id: z.string().max(200).nullable(),
    target: z.literal(100),
    selected: z.number().int().min(0).max(100),
    unusable_titles: z.number().int().nonnegative(),
    sources: trendsTableSchema.shape.batch.unwrap().shape.sources,
    rows: z.array(candidate).max(100),
  })
  .refine(
    (data) =>
      data.selected === data.rows.length &&
      (data.selection_state === "ready") === data.selected > 0 &&
      new Set(data.rows.map((row) => row.identity)).size === data.selected &&
      data.rows.every((row, index) => row.order === index + 1),
    "Candidate selection must have consistent counts, state, identities and order",
  );

export type TrendsCandidates = z.infer<typeof trendsCandidatesSchema>;
