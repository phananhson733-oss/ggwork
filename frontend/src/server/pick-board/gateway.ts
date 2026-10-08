import "server-only";

import { cookies } from "next/headers";
import { type z } from "zod";

import { AUTH_REQUEST_TIMEOUT_MS } from "@/core/auth/constants";
import { getGatewayConfig } from "@/core/auth/gateway-config";
import {
  mirrorFieldSchema,
  type PickSyncStatus,
  syncStatusSchema,
} from "@/core/pick/sync-schema";

/**
 * Server-side reads of the gateway's pick API with the visitor's session, the
 * way core/auth/server.ts reads /auth/me: the internal gateway URL, the
 * access_token cookie forwarded, no-store, a five-second timeout.
 *
 * The result is tagged by status only. A response body never reaches the
 * result or a log: the page shows fixed text per status.
 */

const PICK_API = "/api/pick/";
// Resolves a path the way fetch would, before the real base is known.
const PLACEHOLDER_ORIGIN = "http://gateway.invalid";

export type GatewayResult<T> =
  | Readonly<{ ok: true; data: T }>
  | Readonly<{ ok: false; status: number }>
  | Readonly<{ ok: false; status: "unavailable" }>;

const UNAVAILABLE = Object.freeze({
  ok: false,
  status: "unavailable",
} as const);

type CheckedPath = Readonly<{ pathname: string; target: string }>;

function resolvePath(path: string): URL | null {
  try {
    return new URL(path, PLACEHOLDER_ORIGIN);
  } catch {
    return null;
  }
}

/**
 * The path resolved first, then checked: `%2e%2e` and backslashes are dot
 * segments and separators to a URL parser, so checking the spelling is not
 * enough. The resolved path (no fragment) is what gets fetched.
 */
function checkedPath(path: string): CheckedPath {
  const url = path.startsWith(PICK_API) ? resolvePath(path) : null;
  if (
    url?.origin !== PLACEHOLDER_ORIGIN ||
    !url.pathname.startsWith(PICK_API)
  ) {
    throw new Error("pick-board: the gateway reader reads /api/pick/ only");
  }
  return { pathname: url.pathname, target: `${url.pathname}${url.search}` };
}

async function gatewayRead<T>(
  path: string,
  schema: z.ZodType<T, z.ZodTypeDef, unknown>,
  body?: unknown,
  signal?: AbortSignal,
  budgetMs = AUTH_REQUEST_TIMEOUT_MS,
): Promise<GatewayResult<T>> {
  const { pathname, target } = checkedPath(path);
  let base: string;
  try {
    base = getGatewayConfig().internalGatewayUrl;
  } catch {
    return UNAVAILABLE;
  }
  const jar = await cookies();
  const session = jar.get("access_token");
  const csrf = body === undefined ? undefined : jar.get("csrf_token");
  if (!session) return { ok: false, status: 401 };
  if (body !== undefined && !csrf) return { ok: false, status: 403 };
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    Math.min(AUTH_REQUEST_TIMEOUT_MS, budgetMs),
  );
  try {
    const response = await fetch(`${base}${target}`, {
      method: body === undefined ? "GET" : "POST",
      headers: csrf
        ? {
            Cookie: `access_token=${session.value}; csrf_token=${csrf.value}`,
            "X-CSRF-Token": csrf.value,
            "Content-Type": "application/json",
          }
        : { Cookie: `access_token=${session.value}` },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: signal
        ? AbortSignal.any([signal, controller.signal])
        : controller.signal,
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

export function gatewayGet<T>(
  path: string,
  schema: z.ZodType<T, z.ZodTypeDef, unknown>,
): Promise<GatewayResult<T>> {
  return gatewayRead(path, schema);
}

/** Authenticated POST read: the visitor's CSRF cookie/header, never internal impersonation. */
export function gatewayQuery<T>(
  path: string,
  schema: z.ZodType<T, z.ZodTypeDef, unknown>,
  body: unknown,
  signal?: AbortSignal,
  budgetMs?: number,
): Promise<GatewayResult<T>> {
  return gatewayRead(path, schema, body, signal, budgetMs);
}

/** Field paths of a failed parse, through both branches of a union. */
function issuePaths(issues: readonly z.ZodIssue[]): string[] {
  const paths = issues.flatMap((issue) =>
    issue.code === "invalid_union"
      ? issuePaths(issue.unionErrors.flatMap((error) => error.issues))
      : [issue.path.join(".")],
  );
  return [...new Set(paths)];
}

// The shared schema reads a malformed mirror as absent; here that is logged,
// by field path only. A failed mirror read ({error}) is not malformed.
const boardSyncSchema = syncStatusSchema.extend({
  mirror: mirrorFieldSchema
    .nullable()
    .optional()
    .catch(({ error }) => {
      console.error("[pick-board] gateway mirror malformed", {
        fields: issuePaths(error.issues),
      });
      return undefined;
    }),
});

/** GET /api/pick/sync, with its optional mirror field. */
export function getPickSync(): Promise<GatewayResult<PickSyncStatus>> {
  return gatewayGet("/api/pick/sync", boardSyncSchema);
}
