import { z } from "zod";

const count = z.number().int().nonnegative();
const time = z.string().datetime({ offset: true });
const quality = z.enum(["complete", "partial", "unknown"]);
const coverage = z
  .object({
    dramas: count,
    posts: count,
    measured_posts: count,
    unmatched_posts: count,
    missing_posts: count,
  })
  .refine(
    (v) =>
      Math.max(v.measured_posts, v.unmatched_posts, v.missing_posts) <= v.posts,
  );
export const feedbackItemSchema = z.object({
  key: z.string().min(1),
  evidence_kind: z.enum(["direct", "cohort", "unknown"]),
  metrics: z.record(
    z.string(),
    z.union([z.number().int(), z.string(), z.null()]),
  ),
  revenue: z.array(
    z
      .object({
        source_lane: z.enum(["cps_auto", "cps_manual", "post_rs"]),
        grain: z.enum(["drama", "post", "account", "platform", "unknown"]),
        currency: z.string().min(1),
        metric: z.enum([
          "order_amount",
          "refund",
          "commission",
          "advertising",
          "brokerage",
          "bonus",
          "orders",
        ]),
        amount: z
          .string()
          .regex(/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/)
          .nullable(),
        amount_basis: z.string().nullable(),
        records: count,
        missing_records: count,
      })
      .refine((v) => v.missing_records <= v.records),
  ),
  coverage,
  evidence_refs: z.array(
    z.object({
      table_id: z.string().min(1),
      record_id: z.string().min(1),
      field_ids: z.array(z.string()),
      metric_as_of: z.string().nullable(),
      grain: z.enum([
        "drama",
        "post",
        "account",
        "platform",
        "aggregate",
        "unknown",
      ]),
      attribution: z.enum(["confirmed", "ambiguous", "unmatched"]),
      source_lane: z.string(),
    }),
  ),
  warnings: z.array(z.string()),
});
export const feedbackReplySchema = z
  .object({
    status: z.enum([
      "ok",
      "disabled",
      "unavailable",
      "auth_required",
      "refresh_pending",
      "refresh_failed",
      "schema_changed",
      "unsupported_dimension",
    ]),
    contract_version: z.literal("feedback-v1"),
    notice: z.string(),
    feedback_version_id: z.string().min(1).nullable(),
    scan_started_at: time.nullable(),
    scan_completed_at: time.nullable(),
    last_verified_at: time.nullable(),
    freshness: z.enum(["fresh_scan", "historical", "stale", "unavailable"]),
    source_quality: quality,
    query_scope: z.record(z.string(), z.unknown()),
    items: z.array(feedbackItemSchema),
    coverage,
    warnings: z.array(z.string()),
    total_groups: count,
    has_more: z.boolean(),
  })
  .refine((v) =>
    v.status === "ok"
      ? Boolean(
          v.feedback_version_id &&
          v.scan_started_at &&
          v.scan_completed_at &&
          v.freshness !== "unavailable",
        )
      : v.items.length === 0 && v.freshness !== "fresh_scan",
  );
const run = z.object({
  id: z.string(),
  status: z.enum(["running", "success", "failed"]),
  trigger: z.string(),
  started_at: time,
  finished_at: time.nullable(),
  version_id: z.string().nullable(),
  error_code: z.string().nullable(),
});
export const feedbackStatusSchema = z.object({
  enabled: z.boolean(),
  configured: z.boolean(),
  current: z
    .object({
      id: z.string(),
      scan_started_at: time,
      scan_completed_at: time,
      source_quality: quality,
      tables: z.array(
        z.object({
          table_id: z.string(),
          name: z.string(),
          complete: z.boolean(),
          source_quality: quality,
        }),
      ),
    })
    .nullable(),
  running: run.nullable(),
  last_run: run.nullable(),
  last_verified_at: time.nullable(),
  lease_expired: z.boolean(),
});
export const feedbackSyncSchema = z.object({
  status: z.enum([
    "ok",
    "refresh_pending",
    "refresh_failed",
    "auth_required",
    "unavailable",
    "schema_changed",
  ]),
  run_id: z.string().nullable(),
  error_code: z.string().nullable(),
});
export type FeedbackReply = z.infer<typeof feedbackReplySchema>;
export type FeedbackItem = z.infer<typeof feedbackItemSchema>;
export type FeedbackStatus = z.infer<typeof feedbackStatusSchema>;
export const feedbackLabels = {
  ok: "反馈已读取",
  disabled: "反馈同步未启用",
  unavailable: "反馈暂不可用",
  auth_required: "需要连接飞书",
  refresh_pending: "反馈刷新中",
  refresh_failed: "反馈刷新失败",
  schema_changed: "飞书字段发生变化，需要核对",
  unsupported_dimension: "此分析维度暂无数据",
};
export const qualityLabels = {
  complete: "来源完整",
  partial: "来源部分覆盖",
  unknown: "来源完整性未知",
};

const feedbackErrorLabels: Record<string, string> = {
  auth_required: "需要连接飞书，请完成授权后重新刷新。",
  schema_changed: "飞书字段发生变化，需要核对字段配置后重新刷新。",
  source_changed: "飞书来源在读取期间发生变化，请待源表稳定后重新刷新。",
  incomplete: "源表读取不完整，请核对来源访问权限与表格状态后重新刷新。",
  capacity: "本次读取超过容量限制，请联系管理员核对数据规模与读取限制。",
  lease_expired: "同步任务已过期，请重新刷新。",
  cancelled: "同步任务已中断，请重新读取状态后按需刷新。",
  unavailable: "反馈来源暂不可用，请核对来源配置后重新刷新。",
  refresh_failed: "反馈刷新失败，请重新读取状态后按需刷新。",
  receipt_unavailable: "暂时无法确认刷新结果，请重新读取状态。",
  receipt_expired: "刷新结果凭据已过期，请重新读取状态。",
};
export function feedbackErrorText(code: string): string {
  return (
    feedbackErrorLabels[code] ?? "反馈刷新异常，请重新读取状态或联系管理员。"
  );
}
