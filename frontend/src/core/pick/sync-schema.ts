/**
 * The shape of `GET /api/pick/sync`, shared by the browser client and the
 * server-side pick data board. Pure: zod only, no fetcher, so a server module
 * can import it without pulling browser code in.
 *
 * zod objects drop unknown keys, so `mirror` (P2-8b) is declared explicitly or
 * it would vanish. It is optional (a gateway from before P2-8b), nullable
 * (SQLite never mirrors), or `{error: "<class name>"}` when the gateway failed
 * to read the mirror status (routes.mirror_view): that branch is modelled, so
 * the board can say "no mirror status right now" instead of saying nothing.
 * Any other shape reads as absent: no mirror banners, while the imports tab
 * and the rest of /sync still parse. mirrorStatusSchema itself stays strict,
 * so no banner guesses at a field.
 *
 * `obs` (plan TR-25, D10; contract section 13) is the radar's status: per
 * channel the current live set, the latest set of either mode, the last run
 * and the banners the gateway computed at `checked_at`; or `{error}` when the
 * gateway could not read it. Same declaration as mirror, so a malformed obs
 * reads as absent and never breaks the rest. The channels must be trends
 * then gsc; a banner's level is one of three, its code any snake_case word:
 * a code a newer gateway adds still shows (obs-status.ts words it).
 *
 * Times come in two shapes: current.as_of is RealShort's asOf
 * ("…T22:15:00.000Z"), every other moment is the gateway's stamp
 * ("…T03:52:00.123456+00:00"); parseSyncTime reads both.
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
  published_at: z.string().nullable(),
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

/** A Python class name (type(exc).__name__), never free text. */
const CLASS_NAME = /^[A-Za-z_][A-Za-z0-9_.]{0,99}$/;

/** The gateway could not read the mirror status; it names the class only. */
export const mirrorReadErrorSchema = z
  .object({ error: z.string().regex(CLASS_NAME) })
  .strict();

/** What /sync's mirror key holds when present and not null. */
export const mirrorFieldSchema = z.union([
  mirrorStatusSchema,
  mirrorReadErrorSchema,
]);

const SET_ID = /^[0-9a-f]{32}$/;
const STATUS_CODE = /^[a-z][a-z0-9_]{0,39}$/;
/** obs-status.ts's BANNER_LEVELS and OBS_CHANNELS; this module imports zod only (obs-status.test keeps them equal). */
export const SYNC_BANNER_LEVELS = ["red", "warn", "info"] as const;
export const SYNC_OBS_CHANNELS = ["trends", "gsc"] as const;

export const obsBannerSchema = z.object({
  code: z.string().regex(STATUS_CODE),
  level: z.enum(SYNC_BANNER_LEVELS),
});

export const obsChannelStatusSchema = z.object({
  channel: z.enum(SYNC_OBS_CHANNELS),
  live_set_id: z.string().regex(SET_ID).nullable(),
  live_published_at: z.string().nullable(),
  latest_set_id: z.string().regex(SET_ID).nullable(),
  latest_published_at: z.string().nullable(),
  latest_mode: z.enum(["live", "shadow"]).nullable(),
  last_run_at: z.string().nullable(),
  banners: z.array(obsBannerSchema),
});

export const obsSyncStatusSchema = z
  .object({
    checked_at: z.string(),
    channels: z.array(obsChannelStatusSchema),
  })
  .refine(
    (status) =>
      status.channels.map((c) => c.channel).join() === SYNC_OBS_CHANNELS.join(),
    { message: "channels are trends then gsc", path: ["channels"] },
  );

/** The gateway could not read the radar's status; it names the class only. */
export const obsReadErrorSchema = z
  .object({ error: z.string().regex(CLASS_NAME) })
  .strict();

/** What /sync's obs key holds when present and not null. */
export const obsFieldSchema = z.union([
  obsSyncStatusSchema,
  obsReadErrorSchema,
]);

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
  mirror: mirrorFieldSchema.nullable().optional().catch(undefined),
  obs: obsFieldSchema.nullable().optional().catch(undefined),
});

export type PickSyncRun = z.infer<typeof syncRunSchema>;
export type PickMirrorStatus = z.infer<typeof mirrorStatusSchema>;
export type PickMirrorReadError = z.infer<typeof mirrorReadErrorSchema>;
export type PickMirrorField = z.infer<typeof mirrorFieldSchema>;
export type PickSyncStatus = z.infer<typeof syncStatusSchema>;
export type PickObsBanner = z.infer<typeof obsBannerSchema>;
export type PickObsChannelStatus = z.infer<typeof obsChannelStatusSchema>;
export type PickObsStatus = z.infer<typeof obsSyncStatusSchema>;
export type PickObsReadError = z.infer<typeof obsReadErrorSchema>;
export type PickObsField = z.infer<typeof obsFieldSchema>;

export function isMirrorReadError(
  mirror: PickMirrorField | null | undefined,
): mirror is PickMirrorReadError {
  return typeof mirror === "object" && mirror !== null && "error" in mirror;
}

export function isObsReadError(
  obs: PickObsField | null | undefined,
): obs is PickObsReadError {
  return typeof obs === "object" && obs !== null && "error" in obs;
}

const SYNC_TIME =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;

/** A /sync moment in either of its two shapes; null for anything else. */
export function parseSyncTime(value: string | null | undefined): Date | null {
  if (typeof value !== "string" || !SYNC_TIME.test(value)) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** A /sync moment as the pick pages print it: UTC, to the minute; null when it cannot be read. */
export function utcMinute(value: string | null | undefined): string | null {
  const moment = parseSyncTime(value);
  return moment
    ? `${moment.toISOString().slice(0, 16).replace("T", " ")} UTC`
    : null;
}
