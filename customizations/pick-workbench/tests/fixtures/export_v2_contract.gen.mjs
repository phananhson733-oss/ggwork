// Regenerates export_v2_contract.json from a RealShort checkout (read only). Run from that checkout, so tsx resolves its "@/" paths:
//   cd <realshort> && node --import tsx <this file> "$PWD" > <workbench>/customizations/pick-workbench/tests/fixtures/export_v2_contract.json
// Record the RealShort commit in the output's "commit" (the argument after the checkout path, default: unknown).
// The output's manifest.meta.rules is RealShort's real buildRulesMeta(): its platform-rule and posted-pool Feishu links are
// the business links feed v2 already exports, not secrets; manifestFixture's SECRET, token and SENTINELPV are dropped by
// pickManifest and never reach the output.
import path from "node:path";

const root = process.argv[2];
const commit = process.argv[3] ?? "unknown";
const map = await import(path.join(root, "src/lib/pick/export-v2-map.ts"));
const request = await import(path.join(root, "src/lib/pick/request.ts"));
const metrics = await import(path.join(root, "src/lib/observe/metrics.ts"));

const PAN = "资源 https://pan.baidu.com/s/1AbCdEf 提取码：ab12";
const DEFAULTS = { text: "", day: "2026-09-01", int: 0, float: 0, bool: false, ts: "2026-09-01T00:00:00.000Z", "text[]": [] };

/** One raw row with every column of the resource: plain values that toExportRow leaves as they are, then the overrides. */
function rawRow(resource, overrides) {
  const base = Object.fromEntries(
    map.RESOURCE_SPECS[resource].columns.map((c) => [c.name, c.type === "json" ? (c.name === "posts" ? [] : {}) : DEFAULTS[c.type]]),
  );
  return { ...base, ...overrides };
}

const V2_ROWS = {
  catalog_rows: rawRow("catalog_rows", {
    row_key: "goodshort-K10JEicNmxOWhQPwdg3zdw==", title: PAN, tags: PAN, reoff_note: PAN, title_key: PAN, in_site_ids: [PAN],
    listed_on: PAN, title_cn: "Code Name Reaper II", episodes: 80, pay_start: null, off_on: null,
  }),
  catalog_signals: rawRow("catalog_signals", {
    row_key: PAN, kind: "kd", ord: 2, evidence_on: PAN, rank: null, note: PAN,
    payload: { d: PAN, h: [["2026-09-01", 3, PAN], ["2026-09-02", 4, "提取码："]], best: 1, qy: 12, pid: "p-1" },
  }),
  catalog_posted: rawRow("catalog_posted", {
    sd: PAN, feishu_record: PAN, title: "secret code that opens", who: [PAN, "运营"], accounts: [PAN], row_keys: [PAN], drama_ids: [PAN],
    last_post_on: PAN, online_on: null, note: PAN,
    posts: [{ d: "2026-09-01", acct: "a", url: PAN, md: { x: PAN }, views: 10, how: "剧名", pid: "p1" }, { note: PAN, likes: null }],
  }),
  catalog_accounts: rawRow("catalog_accounts", { id: PAN, name: PAN, url: PAN, fans: null, as_of: PAN }),
  rs_rows: rawRow("rs_rows", {
    row_key: "reelshort-abc123", drama_id: PAN, slug: PAN, title: PAN, description: PAN, tag_list: ["Romance", PAN],
    rr: 1.5, rr7: null, bill_rank: 3, metrics_valid: null, publish_at: null,
  }),
  rs_ids: rawRow("rs_ids", { id: PAN, canonical_id: null, slug: PAN, title: PAN, locale: PAN }),
  rs_clicks14: rawRow("rs_clicks14", { drama_id: PAN, day: PAN, human: 5, bot: 1 }),
  rs_bill_orders: rawRow("rs_bill_orders", { bill_date: PAN, book_id: PAN, promotion_type: PAN, canonical_id: PAN, book_title: PAN, order_cnt: 2 }),
  rs_series_day: rawRow("rs_series_day", { drama_id: PAN, revenue_cents: 12.5, promoters_cnt: 3 }),
};

const V1_ROW = {
  source: "realshort-pick", source_id: PAN, language: "英语", title: PAN, theater: "ShortMax", tags: [PAN, "甜宠"], listed_at: PAN,
  availability: "unknown",
  signals: [{ kind: "kd", label: PAN, source_ref: PAN, observed_at: PAN, rank: null, grade: "", note: PAN }],
  channel_rules: { youtube: "unknown" }, detail_url: PAN,
  posted: { matched: true, records: [PAN], post_count: 1, sched_count: 0, last_post_on: PAN, accounts: [PAN] },
};

const FACETS = { platforms: { reelshort: 3, kalos: 2 }, langs: [{ lang: "英语", n: 5 }], bases: { kd: 1, clk: 2 }, posted: { pool: 1, yes: 2, no: 3 } };

/** tests/pick-export-v2.test.ts manifestFixture() at 816ca2e, verbatim: extra and forbidden keys that pickManifest drops. */
function manifestFixture() {
  return {
    version: map.EXPORT_VERSION,
    asOf: "2026-09-23T10:15:00.000Z",
    fingerprint: "a".repeat(64),
    sourceRevision: null,
    counts: Object.fromEntries(map.ROW_RESOURCES.filter((r) => r !== "rs_series_day").map((r) => [r, 1])),
    latestSnapshot: "2026-09-23",
    snapshotDays: [{ day: "2026-09-23", rows: 30000 }],
    meta: {
      freshness: { importedAt: new Date("2026-09-22T03:10:05Z"), rows: 41661, withSignal: 3000, signals: 3500, posted: 180, rsCanonical: 32000, rsCandidates: 900, rsSyncedAt: null },
      rsCounts: { all: 1, cand: 1, growthD1: 1, growthD7: 1, growthDp1: 1, growthDp7: 1, pc: 1, clk: 1, gsc: 1, bill: 1, ledger: 2 },
      growthBaseline: {
        1: { baselineDay: "2026-09-22", baselineSnapshot: "verified", earliestVerifiedOn: "2026-09-10" },
        7: { baselineDay: "2026-09-16", baselineSnapshot: "verified", earliestVerifiedOn: "2026-09-10" },
      },
      sources: {
        bill: {
          source: "bill",
          status: "success",
          attemptedAt: "2026-09-23T00:00:00.000Z",
          completedAt: "2026-09-23T00:01:00.000Z",
          details: { startDate: "2026-08-20", endDate: "2026-09-22", ratio: 50, rows: 12, token: "SECRET", promotionValue: "SENTINELPV" },
        },
        mystery: { source: "mystery", status: "success", attemptedAt: "x", completedAt: null, details: {} },
      },
      rules: map.buildRulesMeta(),
      control: {
        facetsPick: FACETS,
        facetsAll: FACETS,
        rankCounts: { kd: 10, rs_bill: 4, rs_ledger: 2 },
        postedStats: { total: 180, pubCount: 90, postsSum: 300, viewsSum: 12345, metricAt: "2026-09-20", importedAt: new Date("2026-09-22T03:10:05Z"), accountCount: 20 },
        postedStates: { pub: 90, sched: 10, none: 70, nomatch: 10 },
        ledger: { rows: 2, orders: 5, usd: 99.5, matchedUsd: 10 },
        billTotals: { usd: 99.5 },
      },
      scrub: { "catalog_signals.payload.h[*][*]": 2, "rs_rows.bill_usd": 1 },
      warnings: [{ code: "catalog_import_incomplete", source: "pick_catalog", status: "failed", attemptedAt: "2026-09-23T00:00:00.000Z" }],
      debug: { usd: 1 },
    },
    rawTotals: { revenue_usd: 987654.32 },
  };
}

function panManifest() {
  const m = manifestFixture();
  const control = { ...m.meta.control, facetsPick: { ...FACETS, langs: [{ lang: PAN, n: 1 }] } };
  return { ...m, meta: { ...m.meta, control, warnings: [{ code: PAN, source: "pick_catalog", status: "failed", attemptedAt: PAN }] } };
}

const v2 = Object.fromEntries(
  Object.entries(V2_ROWS).map(([resource, input]) => {
    const out = map.toExportRow(resource, input);
    return [resource, { input, output: out.row, hits: out.hits }];
  }),
);
const panMeta = map.finalizeManifest(panManifest());

const out = {
  about: [
    "Generated from RealShort feed v2 (src/lib/pick/export-v2-map.ts) by export_v2_contract.gen.mjs; do not edit by hand.",
    "manifest is finalizeManifest(manifestFixture()) with manifestFixture copied from tests/pick-export-v2.test.ts; v2_rows[r].hits is toExportRow(r, input).hits; v1_row.hits is scrubAllText(v1_row.input).hits (feed-map.ts toFeedRow); manifest_meta_scan.hits is finalizeManifest(...).hits.",
  ],
  commit,
  resource_specs: map.RESOURCE_SPECS,
  signal_payload_keys: map.SIGNAL_PAYLOAD_KEYS,
  posted_post_keys: map.POSTED_POST_KEYS,
  forbidden_name: { source: map.FORBIDDEN_NAME.source, flags: map.FORBIDDEN_NAME.flags },
  scrub_exempt_keys: [...map.SCRUB_EXEMPT_KEYS],
  scrub_exempt_paths: [...map.SCRUB_EXEMPT_PATHS],
  replacement: map.PAN_SCRUB_REPLACEMENT,
  enums: {
    platforms: request.PLATFORMS, bases: request.BASES, ranks: request.RANKS, rs_ranks: request.RS_RANKS, rs_sorts: metrics.SORTS,
    export_sources: map.EXPORT_SOURCES, source_detail_keys: map.SOURCE_DETAIL_KEYS,
  },
  manifest_shape: map.MANIFEST_SHAPE,
  manifest: map.finalizeManifest(manifestFixture()).manifest,
  v2_rows: v2,
  v1_row: { input: V1_ROW, hits: map.scrubAllText(V1_ROW).hits },
  manifest_meta_scan: { input: pickMeta(panManifest()), hits: panMeta.hits },
};

/** The picked meta that finalizeManifest scans (before its scrub pass), so the workbench scans the same leaves. */
function pickMeta(value) {
  return map.pickManifest(value).meta;
}

process.stdout.write(JSON.stringify(out, null, 1) + "\n");
