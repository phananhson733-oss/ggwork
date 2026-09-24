import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";

import { validateAuthNextPath } from "@/core/auth/next-path";
import { getServerSideUserCached } from "@/core/auth/server";
import { type AuthResult, type User } from "@/core/auth/types";
import {
  PICK_DATA_PATH,
  pickDataNextPath,
  requireBoardUser,
} from "@/server/pick-board/auth";

rs.mock("@/core/auth/server", () => ({
  getServerSideUserCached: rs.fn(),
}));

rs.mock("next/navigation", () => ({
  redirect: rs.fn((url: string) => {
    throw Object.assign(new Error("NEXT_REDIRECT"), { url });
  }),
}));

const mockedUser = rs.mocked(getServerSideUserCached);

function user(id: string): User {
  return {
    id,
    email: `${id}@example.test`,
    system_role: "admin",
    needs_setup: false,
    oauth_provider: null,
  };
}

function answer(result: AuthResult) {
  mockedUser.mockResolvedValueOnce(result);
}

async function redirectOf(nextPath: string): Promise<string> {
  const thrown = await requireBoardUser(nextPath).then(
    () => null,
    (error: unknown) => error,
  );
  const url = (thrown as { url?: unknown } | null)?.url;
  if (typeof url !== "string") throw new Error("expected a redirect");
  return url;
}

beforeEach(() => {
  mockedUser.mockReset();
});

afterEach(() => {
  rs.unstubAllEnvs();
});

describe("requireBoardUser", () => {
  it("sends an anonymous visitor to the login page, back here after", async () => {
    answer({ tag: "unauthenticated" });
    const next = pickDataNextPath({ tab: "rank", rk: "rs_rr" });
    expect(await redirectOf(next)).toBe(
      `/login?next=${encodeURIComponent(next)}`,
    );
  });

  it("sends both setup states to /setup", async () => {
    answer({ tag: "needs_setup", user: user("u-1") });
    expect(await redirectOf(PICK_DATA_PATH)).toBe("/setup");
    answer({ tag: "system_setup_required" });
    expect(await redirectOf(PICK_DATA_PATH)).toBe("/setup");
  });

  it("answers a notice when the gateway is down or misconfigured", async () => {
    answer({ tag: "gateway_unavailable" });
    await expect(requireBoardUser(PICK_DATA_PATH)).resolves.toEqual({
      kind: "notice",
      reason: "gateway_unavailable",
    });
    answer({ tag: "config_error", message: "bad DEER_FLOW_ config" });
    await expect(requireBoardUser(PICK_DATA_PATH)).resolves.toEqual({
      kind: "notice",
      reason: "config_error",
    });
  });

  it("lets a signed-in user through", async () => {
    answer({ tag: "authenticated", user: user("u-42") });
    await expect(requireBoardUser(PICK_DATA_PATH)).resolves.toEqual({
      kind: "ok",
      user: user("u-42"),
    });
  });

  it("refuses the shared auth-disabled and static identities, whatever the environment", async () => {
    const settings: [string, string][] = [
      ["DEER_FLOW_AUTH_DISABLED", "1"],
      ["DEER_FLOW_ENV", "production"],
    ];
    for (const [name, value] of settings) {
      rs.stubEnv(name, value);
      for (const id of ["default", "static-website-user"]) {
        answer({ tag: "authenticated", user: user(id) });
        await expect(requireBoardUser(PICK_DATA_PATH)).resolves.toEqual({
          kind: "notice",
          reason: "forbidden",
        });
      }
      rs.unstubAllEnvs();
    }
  });

  it("falls back to the board itself for a next path login would refuse", async () => {
    answer({ tag: "unauthenticated" });
    expect(await redirectOf("https://evil.example/x")).toBe(
      `/login?next=${encodeURIComponent(PICK_DATA_PATH)}`,
    );
  });
});

describe("pickDataNextPath", () => {
  it("re-encodes the query so login accepts it: no bare colon", () => {
    const next = pickDataNextPath({
      tab: "rank",
      q: "a:b c",
      platform: ["x", "y"],
      empty: undefined,
    });
    expect(next).toBe(
      "/workspace/pick-data?tab=rank&q=a%3Ab+c&platform=x&platform=y",
    );
    expect(next).not.toContain(":");
    expect(validateAuthNextPath(next)).toBe(next);
  });

  it("is the bare path without a query", () => {
    expect(pickDataNextPath({})).toBe(PICK_DATA_PATH);
  });

  it("round-trips through the login URL without a bare colon", async () => {
    answer({ tag: "unauthenticated" });
    const url = await redirectOf(pickDataNextPath({ q: "http://x" }));
    const next = new URL(url, "http://localhost").searchParams.get("next");
    expect(next).not.toContain(":");
    expect(validateAuthNextPath(next)).toBe(next);
  });
});

describe("one /auth/me per request", () => {
  it("the workspace layout reads the same cached user as the board", () => {
    const layout = readFileSync(
      resolve(__dirname, "../../../../src/app/workspace/layout.tsx"),
      "utf8",
    );
    expect(layout).toContain("await getServerSideUserCached()");
    const server = readFileSync(
      resolve(__dirname, "../../../../src/core/auth/server.ts"),
      "utf8",
    );
    expect(server).toContain(
      "export const getServerSideUserCached = cache(getServerSideUser);",
    );
  });
});
