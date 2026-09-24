import { describe, expect, it } from "@rstest/core";

import {
  checkVersion,
  isMirrorVersion,
  itemCheckHref,
  REPLAY_LINK_ENABLED,
  replayHref,
  replayLink,
  rowCheckHref,
} from "@/core/pick/links";
import type { PickDataAsOf, PickResult } from "@/core/pick/types";
import { parsePickRequest } from "@/core/pick-board/request";

const RESULT_ID = "0123456789abcdef0123456789abcdef";

function parsed(href: string) {
  const url = new URL(href, "https://workbench.test");
  expect(url.pathname).toBe("/workspace/pick-data");
  return parsePickRequest(url.searchParams);
}

function asOf(patch: Partial<PickDataAsOf> = {}): PickDataAsOf {
  return {
    source_as_of: "2026-09-23T03:00:00.000Z",
    published_at: "2026-09-23T03:17:34Z",
    shared: true,
    mirror_version: 7,
    ...patch,
  };
}

function identityOf(key: string): string {
  return JSON.stringify([
    "realshort-pick",
    Buffer.from(key, "utf8").toString("base64url"),
    "en",
  ]);
}

describe("rowCheckHref", () => {
  it("builds the evidence tab link in the page's parameter order", () => {
    expect(rowCheckHref("kalos-1", 7)).toBe(
      "/workspace/pick-data?tab=row&row=kalos-1&v=7",
    );
  });

  it.each([
    "shortmax-856049 ",
    " kalos-1",
    "goodshort-mqk++n/L+Wf/xDC0G43CRQ==",
    "shortmax-845227（已设置定时）",
    "a&b=c#d?e%f",
  ])("round-trips %j through the page's own parser", (key) => {
    const request = parsed(rowCheckHref(key, 42));
    expect(request.tab).toBe("row");
    expect(request.rowKey).toBe(key);
    expect(request.v).toBe(42);
  });
});

describe("replayHref", () => {
  it("pins the version when there is one", () => {
    expect(replayHref(RESULT_ID, 7)).toBe(
      `/workspace/pick-data?result=${RESULT_ID}&v=7`,
    );
    const request = parsed(replayHref(RESULT_ID, 7));
    expect(request.result).toBe(RESULT_ID);
    expect(request.v).toBe(7);
  });

  it("leaves v out when the result had no paired version", () => {
    expect(replayHref(RESULT_ID, null)).toBe(
      `/workspace/pick-data?result=${RESULT_ID}`,
    );
    expect(parsed(replayHref(RESULT_ID, null)).v).toBeNull();
  });
});

describe("isMirrorVersion", () => {
  it("takes what the page's v= accepts: 1 to 999999", () => {
    for (const good of [1, 7, 999_999])
      expect(isMirrorVersion(good)).toBe(true);
    for (const bad of [0, -1, 1.5, 1_000_000, Number.NaN, "7", null, undefined])
      expect(isMirrorVersion(bad)).toBe(false);
  });
});

describe("checkVersion", () => {
  it("is the mirror version of a shared result", () => {
    expect(checkVersion(asOf())).toBe(7);
  });

  it.each([
    ["no data_as_of", null],
    ["an old result without data_as_of", undefined],
    ["a personal batch", asOf({ shared: false })],
    ["a null version (degraded publish)", asOf({ mirror_version: null })],
    [
      "a missing version (backend switch off)",
      asOf({ mirror_version: undefined }),
    ],
    ["a version the page would ignore", asOf({ mirror_version: 1_000_000 })],
  ])("is null for %s", (_label, value) => {
    expect(checkVersion(value)).toBeNull();
  });
});

describe("itemCheckHref", () => {
  it("links a decodable RealShort identity at the pinned version", () => {
    const href = itemCheckHref(asOf(), identityOf("shortmax-856049 "));
    expect(href).not.toBeNull();
    const request = parsed(href ?? "");
    expect(request.rowKey).toBe("shortmax-856049 ");
    expect(request.v).toBe(7);
  });

  it("has no link without a version or without a row key", () => {
    expect(
      itemCheckHref(asOf({ mirror_version: null }), identityOf("c-1")),
    ).toBeNull();
    expect(itemCheckHref(asOf(), "source/1")).toBeNull();
    expect(
      itemCheckHref(asOf(), JSON.stringify(["sheet-upload", "Yy0x", "en"])),
    ).toBeNull();
  });
});

describe("replayLink (P4-2 turns it on; critique A4)", () => {
  const result: Pick<PickResult, "id" | "data_as_of"> = {
    id: RESULT_ID,
    data_as_of: asOf(),
  };

  it("stays off until the replay view ships", () => {
    expect(REPLAY_LINK_ENABLED).toBe(false);
    expect(replayLink(result)).toBeNull();
  });

  it("when on, shows for a shared result, with or without a version", () => {
    expect(replayLink(result, true)).toBe(replayHref(RESULT_ID, 7));
    expect(
      replayLink(
        { ...result, data_as_of: asOf({ mirror_version: null }) },
        true,
      ),
    ).toBe(replayHref(RESULT_ID, null));
  });

  it("when on, never shows for a personal batch or an id the page drops", () => {
    expect(
      replayLink({ ...result, data_as_of: asOf({ shared: false }) }, true),
    ).toBeNull();
    expect(replayLink({ ...result, data_as_of: null }, true)).toBeNull();
    expect(replayLink({ ...result, id: "r1" }, true)).toBeNull();
  });
});
