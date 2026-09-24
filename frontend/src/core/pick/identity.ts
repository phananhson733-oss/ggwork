/**
 * A candidate's identity back to the pick-board row_key (P4-1).
 *
 * The backend writes identity as json.dumps([source, source_id, language])
 * (customizations/pick-workbench/ggwork_pick/contracts.py), and RealShort's feed
 * sets source_id = Buffer.from(rowKey).toString("base64url")
 * (realshort@816ca2e src/lib/pick/feed-map.ts:316): base64url, no padding.
 *
 * Decoding follows gates.row_key_of (ggwork_pick/mirror/gates.py) step for step,
 * so the card link and the mirror gate agree on every source_id: the base64url
 * alphabet only, strict UTF-8 with a leading BOM kept as part of the key, and
 * re-encoding must give the source_id back. atob alone ignores non-zero trailing
 * bits, which would let two source_ids name one row. The shared cases live in
 * customizations/pick-workbench/tests/fixtures/identity_row_key_cases.json.
 *
 * Imports row-key.ts and nothing else: the chat route's client cards use this,
 * and request.ts would pull metrics.ts into that bundle (critique B13).
 * No Buffer: this also runs in the browser.
 */
import { isRowKey } from "@/core/pick-board/row-key";

/** The source RealShort's shared sync writes; personal imports use their own. */
export const PICK_SOURCE = "realshort-pick";

const BASE64URL = /^[A-Za-z0-9_-]*$/;

function decodeBase64url(text: string): Uint8Array | null {
  if (text.length % 4 === 1) return null;
  const padded =
    text.replace(/-/g, "+").replace(/_/g, "/") +
    "=".repeat((4 - (text.length % 4)) % 4);
  try {
    return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
  } catch {
    return null;
  }
}

function encodeBase64url(bytes: Uint8Array): string {
  const binary = Array.from(bytes, (byte) => String.fromCharCode(byte)).join(
    "",
  );
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function strictUtf8(bytes: Uint8Array): string | null {
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(
      bytes,
    );
  } catch {
    return null;
  }
}

/** The row_key a source_id encodes, or null; the same answer as gates.row_key_of. */
export function rowKeyFromSourceId(sourceId: string): string | null {
  if (!BASE64URL.test(sourceId)) return null;
  const bytes = decodeBase64url(sourceId);
  if (bytes === null || encodeBase64url(bytes) !== sourceId) return null;
  return strictUtf8(bytes);
}

function sharedSourceId(identity: string): string | null {
  try {
    const parsed: unknown = JSON.parse(identity);
    if (!Array.isArray(parsed) || parsed.length !== 3) return null;
    const [source, sourceId] = parsed as unknown[];
    return source === PICK_SOURCE && typeof sourceId === "string"
      ? sourceId
      : null;
  } catch {
    return null;
  }
}

/**
 * The row_key the pick board can open for this identity, or null: a personal
 * source, a malformed identity, a source_id that does not decode, or a key the
 * page's `row=` would drop (isRowKey; never trimmed).
 */
export function rowKeyFromIdentity(identity: string): string | null {
  const sourceId = sharedSourceId(identity);
  const rowKey = sourceId === null ? null : rowKeyFromSourceId(sourceId);
  return rowKey !== null && isRowKey(rowKey) ? rowKey : null;
}
