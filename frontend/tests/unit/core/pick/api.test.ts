import { beforeEach, describe, expect, it, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  getPickSyncStatus,
  listPickResults,
  savePickSelection,
} from "@/core/pick/api";

const mockedFetch = rs.mocked(fetcher);
beforeEach(() => {
  mockedFetch.mockReset();
});

describe("pick API", () => {
  it("keeps the same command ID on a network retry", async () => {
    const command = {
      request_id: "same-request",
      result_id: "r1",
      item_ids: ["i1"],
      note: "",
    };
    mockedFetch.mockRejectedValueOnce(new Error("offline"));
    mockedFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ request_id: "same-request", saved: [] })),
    );
    await expect(savePickSelection(command)).rejects.toThrow("offline");
    await savePickSelection(command);
    expect(mockedFetch.mock.calls[0]?.[1]?.body).toBe(
      mockedFetch.mock.calls[1]?.[1]?.body,
    );
  });
  it("does not turn a failed query into an empty successful result", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "选剧服务尚未就绪" }), {
        status: 503,
      }),
    );
    await expect(listPickResults("t1")).rejects.toThrow("选剧服务尚未就绪");
  });
  it("rejects malformed candidate payloads", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ results: [{ id: "made-up" }] })),
    );
    await expect(listPickResults("t1")).rejects.toThrow();
  });
  it("reads /sync through the shared schema, keeping the mirror key", async () => {
    const body = {
      configured: true,
      current: null,
      runs: [],
      mirror: { error: "OperationalError" },
    };
    mockedFetch.mockResolvedValueOnce(new Response(JSON.stringify(body)));
    await expect(getPickSyncStatus()).resolves.toEqual(body);
    expect(mockedFetch.mock.calls[0]?.[0]).toBe("/api/pick/sync");
  });
  it("encodes the thread parameter and propagates cancellation", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ results: [] })),
    );
    const signal = new AbortController().signal;
    await listPickResults("thread/a", signal);
    expect(mockedFetch.mock.calls[0]?.[0]).toBe(
      "/api/pick/results?thread_id=thread%2Fa",
    );
    expect(mockedFetch.mock.calls[0]?.[1]?.signal).toBe(signal);
  });
});
