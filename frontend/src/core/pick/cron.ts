import { timingSafeEqual } from "node:crypto";

export type CronEnv = {
  cronSecret?: string;
  internalToken?: string;
  syncToken?: string;
  gatewayUrl: string;
};

/** Vercel Cron sends `Authorization: Bearer $CRON_SECRET`; an unset secret never authorizes. */
export function cronAuthorized(
  header: string | null,
  secret: string | undefined,
) {
  if (!secret || !header) return false;
  const expected = Buffer.from(`Bearer ${secret}`);
  const actual = Buffer.from(header);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

/**
 * The gateway call: host internal auth (the only way past AuthMiddleware without a user session),
 * the pick-specific sync token, and a server-generated CSRF double-submit pair — there is no browser
 * on this path, so the pair only satisfies the host middleware's shape check.
 */
export function gatewaySyncRequest(env: CronEnv, csrf: string) {
  if (!env.internalToken || !env.syncToken) return null;
  return {
    url: `${env.gatewayUrl.replace(/\/+$/, "")}/api/pick/cron/sync`,
    init: {
      method: "POST",
      headers: {
        "X-DeerFlow-Internal-Token": env.internalToken,
        "X-Pick-Sync-Token": env.syncToken,
        "X-CSRF-Token": csrf,
        Cookie: `csrf_token=${csrf}`,
      },
      cache: "no-store",
    } satisfies RequestInit,
  };
}
