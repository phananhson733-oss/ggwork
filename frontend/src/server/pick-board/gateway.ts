import "server-only";

import { cookies } from "next/headers";
import { type z } from "zod";

import { AUTH_REQUEST_TIMEOUT_MS } from "@/core/auth/constants";
import { getGatewayConfig } from "@/core/auth/gateway-config";
import { type PickSyncStatus, syncStatusSchema } from "@/core/pick/sync-schema";

/**
 * Server-side reads of the gateway's pick API with the visitor's session, the
 * way core/auth/server.ts reads /auth/me: the internal gateway URL, the
 * access_token cookie forwarded, no-store, a five-second timeout.
 *
 * The result is tagged by status only. A response body never reaches the
 * result or a log: the page shows fixed text per status.
 */

const PICK_API = "/api/pick/";

export type GatewayResult<T> =
  | Readonly<{ ok: true; data: T }>
  | Readonly<{ ok: false; status: number }>
  | Readonly<{ ok: false; status: "unavailable" }>;

const UNAVAILABLE = Object.freeze({
  ok: false,
  status: "unavailable",
} as const);

function checkedPath(path: string): string {
  const pathname = path.split("?")[0] ?? "";
  if (!pathname.startsWith(PICK_API) || pathname.split("/").includes("..")) {
    throw new Error("pick-board: the gateway reader reads /api/pick/ only");
  }
  return pathname;
}

export async function gatewayGet<T>(
  path: string,
  schema: z.ZodType<T, z.ZodTypeDef, unknown>,
): Promise<GatewayResult<T>> {
  const pathname = checkedPath(path);
  let base: string;
  try {
    base = getGatewayConfig().internalGatewayUrl;
  } catch {
    return UNAVAILABLE;
  }
  const session = (await cookies()).get("access_token");
  if (!session) return { ok: false, status: 401 };
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), AUTH_REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${base}${path}`, {
      headers: { Cookie: `access_token=${session.value}` },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) return { ok: false, status: response.status };
    const parsed = schema.safeParse(await response.json());
    if (parsed.success) return { ok: true, data: parsed.data };
    console.error("[pick-board] gateway answer malformed", {
      path: pathname,
      fields: parsed.error.issues.map((issue) => issue.path.join(".")),
    });
    return UNAVAILABLE;
  } catch {
    return UNAVAILABLE;
  } finally {
    clearTimeout(timeout);
  }
}

/** GET /api/pick/sync, with its optional mirror field. */
export function getPickSync(): Promise<GatewayResult<PickSyncStatus>> {
  return gatewayGet("/api/pick/sync", syncStatusSchema);
}
