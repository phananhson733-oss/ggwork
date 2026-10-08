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
