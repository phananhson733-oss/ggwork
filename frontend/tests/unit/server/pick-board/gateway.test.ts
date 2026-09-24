import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { z } from "zod";

import { AUTH_REQUEST_TIMEOUT_MS } from "@/core/auth/constants";
import { gatewayGet, getPickSync } from "@/server/pick-board/gateway";

const session = { value: "stub-token" as string | undefined };

rs.mock("next/headers", () => ({
  cookies: rs.fn(async () => ({
    get: (name: string) =>
      name === "access_token" && session.value !== undefined
        ? { value: session.value }
        : undefined,
  })),
}));

const itemSchema = z.object({ n: z.number() });
type FetchArgs = [string, RequestInit];

function stubFetch(
  answer: (url: string, init: RequestInit) => Promise<Response>,
) {
  const fetchSpy = rs.fn(answer);
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
  rs.useRealTimers();
});

describe("gatewayGet", () => {
  it("forwards the session cookie, uncached, to the internal gateway", async () => {
    const fetchSpy = stubFetch(async () => Response.json({ n: 1 }));
    await expect(gatewayGet("/api/pick/sync", itemSchema)).resolves.toEqual({
      ok: true,
      data: { n: 1 },
    });
    const [url, init] = fetchSpy.mock.calls[0] as FetchArgs;
    expect(url).toBe("http://127.0.0.1:8001/api/pick/sync");
    expect(init.cache).toBe("no-store");
    expect(new Headers(init.headers).get("Cookie")).toBe(
      "access_token=stub-token",
    );
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("answers 401 without a request when there is no session", async () => {
    session.value = undefined;
    const fetchSpy = stubFetch(async () => Response.json({ n: 1 }));
    await expect(gatewayGet("/api/pick/sync", itemSchema)).resolves.toEqual({
      ok: false,
      status: 401,
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("maps error statuses without reading the body into the result", async () => {
    for (const status of [401, 404, 409, 410, 503]) {
      stubFetch(async () =>
        Response.json({ detail: "secret detail" }, { status }),
      );
      const result = await gatewayGet("/api/pick/results/r1", itemSchema);
      expect(result).toEqual({ ok: false, status });
      expect(JSON.stringify(result)).not.toContain("secret");
    }
  });

  it("is unavailable on a timeout", async () => {
    rs.useFakeTimers();
    stubFetch(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    const pending = gatewayGet("/api/pick/sync", itemSchema);
    await rs.advanceTimersByTimeAsync(AUTH_REQUEST_TIMEOUT_MS);
    await expect(pending).resolves.toEqual({
      ok: false,
      status: "unavailable",
    });
  });

  it("is unavailable when the answer is not JSON or not of the shape", async () => {
    stubFetch(async () => new Response("<html>oops</html>", { status: 200 }));
    await expect(gatewayGet("/api/pick/sync", itemSchema)).resolves.toEqual({
      ok: false,
      status: "unavailable",
    });
    stubFetch(async () => Response.json({ n: "one", secret: "s3cr3t" }));
    await expect(gatewayGet("/api/pick/sync", itemSchema)).resolves.toEqual({
      ok: false,
      status: "unavailable",
    });
    expect(JSON.stringify(errorLog.mock.calls)).not.toContain("s3cr3t");
  });

  it("is unavailable when the gateway cannot be reached", async () => {
    stubFetch(async () => {
      throw new TypeError("fetch failed");
    });
    await expect(gatewayGet("/api/pick/sync", itemSchema)).resolves.toEqual({
      ok: false,
      status: "unavailable",
    });
  });

  it("forwards the query string", async () => {
    const fetchSpy = stubFetch(async () => Response.json({ n: 1 }));
    await gatewayGet("/api/pick/results?thread_id=t1&limit=5", itemSchema);
    expect((fetchSpy.mock.calls[0] as FetchArgs)[0]).toBe(
      "http://127.0.0.1:8001/api/pick/results?thread_id=t1&limit=5",
    );
  });

  it("reads only the pick API, however the path spells its way out", async () => {
    const fetchSpy = stubFetch(async () => Response.json({ n: 1 }));
    for (const path of [
      "/api/v1/auth/me",
      "//evil.example/api/pick/x",
      "/api/pick/../v1/x",
      "/api/pick/results/%2e%2e/%2e%2e/v1/auth/me",
      "/api/pick/%2E%2e/x",
      "/api/pick/.%2e/x",
      "/api/pick/..\\..\\v1/x",
    ]) {
      await expect(gatewayGet(path, itemSchema)).rejects.toThrow(
        "the gateway reader reads /api/pick/ only",
      );
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("fetches the path as resolved, never a spelling the fetch would resolve again", async () => {
    const fetchSpy = stubFetch(async () => Response.json({ n: 1 }));
    await gatewayGet("/api/pick/results/%2e%2e/sync#part", itemSchema);
    expect((fetchSpy.mock.calls[0] as FetchArgs)[0]).toBe(
      "http://127.0.0.1:8001/api/pick/sync",
    );
  });
});

describe("getPickSync", () => {
  it("drops a malformed mirror, logging its field paths only", async () => {
    const body = {
      configured: true,
      current: null,
      runs: [],
      mirror: { enabled: "s3cr3t" },
    };
    stubFetch(async () => Response.json(body));
    const result = await getPickSync();
    expect(result).toEqual({
      ok: true,
      data: { configured: true, current: null, runs: [] },
    });
    expect(errorLog).toHaveBeenCalledWith(
      "[pick-board] gateway mirror malformed",
      { fields: expect.arrayContaining(["mirror.enabled", "mirror.behind"]) },
    );
    expect(JSON.stringify(errorLog.mock.calls)).not.toContain("s3cr3t");
  });

  it("parses /sync with its mirror field", async () => {
    const body = {
      configured: true,
      current: null,
      runs: [],
      mirror: null,
    };
    const fetchSpy = stubFetch(async () => Response.json(body));
    await expect(getPickSync()).resolves.toEqual({ ok: true, data: body });
    expect((fetchSpy.mock.calls[0] as FetchArgs)[0]).toBe(
      "http://127.0.0.1:8001/api/pick/sync",
    );
  });
});
