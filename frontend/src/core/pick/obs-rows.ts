/**
 * The pick_obs rows the data page reads (plan TR-24; contract sections 9-10;
 * D29): sets, states, links, discoveries and run_status, as the reader role
 * sees them, parsed before anything is shown.
 *
 * Column types and the enums the page renders are checked; a row that breaks
 * them is refused (the loader turns that into a notice), never guessed at. The
 * one cross-field rule the page relies on is checked too: a set's channel
 * agrees with its summary's and its frozen inputs'. Status codes are
 * code-shaped strings, not an enum: an unknown one is still shown in red
 * (obs-banner-rules), so it never makes a row unreadable. obs-rows.test runs
 * views.json and set_summaries.json, the fixtures Python's test_contract runs.
 */
import { z } from "zod";

import { ADMISSIONS, CONFIRMATIONS, ID_EVIDENCE_LEVELS } from "./obs-contract";
import { LINK_LABELS } from "./obs-format";
import { STAMP_PATTERN } from "./types";

export const OBS_MODES = ["live", "shadow"] as const;
export const SET_STATUSES = ["published", "pruned"] as const;
export const TRENDS_ROW_STATES = [
  "rising",
  "emerging",
  "cooling",
  "flat",
  "sparse",
  "insufficient_window",
  "ambiguous",
  "failed",
] as const;
export const GSC_ROW_STATES = [
  "rising",
  "surge",
  "from_zero",
  "high_ctr",
  "rank_push",
  "present",
] as const;
export const GSC_LABELS = [
  "surge",
  "from_zero",
  "high_ctr",
  "rank_push",
  "rising",
] as const;
export const TRENDS_FLAGS = [
  "shared_title",
  "unstable",
  "control_unavailable",
] as const;
export const GSC_FLAGS = [
  "migration_suspect",
  "mapping_changed",
  "gap_exceeded",
  "unverifiable",
  "detail_gap",
  "stale_slice",
  "short_history",
] as const;
export const AMBIGUITIES = [
  "clear",
  "generic",
  "contained",
  "unresolved",
  "manual_required",
] as const;
export const DISCOVERY_PROPERTIES = ["web", "youtube"] as const;
export const DISCOVERY_MATCHES = ["unique", "multiple", "out_of_pool"] as const;
/** In the page's order: the queue first, then what is only shown, then b_only's absent A tier. */
export const DISCOVERY_ROUTES = ["queue", "display_only", "a_tier"] as const;
export const RUN_OUTCOMES = [
  "running",
  "published",
  "withheld",
  "failed",
] as const;
export const UNCOVERED_REASONS = [
  "truncated",
  "skipped_breaker",
  "deadline",
] as const;
export const UNATTRIBUTED_KINDS = [
  "legacy_unmapped",
  "delisted",
  "noncanonical",
  "locale_mismatch",
  "site_level",
  "editorial",
  "offsite",
  "blog",
] as const;
export const SITE_ADMISSIONS = [
  "usable",
  "gap_exceeded",
  "unverifiable",
] as const;
export const METRICS = ["impressions", "clicks"] as const;

const stamp = z.string().regex(new RegExp(`^${STAMP_PATTERN}$`));
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const setId = z.string().regex(/^[0-9a-f]{32}$/);
const rowId = z.number().int().min(1);
const count = z.number().int().nonnegative();
const share = z.number().min(0).max(1);
const identity = z.string().min(1).max(512);
const text = z.string().min(1).max(500);
const trendGeo = z.string().regex(/^(WW|[A-Z]{2})$/);
const gscCountry = z.string().regex(/^(ALL|[A-Z]{3})$/);
const scope = z.string().regex(/^(WW|[A-Z]{2}|ALL|[A-Z]{3})$/);
const version = z.string().min(1).max(64);
/** A status code: known ones have texts, an unknown one is still shown (never refused). */
const statusCode = z.string().regex(/^[a-z][a-z0-9_]{0,39}$/);
const channel = z.enum(["trends", "gsc"]);

// ---- set summaries (contract section 10) ---------------------------------------------------------------------------

const uncoveredUnitSchema = z
  .object({
    identity,
    geo: trendGeo,
    reason: z.enum(UNCOVERED_REASONS),
  })
  .strict();

export const trendsSummarySchema = z
  .object({
    channel: z.literal("trends"),
    planned_units: count,
    fetched_units: count,
    uncovered_units: z.array(uncoveredUnitSchema),
    a_tier_coverage: share,
    breaker_events: count,
    all_zero_rate: share.nullable(),
    user_types: z.array(z.string().min(1).max(100)),
    ambiguous_undecided: count,
    carried_over: count,
    stale: count,
    rule4_no_data: z.boolean(),
    status_codes: z.array(statusCode),
  })
  .strict();

const coverageLayersSchema = z
  .object({
    metric: z.enum(METRICS),
    received: count,
    attributed: count,
    unattributed: z.record(z.enum(UNATTRIBUTED_KINDS), count),
    site_total: count.nullable(),
    detail_gap: z.number().int().nullable(),
  })
  .strict()
  .refine(
    (layers) =>
      layers.received ===
      layers.attributed +
        Object.values(layers.unattributed).reduce((a, b) => a + b, 0),
    { message: "第一层不守恒" },
  )
  .refine(
    (layers) =>
      layers.detail_gap ===
      (layers.site_total === null ? null : layers.site_total - layers.received),
    { message: "明细缺口等于全站总量减收到的明细，没有总量时为空" },
  );

const vcheckSummarySchema = z
  .object({
    requests: count,
    succeeded: count,
    failed: count,
    truncated: count,
    stale: count,
    reused: count,
    regex_overflow: count,
  })
  .strict();

export const gscSummarySchema = z
  .object({
    channel: z.literal("gsc"),
    cutoff: stamp,
    cutoff_carried: z.boolean(),
    formal_24h_window: z.boolean(),
    layers: z.array(coverageLayersSchema),
    unknowable: z
      .object({
        truncated_slices: count,
        failed_slices: count,
        stale_slices: count,
        hours: count,
      })
      .strict(),
    site_admission_24h: z.enum(SITE_ADMISSIONS),
    site_admission_7d: z.enum(SITE_ADMISSIONS),
    vcheck_summary: vcheckSummarySchema,
    requests: count,
    quota_errors: count,
    status_codes: z.array(statusCode),
  })
  .strict()
  .refine((s) => !(s.cutoff_carried && s.formal_24h_window), {
    message: "沿用上一轮的 cutoff 时本轮没有正式 24 小时窗口",
  });

export const setSummarySchema = z.union([
  trendsSummarySchema,
  gscSummarySchema,
]);

/** Of the frozen inputs the page reads the channel and the market map's version only. */
const frozenInputsSchema = z
  .object({
    channel,
    market_map_version: z.string().regex(/^market-map-v[0-9A-Za-z]+$/),
  })
  .passthrough();

// ---- the five views (contract section 9) ---------------------------------------------------------------------------

export const obsSetSchema = z
  .object({
    set_id: setId,
    channel,
    mode: z.enum(OBS_MODES),
    status: z.enum(SET_STATUSES),
    published_at: stamp,
    as_of: stamp,
    source_catalog_batch_id: z.string().min(1).max(64),
    collector_version: version,
    rules_version: version,
    link_rules_version: version,
    alias_version: count,
    decisions_version: count,
    target_date: day.nullable(),
    window_end: stamp.nullable(),
    round_id: setId.nullable(),
    frozen_inputs: frozenInputsSchema,
    summary: setSummarySchema,
  })
  .strict()
  .refine(
    (set) =>
      set.channel === set.summary.channel &&
      set.channel === set.frozen_inputs.channel,
    { message: "channel、frozen_inputs.channel 与 summary.channel 一致" },
  );

/** A set named in a channel's head: which sets there are, before one is read whole. */
export const obsSetBriefSchema = z
  .object({ set_id: setId, mode: z.enum(OBS_MODES), published_at: stamp })
  .strict();

const labelHitSchema = z
  .object({
    label: z.enum(GSC_LABELS),
    formal: z.boolean(),
    condition: text,
    counts: z.record(z.string().min(1).max(40), count.nullable()),
  })
  .strict();

const qualityNoteSchema = z
  .object({
    tested: z.boolean(),
    rate_ratio: z.number().nullable(),
    dispersion: z.number().nullable(),
    z: z.number().nullable(),
    p_value: share.nullable(),
    bh_adjusted: share.nullable(),
    bh_q: share,
    bh_passed: z.boolean().nullable(),
    note: z.string().min(1).max(200),
  })
  .strict();

const pasteRowSchema = z
  .object({
    title: text,
    url: z
      .string()
      .max(2048)
      .regex(
        /^https:\/\/[a-z0-9.-]+\/[a-z]{2}(-[A-Za-z]{2,4})?\/drama\/[^/?#\s]+-[0-9a-f]{24}$/,
      ),
    impressions: count.nullable(),
    clicks: count.nullable(),
    window_kind: z.enum(["24h", "7d"]),
    top_query: z.string().min(1).max(200).nullable(),
    verified_on: day,
    note: z.string().max(1000),
  })
  .strict();

export const obsStateSchema = z
  .object({
    row_id: rowId,
    set_id: setId,
    channel,
    mode: z.enum(OBS_MODES),
    identity,
    title: text,
    language: z.string().min(1).max(40),
    theater: z.string().max(100),
    scope,
    window_kind: z.enum(["H", "D", "24h", "7d"]),
    state: z.enum([...TRENDS_ROW_STATES, ...GSC_ROW_STATES] as [
      string,
      ...string[],
    ]),
    confirmation: z.enum(CONFIRMATIONS).nullable(),
    admission: z.enum(ADMISSIONS).nullable(),
    labels: z.array(labelHitSchema),
    tier: z.enum(["A", "B"]).nullable(),
    correspondence: z.enum(["confirmed", "unconfirmed"]).nullable(),
    id_evidence: z.enum(ID_EVIDENCE_LEVELS).nullable(),
    ambiguity: z.enum(AMBIGUITIES).nullable(),
    flags: z.array(z.enum([...TRENDS_FLAGS, ...GSC_FLAGS])),
    carried_over: z.boolean(),
    stale: z.boolean(),
    window_end: stamp,
    latest_block_end: stamp.nullable(),
    metrics: z.record(z.string(), z.unknown()),
    quality_note: qualityNoteSchema.nullable(),
    paste_row: pasteRowSchema.nullable(),
    created_at: stamp,
  })
  .strict();

export const obsLinkSchema = z
  .object({
    id: rowId,
    link_rules_version: version,
    trends_set_id: setId,
    gsc_set_id: setId,
    mode: z.enum(OBS_MODES),
    identity,
    country: gscCountry.nullable(),
    trends_geo: trendGeo.nullable(),
    label: z.enum(LINK_LABELS),
    trends_row_id: rowId.nullable(),
    gsc_row_id: rowId.nullable(),
    trends_anchor: stamp,
    gsc_anchor: stamp,
    pair_gap_minutes: count,
    timely: z.boolean(),
    published_gap_minutes: count,
    stale: z.boolean(),
    created_at: stamp,
  })
  .strict();

export const obsDiscoverySchema = z
  .object({
    discovery_id: rowId,
    set_id: setId,
    mode: z.enum(OBS_MODES),
    geo: z.string().min(1).max(8),
    seed: z.string().min(1).max(200),
    property: z.enum(DISCOVERY_PROPERTIES),
    term: z.string().min(1),
    normalized_term: z.string().min(1),
    language: z.string().min(1).max(40).nullable(),
    match_status: z.enum(DISCOVERY_MATCHES),
    route: z.enum(DISCOVERY_ROUTES),
    matched_identity: identity.nullable(),
    breakout: z.boolean(),
    first_seen_at: stamp,
  })
  .strict();

export const obsRunSchema = z
  .object({
    channel,
    batch_id: z.string().min(1).max(64),
    mode: z.enum(OBS_MODES),
    target_date: day.nullable(),
    round_id: setId.nullable(),
    started_at: stamp,
    finished_at: stamp.nullable(),
    outcome: z.enum(RUN_OUTCOMES),
    requests: count,
    published_set_id: setId.nullable(),
    status_codes: z.array(statusCode),
  })
  .strict();

export const OBS_VIEW_SCHEMAS = {
  sets: obsSetSchema,
  states: obsStateSchema,
  links: obsLinkSchema,
  discoveries: obsDiscoverySchema,
  run_status: obsRunSchema,
} as const;
export type ObsViewName = keyof typeof OBS_VIEW_SCHEMAS;

export type ObsSet = z.infer<typeof obsSetSchema>;
export type ObsSetBrief = z.infer<typeof obsSetBriefSchema>;
export type ObsTrendsSummary = z.infer<typeof trendsSummarySchema>;
export type ObsGscSummary = z.infer<typeof gscSummarySchema>;
export type ObsCoverageLayers = z.infer<typeof coverageLayersSchema>;
export type ObsState = z.infer<typeof obsStateSchema>;
export type ObsLabelHit = z.infer<typeof labelHitSchema>;
export type ObsPasteRow = z.infer<typeof pasteRowSchema>;
export type ObsLink = z.infer<typeof obsLinkSchema>;
export type ObsDiscovery = z.infer<typeof obsDiscoverySchema>;
export type ObsRun = z.infer<typeof obsRunSchema>;
export type DiscoveryRoute = (typeof DISCOVERY_ROUTES)[number];
