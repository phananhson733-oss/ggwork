import { beforeEach, expect, it, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
import { fetch as fetcher } from "@/core/api/fetcher";
import { getFeedbackStatus, refreshFeedback } from "@/core/pick/feedback-api";
const mock = rs.mocked(fetcher);
beforeEach(() => {
  mock.mockReset();
});
it("rejects malformed data visibly", async () => {
  mock.mockResolvedValue(new Response('{"enabled":true}'));
  await expect(getFeedbackStatus()).rejects.toThrow("格式无效");
});
it("uses authenticated fetch with abort signal for POST", async () => {
  mock.mockResolvedValue(
    new Response(
      JSON.stringify({
        status: "refresh_pending",
        run_id: "r",
        error_code: null,
      }),
      { status: 202 },
    ),
  );
  const signal = new AbortController().signal;
  await refreshFeedback(signal);
  expect(mock).toHaveBeenCalledWith("/api/pick/feedback/sync", {
    method: "POST",
    signal,
  });
});
it("does not expose private upstream error text", async () => {
  mock.mockResolvedValue(
    new Response('{"detail":"private data"}', { status: 403 }),
  );
  await expect(getFeedbackStatus()).rejects.toThrow("仅反馈来源所有者");
  mock.mockResolvedValue(new Response("", { status: 401 }));
  await expect(getFeedbackStatus()).rejects.toThrow("请先登录");
});
it.each([
  ["GET", getFeedbackStatus, 200],
  ["POST", refreshFeedback, 202],
] as const)(
  "redacts malformed successful %s response bodies",
  async (_method, request, status) => {
    mock.mockResolvedValue(
      new Response("PRIVATE_FINANCE_MARKER invalid JSON", { status }),
    );
    const error = await request().catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toBe("反馈响应格式无效，请核对状态后重试");
    expect((error as Error).message).not.toContain("PRIVATE_FINANCE_MARKER");
  },
);
it("preserves body decoding cancellation", async () => {
  const error = new DOMException("The operation was aborted", "AbortError");
  const response = new Response();
  rs.spyOn(response, "json").mockRejectedValue(error);
  mock.mockResolvedValue(response);
  await expect(getFeedbackStatus()).rejects.toBe(error);
});
