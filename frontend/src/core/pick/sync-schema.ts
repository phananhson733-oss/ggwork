/**
 * The shape of `GET /api/pick/sync`, shared by the browser client and the
 * server-side pick data board. Pure: zod only, no fetcher, so a server module
 * can import it without pulling browser code in.
 *
 * zod objects drop unknown keys, so `mirror` (P2-8b) is declared explicitly or
 * it would vanish. It is optional (a gateway from before P2-8b) and nullable
 * (SQLite never mirrors). A mirror object that does not match fails the whole
 * parse: the board then says the sync status is unavailable instead of
 * guessing its banners.
 */
import { z } from "zod";

export const syncRunSchema = z.object({
  id: z.string(),
  source: z.string(),
  trigger: z.string(),
  status: z.enum(["running", "success", "failed"]),
  started_at: z.string(),
  finished_at: z.string().nullable(),
  rows: z.number().int().nullable(),
  catalog_batch_id: z.string().nullable(),
  knowledge_batch_id: z.string().nullable(),
  source_as_of: z.string().nullable(),
  error: z.string().nullable(),
});

const mirrorVersionSchema = z.object({
  id: z.number().int().positive(),
  as_of: z.string(),
  latest_snapshot: z.string().nullable(),
  published_at: z.string(),
});

const lockStuckSchema = z.object({
  pid: z.number().int().nullable(),
  holder: z.string().nullable(),
  since: z.string().nullable(),
});

export const mirrorStatusSchema = z.object({
  enabled: z.boolean(),
  current: mirrorVersionSchema.nullable(),
  series_through: z.string().nullable(),
  trimmed_before: z.string().nullable(),
  /** The paired batches are no longer the shared current ones. */
  behind: z.boolean(),
  consecutive_failures: z.number().int().nonnegative(),
  last_failure_at: z.string().nullable(),
  /** One of the fixed failure codes, never free text. */
  last_failure: z.string().nullable(),
  /** consecutive_failures >= 3 */
  alert: z.boolean(),
  /** The warning codes of the current version. */
  warnings: z.array(z.string()),
  lock_stuck: lockStuckSchema.nullable(),
  /** source_as_of of the shared catalog batch, not the signed-in user's. */
  shared_source_as_of: z.string().nullable().optional(),
});

export const syncStatusSchema = z.object({
  configured: z.boolean(),
  current: z
    .object({
      id: z.string(),
      shared: z.boolean(),
      source_as_of: z.string().nullable(),
      published_at: z.string().nullable(),
      freshness: z.record(z.string(), z.unknown()).nullable().optional(),
      scope: z.string().nullable().optional(),
      rows: z.number().int().nullable().optional(),
    })
    .nullable(),
  runs: z.array(syncRunSchema),
  mirror: mirrorStatusSchema.nullable().optional(),
});

export type PickSyncRun = z.infer<typeof syncRunSchema>;
export type PickMirrorStatus = z.infer<typeof mirrorStatusSchema>;
export type PickSyncStatus = z.infer<typeof syncStatusSchema>;
