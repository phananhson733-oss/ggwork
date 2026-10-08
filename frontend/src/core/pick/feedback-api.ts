import { fetch as fetchWithAuth } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import { feedbackStatusSchema, feedbackSyncSchema } from "./feedback-schema";

export class FeedbackAccessError extends Error {
  constructor(public status: number) {
    super(
      status === 401
        ? "请先登录以查看反馈"
        : status === 403
          ? "仅反馈来源所有者可查看或刷新"
          : `反馈读取失败（${status}）`,
    );
  }
}
async function request(path: string, signal?: AbortSignal, method = "GET") {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/pick/feedback/${path}`,
    { method, signal },
  );
  if (!response.ok) throw new FeedbackAccessError(response.status);
  try {
    return (await response.json()) as unknown;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    signal?.throwIfAborted();
    throw new Error("反馈响应格式无效，请核对状态后重试");
  }
}
export async function getFeedbackStatus(signal?: AbortSignal) {
  const parsed = feedbackStatusSchema.safeParse(
    await request("status", signal),
  );
  if (!parsed.success) throw new Error("反馈状态格式无效，请刷新或联系管理员");
  return parsed.data;
}
export async function refreshFeedback(signal?: AbortSignal) {
  const parsed = feedbackSyncSchema.safeParse(
    await request("sync", signal, "POST"),
  );
  if (!parsed.success)
    throw new Error("反馈刷新响应格式无效，请核对状态后重试");
  return parsed.data;
}
