import { beforeEach, describe, expect, it, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  getPickResult,
  getPickSyncStatus,
  listPickResults,
  listSavedPicks,
  savePickSelection,
} from "@/core/pick/api";

import obsPayload from "./fixtures/backend-result-obs.json";
import oldPayload from "./fixtures/backend-result.json";

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

function answer(body: unknown) {
  mockedFetch.mockResolvedValueOnce(new Response(JSON.stringify(body)));
}

function saved(id: string, snapshot: unknown) {
  return {
    id,
    identity: `identity-${id}`,
    source_result_id: "r1",
    source_item_id: `item-${id}`,
    snapshot_json: snapshot,
    note: "",
    state: "selected",
    version: 1,
    created_at: "2026-09-25T04:10:00.000000+00:00",
    updated_at: "2026-09-25T04:10:00.000000+00:00",
  };
}

// Plan section 10's rollback matrix, the frontend's cells (TR-16, counterexample
// 14): from S1 on the frontend is F1 and never goes back, while the gateway may
// still be M0 (old cards only) or M1 (old, new and mixed). Every cell parses.
describe("rollback matrix: the new frontend reads every card it can meet", () => {
  it("F1 x old card: an old result, with no observations key", async () => {
    answer(oldPayload);
    const result = await getPickResult(oldPayload.id);
    expect(result.observations).toBeUndefined();
    expect(result.ranking_version).toBe("signal-rank-v1");
  });
  it("F1 x new card: a result with observation conditions, evidence and observations", async () => {
    answer(obsPayload);
    const result = await getPickResult(obsPayload.id);
    expect(result.conditions.sort).toBe("obs");
    expect(result.observations?.trends?.set_id).toBe(
      obsPayload.observations.trends.set_id,
    );
    expect(result.items[0]?.evidence.map((e) => e.kind)).toEqual([
      "kd",
      "qc",
      "obs_trends",
      "obs_gsc",
      "obs_discovery",
    ]);
  });
  it("F1 x mixed session: listPickResults parses old and new cards together", async () => {
    answer({ results: [oldPayload, obsPayload, oldPayload] });
    const results = await listPickResults("t1");
    expect(results.map((r) => r.observations !== undefined)).toEqual([
      false,
      true,
      false,
    ]);
  });
  it("F1 x stored snapshots: listSavedPicks parses items saved before and after", async () => {
    answer({
      selections: [
        saved("old", oldPayload.items[0]),
        saved("new", obsPayload.items[0]),
      ],
    });
    const selections = await listSavedPicks();
    expect(
      selections.map((s) => s.snapshot_json.evidence.map((e) => e.kind)),
    ).toEqual([
      oldPayload.items[0]?.evidence.map((e) => e.kind),
      ["kd", "qc", "obs_trends", "obs_gsc", "obs_discovery"],
    ]);
  });
});
