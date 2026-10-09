import { beforeEach, expect, it, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
import { fetch as fetcher } from "@/core/api/fetcher";
import { createPlan, queryPick, previewPlan } from "@/core/pick/completion-api";

import fixture from "./fixtures/completion-v1.json";
const http = rs.mocked(fetcher);
beforeEach(() => {
  http.mockReset();
});
it("retains the exact create command after an unknown network outcome", async () => {
  http.mockRejectedValueOnce(new Error("offline"));
  http.mockResolvedValueOnce(Response.json(fixture.plan));
  await expect(createPlan(fixture.draft)).rejects.toThrow("offline");
  await expect(createPlan(fixture.draft)).resolves.toEqual(fixture.plan);
  expect(http.mock.calls[0]?.[1]?.body).toEqual(http.mock.calls[1]?.[1]?.body);
});
it("queries the authenticated public boundary and preserves version and counts", async () => {
  http.mockResolvedValueOnce(Response.json(fixture.response));
  const abort = new AbortController();
  const result = await queryPick(fixture.query, abort.signal);
  expect(result.pin).toEqual(fixture.response.pin);
  expect(result.counts).toEqual(fixture.response.counts);
  expect(http.mock.calls[0]?.[0]).toBe("/api/pick/query");
  expect(http.mock.calls[0]?.[1]?.signal).toBe(abort.signal);
});
it("returns structured conflicts without leaking malformed backend bodies", async () => {
  http.mockResolvedValueOnce(
    Response.json(
      {
        detail: {
          code: "version_conflict",
          message: "版本已变化",
          retryable: false,
          current_version: 4,
        },
      },
      { status: 409 },
    ),
  );
  await expect(createPlan(fixture.draft)).rejects.toMatchObject({
    code: "version_conflict",
    currentVersion: 4,
    retryable: false,
  });
  http.mockResolvedValueOnce(
    Response.json(
      { detail: { secret: "internal database password", message: "raw" } },
      { status: 500 },
    ),
  );
  await expect(createPlan(fixture.draft)).rejects.toThrow("操作未完成（500）");
});
it("rejects owner injection before writing and malformed success without calling it saved", async () => {
  await expect(
    createPlan({ ...fixture.draft, owner_id: "someone-else" }),
  ).rejects.toThrow("请检查");
  expect(http.mock.calls).toHaveLength(0);
  http.mockResolvedValueOnce(
    Response.json({ id: "fake-success", secret: "internal" }),
  );
  await expect(createPlan(fixture.draft)).rejects.toThrow(
    "服务返回的资料格式无法识别",
  );
});
it("rejects an exportable preview that omits a plan row or checks another revision", async () => {
  http.mockResolvedValueOnce(
    Response.json({ ...fixture.preview, exportable: true, checks: [] }),
  );
  await expect(
    previewPlan(fixture.plan.id, {
      request_id: "check",
      expected_version: fixture.plan.version,
    }),
  ).rejects.toThrow("核对结果不完整");
  http.mockResolvedValueOnce(
    Response.json({
      ...fixture.preview,
      plan: { ...fixture.plan, version: fixture.plan.version + 1 },
    }),
  );
  await expect(
    previewPlan(fixture.plan.id, {
      request_id: "check",
      expected_version: fixture.plan.version,
    }),
  ).rejects.toThrow("核对版本不一致");
});
