import { z } from "zod";

const number = z.number().finite().nullable();
export const pointSchema = z.object({
  date: z.string(),
  value: z.number().min(0).max(100),
});
export const scoreSchema = z.object({
  score: number,
  tier: z.string().nullable(),
  prediction_label: z.string(),
  growth_pct: number,
  growth_state: z.string(),
  rules_version: z.string(),
  heat_score: number,
  momentum_score: number,
  platform_score: number,
  promo_score: number,
  avg_heat: number,
  peak_heat: number,
  velocity_24h: number,
  acceleration: number,
  climb_days: number,
});
export const rowSchema = z.object({
  id: z.number().int(),
  original_title: z.string().nullable(),
  clean_title: z.string().nullable(),
  platforms: z.array(z.string()).nullable(),
  genres: z.array(z.string()).nullable(),
  promo_tags: z.array(z.string()).nullable(),
  platform_urls: z.record(z.string()).nullable(),
  geo: z.string().nullable(),
  timeframe: z.string().nullable(),
  series_status: z.enum([
    "historical_data",
    "historical_zero",
    "fallback_unverified",
    "missing_series",
    "invalid_series",
  ]),
  series_start: z.string().nullable(),
  series_end: z.string().nullable(),
  period_days: number,
  cache_written_at: z.string().nullable(),
  is_older_window: z.boolean().nullable(),
  provenance: z.literal("imported_unverified"),
  snapshot_id: z.string(),
  recent_series: z.array(pointSchema).nullable(),
  pilot: scoreSchema,
  record_errors: z.array(z.object({ field: z.string(), code: z.string() })),
});
const savedScore = z
  .object({
    score: number.optional(),
    tier: z.string().nullable().optional(),
    prediction_label: z.string().nullable().optional(),
  })
  .nullable();
export const detailSchema = rowSchema.extend({
  timeline_data: z.array(pointSchema).nullable(),
  synopsis: z.string().nullable(),
  original: savedScore,
  legacy_replay: savedScore,
  score_differences: z.record(z.unknown()),
});
export const listSchema = z.object({
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  items: z.array(rowSchema),
  snapshot_id: z.string(),
});
export const statsSchema = z.object({
  catalog_total: z.number().int(),
  with_series: z.number().int(),
  without_series: z.number().int(),
  latest_series_end: z.string().nullable(),
  series_end_dates: z.array(z.string()),
  snapshot: z.object({
    snapshot_id: z.string(),
    latest_cache_written_at: z.string().nullable(),
    rules_version: z.string(),
  }),
});
export type RadarRow = z.infer<typeof rowSchema>;
export type RadarDetail = z.infer<typeof detailSchema>;
export type RadarPoint = z.infer<typeof pointSchema>;
