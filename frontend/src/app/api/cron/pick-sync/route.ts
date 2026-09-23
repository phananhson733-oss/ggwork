import { randomBytes } from "node:crypto";

import { getGatewayConfig } from "@/core/auth/gateway-config";
import { cronAuthorized, gatewaySyncRequest } from "@/core/pick/cron";

export const dynamic = "force-dynamic";
export const maxDuration = 30;

// Vercel Cron → this route → Gateway /api/pick/cron/sync. The pull itself runs in the
// Gateway background, so this only reports whether it started.
export async function GET(request: Request) {
  const json = (body: unknown, status = 200) =>
    Response.json(body, { status, headers: { "cache-control": "no-store" } });
  if (
    !cronAuthorized(
      request.headers.get("authorization"),
      process.env.CRON_SECRET?.trim(),
    )
  )
    return json({ ok: false, error: "unauthorized" }, 401);
  const call = gatewaySyncRequest(
    {
      internalToken: process.env.DEER_FLOW_INTERNAL_AUTH_TOKEN?.trim(),
      syncToken: process.env.PICK_SYNC_TOKEN?.trim(),
      gatewayUrl: getGatewayConfig().internalGatewayUrl,
    },
    randomBytes(24).toString("base64url"),
  );
  if (!call) return json({ ok: false, error: "sync_unconfigured" }, 503);
  try {
    const response = await fetch(call.url, call.init);
    const body = (await response.json().catch(() => null)) as {
      status?: string;
    } | null;
    const ok = response.status === 202;
    if (!ok) console.error("[pick-sync] gateway refused", response.status);
    return json(
      { ok, gatewayStatus: response.status, status: body?.status ?? null },
      ok ? 200 : 502,
    );
  } catch {
    console.error("[pick-sync] gateway unreachable");
    return json({ ok: false, error: "gateway_unreachable" }, 502);
  }
}
