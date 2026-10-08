import { afterEach, expect, it, rs } from "@rstest/core";

rs.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => ({
      value: name === "access_token" ? "session" : "csrf",
    }),
  }),
}));
import { queryPickBoard } from "@/server/pick-board/common-query";

import fixture from "../../core/pick/fixtures/completion-v1.json";
afterEach(() => {
  rs.unstubAllGlobals();
});
it("forwards visitor session plus matching CSRF token for common-query POST", async () => {
  const fetcher = rs.fn(async (_url: string, _init: RequestInit) =>
    Response.json(fixture.response),
  );
  rs.stubGlobal("fetch", fetcher);
  const result = await queryPickBoard(fixture.query);
  expect(result.ok).toBe(true);
  const init = fetcher.mock.calls[0]![1];
  expect(new Headers(init.headers).get("Cookie")).toBe(
    "access_token=session; csrf_token=csrf",
  );
  expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf");
  expect(init.method).toBe("POST");
  expect(init.cache).toBe("no-store");
});
it("retains only a validated completion error code for safe period-specific rendering", async () => {
  rs.stubGlobal(
    "fetch",
    rs.fn(async () =>
      Response.json(
        {
          detail: {
            code: "period_missing",
            message: "internal detail must not reach SSR",
            retryable: false,
            current_version: null,
          },
        },
        { status: 422 },
      ),
    ),
  );
  const result = await queryPickBoard(fixture.query);
  expect(result).toEqual({ ok: false, status: 422, code: "period_missing" });
  expect(JSON.stringify(result)).not.toContain("internal detail");
});
it("shortens the downstream query budget to the existing earlier SSR deadline", async () => {
  const fetcher = rs.fn(async (_url: string, _init: RequestInit) =>
    Response.json(fixture.response),
  );
  rs.stubGlobal("fetch", fetcher);
  await queryPickBoard({ ...fixture.query, budget_ms: 10000 });
  const sent = JSON.parse(fetcher.mock.calls[0]![1].body as string) as {
    budget_ms: number;
  };
  expect(sent.budget_ms).toBeLessThanOrEqual(5000);
  expect(sent.budget_ms).toBeGreaterThan(0);
  const header = Number(
    new Headers(fetcher.mock.calls[0]![1].headers).get(
      "X-Pick-Query-Budget-Ms",
    ),
  );
  expect(header).toBeGreaterThan(0);
  expect(header).toBeLessThanOrEqual(sent.budget_ms);
});
