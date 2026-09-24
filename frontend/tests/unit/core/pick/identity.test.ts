import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

import {
  PICK_SOURCE,
  rowKeyFromIdentity,
  rowKeyFromSourceId,
} from "@/core/pick/identity";

import {
  REPO_ROOT,
  readSource,
  stripComments,
} from "../pick-board/ported-source";

type SharedCase = {
  name: string;
  source_id: string;
  identity: string;
  row_key: string | null;
  card_row_key: string | null;
};

// The same file customizations/pick-workbench/tests/mirror/test_mirror_identity_cases.py
// checks against gates.row_key_of, so the card and the mirror gate decode alike.
const shared = JSON.parse(
  readFileSync(
    path.join(
      REPO_ROOT,
      "customizations/pick-workbench/tests/fixtures/identity_row_key_cases.json",
    ),
    "utf8",
  ),
) as { cases: SharedCase[] };

function b64url(text: string): string {
  return Buffer.from(text, "utf8").toString("base64url");
}

function identityOf(sourceId: unknown, source: unknown = PICK_SOURCE): string {
  return JSON.stringify([source, sourceId, "en"]);
}

describe("the cases shared with gates.row_key_of", () => {
  it("has enough cases to mean something", () => {
    expect(shared.cases.length).toBeGreaterThanOrEqual(25);
  });

  it.each(shared.cases)("$name: the source_id decodes as Python does", (c) => {
    expect(rowKeyFromSourceId(c.source_id)).toBe(c.row_key);
  });

  it.each(shared.cases)("$name: the card reads the identity", (c) => {
    expect(identityOf(c.source_id)).toBe(c.identity);
    expect(rowKeyFromIdentity(c.identity)).toBe(c.card_row_key);
  });
});

describe("rowKeyFromIdentity", () => {
  it.each([
    ["an ASCII key", "c-1"],
    ["a key with Han characters", "shortmax-845227（已设置定时）"],
    ["a key with a trailing space, not trimmed", "shortmax-856049 "],
    ["a GoodShort id with + / =", "goodshort-mqk++n/L+Wf/xDC0G43CRQ=="],
  ])("returns %s exactly", (_label, key) => {
    expect(rowKeyFromIdentity(identityOf(b64url(key)))).toBe(key);
  });

  it("keeps a leading byte order mark as part of the key", () => {
    const key = `${String.fromCharCode(0xfeff)}kalos-1`;
    expect(rowKeyFromIdentity(identityOf(b64url(key)))).toBe(key);
  });

  it.each([
    ["a personal batch source", identityOf(b64url("c-1"), "sheet-upload")],
    [
      "a source differing only in case",
      identityOf(b64url("c-1"), "RealShort-Pick"),
    ],
    ["two elements", JSON.stringify([PICK_SOURCE, b64url("c-1")])],
    ["four elements", JSON.stringify([PICK_SOURCE, b64url("c-1"), "en", "x"])],
    ["a number source_id", identityOf(12)],
    ["a null source_id", identityOf(null)],
    ["an object", JSON.stringify({ source: PICK_SOURCE, source_id: "Yy0x" })],
    ["a bare string", JSON.stringify("Yy0x")],
    ["broken JSON", `["${PICK_SOURCE}","Yy0x","en"`],
    ["an empty string", ""],
    ["a slash identity from an old import", "source/1"],
    ["bad base64url", identityOf("YS+i")],
    ["bad UTF-8", identityOf("_w")],
    ["non-zero trailing bits", identityOf("YS1")],
  ])("returns null for %s", (_label, identity) => {
    expect(rowKeyFromIdentity(identity)).toBeNull();
  });

  it("reads identities with JSON whitespace the way JSON.parse does", () => {
    expect(rowKeyFromIdentity(`[ "${PICK_SOURCE}" , "Yy0x" , "en" ]`)).toBe(
      "c-1",
    );
  });
});

describe("identity.ts stays out of request.ts (critique B13)", () => {
  it("imports row-key.ts and nothing else", () => {
    const code = stripComments(readSource("src/core/pick/identity.ts"));
    const specifiers = [
      ...code.matchAll(/\bfrom\s*["'`]([^"'`]+)["'`]/g),
      ...code.matchAll(/\bimport\s*\(?\s*["'`]([^"'`]+)["'`]/g),
      ...code.matchAll(/\brequire\s*\(\s*["'`]([^"'`]+)["'`]/g),
    ].map((match) => match[1]);
    expect(specifiers).toEqual(["@/core/pick-board/row-key"]);
  });
});
