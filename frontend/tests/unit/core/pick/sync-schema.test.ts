import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "@rstest/core";

import {
  isMirrorReadError,
  isObsReadError,
  mirrorStatusSchema,
  obsSyncStatusSchema,
  parseSyncTime,
  syncStatusSchema,
} from "@/core/pick/sync-schema";

import answers from "./fixtures/backend-sync.json";

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
    as_of: "2026-09-24T03:38:00.000Z",
    latest_snapshot: "2026-09-23",
    published_at: "2026-09-24T03:52:00.123456+00:00",
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

const OBS_CHANNEL = {
  live_set_id: "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c",
  live_published_at: "2026-09-25T01:52:10.000000+00:00",
  latest_set_id: "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c",
  latest_published_at: "2026-09-25T01:52:10.000000+00:00",
  latest_mode: "live",
  last_run_at: "2026-09-24T20:30:05.000000+00:00",
  banners: [],
};

const OBS = {
  checked_at: "2026-09-25T04:10:00.000000+00:00",
  channels: [
    { channel: "trends", ...OBS_CHANNEL },
    {
      channel: "gsc",
      ...OBS_CHANNEL,
      live_set_id: null,
      live_published_at: null,
      latest_mode: "shadow",
      banners: [{ code: "shadow_mode", level: "info" }],
    },
  ],
};

/** Production today: 0007 is there, the crons are not (TR-25). */
const OBS_EMPTY = {
  checked_at: "2026-09-29T08:00:00.000000+00:00",
  channels: ["trends", "gsc"].map((channel) => ({
    channel,
    live_set_id: null,
    live_published_at: null,
    latest_set_id: null,
    latest_published_at: null,
    latest_mode: null,
    last_run_at: null,
    banners: [],
  })),
};

// /sync's obs key (plan TR-25, D10; contract section 13): declared
// .nullable().optional().catch(undefined), so it never breaks the rest.
describe("syncStatusSchema's obs key", () => {
  it("keeps the radar's status, empty or not", () => {
    for (const obs of [OBS, OBS_EMPTY]) {
      const parsed = syncStatusSchema.parse(status({ obs }));
      expect(parsed.obs).toEqual(obs);
      expect(parsed.runs).toEqual([RUN]);
    }
  });

  it("is absent before TR-25 and may be null", () => {
    expect(syncStatusSchema.parse(status()).obs).toBeUndefined();
    expect(syncStatusSchema.parse(status({ obs: null })).obs).toBeNull();
  });

  it("keeps a failed read as its class name", () => {
    const parsed = syncStatusSchema.parse(
      status({ obs: { error: "OperationalError" } }),
    );
    expect(parsed.obs).toEqual({ error: "OperationalError" });
    expect(isObsReadError(parsed.obs)).toBe(true);
    expect(isObsReadError(obsSyncStatusSchema.parse(OBS))).toBe(false);
    expect(isObsReadError(null)).toBe(false);
    expect(isObsReadError(undefined)).toBe(false);
  });

  it("reads a malformed obs as absent; the rest of /sync still parses", () => {
    const [trends, gsc] = OBS.channels;
    for (const broken of [
      { ...OBS, channels: [gsc, trends] },
      { ...OBS, channels: [trends] },
      {
        ...OBS,
        channels: [
          trends,
          { ...gsc, banners: [{ code: "run_missed", level: "loud" }] },
        ],
      },
      { ...OBS, channels: [trends, { ...gsc, latest_mode: "draft" }] },
      { ...OBS, channels: [trends, { ...gsc, live_set_id: 7 }] },
      { ...OBS, checked_at: 42 },
      { error: "value 'secret' is wrong" },
      { error: "RuntimeError", detail: "x" },
      "obs",
    ]) {
      const parsed = syncStatusSchema.parse(
        status({ mirror: null, obs: broken }),
      );
      expect(parsed.obs).toBeUndefined();
      expect(parsed.runs).toEqual([RUN]);
      expect(parsed.mirror).toBeNull();
    }
  });

  it("keeps a status code this page does not know yet, at the gateway's level", () => {
    // A newer gateway may add a code; the banner still shows, worded generically (obs-status.ts).
    const [trends, gsc] = OBS.channels;
    const obs = {
      ...OBS,
      channels: [
        trends,
        { ...gsc, banners: [{ code: "future_code", level: "red" }] },
      ],
    };
    expect(obsSyncStatusSchema.parse(obs)).toEqual(obs);
    const odd = { code: "Not A Code!", level: "red" };
    const refused = { ...OBS, channels: [trends, { ...gsc, banners: [odd] }] };
    expect(obsSyncStatusSchema.safeParse(refused).success).toBe(false);
  });
});

// Generated by customizations/pick-workbench/tests/mirror/test_mirror_sync_status.py
// from real /sync answers (P2 final review seams-3).
describe("the gateway's /sync answers (backend-sync.json)", () => {
  const published = answers.every_field_set.mirror;

  it("parses every answer and keeps each mirror field it sends", () => {
    for (const answer of Object.values(answers)) {
      const parsed = syncStatusSchema.parse(answer);
      expect(parsed.mirror).toEqual(answer.mirror);
      expect(parsed.obs).toEqual(answer.obs);
      expect(parsed.runs).toHaveLength(answer.runs.length);
    }
    expect(Object.keys(published).sort()).toEqual(
      Object.keys(mirrorStatusSchema.shape).sort(),
    );
  });

  it("carries the obs key in every answer (TR-25)", () => {
    expect(isObsReadError(answers.read_error.obs)).toBe(true);
    for (const answer of [answers.before_any_version, answers.sqlite])
      for (const channel of obsSyncStatusSchema.parse(answer.obs).channels)
        expect(channel).toMatchObject({
          live_set_id: null,
          last_run_at: null,
          banners: [],
        });
    const observed = obsSyncStatusSchema.parse(answers.every_field_set.obs);
    const levels = observed.channels.flatMap((c) =>
      c.banners.map((b) => b.level),
    );
    expect(new Set(levels)).toEqual(new Set(["red", "warn", "info"]));
    for (const channel of observed.channels)
      for (const moment of [
        channel.live_published_at,
        channel.latest_published_at,
        channel.last_run_at,
      ])
        expect(parseSyncTime(moment)).not.toBeNull();
  });

  it("tells the four answers apart", () => {
    expect(answers.before_any_version.mirror.current).toBeNull();
    expect(isMirrorReadError(answers.read_error.mirror)).toBe(true);
    expect(syncStatusSchema.parse(answers.sqlite).mirror).toBeNull();
    const parsed = syncStatusSchema.parse(answers.every_field_set).mirror;
    expect(isMirrorReadError(parsed)).toBe(false);
    for (const value of Object.values(published)) expect(value).not.toBeNull();
  });

  it("reads every moment in both of its notations", () => {
    expect(published.current.as_of).toMatch(/T\d{2}:\d{2}:00\.000Z$/);
    expect(published.current.published_at).toMatch(/\.\d{6}\+00:00$/);
    const run = answers.every_field_set.runs[0];
    const current = answers.every_field_set.current;
    const moments = [
      published.current.as_of,
      published.current.published_at,
      published.last_failure_at,
      published.lock_stuck.since,
      published.shared_source_as_of,
      current.source_as_of,
      current.published_at,
      run?.started_at,
      run?.finished_at,
      run?.source_as_of,
    ];
    for (const moment of moments) expect(parseSyncTime(moment)).not.toBeNull();
  });

  it("sends days as plain dates, which are not moments", () => {
    const days = [
      published.current.latest_snapshot,
      published.series_through,
      published.trimmed_before,
    ];
    for (const day of days) {
      expect(day).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(parseSyncTime(day)).toBeNull();
    }
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
