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
  })
  .strict();

export const pickEvidenceSchema = z
  .object({
    citation_id: identifier,
    kind: z.string().min(1).max(100),
    source_ref: z.string().min(1).max(2048),
    observed_at: z.string().max(40).nullable(),
    value: z.union([z.string().max(4000), z.number().finite(), z.null()]),
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
