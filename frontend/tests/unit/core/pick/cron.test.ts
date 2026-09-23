import { describe, expect, it } from "@rstest/core";

import { cronAuthorized, gatewaySyncRequest } from "@/core/pick/cron";

describe("pick sync cron", () => {
  it("accepts only the exact Vercel cron bearer", () => {
    expect(cronAuthorized("Bearer s3cret", "s3cret")).toBe(true);
    expect(cronAuthorized("Bearer s3cre", "s3cret")).toBe(false);
    expect(cronAuthorized("Bearer ", "")).toBe(false);
    expect(cronAuthorized(null, "s3cret")).toBe(false);
    expect(cronAuthorized("Bearer x", undefined)).toBe(false);
  });
  it("builds an internal gateway call with a matching CSRF pair", () => {
    const call = gatewaySyncRequest(
      { internalToken: "i", syncToken: "s", gatewayUrl: "https://gw.test/" },
      "pair",
    );
    expect(call?.url).toBe("https://gw.test/api/pick/cron/sync");
    const headers = call?.init.headers as Record<string, string>;
    expect(headers["X-DeerFlow-Internal-Token"]).toBe("i");
    expect(headers["X-Pick-Sync-Token"]).toBe("s");
    expect(headers.Cookie).toBe(`csrf_token=${headers["X-CSRF-Token"]}`);
    expect(headers).not.toHaveProperty("Authorization");
  });
  it("refuses to call without both tokens", () => {
    expect(
      gatewaySyncRequest({ internalToken: "i", gatewayUrl: "https://gw" }, "p"),
    ).toBeNull();
    expect(
      gatewaySyncRequest({ syncToken: "s", gatewayUrl: "https://gw" }, "p"),
    ).toBeNull();
  });
});
