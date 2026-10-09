/** Additive v1 DTOs. See docs/pick-workbench/completion-contract.md. */
import { z } from "zod";

export const queryPinSchema = z
  .object({
    catalog_batch_id: z.string().trim().min(1).max(64),
    mirror_version: z.union([z.number().int().min(1), z.null()]),
    knowledge_batch_id: z.union([z.string().trim().min(1).max(64), z.null()]),
    rule_version: z.string().trim().min(1).max(128),
    feedback_version_id: z
      .union([z.string().trim().min(1).max(64), z.null()])
      .default(null),
  })
  .strict();

export const queryPeriodSchema = z
  .object({
    kind: z
      .union([z.literal("latest"), z.literal("daily"), z.literal("weekly")])
      .default("latest"),
    value: z
      .union([
        z.string().trim().regex(new RegExp("^\\d{4}-\\d{2}-\\d{2}$")),
        z.null(),
      ])
      .default(null),
  })
  .strict()
  .refine(
    (p) =>
      (p.kind === "latest") === (p.value === null) &&
      (p.value === null ||
        (!Number.isNaN(Date.parse(p.value)) &&
          new Date(p.value).toISOString().slice(0, 10) === p.value)),
    "历史期次必须指定日期",
  );

export const commonQuerySchema = z
  .object({
    domain: z.union([
      z.literal("candidates"),
      z.literal("catalog"),
      z.literal("rankings"),
      z.literal("posted"),
      z.literal("rules"),
    ]),
    scope: z.union([z.literal("candidate_pool"), z.literal("full_catalog")]),
    pin: z.union([queryPinSchema, z.null()]).default(null),
    query: z.union([z.string().trim().max(200), z.null()]).default(null),
    source: z.union([z.string().trim().max(100), z.null()]).default(null),
    source_id: z.union([z.string().trim().max(256), z.null()]).default(null),
    language: z.union([z.string().trim().max(40), z.null()]).default(null),
    theater: z.union([z.string().trim().max(100), z.null()]).default(null),
    channel: z
      .union([
        z.union([
          z.literal("youtube"),
          z.literal("tiktok"),
          z.literal("facebook"),
        ]),
        z.null(),
      ])
      .default(null),
    account: z.union([z.string().trim().max(200), z.null()]).default(null),
    published_from: z
      .union([
        z.string().trim().regex(new RegExp("^\\d{4}-\\d{2}-\\d{2}$")),
        z.null(),
      ])
      .default(null),
    published_to: z
      .union([
        z.string().trim().regex(new RegExp("^\\d{4}-\\d{2}-\\d{2}$")),
        z.null(),
      ])
      .default(null),
    exclude_posted: z.boolean().default(false),
    exclude_selected: z.boolean().default(false),
    confirmed_eligible_only: z.boolean().default(true),
    exclude_previous: z.boolean().default(false),
    hot_only: z.boolean().default(false),
    tags: z.array(z.string().trim().min(1).max(100)).max(20).default([]),
    signal_kind: z.union([z.string().trim().max(20), z.null()]).default(null),
    period: queryPeriodSchema.default({}),
    order: z
      .union([
        z.literal("evidence_date"),
        z.literal("evidence"),
        z.literal("listed"),
        z.literal("rank"),
        z.literal("title"),
        z.literal("published_at"),
      ])
      .default("evidence_date"),
    offset: z.number().int().min(0).default(0),
    limit: z.number().int().min(1).max(200).default(20),
    budget_ms: z.number().int().min(1).max(10000).default(10000),
    posted_filter: z
      .union([
        z.literal(""),
        z.literal("no"),
        z.literal("yes"),
        z.literal("pool"),
      ])
      .default(""),
    posted_state: z
      .union([
        z.literal(""),
        z.literal("pub"),
        z.literal("sched"),
        z.literal("none"),
        z.literal("nomatch"),
      ])
      .default(""),
    with_off: z.boolean().default(false),
    signal_only: z.boolean().default(false),
    wide: z.boolean().default(false),
    youtube_ok: z.boolean().default(false),
    dated_only: z.boolean().default(false),
    in_use_only: z.boolean().default(false),
    rank: z
      .union([
        z.union([
          z.literal("kd"),
          z.literal("kw"),
          z.literal("qc"),
          z.literal("qr"),
          z.literal("sm"),
          z.literal("smd"),
          z.literal("mg"),
          z.literal("fh"),
          z.literal("sh"),
          z.literal("gh"),
          z.literal("gn"),
          z.literal("ghh"),
          z.literal("dbn"),
          z.literal("rs_rr"),
          z.literal("rs_growth"),
          z.literal("rs_cand"),
          z.literal("rs_pc"),
          z.literal("rs_clk"),
          z.literal("rs_gsc"),
          z.literal("rs_bill"),
          z.literal("rs_ledger"),
        ]),
        z.null(),
      ])
      .default(null),
    grade: z
      .union([
        z.union([
          z.literal("SSS"),
          z.literal("SS"),
          z.literal("S"),
          z.literal("A"),
          z.literal("B"),
          z.literal("C"),
          z.literal("D"),
        ]),
        z.null(),
      ])
      .default(null),
    rs_sort: z
      .union([
        z.literal("rr"),
        z.literal("d1"),
        z.literal("d7"),
        z.literal("dp1"),
        z.literal("dp7"),
        z.literal("promoters"),
        z.literal("publish"),
        z.literal("bill"),
        z.literal("eff"),
        z.literal("gsc"),
        z.literal("clicks"),
      ])
      .default("rr"),
    rs_locale: z.union([z.string().trim().max(40), z.null()]).default(null),
    rs_bucket: z
      .union([
        z.union([
          z.literal("0-7"),
          z.literal("8-30"),
          z.literal("31-90"),
          z.literal("91-365"),
          z.literal("366+"),
        ]),
        z.null(),
      ])
      .default(null),
    legacy_week_label: z
      .union([z.string().trim().max(40), z.null()])
      .default(null),
    result_id: z
      .union([z.string().trim().min(1).max(64), z.null()])
      .default(null),
  })
  .strict();

export const queryCountsSchema = z
  .object({
    total: z.number().int().min(0),
    matched: z.number().int().min(0),
    returned: z.number().int().min(0),
    excluded: z.record(z.string().trim(), z.number().int().min(0)).default({}),
  })
  .strict();

export const signalInputSchema = z
  .object({
    kind: z.string().trim().min(1).max(100),
    source_ref: z.string().trim().min(1).max(2048),
    observed_at: z
      .union([z.string().trim(), z.string().trim(), z.null()])
      .default(null),
    value: z
      .union([
        z.string().trim().max(4000),
        z.number().int(),
        z.number().finite(),
        z.null(),
      ])
      .default(null),
    label: z.string().trim().max(200).default(""),
    rank: z
      .union([z.number().int().min(0).max(1000000), z.null()])
      .default(null),
    grade: z.string().trim().max(100).default(""),
    note: z.string().trim().max(1000).default(""),
  })
  .strict();

export const postedInputSchema = z
  .object({
    matched: z.boolean(),
    records: z.array(z.string().trim().min(1).max(64)).max(50).default([]),
    post_count: z.number().int().min(0).max(1000000),
    sched_count: z.number().int().min(0).max(1000000).default(0),
    last_post_on: z.union([z.string().trim(), z.null()]).default(null),
    accounts: z.array(z.string().trim().min(1).max(200)).max(100).default([]),
  })
  .strict();

export const dramaInputSchema = z
  .object({
    source: z.string().trim().min(1).max(100),
    source_id: z.string().trim().min(1).max(256),
    language: z.string().trim().min(1).max(40),
    title: z.string().trim().min(1).max(500),
    theater: z.string().trim().max(100).default(""),
    tags: z.array(z.string().trim().min(1).max(100)).max(20).default([]),
    listed_at: z.union([z.string().trim(), z.null()]).default(null),
    availability: z
      .union([z.literal("active"), z.literal("delisted"), z.literal("unknown")])
      .default("unknown"),
    signals: z.array(signalInputSchema).max(50).default([]),
    channel_rules: z
      .record(
        z.union([
          z.literal("youtube"),
          z.literal("tiktok"),
          z.literal("facebook"),
        ]),
        z.union([
          z.literal("allowed"),
          z.literal("denied"),
          z.literal("unknown"),
        ]),
      )
      .default({}),
    detail_url: z.union([z.string().trim().max(2048), z.null()]).default(null),
    posted: z.union([postedInputSchema, z.null()]).default(null),
  })
  .strict();

export const queryRowSchema = z
  .object({
    identity: z.string().trim().min(1).max(512),
    drama: dramaInputSchema,
    posted_status: z.union([
      z.literal("posted"),
      z.literal("not_posted"),
      z.literal("unknown"),
    ]),
    posted_scope_complete: z.boolean(),
    evidence_refs: z
      .array(z.string().trim().min(1).max(2048))
      .max(100)
      .default([]),
    exclusion_reasons: z.array(z.string().trim().max(200)).max(30).default([]),
  })
  .strict();

export const catalogRowsRowSchema = z
  .object({
    row_key: z.string(),
    platform: z.string(),
    source_table: z.string(),
    title: z.string(),
    title_cn: z.string(),
    lang: z.string(),
    kind: z.string(),
    origin: z.string(),
    tags: z.string(),
    listed_on: z.union([z.string(), z.null()]),
    episodes: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    pay_start: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    youtube: z.boolean(),
    merged_rows: z.number().int().min(-9007199254740991).max(9007199254740991),
    off_on: z.union([z.string(), z.null()]),
    reoff_note: z.string(),
    title_key: z.string(),
    in_site_ids: z.array(z.string()),
    legacy_only: z.boolean(),
    site_other: z.boolean(),
    has_signal: z.boolean(),
    latest_evidence_on: z.union([z.string(), z.null()]),
    imported_at: z.string(),
    has_pan: z.boolean(),
  })
  .strict();

export const signalPayloadSchema = z
  .object({
    d: z.unknown().optional(),
    w: z.unknown().optional(),
    weeks: z.unknown().optional(),
    best: z.unknown().optional(),
    days: z.unknown().optional(),
    first: z.unknown().optional(),
    h: z.unknown().optional(),
    qy: z.unknown().optional(),
    pid: z.unknown().optional(),
  })
  .strict();

export const catalogSignalsRowSchema = z
  .object({
    row_key: z.string(),
    kind: z.string(),
    ord: z.number().int().min(-9007199254740991).max(9007199254740991),
    evidence_on: z.union([z.string(), z.null()]),
    rank: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    grade: z.string(),
    note: z.string(),
    payload: signalPayloadSchema,
  })
  .strict();

export const postedPostSchema = z
  .object({
    d: z.unknown().optional(),
    acct: z.unknown().optional(),
    st: z.unknown().optional(),
    views: z.unknown().optional(),
    likes: z.unknown().optional(),
    favs: z.unknown().optional(),
    cmts: z.unknown().optional(),
    shares: z.unknown().optional(),
    md: z.unknown().optional(),
    url: z.unknown().optional(),
    note: z.unknown().optional(),
    how: z.unknown().optional(),
    pid: z.unknown().optional(),
  })
  .strict();

export const catalogPostedRowSchema = z
  .object({
    sd: z.string(),
    feishu_record: z.string(),
    title: z.string(),
    title_key: z.string(),
    lang: z.string(),
    platform: z.string(),
    life: z.string(),
    scheduled: z.boolean(),
    online_on: z.union([z.string(), z.null()]),
    why: z.string(),
    note: z.string(),
    archived: z.boolean(),
    post_count: z.number().int().min(-9007199254740991).max(9007199254740991),
    last_post_on: z.union([z.string(), z.null()]),
    views_total: z.number().int().min(-9007199254740991).max(9007199254740991),
    sources: z.array(z.string()),
    cats: z.array(z.string()),
    who: z.array(z.string()),
    accounts: z.array(z.string()),
    created_on: z.union([z.string(), z.null()]),
    updated_on: z.union([z.string(), z.null()]),
    first_post_on: z.union([z.string(), z.null()]),
    metric_at: z.union([z.string(), z.null()]),
    sched_count: z.number().int().min(-9007199254740991).max(9007199254740991),
    views_count: z.number().int().min(-9007199254740991).max(9007199254740991),
    posts: z.array(postedPostSchema),
    row_keys: z.array(z.string()),
    drama_ids: z.array(z.string()),
    imported_at: z.string(),
  })
  .strict();

export const catalogAccountsRowSchema = z
  .object({
    id: z.string(),
    name: z.string(),
    url: z.string(),
    grp: z.string(),
    form: z.string(),
    niche: z.string(),
    status: z.string(),
    fans: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    as_of: z.union([z.string(), z.null()]),
    imported_at: z.string(),
  })
  .strict();

export const rsRowsRowSchema = z
  .object({
    row_key: z.string(),
    platform: z.string(),
    source_table: z.string(),
    title: z.string(),
    title_cn: z.string(),
    lang: z.string(),
    kind: z.string(),
    origin: z.string(),
    tags: z.string(),
    listed_on: z.union([z.string(), z.null()]),
    episodes: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    pay_start: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    youtube: z.boolean(),
    merged_rows: z.number().int().min(-9007199254740991).max(9007199254740991),
    off_on: z.union([z.string(), z.null()]),
    reoff_note: z.string(),
    title_key: z.string(),
    in_site_ids: z.array(z.string()),
    legacy_only: z.boolean(),
    site_other: z.boolean(),
    has_signal: z.boolean(),
    latest_evidence_on: z.union([z.string(), z.null()]),
    has_pan: z.boolean(),
    rs_clk: z.boolean(),
    rs_bill: z.boolean(),
    rs_gsc: z.boolean(),
    rs_clk_on: z.union([z.string(), z.null()]),
    rs_bill_on: z.union([z.string(), z.null()]),
    rs_gsc_on: z.union([z.string(), z.null()]),
    drama_id: z.string(),
    locale: z.string(),
    slug: z.string(),
    publish_at: z.union([z.string(), z.null()]),
    chapter_count: z
      .number()
      .int()
      .min(-9007199254740991)
      .max(9007199254740991),
    pay_start_raw: z
      .number()
      .int()
      .min(-9007199254740991)
      .max(9007199254740991),
    rr: z.number().finite(),
    promoters_cnt: z
      .number()
      .int()
      .min(-9007199254740991)
      .max(9007199254740991),
    metrics_valid: z.union([z.boolean(), z.null()]),
    synced_at: z.union([z.string(), z.null()]),
    search_impressions: z
      .number()
      .int()
      .min(-9007199254740991)
      .max(9007199254740991),
    search_data_at: z.union([z.string(), z.null()]),
    detail_synced_at: z.union([z.string(), z.null()]),
    tag_list: z.array(z.string()),
    description: z.string(),
    baseline1_at: z.union([z.string(), z.null()]),
    baseline7_at: z.union([z.string(), z.null()]),
    baseline15_at: z.union([z.string(), z.null()]),
    rr1: z.union([z.number().finite(), z.null()]),
    p1: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    rr7: z.union([z.number().finite(), z.null()]),
    p7: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    rr15: z.union([z.number().finite(), z.null()]),
    p15: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    s1_rr: z.union([z.number().finite(), z.null()]),
    s1_p: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    s7_rr: z.union([z.number().finite(), z.null()]),
    s7_p: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
    clicks7: z.number().int().min(-9007199254740991).max(9007199254740991),
    last_click_on: z.union([z.string(), z.null()]),
    bill_orders: z.number().int().min(-9007199254740991).max(9007199254740991),
    last_bill_on: z.union([z.string(), z.null()]),
    bill_rank: z.union([
      z.number().int().min(-9007199254740991).max(9007199254740991),
      z.null(),
    ]),
  })
  .strict();

export const rsBillOrdersRowSchema = z
  .object({
    bill_date: z.string(),
    book_id: z.string(),
    promotion_type: z.string(),
    canonical_id: z.union([z.string(), z.null()]),
    book_title: z.string(),
    order_cnt: z.number().int().min(-9007199254740991).max(9007199254740991),
    source_rows: z.number().int().min(-9007199254740991).max(9007199254740991),
    same_day_clicks: z
      .number()
      .int()
      .min(-9007199254740991)
      .max(9007199254740991),
  })
  .strict();

export const platformRuleSchema = z
  .object({
    key: z.unknown(),
    name: z.unknown(),
    doc: z.unknown(),
    updated: z.unknown(),
    back: z.unknown(),
    report: z.unknown(),
    yt: z.unknown(),
    ytNote: z.unknown(),
    tag: z.unknown(),
    unban: z.unknown(),
    material: z.unknown(),
    signals: z.unknown(),
  })
  .strict();

export const youtubeLabelsSchema = z
  .object({
    ok: z.unknown().optional(),
    only: z.unknown().optional(),
    warn: z.unknown().optional(),
    no: z.unknown().optional(),
  })
  .strict();

export const glossaryItemSchema = z
  .object({
    t: z.unknown().optional(),
    a: z.unknown().optional(),
    ask: z.unknown().optional(),
    d: z.unknown().optional(),
    h: z.unknown().optional(),
  })
  .strict();

export const glossaryGroupSchema = z
  .object({
    g: z.unknown(),
    d: z.unknown(),
    items: z.array(glossaryItemSchema),
  })
  .strict();

export const rulesSchema = z
  .object({
    platformRules: z.record(z.string(), platformRuleSchema),
    inUse: z.array(z.unknown()),
    basisLabels: z.record(z.string(), z.unknown()),
    basisDateLabels: z.record(z.string(), z.unknown()),
    rsRankLabels: z.record(z.string(), z.unknown()),
    youtubeLabels: youtubeLabelsSchema,
    glossary: z.array(glossaryGroupSchema),
    ruleHints: z.record(z.string(), z.unknown()),
    langLoc: z.record(z.string(), z.unknown()),
    postedPoolUrl: z.unknown(),
    sortLabels: z.record(z.string(), z.unknown()),
  })
  .strict();

const mirrorScalar = z.union([z.string(), z.number(), z.boolean(), z.null()]);
const mirrorSourceSchema = z
  .object({
    source: mirrorScalar,
    status: mirrorScalar,
    attemptedAt: mirrorScalar,
    completedAt: mirrorScalar,
    details: z
      .object({
        startDate: mirrorScalar.optional(),
        endDate: mirrorScalar.optional(),
        timezone: mirrorScalar.optional(),
        dataState: mirrorScalar.optional(),
        rows: mirrorScalar.optional(),
        expectedRows: mirrorScalar.optional(),
        unresolvedPages: mirrorScalar.optional(),
        unmatchedQueries: mirrorScalar.optional(),
        pageRows: mirrorScalar.optional(),
        queryRows: mirrorScalar.optional(),
        truncated: mirrorScalar.optional(),
        partial: mirrorScalar.optional(),
        scope: mirrorScalar.optional(),
        ratio: mirrorScalar.optional(),
        billPeriod: mirrorScalar.optional(),
        termsFetchedAt: mirrorScalar.optional(),
      })
      .strict(),
  })
  .strict();

export const queryBoardDataSchema = z
  .object({
    catalog_rows: z.array(catalogRowsRowSchema).max(200).default([]),
    signals: z.array(catalogSignalsRowSchema).max(10000).default([]),
    posted: z.array(catalogPostedRowSchema).max(1000).default([]),
    accounts: z.array(catalogAccountsRowSchema).max(1000).default([]),
    rs_rows: z.array(rsRowsRowSchema).max(200).default([]),
    rs_ids: z
      .array(
        z
          .object({
            id: z.string(),
            canonical_id: z.string().nullable(),
            locale: z.string(),
            slug: z.string(),
            title: z.string(),
            chapter_count: z.number().int(),
            pay_start: z.number().int(),
            is_public_canonical: z.boolean(),
          })
          .strict(),
      )
      .max(10000)
      .default([]),
    bill_orders: z.array(rsBillOrdersRowSchema).max(200).default([]),
    rs_counts: z
      .object({
        all: mirrorScalar,
        cand: mirrorScalar,
        growthD1: mirrorScalar,
        growthD7: mirrorScalar,
        growthDp1: mirrorScalar,
        growthDp7: mirrorScalar,
        pc: mirrorScalar,
        clk: mirrorScalar,
        gsc: mirrorScalar,
        bill: mirrorScalar,
        ledger: mirrorScalar,
      })
      .strict()
      .nullable()
      .default(null),
    growth_baseline: z
      .record(
        z.string(),
        z
          .object({
            baselineDay: mirrorScalar,
            baselineSnapshot: mirrorScalar,
            earliestVerifiedOn: mirrorScalar,
          })
          .strict(),
      )
      .default({}),
    sources: z.record(z.string(), mirrorSourceSchema).default({}),
    posted_stats: z
      .object({
        total: mirrorScalar,
        pubCount: mirrorScalar,
        postsSum: mirrorScalar,
        viewsSum: mirrorScalar,
        metricAt: mirrorScalar,
        importedAt: mirrorScalar,
        accountCount: mirrorScalar,
      })
      .strict()
      .nullable()
      .default(null),
    rank_rows: z
      .array(
        z
          .object({
            row_key: z.string().min(1).max(512),
            signal: catalogSignalsRowSchema,
            day_rank: z.number().int().nullable().default(null),
            day_note: z.string().default(""),
          })
          .strict(),
      )
      .max(200)
      .default([]),
    bill_rows: z
      .array(
        z
          .object({
            bill_date: z.string(),
            book_id: z.string(),
            promotion_type: z.string(),
            canonical_id: z.string().nullable(),
            title: z.string(),
            locale: z.string().nullable(),
            order_cnt: z.number().int(),
            source_rows: z.number().int(),
            same_day_clicks: z.number().int(),
          })
          .strict(),
      )
      .max(200)
      .default([]),
    bill_totals: z
      .object({
        rows: z.number().int().min(0),
        merged_rows: z.number().int().min(0),
        orders: z.number().int().min(0),
        merged_with_clicks: z.number().int().min(0),
        rows_with_clicks: z.number().int().min(0),
      })
      .strict()
      .nullable()
      .default(null),
    effective_sort: z.string().nullable().default(null),
    legacy_total: z.number().int().min(0).nullable().default(null),
    rank_limit: z.number().int().min(0).nullable().default(null),
    rules: z.union([rulesSchema, z.null()]).default(null),
    row_keys: z.array(z.string().min(1).max(512)).max(200).default([]),
  })
  .strict();

export const queryFacetsSchema = z
  .object({
    language_order: z.array(z.string()).default([]),
    platforms: z.record(z.string(), z.number().int().min(0)).default({}),
    languages: z.record(z.string(), z.number().int().min(0)).default({}),
    bases: z.record(z.string(), z.number().int().min(0)).default({}),
    posted: z
      .record(
        z.union([z.literal("no"), z.literal("yes"), z.literal("pool")]),
        z.number().int().min(0),
      )
      .default({}),
    posted_states: z
      .record(
        z.union([
          z.literal("pub"),
          z.literal("sched"),
          z.literal("none"),
          z.literal("nomatch"),
        ]),
        z.number().int().min(0),
      )
      .default({}),
    ranks: z.record(z.string(), z.number().int().min(0)).default({}),
    grades: z
      .record(
        z.union([
          z.literal("SSS"),
          z.literal("SS"),
          z.literal("S"),
          z.literal("A"),
          z.literal("B"),
          z.literal("C"),
          z.literal("D"),
        ]),
        z.number().int().min(0),
      )
      .default({}),
  })
  .strict();

export const weekOptionSchema = z
  .object({
    week: z.string().max(40),
    start: z.string().regex(new RegExp("^\\d{4}-\\d{2}-\\d{2}$")),
  })
  .strict();

export const queryPeriodOptionsSchema = z
  .object({
    days: z
      .array(z.string().regex(new RegExp("^\\d{4}-\\d{2}-\\d{2}$")))
      .default([]),
    weeks: z.array(weekOptionSchema).default([]),
    resolution: z.union([
      z.literal("latest"),
      z.literal("exact"),
      z.literal("label"),
      z.literal("ambiguous"),
      z.literal("missing"),
    ]),
  })
  .strict();

export const queryResponseSchema = z
  .object({
    contract_version: z
      .literal("pick-completion-v1")
      .default("pick-completion-v1"),
    request: commonQuerySchema,
    pin: queryPinSchema,
    actual_period: z.union([queryPeriodSchema, z.null()]),
    order_version: z.string().trim().min(1).max(128),
    counts: queryCountsSchema,
    truncated: z.boolean(),
    next_offset: z.union([z.number().int().min(0), z.null()]),
    rows: z.array(queryRowSchema).max(200),
    board: z.union([queryBoardDataSchema, z.null()]).default(null),
    facets: z.union([queryFacetsSchema, z.null()]).default(null),
    period_options: z.union([queryPeriodOptionsSchema, z.null()]).default(null),
    source_as_of: z.union([z.string().trim().max(40), z.null()]),
    mirror_synced_at: z.union([z.string().trim().max(40), z.null()]),
    warnings: z.array(z.string().trim()).max(50).default([]),
  })
  .strict();

export const completionErrorSchema = z
  .object({
    code: z.union([
      z.literal("invalid_query"),
      z.literal("unauthorized"),
      z.literal("not_found"),
      z.literal("version_gone"),
      z.literal("period_missing"),
      z.literal("source_unavailable"),
      z.literal("query_timeout"),
      z.literal("version_conflict"),
      z.literal("export_blocked"),
      z.literal("link_conflict"),
    ]),
    message: z.string().trim().min(1).max(1000),
    retryable: z.boolean(),
    current_version: z.union([z.number().int().min(1), z.null()]).default(null),
  })
  .strict();

export const resultReferenceSchema = z
  .object({
    result_id: z.string().trim().min(1).max(64),
    item_ids: z.array(z.string().trim().min(1).max(64)).min(1).max(20),
  })
  .strict();

export const checkedFactSchema = z
  .object({
    claim: z.string().trim().min(1).max(4000),
    status: z.union([
      z.literal("confirmed"),
      z.literal("unknown"),
      z.literal("contradicted"),
    ]),
    evidence_refs: z.array(z.string().trim().min(1).max(2048)).max(100),
    reason: z.string().trim().max(1000),
  })
  .strict()
  .refine(
    (f) => f.status !== "confirmed" || f.evidence_refs.length > 0,
    "已确认事实必须有证据引用",
  );

export const checkedPublicationSchema = z
  .object({
    thread_id: z.string().trim().min(1).max(64),
    run_id: z.string().trim().min(1).max(64),
    message_id: z.string().trim().min(1).max(64),
    status: z.union([
      z.literal("confirmed"),
      z.literal("partial"),
      z.literal("incomplete"),
    ]),
    content: z.string().trim().max(100000),
    facts: z.array(checkedFactSchema).max(500),
    references: z.array(resultReferenceSchema).max(2),
    checker_version: z.string().trim().min(1).max(128),
    correction_count: z.number().int().min(0).max(1),
    checked_at: z.string().trim().min(1).max(40),
  })
  .strict()
  .refine(
    (p) =>
      p.status !== "confirmed" ||
      p.facts.every((f) => f.status === "confirmed"),
    "存在未确认事实",
  );

export const planRowInputSchema = z
  .object({
    row_id: z.string().trim().min(1).max(64),
    identity: z.string().trim().min(1).max(512),
    source_result_id: z.string().trim().min(1).max(64),
    source_item_id: z.string().trim().min(1).max(64),
    selection_id: z
      .union([z.string().trim().min(1).max(64), z.null()])
      .default(null),
    account: z.union([z.string().trim().max(200), z.null()]).default(null),
    channel: z
      .union([
        z.union([
          z.literal("youtube"),
          z.literal("tiktok"),
          z.literal("facebook"),
        ]),
        z.null(),
      ])
      .default(null),
    local_time: z
      .union([
        z
          .string()
          .trim()
          .regex(new RegExp("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}$")),
        z.null(),
      ])
      .default(null),
    fold: z.union([z.number().int().min(0).max(1), z.null()]).default(null),
    copy_text: z.string().trim().max(4000).default(""),
    note: z.string().trim().max(2000).default(""),
  })
  .strict();

export const planCreateSchema = z
  .object({
    request_id: z.string().trim().min(1).max(128),
    title: z.string().trim().min(1).max(200),
    timezone: z.string().trim().min(1).max(100),
    rows: z.array(planRowInputSchema).max(100),
  })
  .strict()
  .refine(
    (p) => new Set(p.rows.map((r) => r.row_id)).size === p.rows.length,
    "计划行标识不能重复",
  );

export const planUpdateSchema = z
  .object({
    request_id: z.string().trim().min(1).max(128),
    title: z.string().trim().min(1).max(200),
    timezone: z.string().trim().min(1).max(100),
    rows: z.array(planRowInputSchema).max(100),
    expected_version: z.number().int().min(1),
    timezone_change: z
      .union([
        z.union([z.literal("keep_local_time"), z.literal("keep_instant")]),
        z.null(),
      ])
      .default(null),
  })
  .strict()
  .refine(
    (p) => new Set(p.rows.map((r) => r.row_id)).size === p.rows.length,
    "计划行标识不能重复",
  );

export const planRowSchema = z
  .object({
    row_id: z.string().trim().min(1).max(64),
    identity: z.string().trim().min(1).max(512),
    source_result_id: z.string().trim().min(1).max(64),
    source_item_id: z.string().trim().min(1).max(64),
    selection_id: z
      .union([z.string().trim().min(1).max(64), z.null()])
      .default(null),
    account: z.union([z.string().trim().max(200), z.null()]).default(null),
    channel: z
      .union([
        z.union([
          z.literal("youtube"),
          z.literal("tiktok"),
          z.literal("facebook"),
        ]),
        z.null(),
      ])
      .default(null),
    local_time: z
      .union([
        z
          .string()
          .trim()
          .regex(new RegExp("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}$")),
        z.null(),
      ])
      .default(null),
    fold: z.union([z.number().int().min(0).max(1), z.null()]).default(null),
    copy_text: z.string().trim().max(4000).default(""),
    note: z.string().trim().max(2000).default(""),
    title: z.string().trim().min(1).max(500),
    theater: z.string().trim().max(100),
    language: z.string().trim().min(1).max(40),
    source_pin: queryPinSchema,
    scheduled_at: z.union([z.string().trim().max(40), z.null()]),
  })
  .strict();

export const planSchema = z
  .object({
    id: z.string().trim().min(1).max(64),
    version: z.number().int().min(1),
    title: z.string().trim().min(1).max(200),
    timezone: z.string().trim().min(1).max(100),
    rows: z.array(planRowSchema).max(100),
    created_at: z.string().trim().min(1).max(40),
    updated_at: z.string().trim().min(1).max(40),
  })
  .strict();

export const planVersionCommandSchema = z
  .object({
    request_id: z.string().trim().min(1).max(128),
    expected_version: z.number().int().min(1),
  })
  .strict();

export const planCheckSchema = z
  .object({
    row_id: z.string().trim().min(1).max(64),
    status: z.union([z.literal("ready"), z.literal("blocked")]),
    blockers: z.array(z.string().trim()).max(30),
    warnings: z.array(z.string().trim()).max(30),
    current_pin: z.union([queryPinSchema, z.null()]),
  })
  .strict();

export const planPreviewSchema = z
  .object({
    plan: planSchema,
    preview_id: z.string().trim().min(1).max(64),
    checked_at: z.string().trim().min(1).max(40),
    exportable: z.boolean(),
    checks: z.array(planCheckSchema).max(100),
  })
  .strict();

export const planExportCommandSchema = z
  .object({
    request_id: z.string().trim().min(1).max(128),
    expected_version: z.number().int().min(1),
    preview_id: z.string().trim().min(1).max(64),
  })
  .strict();

export const planExportSchema = z
  .object({
    id: z.string().trim().min(1).max(64),
    plan_id: z.string().trim().min(1).max(64),
    plan_version: z.number().int().min(1),
    preview_id: z.string().trim().min(1).max(64),
    created_at: z.string().trim().min(1).max(40),
    filename: z.string().trim().min(1).max(200),
    row_count: z.number().int().min(0),
    sha256: z.string().trim().regex(new RegExp("^[0-9a-f]{64}$")),
  })
  .strict();

export const planLinkCommandSchema = z
  .object({
    request_id: z.string().trim().min(1).max(128),
    plan_id: z.string().trim().min(1).max(64),
    row_id: z.string().trim().min(1).max(64),
    expected_plan_version: z.number().int().min(1),
    feedback_version_id: z.string().trim().min(1).max(64),
    post_key: z.string().trim().min(1).max(512),
    confirmation: z.literal("manual"),
  })
  .strict();

export const planLinkSchema = z
  .object({
    id: z.string().trim().min(1).max(64),
    plan_id: z.string().trim().min(1).max(64),
    row_id: z.string().trim().min(1).max(64),
    plan_version: z.number().int().min(1),
    feedback_version_id: z.string().trim().min(1).max(64),
    post_key: z.string().trim().min(1).max(512),
    method: z.union([z.literal("manual"), z.literal("verified_external_id")]),
    status: z.union([
      z.literal("confirmed"),
      z.literal("needs_review"),
      z.literal("conflict"),
    ]),
    evidence_refs: z.array(z.string().trim().min(1).max(2048)).min(1).max(100),
    created_at: z.string().trim().min(1).max(40),
  })
  .strict();

export const planListSchema = z
  .object({
    items: z.array(planSchema).max(100),
    total: z.number().int().min(0),
    next_offset: z.union([z.number().int().min(0), z.null()]),
  })
  .strict();

export const planLinkListSchema = z
  .object({
    items: z.array(planLinkSchema),
  })
  .strict();

export const reviewQuerySchema = z
  .object({
    feedback_version_id: z
      .union([z.string().trim().min(1).max(64), z.null()])
      .default(null),
    account_id: z
      .union([z.string().trim().min(1).max(128), z.null()])
      .default(null),
    channel: z
      .union([
        z.union([
          z.literal("youtube"),
          z.literal("tiktok"),
          z.literal("facebook"),
        ]),
        z.null(),
      ])
      .default(null),
    language: z.union([z.string().trim().max(40), z.null()]).default(null),
    published_from: z
      .union([
        z.string().trim().regex(new RegExp("^\\d{4}-\\d{2}-\\d{2}$")),
        z.null(),
      ])
      .default(null),
    published_to: z
      .union([
        z.string().trim().regex(new RegExp("^\\d{4}-\\d{2}-\\d{2}$")),
        z.null(),
      ])
      .default(null),
    offset: z.number().int().min(0).default(0),
    limit: z.number().int().min(1).max(50).default(20),
  })
  .strict();

export const revenueObservationSchema = z
  .object({
    record_id: z.string().trim().min(1).max(128),
    source_lane: z.union([
      z.literal("cps_auto"),
      z.literal("cps_manual"),
      z.literal("post_rs"),
    ]),
    grain: z.union([
      z.literal("drama"),
      z.literal("post"),
      z.literal("account"),
      z.literal("platform"),
      z.literal("unknown"),
    ]),
    currency: z.string().trim().min(1).max(12),
    metric: z.union([
      z.literal("order_amount"),
      z.literal("refund"),
      z.literal("commission"),
      z.literal("advertising"),
      z.literal("brokerage"),
      z.literal("bonus"),
      z.literal("orders"),
    ]),
    // Python Decimal JSON uses strings, including scientific notation.
    amount: z
      .string()
      .regex(/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/)
      .nullable()
      .default(null),
    drama_record_id: z
      .union([z.string().trim().min(1).max(128), z.null()])
      .default(null),
    metric_on: z.union([z.string().trim(), z.null()]).default(null),
    amount_basis: z.union([z.string().trim().max(100), z.null()]).default(null),
    attribution: z
      .union([
        z.literal("confirmed"),
        z.literal("ambiguous"),
        z.literal("unmatched"),
      ])
      .default("unmatched"),
  })
  .strict();

export const reviewPostSchema = z
  .object({
    post_key: z.string().trim().min(1).max(512),
    account_id: z.union([z.string().trim().min(1).max(128), z.null()]),
    channel: z.union([
      z.union([
        z.literal("youtube"),
        z.literal("tiktok"),
        z.literal("facebook"),
      ]),
      z.null(),
    ]),
    identity: z.union([z.string().trim().min(1).max(512), z.null()]),
    title: z.string().trim().max(500),
    language: z.union([z.string().trim().max(40), z.null()]),
    published_at: z.union([z.string().trim().max(40), z.null()]),
    observed_at: z.union([z.string().trim().max(40), z.null()]),
    observation_days: z.union([z.number().int().min(0), z.null()]),
    requested_observation_days: z.number().int().min(1),
    window_complete: z.boolean(),
    views: z.union([z.number().int().min(0), z.null()]),
    likes: z.union([z.number().int().min(0), z.null()]),
    comments: z.union([z.number().int().min(0), z.null()]),
    revenue: z.array(revenueObservationSchema).default([]),
    link: z.union([planLinkSchema, z.null()]),
    evidence_refs: z.array(z.string().trim().min(1).max(2048)).min(1).max(100),
  })
  .strict();

export const reviewPostsSchema = z
  .object({
    status: z.union([
      z.literal("ok"),
      z.literal("disabled"),
      z.literal("unavailable"),
      z.literal("auth_required"),
    ]),
    feedback_version_id: z.union([z.string().trim().min(1).max(64), z.null()]),
    scan_completed_at: z.union([z.string().trim().max(40), z.null()]),
    items: z.array(reviewPostSchema).max(50),
    total: z.union([z.number().int().min(0), z.null()]),
    next_offset: z.union([z.number().int().min(0), z.null()]),
    warnings: z.array(z.string().trim()).default([]),
  })
  .strict();

export type QueryPin = z.infer<typeof queryPinSchema>;
export type QueryPeriod = z.infer<typeof queryPeriodSchema>;
export type CommonQuery = z.infer<typeof commonQuerySchema>;
export type QueryCounts = z.infer<typeof queryCountsSchema>;
export type QueryRow = z.infer<typeof queryRowSchema>;
export type QueryResponse = z.infer<typeof queryResponseSchema>;
export type CompletionError = z.infer<typeof completionErrorSchema>;
export type ResultReference = z.infer<typeof resultReferenceSchema>;
export type CheckedFact = z.infer<typeof checkedFactSchema>;
export type CheckedPublication = z.infer<typeof checkedPublicationSchema>;
export type PlanRowInput = z.infer<typeof planRowInputSchema>;
export type PlanCreate = z.infer<typeof planCreateSchema>;
export type PlanUpdate = z.infer<typeof planUpdateSchema>;
export type PlanRow = z.infer<typeof planRowSchema>;
export type Plan = z.infer<typeof planSchema>;
export type PlanVersionCommand = z.infer<typeof planVersionCommandSchema>;
export type PlanCheck = z.infer<typeof planCheckSchema>;
export type PlanPreview = z.infer<typeof planPreviewSchema>;
export type PlanExportCommand = z.infer<typeof planExportCommandSchema>;
export type PlanExport = z.infer<typeof planExportSchema>;
export type PlanLinkCommand = z.infer<typeof planLinkCommandSchema>;
export type PlanLink = z.infer<typeof planLinkSchema>;
export type PlanList = z.infer<typeof planListSchema>;
export type PlanLinkList = z.infer<typeof planLinkListSchema>;
export type ReviewQuery = z.infer<typeof reviewQuerySchema>;
export type ReviewPost = z.infer<typeof reviewPostSchema>;
export type ReviewPosts = z.infer<typeof reviewPostsSchema>;

/** Host-written additional_kwargs.pick_completion; never accepted from client input. */
export const checkedMessageMetadataSchema = z
  .object({
    status: z.enum(["confirmed", "partial", "incomplete"]),
    checker_version: z.string().trim().min(1).max(128),
    checked_at: z.string().trim().min(1).max(40),
    correction_count: z.number().int().min(0).max(1),
  })
  .strict();
export const pickProcessingEventSchema = z
  .object({
    thread_id: z.string().trim().min(1).max(64),
    run_id: z.string().trim().min(1).max(64),
    stage: z.enum(["querying", "checking", "correcting", "finalizing"]),
  })
  .strict();
export type CheckedMessageMetadata = z.infer<
  typeof checkedMessageMetadataSchema
>;
export type PickProcessingEvent = z.infer<typeof pickProcessingEventSchema>;

// Bounded model/operator result. HTTP queryResponseSchema above remains complete.
const modelReference = z.string().min(1).max(1024);
const modelSignalSchema = z
  .object({
    kind: z.string().max(100),
    observed_at: z.string().max(40).nullable(),
    value: z.union([z.number(), z.string().max(64), z.null()]),
    rank: z.number().int().nullable(),
    grade: z.string().max(100),
    reference: modelReference,
  })
  .strict();
const modelRankMetricSchema = z
  .object({
    key: z.enum([
      "rr",
      "d1",
      "d7",
      "dp1",
      "dp7",
      "promoters",
      "publish",
      "bill",
      "eff",
      "gsc",
      "clicks",
    ]),
    value: z.string().max(64).nullable(),
    current: z.string().max(64).nullable(),
    baseline: z.string().max(64).nullable(),
    denominator: z.number().int().nullable(),
    comparison_days: z.union([z.literal(1), z.literal(7), z.null()]),
    unit: z.enum([
      "source_cents",
      "people",
      "source_cents_per_promoter",
      "timestamp",
      "rank",
      "impressions",
      "clicks",
    ]),
    scope: z.enum([
      "upstream_platform_rolling_30d",
      "change_in_promoters",
      "change_in_platform_rolling_30d",
      "platform_metric_per_promoter",
      "publication_date",
      "upstream_promoters",
      "source_bill_rank",
      "site_search_impressions",
      "site_outbound_7d",
    ]),
    observed_at: z.string().max(40).nullable(),
    baseline_at: z.string().max(40).nullable(),
    verified: z.boolean().nullable(),
    reference: modelReference,
  })
  .strict();
const modelDramaRowSchema = z
  .object({
    kind: z.literal("drama"),
    episodes: z.number().int().min(0).nullable().optional(),
    episodes_source_ref: z.string().max(1024).nullable().optional(),
    rank_metric: modelRankMetricSchema.nullable().default(null),
    identity: z.string().min(1).max(512),
    reference: modelReference,
    source: z.string().max(100),
    source_id: z.string().max(256),
    title: z.string().max(500),
    language: z.string().max(40),
    theater: z.string().max(100),
    availability: z.enum(["active", "delisted", "unknown"]),
    channel_rules: z.record(
      z.enum(["youtube", "tiktok", "facebook"]),
      z.enum(["allowed", "denied", "unknown"]),
    ),
    posted_status: z.enum(["posted", "not_posted", "unknown"]),
    posted_scope_complete: z.boolean(),
    signals: z.array(modelSignalSchema).max(5),
    signal_count: z.number().int().min(0),
    signals_truncated: z.boolean(),
  })
  .strict();
const modelPostedRowSchema = z
  .object({
    kind: z.literal("posted"),
    identity: z.string().min(1).max(512),
    reference: modelReference,
    sd: z.string().min(1).max(512),
    title: z.string().max(500),
    title_truncated: z.boolean(),
    archived: z.boolean(),
    post_count: z.number().int(),
    sched_count: z.number().int(),
    last_post_on: z.string().max(40).nullable(),
    accounts: z.array(z.string().max(200)).max(5),
    account_count: z.number().int().min(0),
    accounts_truncated: z.boolean(),
  })
  .strict();
const modelBillRowSchema = z
  .object({
    kind: z.literal("bill"),
    identity: z.string().min(1).max(512),
    reference: modelReference,
    book_id: z.string().max(256),
    canonical_id: z.string().max(256).nullable(),
    bill_date: z.string().max(40),
    title: z.string().max(500),
    title_truncated: z.boolean(),
    promotion_type: z.string().max(100),
    order_cnt: z.number().int(),
  })
  .strict();
const modelRuleRowSchema = z
  .object({
    kind: z.literal("rule"),
    identity: z.string().min(1).max(512),
    reference: modelReference,
    platform: z.string().max(100),
    name: z.string().max(500),
    name_truncated: z.boolean(),
    youtube_rule: z.enum(["ok", "only", "warn", "no"]).nullable(),
  })
  .strict();
const modelCatalogRecordSchema = z
  .object({
    kind: z.literal("catalog_record"),
    rank_metric: modelRankMetricSchema.nullable().default(null),
    identity: z.null(),
    row_key: z.string().min(1).max(512),
    reference: modelReference,
    source_table: z.enum(["catalog_rows", "rs_rows"]),
    source_ref: z.string().min(1).max(1024),
    title: z.string().max(500),
    title_truncated: z.boolean(),
    language: z.string().max(40),
    theater: z.string().max(100),
    listed_on: z.string().max(40).nullable(),
    availability: z.enum(["unknown", "delisted"]),
    eligibility: z.literal("unknown"),
  })
  .strict();
export const modelQueryRowSchema = z.discriminatedUnion("kind", [
  modelDramaRowSchema,
  modelPostedRowSchema,
  modelBillRowSchema,
  modelRuleRowSchema,
  modelCatalogRecordSchema,
]);
export const queryModelProjectionSchema = z
  .object({
    projection_version: z.literal("pick-query-model-v1"),
    effective_sort: z.string().max(40).nullable().default(null),
    rank_limit: z.number().int().min(0).nullable().default(null),
    request: commonQuerySchema,
    pin: queryPinSchema,
    actual_period: queryPeriodSchema.nullable(),
    period_resolution: z
      .enum(["latest", "exact", "label", "ambiguous", "missing"])
      .nullable(),
    source_as_of: z.string().max(40).nullable(),
    mirror_synced_at: z.string().max(40).nullable(),
    order_version: z.string().min(1).max(128),
    counts: queryCountsSchema,
    query_next_offset: z.number().int().min(0).nullable(),
    query_truncated: z.boolean(),
    projection: z
      .object({
        page_limit: z.literal(20),
        byte_limit: z.literal(48000),
        requested_limit: z.number().int().min(1).max(200),
        shown: z.number().int().min(0).max(20),
        available_count: z.number().int().min(0),
        omitted_rows: z.number().int().min(0),
        signals_omitted: z.number().int().min(0),
        next_offset: z.number().int().min(0).nullable(),
        truncated: z.boolean(),
      })
      .strict(),
    rows: z.array(modelQueryRowSchema).max(20),
  })
  .strict();
export type QueryModelProjection = z.infer<typeof queryModelProjectionSchema>;
export type ModelQueryRow = z.infer<typeof modelQueryRowSchema>;
