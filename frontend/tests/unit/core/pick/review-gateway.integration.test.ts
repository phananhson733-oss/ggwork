/** Actual ordinary-session Gateway requests through completion-api and the real CSRF fetcher.
 * Only Node's absent browser cookie jar and backend origin are supplied; no API outputs are mocked.
 */
import { readFileSync } from "node:fs";

import { afterAll, beforeAll, describe, expect, it, rs } from "@rstest/core";

import {
  createPlan,
  linkPlanPost,
  listPlanLinks,
  listReviewPosts,
} from "@/core/pick/completion-api";

const path = process.env.PICK_REVIEW_GATEWAY_FIXTURE;
const fixture = path
  ? (JSON.parse(readFileSync(path, "utf8")) as {
      origin: string;
      cookies: Record<string, string>;
      plan: unknown;
    })
  : null;
rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => fixture?.origin ?? "",
}));
const originalFetch = globalThis.fetch;
const originalDocument = Object.getOwnPropertyDescriptor(
  globalThis,
  "document",
);
const requested: string[] = [];

describe.skipIf(!fixture)("actual private review requests", () => {
  beforeAll(() => {
    if (!fixture || new URL(fixture.origin).hostname !== "127.0.0.1")
      throw new Error("Local fixture required");
    Object.defineProperty(globalThis, "document", {
      configurable: true,
      value: { cookie: `csrf_token=${fixture.cookies.csrf_token}` },
    });
    globalThis.fetch = async (input, init) => {
      const url = String(input);
      if (new URL(url).origin !== fixture.origin)
        throw new Error("Unexpected request origin");
      requested.push(new URL(url).pathname);
      const headers = new Headers(init?.headers);
      headers.set(
        "Cookie",
        Object.entries(fixture.cookies)
          .map(([key, value]) => `${key}=${value}`)
          .join("; "),
      );
      return originalFetch(input, { ...init, headers });
    };
  });
  afterAll(() => {
    globalThis.fetch = originalFetch;
    if (originalDocument)
      Object.defineProperty(globalThis, "document", originalDocument);
    else Reflect.deleteProperty(globalThis, "document");
  });
  it("reads zero/unknown, creates explicit manual receipt, retries and refetches exact linked evidence", async () => {
    const before = await listReviewPosts({ limit: 2, offset: 1 });
    expect(before.total).toBe(3);
    expect(before.items.map((post) => post.views)).toEqual([null, 0]);
    const plan = await createPlan(fixture!.plan);
    const post = (await listReviewPosts({ limit: 1 })).items[0]!;
    const command = {
      request_id: "actual-frontend-link",
      plan_id: plan.id,
      row_id: plan.rows[0]!.row_id,
      expected_plan_version: plan.version,
      feedback_version_id: before.feedback_version_id,
      post_key: post.post_key,
      confirmation: "manual",
    };
    const receipt = await linkPlanPost(command);
    expect(receipt.method).toBe("manual");
    expect(receipt.status).toBe("confirmed");
    expect(await linkPlanPost(command)).toEqual(receipt);
    expect((await listPlanLinks(plan.id)).items).toEqual([receipt]);
    expect((await listReviewPosts({ limit: 1 })).items[0]!.link).toEqual(
      receipt,
    );
    expect(new Set(requested)).toEqual(
      new Set([
        "/api/pick/feedback/posts",
        "/api/pick/plans",
        "/api/pick/feedback/plan-links",
      ]),
    );
  });
});
