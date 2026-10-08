import { type z } from "zod";

import { PickApiError, responseFor } from "./api";
import {
  commonQuerySchema,
  queryResponseSchema,
  planCreateSchema,
  planSchema,
  planUpdateSchema,
  planListSchema,
  planVersionCommandSchema,
  planPreviewSchema,
  planExportCommandSchema,
  planExportSchema,
  reviewQuerySchema,
  reviewPostsSchema,
  planLinkCommandSchema,
  planLinkSchema,
  planLinkListSchema,
  type PlanExport,
} from "./completion-types";

async function read<T>(
  path: string,
  schema: z.ZodType<T, z.ZodTypeDef, unknown>,
  init?: RequestInit,
): Promise<T> {
  const body: unknown = await (await responseFor(path, init)).json();
  const parsed = schema.safeParse(body);
  if (!parsed.success)
    throw new Error(
      "服务返回的资料格式无法识别，请重新读取；写入结果请使用原操作重试。",
    );
  return parsed.data;
}
function command(
  schema: z.ZodTypeAny,
  input: unknown,
  signal?: AbortSignal,
  method = "POST",
): RequestInit {
  const parsed = schema.safeParse(input);
  if (!parsed.success)
    throw new PickApiError(
      "请检查必填字段及输入格式。",
      422,
      "invalid_query",
      null,
      false,
    );
  return {
    method,
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(parsed.data),
  };
}
const id = encodeURIComponent;
export const queryPick = async (input: unknown, signal?: AbortSignal) =>
  read(
    "/query",
    queryResponseSchema,
    command(commonQuerySchema, input, signal),
  );
export const createPlan = async (input: unknown, signal?: AbortSignal) =>
  read("/plans", planSchema, command(planCreateSchema, input, signal));
export const getPlan = (planId: string, signal?: AbortSignal) =>
  read(`/plans/${id(planId)}`, planSchema, { signal });
export const listPlans = (offset = 0, signal?: AbortSignal) =>
  read(
    `/plans?offset=${Math.max(0, Math.floor(offset))}&limit=20`,
    planListSchema,
    { signal },
  );
export const updatePlan = async (
  planId: string,
  input: unknown,
  signal?: AbortSignal,
) =>
  read(
    `/plans/${id(planId)}`,
    planSchema,
    command(planUpdateSchema, input, signal, "PATCH"),
  );
export const previewPlan = async (
  planId: string,
  input: unknown,
  signal?: AbortSignal,
) =>
  read(
    `/plans/${id(planId)}/preview`,
    planPreviewSchema,
    command(planVersionCommandSchema, input, signal),
  );
export const exportPlan = async (
  planId: string,
  input: unknown,
  signal?: AbortSignal,
) =>
  read(
    `/plans/${id(planId)}/exports`,
    planExportSchema,
    command(planExportCommandSchema, input, signal),
  );
export const linkPlanPost = async (input: unknown, signal?: AbortSignal) =>
  read(
    "/feedback/plan-links",
    planLinkSchema,
    command(planLinkCommandSchema, input, signal),
  );
export const listPlanLinks = (planId: string, signal?: AbortSignal) =>
  read(`/feedback/plan-links?plan_id=${id(planId)}`, planLinkListSchema, {
    signal,
  });
export async function listReviewPosts(
  input: unknown = {},
  signal?: AbortSignal,
) {
  const parsed = reviewQuerySchema.safeParse(input);
  if (!parsed.success)
    throw new PickApiError(
      "请检查复盘筛选条件。",
      422,
      "invalid_query",
      null,
      false,
    );
  const params = new URLSearchParams();
  Object.entries(parsed.data).forEach(([key, value]) => {
    if (value !== null) params.set(key, String(value));
  });
  return read(`/feedback/posts?${params}`, reviewPostsSchema, { signal });
}
/** Download only the immutable receipt; never generate a replacement on download retry. */
export async function downloadPlanExport(
  receipt: PlanExport,
  signal?: AbortSignal,
) {
  const response = await responseFor(`/exports/${id(receipt.id)}`, { signal });
  if (!response.headers.get("content-type")?.startsWith("text/csv"))
    throw new Error("执行表下载格式不正确，请重试原文件。");
  const blob = await response.blob();
  signal?.throwIfAborted();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = receipt.filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
