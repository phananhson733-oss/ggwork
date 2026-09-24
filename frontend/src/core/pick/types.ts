import { z } from "zod";

const identifier = z.string().min(1).max(512);

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
    sort: z.enum(["evidence_date", "rank"]).optional(),
    exclude_posted: z.boolean().optional(),
    posted_account: z.string().max(200).nullable().optional(),
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
