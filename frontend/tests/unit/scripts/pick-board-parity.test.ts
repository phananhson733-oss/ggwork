/**
 * The field-by-field comparison behind scripts/pick-board-parity.ts (P4-3):
 * which RealShort-versus-mirror differences the whitelist lets through and
 * which it does not. Synthetic values only; nothing here opens a database.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

import { PLATFORMS } from "@/core/pick-board/request";

import {
  checkVersionMatch,
  compareSnapshot,
  formatReport,
  parseParityArgs,
  platformGap,
  readSnapshot,
} from "../../../scripts/pick-board-parity";
import {
  PAN_SCRUB_REPLACEMENT,
  POSTED_POST_KEYS,
  SIGNAL_PAYLOAD_KEYS,
  compareCase,
  settleScrub,
  type CompareContext,
} from "../../../scripts/pick-board-parity-compare";
import {
  SCRUBBED,
  SNAPSHOT_FORMAT,
  STRIPPED,
  type Json,
} from "../../../scripts/pick-board-snapshot-core.rs";

import {
  PAN_TEXT,
  PLAIN,
  failures,
  list,
  pickRow,
  rsRow,
  rules,
} from "./pick-board-parity-fixtures";

describe("compareCase: plain values", () => {
  it("finds nothing when the two sides agree", () => {
    const same = list([pickRow()]);
    expect(compareCase("pick", same, same, PLAIN)).toEqual({
      findings: [],
      charges: [],
    });
  });

  it("reports a changed value by path, and never prints RealShort's text", () => {
    const { findings } = compareCase(
      "pick",
      list([pickRow({ title: "原来的剧名" })]),
      list([pickRow({ title: "镜像的剧名" })]),
      PLAIN,
    );
    expect(findings).toHaveLength(1);
    const [finding] = findings;
    expect(finding?.rule).toBeNull();
    expect(finding?.path).toBe("page.rows[kalos-a].title");
    expect(finding?.rs).not.toContain("原来的剧名");
    expect(finding?.rs).toContain("5");
    expect(finding?.mirror).toContain("镜像的剧名");
  });

  it("does not whitelist clicks 0 against null (critique B16)", () => {
    for (const [rs, mirror] of [
      [0, null],
      [null, 0],
    ] as const) {
      const { findings } = compareCase(
        "rank.rs_clk",
        { result: { kind: "rows", rows: [{ id: "rs0001", clicks7: rs }] } },
        { result: { kind: "rows", rows: [{ id: "rs0001", clicks7: mirror }] } },
        PLAIN,
      );
      expect(failures(findings)).toHaveLength(1);
    }
  });

  it("names missing and extra rows of keyed lists", () => {
    const { findings } = compareCase(
      "pick",
      list([pickRow(), pickRow({ rowKey: "kalos-b" })]),
      list([pickRow(), pickRow({ rowKey: "kalos-c" })]),
      PLAIN,
    );
    const what = failures(findings).map((f) => `${f.path} ${f.what}`);
    expect(what.join("\n")).toContain("kalos-b");
    expect(what.join("\n")).toContain("kalos-c");
  });
});

describe("compareCase: deleted fields and the pan cell", () => {
  it("never prints a pan or money value, from either side", () => {
    const report = compareSnapshot({
      rsCases: [
        {
          id: "pick",
          result: list([
            rsRow({
              billUsd: STRIPPED,
              rs: { id: "rs1", billUsd30: STRIPPED },
            }),
          ]),
        },
      ],
      mirror: new Map([
        [
          "pick",
          {
            ok: true,
            result: list([
              pickRow({
                panUrl: "https://pan.example/s/9",
                panPw: "zz99",
                billUsd: 12.5,
                rs: { id: "rs1", billUsd30: 45.75, usd: 7.25 },
              }),
            ]),
          },
        ],
      ]),
      ctx: PLAIN,
    });
    expect(report.failures.map((f) => f.path).sort()).toEqual([
      "page.rows[kalos-a].billUsd",
      "page.rows[kalos-a].panPw",
      "page.rows[kalos-a].panUrl",
      "page.rows[kalos-a].rs.billUsd30",
      "page.rows[kalos-a].rs.usd",
    ]);
    for (const f of report.failures)
      expect([f.rs, f.mirror]).toEqual([undefined, undefined]);
    const text = formatReport(report).join("\n");
    expect(text).not.toMatch(/pan\.example|zz99|12\.5|45\.75|7\.25/);
  });

  it("keeps the ledger-only rules to the ledger", () => {
    // publishAt is dropped only from order-ledger rows; canonicalId and the
    // merged counts are extra only on ledger rows and totals.
    const rank = compareCase(
      "rank.rs_rr",
      {
        result: { rows: [{ id: "rs1", title: "t", publishAt: "2026-09-01" }] },
      },
      { result: { rows: [{ id: "rs1", title: "t", canonicalId: "rs1" }] } },
      PLAIN,
    );
    const evidence = compareCase(
      "row.reelshort1",
      { row: { id: "rs1", revenueCents: 1, publishAt: "2026-09-01" } },
      { row: { id: "rs1", revenueCents: 1 } },
      PLAIN,
    );
    const stats = compareCase(
      "x",
      { stats: { orders: 2 } },
      { stats: { orders: 2, mergedRows: 2 } },
      PLAIN,
    );
    const paths = [rank, evidence, stats].flatMap((c) =>
      failures(c.findings).map((f) => f.path),
    );
    expect(paths.sort()).toEqual([
      "result.rows[rs1].canonicalId",
      "result.rows[rs1].publishAt",
      "row.publishAt",
      "stats.mergedRows",
    ]);
  });

  it("lets RealShort's stripped pan columns and money fields through", () => {
    const rs = list([
      rsRow({ rs: { id: "rs1", billUsd: STRIPPED, billUsd30: STRIPPED } }),
    ]);
    const mirror = list([pickRow({ rs: { id: "rs1" } })]);
    const { findings } = compareCase("pick", rs, mirror, PLAIN);
    expect(failures(findings)).toEqual([]);
    expect(rules(findings)).toEqual(["deleted-field", "pan-cell"]);
  });

  it("still checks the cell itself: whether there is a pan link", () => {
    const { findings } = compareCase(
      "pick",
      list([rsRow({ hasPan: false })]),
      list([pickRow({ hasPan: true })]),
      PLAIN,
    );
    expect(failures(findings).map((f) => f.path)).toEqual([
      "page.rows[kalos-a].hasPan",
    ]);
  });

  it("does not let any other one-sided field through", () => {
    const { findings } = compareCase(
      "pick",
      list([rsRow({ note: "x" })]),
      list([pickRow({ extra: 1 })]),
      PLAIN,
    );
    expect(
      failures(findings)
        .map((f) => f.path)
        .sort(),
    ).toEqual(["page.rows[kalos-a].extra", "page.rows[kalos-a].note"]);
  });
});

describe("compareCase and settleScrub: pan scrub placeholders", () => {
  const signal = (h: Json): Json => ({
    kind: "kd",
    ord: 0,
    evidenceOn: null,
    rank: 1,
    grade: "",
    note: "",
    payload: { h },
  });
  const scrub = {
    "catalog_rows.title": 1,
    "catalog_signals.payload.h[*][*]": 1,
    "catalog_posted.posts[*].url": 1,
    "rs_rows.title": 1,
  };
  const ctx: CompareContext = { scrub, collations: null };

  it("lets a placeholder through on a meta.scrub path, charged once per cell", () => {
    const rs = list([rsRow({ title: SCRUBBED })]);
    const mirror = list([pickRow({ title: PAN_SCRUB_REPLACEMENT })]);
    const a = compareCase("pick", rs, mirror, ctx);
    const b = compareCase("pick.platform.kalos", rs, mirror, ctx);
    expect(failures([...a.findings, ...b.findings])).toEqual([]);
    expect(rules(a.findings)).toEqual(["pan-cell", "scrub"]);
    expect(a.charges.map((c) => c.key)).toEqual(["catalog_rows.title"]);
    const settled = settleScrub([...a.charges, ...b.charges], scrub);
    expect(settled.findings).toEqual([]);
    expect(settled.usage).toContainEqual({
      key: "catalog_rows.title",
      observed: 1,
      budget: 1,
    });
  });

  it("fails a cell RealShort's scrubber flagged but the mirror kept, printing neither", () => {
    const { findings, charges } = compareCase(
      "pick",
      list([rsRow({ title: SCRUBBED })]),
      list([pickRow({ title: PAN_TEXT })]),
      ctx,
    );
    expect(failures(findings).map((f) => f.path)).toEqual([
      "page.rows[kalos-a].title",
    ]);
    expect(JSON.stringify(findings)).not.toMatch(/pan\.example|zz99/);
    expect(charges).toEqual([]);
  });

  it("still charges a placeholder where the snapshot kept RealShort's text", () => {
    // The export scrubs its own normalized value (a whole tag list, say);
    // the loader's piece of it may not trip the scrubber on its own.
    const { findings, charges } = compareCase(
      "pick",
      list([rsRow({ title: "资源在群里" })]),
      list([pickRow({ title: PAN_SCRUB_REPLACEMENT })]),
      ctx,
    );
    expect(failures(findings)).toEqual([]);
    expect(charges.map((c) => c.key)).toEqual(["catalog_rows.title"]);
  });

  it("fails when more cells are scrubbed than meta.scrub counted", () => {
    const rs = list([
      rsRow({ title: SCRUBBED }),
      rsRow({ rowKey: "kalos-b", title: SCRUBBED }),
    ]);
    const mirror = list([
      pickRow({ title: PAN_SCRUB_REPLACEMENT }),
      pickRow({ rowKey: "kalos-b", title: PAN_SCRUB_REPLACEMENT }),
    ]);
    const { charges } = compareCase("pick", rs, mirror, ctx);
    const settled = settleScrub(charges, scrub);
    expect(settled.findings).toHaveLength(1);
    expect(settled.findings[0]?.what).toContain("2");
  });

  it("fails a placeholder on a path meta.scrub does not name", () => {
    const { findings } = compareCase(
      "pick",
      list([rsRow({ reoffNote: SCRUBBED })]),
      list([pickRow({ reoffNote: PAN_SCRUB_REPLACEMENT })]),
      ctx,
    );
    expect(failures(findings).map((f) => f.path)).toEqual([
      "page.rows[kalos-a].reoffNote",
    ]);
  });

  it("maps signal payloads, posts and ReelShort rows to their export paths", () => {
    const rsList = list([
      rsRow({ signals: [signal([["2026-09-01", 3, SCRUBBED]])] }),
      rsRow({
        rowKey: "reelshort-rs0001",
        platform: "reelshort",
        title: SCRUBBED,
        rs: { id: "rs0001", revenueCents: 1, title: SCRUBBED },
      }),
    ]);
    const mirrorList = list([
      pickRow({
        signals: [signal([["2026-09-01", 3, PAN_SCRUB_REPLACEMENT]])],
      }),
      pickRow({
        rowKey: "reelshort-rs0001",
        platform: "reelshort",
        title: PAN_SCRUB_REPLACEMENT,
        rs: { id: "rs0001", revenueCents: 1, title: PAN_SCRUB_REPLACEMENT },
      }),
    ]);
    const lists = compareCase("pick", rsList, mirrorList, ctx);
    const record = (url: string): Json => ({
      sd: "SD-1",
      feishuRecord: "r",
      title: "t",
      posts: [{ d: "2026-09-01", url }],
    });
    const posted = compareCase(
      "posted.record1",
      { record: record(SCRUBBED), links: {} },
      { record: record(PAN_SCRUB_REPLACEMENT), links: {} },
      ctx,
    );
    const charges = [...lists.charges, ...posted.charges];
    expect(failures([...lists.findings, ...posted.findings])).toEqual([]);
    expect([...new Set(charges.map((c) => c.key))].sort()).toEqual([
      "catalog_posted.posts[*].url",
      "catalog_signals.payload.h[*][*]",
      "rs_rows.title",
    ]);
    expect(settleScrub(charges, scrub).findings).toEqual([]);
  });
  it("charges a rank row's dayNote to the payload cell it was read from", () => {
    const h = (note: Json): Json => [["2026-09-01", 3, note]];
    const rankRow = (note: Json, base: Record<string, Json>): Json => ({
      ...base,
      signal: signal(h(note)),
      dayRank: 3,
      dayNote: note,
    });
    const ranked = compareCase(
      "rank.kd",
      { meta: {}, page: { rows: [rankRow(SCRUBBED, rsRow())] } },
      {
        meta: {},
        page: { rows: [rankRow(PAN_SCRUB_REPLACEMENT, pickRow())] },
      },
      ctx,
    );
    const listed = compareCase(
      "pick.basis.kd",
      list([rsRow({ signals: [signal(h(SCRUBBED))] })]),
      list([pickRow({ signals: [signal(h(PAN_SCRUB_REPLACEMENT))] })]),
      ctx,
    );
    expect(failures([...ranked.findings, ...listed.findings])).toEqual([]);
    const charges = [...ranked.charges, ...listed.charges];
    expect(new Set(charges.map((c) => c.key))).toEqual(
      new Set(["catalog_signals.payload.h[*][*]"]),
    );
    const one = { "catalog_signals.payload.h[*][*]": 1 };
    expect(settleScrub(charges, one).findings).toEqual([]);
  });
});

describe("compareCase: payload and posts keys", () => {
  it("lets through keys outside the export whitelist, not keys inside it", () => {
    const withPayload = (payload: Json): Json =>
      list([
        rsRow({
          signals: [{ kind: "kd", ord: 0, payload }],
        }),
      ]);
    const loose = compareCase(
      "pick",
      withPayload({ h: [1], zz: "x" }),
      list([
        pickRow({ signals: [{ kind: "kd", ord: 0, payload: { h: [1] } }] }),
      ]),
      PLAIN,
    );
    expect(failures(loose.findings)).toEqual([]);
    expect(rules(loose.findings)).toContain("payload-key");
    const strict = compareCase(
      "pick",
      withPayload({ h: [1], d: "2026-09-01" }),
      list([
        pickRow({ signals: [{ kind: "kd", ord: 0, payload: { h: [1] } }] }),
      ]),
      PLAIN,
    );
    expect(failures(strict.findings)).toHaveLength(1);
  });

  it("does the same for a posted record's posts", () => {
    const record = (post: Json): Json => ({
      record: { sd: "SD-1", feishuRecord: "r", posts: [post] },
    });
    const { findings } = compareCase(
      "posted.record1",
      record({ d: "2026-09-01", views: 3, internal: true }),
      record({ d: "2026-09-01", views: 3 }),
      PLAIN,
    );
    expect(failures(findings)).toEqual([]);
    expect(rules(findings)).toEqual(["payload-key"]);
  });

  it("uses the export's key lists (the workbench contract copies them)", () => {
    const contracts = readFileSync(
      path.resolve(
        __dirname,
        "../../../../customizations/pick-workbench/ggwork_pick/mirror/contracts.py",
      ),
      "utf8",
    );
    const tuple = (name: string) =>
      [
        ...(new RegExp(`^${name} = \\(([^)]*)\\)`, "m")
          .exec(contracts)?.[1]
          ?.matchAll(/"([^"]+)"/g) ?? []),
      ].map((m) => m[1]);
    expect(SIGNAL_PAYLOAD_KEYS).toEqual(tuple("SIGNAL_PAYLOAD_KEYS"));
    expect(POSTED_POST_KEYS).toEqual(tuple("POSTED_POST_KEYS"));
  });
});

type Bill = Record<string, Json>;

function rsBill(day: string, book: string, orders: number): Bill {
  return {
    billDate: day,
    bookId: book,
    title: `剧 ${book}`,
    locale: "en",
    publishAt: null,
    promotionType: "cps",
    promotionValue: STRIPPED,
    orderCnt: orders,
    revenueUsd: STRIPPED,
    sameDayClicks: 1,
  };
}

function mirrorBill(day: string, book: string, orders: number, n = 1): Bill {
  return {
    billDate: day,
    bookId: book,
    canonicalId: book,
    title: `剧 ${book}`,
    locale: "en",
    promotionType: "cps",
    orderCnt: orders,
    sourceRows: n,
    sameDayClicks: 1,
  };
}

function ledger(rows: Bill[], totals: Json): Json {
  return { meta: {}, result: { kind: "ledger", rows, totals, source: null } };
}

describe("compareCase: the order ledger", () => {
  const rsTotals = { usd: STRIPPED, matchedUsd: STRIPPED, orders: 6, rows: 3 };
  const mirrorTotals = {
    rows: 3,
    mergedRows: 2,
    orders: 6,
    mergedWithClicks: 2,
    rowsWithClicks: 3,
  };
  const rsRows = [
    rsBill("2026-09-22", "rs0001", 2),
    rsBill("2026-09-21", "rs0002", 1),
    rsBill("2026-09-22", "rs0001", 3),
  ];

  it("merges RealShort's raw rows the way the export does, in any order", () => {
    const { findings } = compareCase(
      "rank.rs_ledger",
      ledger(rsRows, rsTotals),
      ledger(
        [
          mirrorBill("2026-09-21", "rs0002", 1),
          mirrorBill("2026-09-22", "rs0001", 5, 2),
        ],
        mirrorTotals,
      ),
      PLAIN,
    );
    expect(failures(findings)).toEqual([]);
    expect(rules(findings)).toEqual([
      "bill-order-limit",
      "deleted-field",
      "ledger-merge",
    ]);
  });

  it("still compares the merged orders and source rows", () => {
    const { findings } = compareCase(
      "rank.rs_ledger",
      ledger(rsRows, rsTotals),
      ledger(
        [
          mirrorBill("2026-09-22", "rs0001", 4, 2),
          mirrorBill("2026-09-21", "rs0002", 1, 2),
        ],
        mirrorTotals,
      ),
      PLAIN,
    );
    expect(
      failures(findings)
        .map((f) => f.path)
        .sort(),
    ).toEqual([
      "result.rows[2026-09-21|rs0002|cps].sourceRows",
      "result.rows[2026-09-22|rs0001|cps].orderCnt",
    ]);
  });

  it("forgives only the boundary day of a list cut at its LIMIT", () => {
    const newer = Array.from(
      { length: 198 },
      (_, i) => `rs${String(i).padStart(4, "0")}`,
    );
    // RealShort's 200 raw rows end inside old1's group; the mirror's 200
    // merged rows have all of old1 and then old2.
    const rs = [
      ...newer.map((b) => rsBill("2026-09-22", b, 1)),
      rsBill("2026-09-21", "old1", 1),
      rsBill("2026-09-21", "old1", 1),
    ];
    const mirror = [
      ...newer.map((b) => mirrorBill("2026-09-22", b, 1)),
      mirrorBill("2026-09-21", "old1", 3, 3),
      mirrorBill("2026-09-21", "old2", 1),
    ];
    const cut = compareCase(
      "rank.rs_ledger",
      ledger(rs, {}),
      ledger(mirror, {}),
      PLAIN,
    );
    expect(failures(cut.findings)).toEqual([]);
    const missingNewer = compareCase(
      "rank.rs_ledger",
      ledger(rs, {}),
      ledger(mirror.slice(1), {}),
      PLAIN,
    );
    expect(failures(missingNewer.findings)).toHaveLength(1);
  });

  it("on the evidence page: RealShort's constant same-day 0 and its raw-row cut", () => {
    const rs = {
      bill: [rsBill("2026-09-22", "rs0001", 2)],
      billTruncated: true,
    };
    const mirror = {
      bill: [{ ...mirrorBill("2026-09-22", "rs0001", 2), sameDayClicks: 3 }],
      billTruncated: false,
    };
    const ok = compareCase(
      "row.reelshort1",
      {
        ...rs,
        bill: [{ ...rsBill("2026-09-22", "rs0001", 2), sameDayClicks: 0 }],
      },
      mirror,
      PLAIN,
    );
    expect(failures(ok.findings)).toEqual([]);
    const reversed = compareCase(
      "row.reelshort1",
      { ...rs, billTruncated: false },
      { ...mirror, billTruncated: true },
      PLAIN,
    );
    expect(failures(reversed.findings).map((f) => f.path)).toContain(
      "billTruncated",
    );
  });
});

describe("compareCase: renames, timestamps and collation", () => {
  it("lets the 分成 to 订单 rename through, nothing else", () => {
    const renamed = compareCase(
      "x",
      { label: "ReelShort 分成对账" },
      { label: "ReelShort 订单对账" },
      PLAIN,
    );
    expect(rules(renamed.findings)).toEqual(["rename"]);
    const other = compareCase(
      "x",
      { label: "ReelShort 分成对账" },
      { label: "ReelShort 订单明细" },
      PLAIN,
    );
    expect(failures(other.findings)).toHaveLength(1);
    // Only in label fields: data such as a title is compared as it is.
    const tab = compareCase(
      "x",
      { tabLabel: "分成对账" },
      { tabLabel: "订单对账" },
      PLAIN,
    );
    expect(rules(tab.findings)).toEqual(["rename"]);
    const title = compareCase("x", { title: "分成" }, { title: "订单" }, PLAIN);
    expect(failures(title.findings)).toHaveLength(1);
  });

  it("compares timestamptz text to the millisecond, in time fields only", () => {
    const pgText = "2026-09-20 12:00:00.123456+00";
    const iso = "2026-09-20T12:00:00.123Z";
    for (const key of ["syncedAt", "detail_synced_at"]) {
      const same = compareCase("x", { [key]: pgText }, { [key]: iso }, PLAIN);
      expect(rules(same.findings)).toEqual(["timestamptz-ms"]);
    }
    const off = compareCase(
      "x",
      { syncedAt: pgText },
      { syncedAt: "2026-09-20T12:00:00.124Z" },
      PLAIN,
    );
    expect(failures(off.findings)).toHaveLength(1);
    const note = compareCase("x", { note: pgText }, { note: iso }, PLAIN);
    expect(failures(note.findings)).toHaveLength(1);
  });

  it("never forgives the bill_rank order (plan P4-3)", () => {
    const rows = [
      { id: "rs0001", title: "Beta" },
      { id: "rs0002", title: "alpha" },
    ];
    const { findings } = compareCase(
      "rank.rs_bill",
      { result: { kind: "rows", rows } },
      { result: { kind: "rows", rows: [...rows].reverse() } },
      { scrub: {}, collations: { rs: "C", mirror: "en_US.UTF-8" } },
    );
    expect(failures(findings)).toHaveLength(1);
  });
});

describe("compareSnapshot and formatReport", () => {
  const rsCases = [
    { id: "pick", query: "", result: list([rsRow({ title: PAN_TEXT })]) },
    { id: "all", query: "tab=all", result: list([rsRow()]) },
    { id: "posted", query: "tab=posted", result: { list: { rows: [] } } },
  ];

  it("fails missing and broken mirror cases and summarizes the rest", () => {
    const report = compareSnapshot({
      rsCases,
      mirror: new Map([
        [
          "pick",
          {
            ok: true,
            result: list([pickRow({ title: PAN_SCRUB_REPLACEMENT })]),
          },
        ],
        ["all", { ok: false, error: "MirrorBusy" }],
      ]),
      ctx: { scrub: { "catalog_rows.title": 1 }, collations: null },
    });
    expect(report.caseCount).toBe(3);
    expect(report.failures.map((f) => f.caseId).sort()).toEqual([
      "all",
      "posted",
    ]);
    expect(report.whitelisted.map((w) => w.rule).sort()).toEqual([
      "pan-cell",
      "scrub",
    ]);
    const text = formatReport(report).join("\n");
    expect(text).toContain("MirrorBusy");
    expect(text).toContain("catalog_rows.title");
    expect(text).not.toContain("pan.example");
    expect(text).not.toContain("zz99");
  });

  it("says so plainly when nothing is off the whitelist", () => {
    const report = compareSnapshot({
      rsCases: [rsCases[1]!],
      mirror: new Map([["all", { ok: true, result: list([pickRow()]) }]]),
      ctx: PLAIN,
    });
    expect(report.failures).toEqual([]);
    expect(formatReport(report).join("\n")).toContain("白名单外差异：0");
  });
});

describe("platformGap", () => {
  it("lists the version's theater keys the local PLATFORMS lacks, in version order", () => {
    expect(
      platformGap({ zeta: {}, kalos: {}, alpha: {}, reelshort: {} }, PLATFORMS),
    ).toEqual(["zeta", "alpha"]);
    expect(platformGap({ kalos: {} }, PLATFORMS)).toEqual([]);
  });
});

describe("the parity runner's inputs", () => {
  it("parses --v and --snapshot", () => {
    expect(parseParityArgs(["--v", "12", "--snapshot", "/tmp/s.json"])).toEqual(
      {
        ok: true,
        args: { v: 12, snapshot: "/tmp/s.json" },
      },
    );
    for (const argv of [
      ["--snapshot", "x"],
      ["--v", "0", "--snapshot", "x"],
      ["--v", "1000000", "--snapshot", "x"],
      ["--v", "12"],
      ["--v", "12", "--snapshot", "x", "--more"],
    ])
      expect(parseParityArgs(argv).ok).toBe(false);
  });

  const doc = {
    format: SNAPSHOT_FORMAT,
    asOf: "2026-09-23T03:40:00.000Z",
    fingerprint: "c".repeat(64),
    sourceRevision: "0123456789abcdef0123456789abcdef01234567",
    collation: "C.UTF-8",
    rowLimit: 50,
    startedAt: "2026-09-23T04:00:00.000Z",
    finishedAt: "2026-09-23T04:02:00.000Z",
    elapsedMs: 120000,
    cases: [{ id: "pick", query: "", ms: 12, result: { page: { rows: [] } } }],
  };

  it("reads a snapshot of the right format, and nothing else", () => {
    const ok = readSnapshot(JSON.stringify(doc));
    expect(ok.ok).toBe(true);
    for (const bad of [
      "{",
      JSON.stringify({ ...doc, format: "other" }),
      JSON.stringify({ ...doc, fingerprint: "xyz" }),
      JSON.stringify({ ...doc, cases: [{ id: "pick" }] }),
      JSON.stringify({ ...doc, cases: [doc.cases[0], doc.cases[0]] }),
    ])
      expect(readSnapshot(bad).ok).toBe(false);
  });

  it("refuses a snapshot of another version, moment or source revision", () => {
    const version = {
      versionId: 12,
      asOf: "2026-09-23 03:40:00+00",
      fingerprint: doc.fingerprint,
      sourceRevision: doc.sourceRevision,
    };
    const read = readSnapshot(JSON.stringify(doc));
    if (!read.ok) throw new Error(read.error);
    expect(checkVersionMatch(read.doc, version)).toBeNull();
    expect(
      checkVersionMatch(read.doc, { ...version, fingerprint: "d".repeat(64) }),
    ).toContain("fingerprint");
    expect(
      checkVersionMatch(read.doc, {
        ...version,
        asOf: "2026-09-23 03:41:00+00",
      }),
    ).toContain("as_of");
    expect(
      checkVersionMatch(read.doc, { ...version, sourceRevision: null }),
    ).toContain("sourceRevision");
  });
});
