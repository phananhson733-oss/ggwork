import "server-only";

import { redirect } from "next/navigation";

import { validateAuthNextPath } from "@/core/auth/next-path";
import { getServerSideUserCached } from "@/core/auth/server";
import { assertNever, buildLoginUrl, type User } from "@/core/auth/types";

/**
 * The pick data board checks its own visitor: there is no middleware, and the
 * workspace layout lets gateway_unavailable through with a fallback user.
 */

export const PICK_DATA_PATH = "/workspace/pick-data";

// The shared identities of auth-disabled mode (auth-disabled-user.ts) and the
// static demo (static-user.ts). Refused by id, whatever the environment says:
// the board shows business data only to a real signed-in account.
const SHARED_USER_IDS: ReadonlySet<string> = new Set([
  "default",
  "static-website-user",
]);

export type BoardNoticeReason =
  | "gateway_unavailable"
  | "config_error"
  | "forbidden";

export type BoardAccess =
  | Readonly<{ kind: "ok"; user: User }>
  | Readonly<{ kind: "notice"; reason: BoardNoticeReason }>;

export type SearchParamsRecord = Readonly<
  Record<string, string | readonly string[] | undefined>
>;

/**
 * The board's path with its query re-serialized by URLSearchParams, so a ':'
 * in a value becomes %3A and login's next-path check accepts it.
 */
export function pickDataNextPath(params: SearchParamsRecord): string {
  const pairs = Object.entries(params).flatMap(([key, value]) => {
    if (value === undefined) return [];
    const values: readonly string[] =
      typeof value === "string" ? [value] : value;
    return values.map((item): [string, string] => [key, item]);
  });
  const search = new URLSearchParams(pairs).toString();
  return search ? `${PICK_DATA_PATH}?${search}` : PICK_DATA_PATH;
}

/**
 * Redirects to login or setup; otherwise the user, or why not to show data.
 *
 * The workspace layout checks the same visitor first and is left as it is
 * (P3-5): signed out, it redirects to /login without `next`; on config_error
 * it throws to the app's error boundary. Next renders the layout and the page
 * together, so those two branches here seldom decide the outcome; they stay
 * so the page never reads data for a visitor the layout would turn away,
 * whichever runs first.
 */
export async function requireBoardUser(nextPath: string): Promise<BoardAccess> {
  const result = await getServerSideUserCached();
  switch (result.tag) {
    case "authenticated":
      return SHARED_USER_IDS.has(result.user.id)
        ? { kind: "notice", reason: "forbidden" }
        : { kind: "ok", user: result.user };
    case "unauthenticated":
      redirect(buildLoginUrl(validateAuthNextPath(nextPath) ?? PICK_DATA_PATH));
    case "needs_setup":
    case "system_setup_required":
      redirect("/setup");
    case "gateway_unavailable":
    case "config_error":
      return { kind: "notice", reason: result.tag };
    default:
      return assertNever(result);
  }
}
