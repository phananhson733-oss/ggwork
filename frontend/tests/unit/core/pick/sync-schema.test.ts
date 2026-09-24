import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "@rstest/core";

import {
  isMirrorReadError,
  mirrorStatusSchema,
  parseSyncTime,
  syncStatusSchema,
} from "@/core/pick/sync-schema";

const RUN = {
  id: "run-1",
  source: "realshort",
  trigger: "schedule",
  status: "success",
  started_at: "2026-09-24T03:40:00+00:00",
  finished_at: "2026-09-24T03:52:00+00:00",
  rows: 120,
  catalog_batch_id: "b-cat",
  knowledge_batch_id: "b-kn",
  source_as_of: "2026-09-24T03:38:00.000Z",
  error: null,
};

const MIRROR = {
  enabled: true,
  current: {
    id: 7,
    as_of: "2026-09-24T03:38:00+00:00",
    latest_snapshot: "2026-09-23",
    published_at: "2026-09-24T03:52:00+00:00",
  },
  series_through: "2026-09-23",
  trimmed_before: "2026-06-25",
  behind: false,
  consecutive_failures: 0,
  last_failure_at: null,
  last_failure: null,
  alert: false,
  warnings: ["catalog_import_incomplete"],
  lock_stuck: null,
  shared_source_as_of: "2026-09-24T03:38:00.000Z",
};

function status(extra: Record<string, unknown> = {}) {
  return {
    configured: true,
    current: {
      id: "b-cat",
      shared: true,
      source_as_of: "2026-09-24T03:38:00.000Z",
      published_at: "2026-09-24T03:52:00+00:00",
    },
    runs: [RUN],
    ...extra,
  };
}

describe("syncStatusSchema", () => {
  it("parses the SQLite answer, where mirror is null", () => {
    const parsed = syncStatusSchema.parse(status({ mirror: null }));
    expect(parsed.mirror).toBeNull();
    expect(parsed.runs).toHaveLength(1);
  });

  it("parses an answer from before P2-8b, with no mirror key at all", () => {
    expect(syncStatusSchema.parse(status()).mirror).toBeUndefined();
  });

  it("keeps the mirror object instead of dropping it as an unknown key", () => {
    const parsed = syncStatusSchema.parse(status({ mirror: MIRROR }));
    expect(parsed.mirror).toEqual(MIRROR);
  });

  it("accepts a stuck lock and a mirror with no version yet", () => {
    const mirror = {
      ...MIRROR,
      current: null,
      series_through: null,
      trimmed_before: null,
      lock_stuck: {
        pid: 4242,
        holder: "backfill",
        since: "2026-09-24T02:00:00+00:00",
      },
    };
    expect(mirrorStatusSchema.parse(mirror).lock_stuck?.holder).toBe(
      "backfill",
    );
  });

  it("accepts a mirror answer without shared_source_as_of", () => {
    const mirror = Object.fromEntries(
      Object.entries(MIRROR).filter(([key]) => key !== "shared_source_as_of"),
    );
    expect(mirrorStatusSchema.parse(mirror).shared_source_as_of).toBe(
      undefined,
    );
  });

  it("reads a malformed mirror as absent; the rest of /sync still parses", () => {
    for (const broken of [
      { ...MIRROR, behind: "yes" },
      { ...MIRROR, lock_stuck: { pid: "4242" } },
      "mirror",
    ]) {
      const parsed = syncStatusSchema.parse(status({ mirror: broken }));
      expect(parsed.mirror).toBeUndefined();
      expect(parsed.runs).toEqual([RUN]);
      expect(parsed.current?.id).toBe("b-cat");
    }
  });

  it("keeps the mirror schema itself strict, for its banners", () => {
    const noWarnings = { ...MIRROR, warnings: "catalog_import_incomplete" };
    expect(mirrorStatusSchema.safeParse(noWarnings).success).toBe(false);
    const broken = { ...MIRROR, behind: "yes" };
    expect(mirrorStatusSchema.safeParse(broken).success).toBe(false);
  });

  it("keeps the run status to the three values the gateway writes", () => {
    const run = { ...RUN, status: "unknown" };
    expect(syncStatusSchema.safeParse(status({ runs: [run] })).success).toBe(
      false,
    );
  });

  it("keeps a failed mirror read as its own branch, not as absent", () => {
    const parsed = syncStatusSchema.parse(
      status({ mirror: { error: "OperationalError" } }),
    );
    expect(parsed.mirror).toEqual({ error: "OperationalError" });
    expect(isMirrorReadError(parsed.mirror)).toBe(true);
    expect(isMirrorReadError(MIRROR)).toBe(false);
    expect(isMirrorReadError(null)).toBe(false);
    expect(isMirrorReadError(undefined)).toBe(false);
  });

  it("reads an error branch that is not a class name as malformed", () => {
    for (const mirror of [
      { error: "" },
      { error: "value 'secret' is wrong" },
      { error: "RuntimeError", detail: "x" },
    ]) {
      expect(syncStatusSchema.parse(status({ mirror })).mirror).toBe(undefined);
    }
  });

  it("takes the gateway's own time formats and a null published_at", () => {
    const mirror = {
      ...MIRROR,
      current: {
        id: 3,
        as_of: "2026-09-23T22:15:00.000Z",
        latest_snapshot: "2026-09-23",
        published_at: null,
      },
      last_failure_at: "2026-09-24T03:52:00.123456+00:00",
    };
    expect(syncStatusSchema.parse(status({ mirror })).mirror).toEqual(mirror);
  });
});

describe("parseSyncTime", () => {
  it("reads RealShort's asOf and the gateway's stamp", () => {
    expect(parseSyncTime("2026-09-23T22:15:00.000Z")?.toISOString()).toBe(
      "2026-09-23T22:15:00.000Z",
    );
    expect(
      parseSyncTime("2026-09-24T03:52:00.123456+00:00")?.toISOString(),
    ).toBe("2026-09-24T03:52:00.123Z");
    expect(parseSyncTime("2026-09-24T11:52:00+08:00")?.toISOString()).toBe(
      "2026-09-24T03:52:00.000Z",
    );
  });

  it("answers null for anything else", () => {
    for (const value of [
      null,
      undefined,
      "",
      "2026-09-24",
      "2026-09-24 03:52:00+00",
      "yesterday",
      "2026-13-40T99:00:00Z",
    ])
      expect(parseSyncTime(value)).toBeNull();
  });
});

describe("sync-schema module", () => {
  it("imports nothing but zod, so the server can share it", () => {
    const source = readFileSync(
      resolve(__dirname, "../../../../src/core/pick/sync-schema.ts"),
      "utf8",
    );
    const imports = [...source.matchAll(/from\s+"([^"]+)"/g)].map((m) => m[1]);
    expect(imports).toEqual(["zod"]);
  });
});
