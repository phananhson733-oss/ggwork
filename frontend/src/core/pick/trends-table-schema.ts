/**
 * The gateway's GET /api/pick/obs/trends-table answer (simplified scope 2026-09-30, section 6 item 4): the TS twin of
 * ggwork_pick/observe/trends_table.TrendsTable. contract.test parses the fixture the Python test writes from a real
 * answer (tests/unit/core/pick/fixtures/backend-trends-table.json).
 *
 * One night's task list, every planned drama a row in the source's order: its basis, its term, and what that night
 * fetched (result), with the series as Google answered it (a day without data is null, never 0). Averages, labels and
 * links are the page's (trends-table.ts). Unknown keys are dropped; a value out of shape fails the whole answer, which
 * the page shows as "unreadable", never as a partial table.
 */
import { z } from "zod";

import { BANNER_LEVELS } from "./obs-status";
import { STAMP_PATTERN } from "./types";

/** trends_table.ROW_LIMIT and MAX_POINTS */
export const TABLE_ROW_LIMIT = 200;
export const TABLE_MAX_POINTS = 40;
const IDENTITY_MAX = 512;

const stamp = z.string().regex(new RegExp(`^${STAMP_PATTERN}$`));
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const short = z.string().max(100);
const count = z.number().int().nonnegative();

export const BASIS_KINDS = ["qc", "qr", "kd", "revenue"] as const;
export type BasisKind = (typeof BASIS_KINDS)[number];

export const TABLE_RESULTS = [
  "data",
  "no_data",
  "not_fetched",
  "pending",
] as const;
export type TableResult = (typeof TABLE_RESULTS)[number];

const basisSchema = z.object({
  kind: z.enum(BASIS_KINDS),
  board_date: day.nullable(),
  rank: z.number().int().min(0).max(1_000_000),
  identity: z.string().min(1).max(IDENTITY_MAX).nullable().optional(),
});

const pointSchema = z.object({
  date: day,
  value: z.number().int().min(0).max(100).nullable(),
  partial: z.boolean(),
});

const rowSchema = z.object({
  order: z.number().int().min(1),
  unit: short,
  identity: z.string().min(1).max(IDENTITY_MAX),
  title: z.string().max(500),
  platform: short,
  language: short,
  term: z.string().min(1).max(200),
  geo: z.string().regex(/^(WW|[A-Z]{2})$/),
  time_range: short,
  /** a pick joins the basis of every drama asking Google the same term; no cap on the backend, a sanity one here */
  basis: z.array(basisSchema).max(1000),
  result: z.enum(TABLE_RESULTS),
  status: short.nullable(),
  series: z.array(pointSchema).max(TABLE_MAX_POINTS).nullable(),
});

const boardSchema = z.object({
  kind: z.enum(BASIS_KINDS),
  board_date: day.nullable(),
  listed: count,
});

const revenueSchema = z.object({
  available: z.boolean(),
  reason: short.nullable(),
  mirror_version: z.number().int().positive().nullable(),
  day: day.nullable(),
  filled: count,
});

const batchSchema = z.object({
  batch_id: short,
  target_date: day,
  collect_mode: short,
  outcome: short,
  started_at: stamp,
  finished_at: stamp.nullable(),
  window_end: stamp,
  catalog_batch_id: z.string().max(200).nullable(),
  sources: z.object({
    boards: z.array(boardSchema).max(8),
    revenue: revenueSchema.nullable(),
  }),
  counts: z.object({
    planned: count,
    data: count,
    no_data: count,
    not_fetched: count,
    pending: count,
  }),
});

export const trendsTableSchema = z.object({
  checked_at: stamp,
  banners: z
    .array(
      z.object({
        code: z.string().min(1).max(64),
        level: z.enum(BANNER_LEVELS),
      }),
    )
    .max(32),
  batch: batchSchema.nullable(),
  rows: z.array(rowSchema).max(TABLE_ROW_LIMIT),
  row_limit: count,
  truncated: z.boolean(),
});

export type TrendsTable = z.infer<typeof trendsTableSchema>;
export type TrendsTableRow = TrendsTable["rows"][number];
export type TrendsTableBatch = NonNullable<TrendsTable["batch"]>;
export type TrendsTableBasis = TrendsTableRow["basis"][number];
