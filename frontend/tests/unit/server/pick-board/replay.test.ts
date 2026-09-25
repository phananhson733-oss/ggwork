/**
 * loadReplay (P4-2): the agent's re-run list from GET /api/pick/replay and the
 * result's conditions from GET /api/pick/results/{id}, both through the
 * visitor's session, turned into one tagged answer. The replay body is the
 * backend's own answer for board_fixture's v2 pull
 * (customizations/pick-workbench/tests/fixtures/board_replay_cases.json,
 * written by test_board_replay_cases.py), so a field the backend renames
 * fails here.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";

import { loadReplay } from "@/server/pick-board/replay";

import obsResult from "../../core/pick/fixtures/backend-result-obs.json";
import backendResult from "../../core/pick/fixtures/backend-result.json";

const REPO_ROOT = path.resolve(__dirname, "../../../../..");
const CASES = JSON.parse(
  readFileSync(
    path.join(
      REPO_ROOT,
      "customizations/pick-workbench/tests/fixtures/board_replay_cases.json",
    ),
    "utf8",
  ),
) as { replay: Record<string, unknown>; conditions: Record<string, unknown> };

const RESULT_ID = "0123456789abcdef0123456789abcdef";

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

const RESULT_BODY = { ...backendResult, conditions: CASES.conditions };

function stubGateway(replay: Answer, result: Answer = ok(RESULT_BODY)) {
  const fetchSpy = rs.fn(async (url: string) => {
    const answer = new URL(url).pathname.endsWith("/replay") ? replay : result;
    if (answer === "network") throw new TypeError("fetch failed");
    return Response.json(answer.body, { status: answer.status });
  });
  rs.stubGlobal("fetch", fetchSpy);
  return fetchSpy;
}

function ok(body: unknown): Answer {
  return { status: 200, body };
}

function status(code: number): Answer {
  return { status: code, body: { detail: "secret detail" } };
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

describe("loadReplay", () => {
  it("reads the backend's replay and the result's conditions, with the session", async () => {
    const fetchSpy = stubGateway(ok(CASES.replay));
    const loaded = await loadReplay(RESULT_ID);
    if (loaded.kind !== "ok")
      throw new Error(`expected ok, got ${loaded.kind}`);
    const replay = CASES.replay;
    expect(loaded.answer).toEqual({
      resultId: RESULT_ID,
      mirrorVersion: null,
      limit: replay.limit,
      total: replay.total,
      identities: replay.identities,
      truncated: false,
      excludedReproducible: true,
      rankingReproducible: true,
      unmappable: ["exclude_selected"],
      sourceAsOf: "2026-09-23T22:15:00.000Z",
    });
    expect(loaded.conditions).toEqual(CASES.conditions);
    const urls = fetchSpy.mock.calls.map(([url]) => String(url)).sort();
    expect(urls).toEqual([
      `http://127.0.0.1:8001/api/pick/replay?result_id=${RESULT_ID}`,
      `http://127.0.0.1:8001/api/pick/results/${RESULT_ID}`,
    ]);
    for (const [, init] of fetchSpy.mock.calls as unknown as [
      string,
      RequestInit,
    ][])
      expect(new Headers(init.headers).get("Cookie")).toBe(
        "access_token=stub-token",
      );
  });

  it("keeps the paired mirror version, and reads a missing one as none", async () => {
    stubGateway(ok({ ...CASES.replay, mirror_version: 7 }));
    const paired = await loadReplay(RESULT_ID);
    expect(paired.kind === "ok" && paired.answer.mirrorVersion).toBe(7);
    const older = Object.fromEntries(
      Object.entries(CASES.replay).filter(([key]) => key !== "mirror_version"),
    );
    stubGateway(ok(older));
    const absent = await loadReplay(RESULT_ID);
    expect(absent.kind === "ok" && absent.answer.mirrorVersion).toBeNull();
  });

  it("maps the gateway's statuses; the body never reaches the answer", async () => {
    const cases: [number, string][] = [
      [401, "unauthenticated"],
      [404, "notFound"],
      [409, "conflict"],
      [410, "gone"],
      [422, "unavailable"],
      [500, "unavailable"],
      [503, "unavailable"],
    ];
    for (const [code, kind] of cases) {
      stubGateway(status(code));
      const loaded = await loadReplay(RESULT_ID);
      expect(loaded.kind, String(code)).toBe(kind);
      expect(JSON.stringify(loaded)).not.toContain("secret");
    }
  });

  it("409 and 410 still carry the conditions, for the near filter", async () => {
    for (const code of [409, 410]) {
      stubGateway(status(code));
      const loaded = await loadReplay(RESULT_ID);
      expect(
        loaded.kind === "conflict" || loaded.kind === "gone"
          ? loaded.conditions
          : "none",
      ).toEqual(CASES.conditions);
    }
  });

  it("is unavailable when the gateway is down or the replay is malformed", async () => {
    stubGateway("network");
    expect((await loadReplay(RESULT_ID)).kind).toBe("unavailable");
    stubGateway(ok({ ...CASES.replay, identities: "not a list" }));
    expect((await loadReplay(RESULT_ID)).kind).toBe("unavailable");
    const tooMany = Array.from({ length: 2001 }, (_, n) => `["x","${n}","en"]`);
    stubGateway(ok({ ...CASES.replay, identities: tooMany }));
    expect((await loadReplay(RESULT_ID)).kind).toBe("unavailable");
  });

  it("a replay without conditions is still a replay (no near filter)", async () => {
    for (const result of [
      status(500),
      "network" as const,
      ok({ conditions: { limit: "five" } }),
    ]) {
      stubGateway(ok(CASES.replay), result);
      const loaded = await loadReplay(RESULT_ID);
      expect(loaded.kind).toBe("ok");
      expect(loaded.kind === "ok" && loaded.conditions).toBeNull();
    }
  });

  it("a malformed data_as_of costs only the batch time", async () => {
    stubGateway(ok({ ...CASES.replay, data_as_of: { shared: "yes" } }));
    const loaded = await loadReplay(RESULT_ID);
    expect(loaded.kind === "ok" && loaded.answer.sourceAsOf).toBeNull();
  });

  it("asks nothing for an id the page would not have passed", async () => {
    const fetchSpy = stubGateway(ok(CASES.replay));
    for (const id of [
      "",
      "r1",
      `${RESULT_ID}/../sync`,
      RESULT_ID.toUpperCase(),
    ])
      expect((await loadReplay(id)).kind).toBe("notFound");
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

// Plan TR-16: a result with observation conditions (sort=obs, the seven
// optional keys) still gives the near filter its conditions, on 200 and on the
// 409 / 410 that carry no list of their own.
describe("loadReplay with observation conditions (TR-16)", () => {
  it("parses the result's observation conditions", async () => {
    for (const replay of [ok(CASES.replay), status(409), status(410)]) {
      stubGateway(replay, ok(obsResult));
      const loaded = await loadReplay(RESULT_ID);
      const conditions =
        "conditions" in loaded ? loaded.conditions : "no conditions";
      expect(conditions).toEqual(obsResult.conditions);
    }
  });
});
