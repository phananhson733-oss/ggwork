/**
 * Links from a candidate result into the pick board (/workspace/pick-data), P4-1.
 *
 * Parameter names match parsePickRequest in core/pick-board/request.ts: `tab=row`
 * with `row=<row_key as is>` opens the evidence page, `v=<id>` pins a mirror
 * version, `result=<id>` replays a result. That module is not imported here: the
 * chat route's client cards use this file (critique B13); links.test.ts parses
 * every href with it instead.
 */
import { rowKeyFromIdentity } from "./identity";
import type { PickDataAsOf, PickResult } from "./types";

const BOARD_PATH = "/workspace/pick-data";

/** pick_mirror's MAX_VERSION_ID; the page's `v=` takes 1 to 6 digits. */
const VERSION_MAX = 999_999;

/** Result ids are uuid4().hex; the page's `result=` drops anything else. */
const RESULT_ID = /^[0-9a-f]{32}$/;

/**
 * The replay link waits for the replay view (P4-2): until then a `result=` link
 * lands on a page that ignores it (critique A4). P4-2 sets this to true.
 */
export const REPLAY_LINK_ENABLED = false;

export function isMirrorVersion(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 1 &&
    value <= VERSION_MAX
  );
}

export function rowCheckHref(rowKey: string, version: number): string {
  const params = new URLSearchParams({
    tab: "row",
    row: rowKey,
    v: String(version),
  });
  return `${BOARD_PATH}?${params.toString()}`;
}

export function replayHref(resultId: string, version: number | null): string {
  const params = new URLSearchParams({ result: resultId });
  if (version !== null) params.set("v", String(version));
  return `${BOARD_PATH}?${params.toString()}`;
}

/** The version a per-card check opens: shared batches with a mirror version only. */
export function checkVersion(
  asOf: PickDataAsOf | null | undefined,
): number | null {
  if (asOf?.shared !== true) return null;
  return isMirrorVersion(asOf.mirror_version) ? asOf.mirror_version : null;
}

/** 「在选剧资料核对」for one card, or null when there is no version or no row key. */
export function itemCheckHref(
  asOf: PickDataAsOf | null | undefined,
  identity: string,
): string | null {
  const version = checkVersion(asOf);
  if (version === null) return null;
  const rowKey = rowKeyFromIdentity(identity);
  return rowKey === null ? null : rowCheckHref(rowKey, version);
}

/**
 * 「回放这份候选」for a shared result, pinned to its version when it has one
 * (null falls back to the current version's rows). Personal batches never.
 */
export function replayLink(
  result: Pick<PickResult, "id" | "data_as_of">,
  enabled: boolean = REPLAY_LINK_ENABLED,
): string | null {
  const asOf = result.data_as_of;
  if (!enabled || asOf?.shared !== true || !RESULT_ID.test(result.id))
    return null;
  const version = isMirrorVersion(asOf.mirror_version)
    ? asOf.mirror_version
    : null;
  return replayHref(result.id, version);
}
