/**
 * loadTrendsTable (simplified scope 2026-09-30, section 6 item 7): the trends tab asks the gateway's
 * GET /api/pick/obs/trends-table with the visitor's session. 401 is its own answer (the page goes to the login);
 * every other failure is "unavailable", and the body never reaches the answer.
 */
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";

import { loadTrendsTable } from "@/server/pick-board/trends-table";

import table from "../../core/pick/fixtures/backend-trends-table.json";

const session = { value: "stub-token" as string | undefined };

rs.mock("next/headers", () => ({
  cookies: rs.fn(async () => ({
    get: (name: string) =>
      name === "access_token" && session.value !== undefined
        ? { value: session.value }
        : undefined,
  })),
}));

type Answer = { status: number; body: unknown } | "network";

function stubGateway(answer: Answer) {
  const fetchSpy = rs.fn(async () => {
    if (answer === "network") throw new TypeError("fetch failed");
    return Response.json(answer.body, { status: answer.status });
  });
  rs.stubGlobal("fetch", fetchSpy);
  return fetchSpy;
}

let errorLog: ReturnType<typeof rs.spyOn>;

beforeEach(() => {
  session.value = "stub-token";
  errorLog = rs.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  errorLog.mockRestore();
  rs.unstubAllGlobals();
});

describe("loadTrendsTable", () => {
  it("reads the table with the session", async () => {
    const fetchSpy = stubGateway({ status: 200, body: table });
    const loaded = await loadTrendsTable();
    expect(loaded.kind).toBe("ok");
    expect(loaded.kind === "ok" && loaded.table.rows.length).toBe(
      table.rows.length,
    );
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(new URL(url).pathname).toBe("/api/pick/obs/trends-table");
    expect(new Headers(init.headers).get("Cookie")).toBe(
      "access_token=stub-token",
    );
  });

  it("maps 401 to unauthenticated and everything else to unavailable", async () => {
    for (const [code, kind] of [
      [401, "unauthenticated"],
      [403, "unavailable"],
      [500, "unavailable"],
      [503, "unavailable"],
    ] as const) {
      stubGateway({ status: code, body: { detail: "secret detail" } });
      const loaded = await loadTrendsTable();
      expect(loaded.kind, String(code)).toBe(kind);
      expect(JSON.stringify(loaded)).not.toContain("secret");
    }
  });

  it("is unauthenticated without a session, and asks nothing", async () => {
    session.value = undefined;
    const fetchSpy = stubGateway({ status: 200, body: table });
    expect((await loadTrendsTable()).kind).toBe("unauthenticated");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("is unavailable when the gateway is down or the answer is malformed", async () => {
    stubGateway("network");
    expect((await loadTrendsTable()).kind).toBe("unavailable");
    stubGateway({ status: 200, body: { ...table, rows: "not a list" } });
    expect((await loadTrendsTable()).kind).toBe("unavailable");
  });
});
