import { z } from "zod";

const identifier = z.string().min(1).max(512);

// ---- observation radar (plan TR-16; contract TR-33: docs/pick-workbench/observe-contract.md) -------------------------
// The shapes the gateway sends once it knows observations (design 7.5 step 1: the frontend lets them through first).
// Every enum and pattern here is ggwork_pick/observe/contract.py's; contract-fixtures.test keeps them equal.

export const PICK_SORTS = ["evidence_date", "rank", "obs"] as const;
export const TREND_STATES = ["rising", "emerging"] as const;
export const GSC_STATES = [
  "rising",
  "surge",
  "from_zero",
  "high_ctr",
  "rank_push",
  "present",
] as const;
export const LINK_STATES = [
  "both_rising",
  "trends_lead_page",
  "trends_lead_distribution",
  "site_only",
  "cooling",
] as const;
export const EXCLUSION_REASONS = [
  "trend_first_only",
  "emerging_not_requested",
  "title_ambiguous",
  "shared_title",
  "correspondence_unconfirmed",
  "correspondence_presumed",
  "stale",
  "carried_over",
  "b_tier",
  "unstable",
  "control_unavailable",
  "gsc_descriptive_only",
  "migration_suspect",
  "mapping_changed",
  "set_batch_mismatch",
] as const;
/** repository.stamp(): UTC, six fractional digits, "+00:00" */
export const STAMP_PATTERN = String.raw`\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00`;
/** Trends says WW for worldwide; GSC says ALL for the site total */
const TREND_GEO = /^(WW|[A-Z]{2})$/;
const GSC_COUNTRY = /^(ALL|[A-Z]{3})$/;
const MAX_TREND_GEOS = 10;
const MAX_GSC_COUNTRIES = 30;

const stamp = z.string().regex(new RegExp(`^${STAMP_PATTERN}$`));
const setId = z.string().regex(/^[0-9a-f]{32}$/);
const count = z.number().int().nonnegative();

/** The result-level observations key (D28): obs_as_of_json's references, then its frozen counts. */
export const pickObservationsSchema = z
  .object({
    trends: z
      .object({ set_id: setId, published_at: stamp, latest_block_end: stamp })
      .strict()
      .nullable(),
    gsc: z
      .object({ set_id: setId, published_at: stamp, cutoff: stamp })
      .strict()
      .nullable(),
    link_rules_version: z
      .string()
      .regex(/^link-rules-v[0-9A-Za-z]+$/)
      .nullable(),
    judged_at: stamp,
    coverage: z
      .object({
        pool_identities: count,
        trends_observed: count,
        gsc_observed: count,
      })
      .strict(),
    /** Only the reasons that excluded something, each at least once */
    excluded: z.record(z.enum(EXCLUSION_REASONS), z.number().int().min(1)),
  })
  .strict();

export const pickConditionsSchema = z
  .object({
    theater: z.string().max(100).nullable().optional(),
    language: z.string().max(40).nullable().optional(),
    channel: z.enum(["youtube", "tiktok", "facebook"]).nullable().optional(),
    query: z.string().max(200).nullable().optional(),
    tags: z.array(z.string().max(100)).max(20).optional(),
    limit: z.number().int().min(1).max(20),
    exclude_selected: z.boolean(),
    confirmed_eligible_only: z.boolean().optional(),
    exclude_previous: z.boolean().optional(),
    signal_kind: z.string().max(20).nullable().optional(),
    sort: z.enum(PICK_SORTS).optional(),
    exclude_posted: z.boolean().optional(),
    posted_account: z.string().max(200).nullable().optional(),
    // The seven observation fields (D31), stored only when set: an old card has none of them.
    trend_state: z.enum(TREND_STATES).nullable().optional(),
    trend_geos: z
      .array(z.string().regex(TREND_GEO))
      .max(MAX_TREND_GEOS)
      .optional(),
    trend_include_first: z.boolean().optional(),
    trend_include_presumed: z.boolean().optional(),
    gsc_state: z.enum(GSC_STATES).nullable().optional(),
    gsc_countries: z
      .array(z.string().regex(GSC_COUNTRY))
      .max(MAX_GSC_COUNTRIES)
      .optional(),
    link_state: z.enum(LINK_STATES).nullable().optional(),
  })
  .strict();

export const pickEvidenceSchema = z
  .object({
    citation_id: identifier,
    kind: z.string().min(1).max(100),
    source_ref: z.string().min(1).max(2048),
    observed_at: z.string().max(40).nullable(),
    value: z.union([z.string().max(4000), z.number().finite(), z.null()]),
    label: z.string().max(200).optional(),
    rank: z.number().int().nullable().optional(),
    grade: z.string().max(100).optional(),
    note: z.string().max(1000).optional(),
  })
  .strict();

export const pickPostedSchema = z
  .object({
    matched: z.boolean(),
    records: z.array(z.string().max(64)).max(50),
    post_count: z.number().int().nonnegative(),
    sched_count: z.number().int().nonnegative(),
    last_post_on: z.string().max(40).nullable(),
    accounts: z.array(z.string().max(200)).max(100),
  })
  .strict();

export const pickDataAsOfSchema = z
  .object({
    source_as_of: z.string().max(40).nullable(),
    published_at: z.string().max(40).nullable(),
    freshness: z.record(z.string(), z.unknown()).nullable().optional(),
    scope: z.string().max(500).nullable().optional(),
    shared: z.boolean(),
    /**
     * The pick_mirror version this result was pinned to (P4-1). Absent while the
     * backend switch is off; null when the sync published without a paired mirror.
     */
    mirror_version: z.number().int().positive().nullable().optional(),
  })
  .strict();

export const pickItemSchema = z
  .object({
    item_id: identifier,
    identity: identifier,
    title: z.string().min(1).max(500),
    theater: z.string().max(100),
    language: z.string().min(1).max(40),
    availability: z.enum(["active", "delisted", "unknown"]),
    reason: z.string().max(4000),
    warnings: z.array(z.string().max(1000)).max(20),
    evidence: z.array(pickEvidenceSchema).max(50),
    posted: pickPostedSchema.optional(),
    detail_url: z.string().max(2048).optional(),
    matched_total: z.number().int().nonnegative().optional(),
  })
  .strict();

export const pickResultSchema = z
  .object({
    id: identifier,
    thread_id: identifier,
    run_id: identifier,
    run_status: z.enum([
      "pending",
      "running",
      "success",
      "error",
      "timeout",
      "interrupted",
      "unknown",
    ]),
    catalog_batch_id: identifier,
    knowledge_batch_id: identifier.nullable(),
    rule_version: identifier,
    ranking_version: identifier,
    conditions: pickConditionsSchema,
    items: z.array(pickItemSchema).max(20),
    created_at: z.string().datetime({ offset: true }),
    matched_total: z.number().int().nonnegative().nullable().optional(),
    data_as_of: pickDataAsOfSchema.nullable().optional(),
    /** Only on results with observation conditions; evidence carries obs_* entries in its existing shape (D8) */
    observations: pickObservationsSchema.optional(),
  })
  .strict()
  .refine(
    (result) =>
      new Set(result.items.map((item) => item.item_id)).size ===
      result.items.length,
    "候选条目标识重复",
  );

export type PickConditions = z.infer<typeof pickConditionsSchema>;
export type PickEvidence = z.infer<typeof pickEvidenceSchema>;
export type PickPosted = z.infer<typeof pickPostedSchema>;
export type PickDataAsOf = z.infer<typeof pickDataAsOfSchema>;
export type PickObservations = z.infer<typeof pickObservationsSchema>;
export type PickItem = z.infer<typeof pickItemSchema>;
export type PickResult = z.infer<typeof pickResultSchema>;

export const pickRunStatusLabel: Record<PickResult["run_status"], string> = {
  pending: "等待运行完成，暂不可保存",
  running: "运行中，当前为部分结果，暂不可保存",
  success: "运行已完成",
  error: "运行失败，当前为部分结果，请重新选剧",
  timeout: "运行超时，当前为部分结果，请重新选剧",
  interrupted: "运行已取消，当前为部分结果，请重新选剧",
  unknown: "无法核实来源运行状态，暂不可保存",
};
