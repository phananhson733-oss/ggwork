import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "@rstest/core";

import { mirrorStatusSchema, syncStatusSchema } from "@/core/pick/sync-schema";

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

  it("keeps a mirror the gateway could not read, as its error class", () => {
    // routes.mirror_view: a failed read of pick_mirror answers { error: <class> }, the rest of /sync still 200.
    const parsed = syncStatusSchema.parse(
      status({ mirror: { error: "OperationalError" } }),
    );
    expect(parsed.mirror).toEqual({ error: "OperationalError" });
    expect(parsed.runs).toEqual([RUN]);
  });

  it("reads an error object with more keys, or a long text, as absent", () => {
    for (const broken of [
      { error: "OperationalError", enabled: true },
      { error: "E".repeat(201) },
      { error: 42 },
    ]) {
      expect(syncStatusSchema.parse(status({ mirror: broken })).mirror).toBe(
        undefined,
      );
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
