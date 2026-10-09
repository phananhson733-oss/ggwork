import assert from "node:assert/strict";
import test from "node:test";

import { EXPORT_VERSION, RESOURCE_SPECS, ROW_RESOURCES, type CursorKey, type RowResource } from "../src/lib/pick/export-v2-map";
import {
  MAX_CURSOR_LENGTH,
  PAGE_BYTE_BUDGET,
  ROW_HARD_LIMIT,
  cutPageByBytes,
  decodeCursor,
  encodeCursor,
  parseAsOf,
  parseExportQuery,
  parseFingerprint,
  type PageEntry,
} from "../src/lib/pick/export-v2-page";

/**
 * feed v2（pick-export-v2）路由用的纯函数合同（方案 4.1、4.4、4.8，P1-2）：查询参数、keyset 游标、按字节截页。
 * 列白名单、清洗与 manifest 的合同在 tests/pick-export-v2.test.ts。
 */

/* ---------- 参数 ---------- */

const NOW = new Date("2026-09-23T10:17:42.123Z");

test("as_of：精确到分钟的 UTC 时间，不晚于 now、不早于 now − 30 分钟", () => {
  const iso = (s: string, now = NOW) => parseAsOf(s, now)?.toISOString() ?? null;
  for (const s of ["2026-09-23T10:15Z", "2026-09-23T10:15:00Z", "2026-09-23T10:15:00.000Z"]) assert.equal(iso(s), "2026-09-23T10:15:00.000Z", s);
  assert.equal(iso("2026-09-23T09:48Z"), "2026-09-23T09:48:00.000Z");
  assert.equal(iso("2026-09-23T10:17Z"), "2026-09-23T10:17:00.000Z");
  const edge = new Date("2026-09-23T10:17:00.000Z");
  assert.equal(iso("2026-09-23T09:47Z", edge), "2026-09-23T09:47:00.000Z", "恰好 30 分钟前");
  assert.equal(iso("2026-09-23T10:17Z", edge), "2026-09-23T10:17:00.000Z", "恰好 now");
  for (const bad of [
    "2026-09-23T09:47Z",
    "2026-09-23T10:18Z",
    "2026-09-23T10:15:30Z",
    "2026-09-23T10:15:00.500Z",
    "2026-09-23T10:15",
    "2026-09-23T10:15+00:00",
    "2026-09-23 10:15Z",
    "2026-09-23T24:00Z",
    "2026-02-30T10:15Z",
    "2026-09-23",
    "",
  ])
    assert.equal(iso(bad), null, bad);
  assert.equal(parseAsOf(null, NOW), null);
});

test("fp 是 64 位小写十六进制", () => {
  assert.equal(parseFingerprint("a".repeat(64)), "a".repeat(64));
  for (const bad of ["A".repeat(64), "a".repeat(63), "a".repeat(65), "g".repeat(64), "", null]) assert.equal(parseFingerprint(bad), null, String(bad));
});

const FP = "b".repeat(64);
const q = (resource: string, s: string, now = NOW) => parseExportQuery(resource, new URLSearchParams(s), now);

test("参数：manifest 只收 as_of；其它资源必须带 fp，limit 缺省取该资源上限、越界拒绝", () => {
  const m = q("manifest", "as_of=2026-09-23T10:15Z");
  assert.ok(m.ok);
  assert.deepEqual(m.ok && { ...m.query, asOf: m.query.asOf.toISOString() }, { resource: "manifest", asOf: "2026-09-23T10:15:00.000Z", fp: null, cursor: null, limit: 1, day: null });
  for (const bad of ["", "as_of=2026-09-23T10:15Z&fp=" + FP, "as_of=2026-09-23T10:15Z&limit=1", "as_of=2026-09-23T10:18Z"]) assert.equal(q("manifest", bad).ok, false, bad);
  const r = q("catalog_rows", `as_of=2026-09-23T10:15Z&fp=${FP}`);
  assert.ok(r.ok && r.query.limit === 5000 && r.query.cursor === null && r.query.fp === FP);
  for (const res of ROW_RESOURCES) {
    const extra = res === "rs_series_day" ? "&day=2026-09-20" : "";
    const max = RESOURCE_SPECS[res].maxLimit;
    const ok = q(res, `as_of=2026-09-23T10:15Z&fp=${FP}&limit=${max}${extra}`);
    assert.ok(ok.ok && ok.query.limit === max, res);
    for (const lim of ["0", String(max + 1), "-1", "1e3", "abc", ""]) assert.equal(q(res, `as_of=2026-09-23T10:15Z&fp=${FP}&limit=${lim}${extra}`).ok, false, `${res} limit=${lim}`);
    assert.equal(q(res, `as_of=2026-09-23T10:15Z${extra}`).ok, false, `${res} 缺 fp`);
  }
});

test("参数：未知资源、未知参数、重复参数、坏游标一律拒绝，拒绝原因里不回显参数值", () => {
  const base = `as_of=2026-09-23T10:15Z&fp=${FP}`;
  const cases: [string, string, string][] = [
    ["nope", base, "resource"],
    ["catalog_rows", base + "&x=SECRETVALUE", "unknown_param"],
    ["catalog_rows", base + "&fp=" + FP, "duplicate_param"],
    ["catalog_rows", base + "&cursor=SECRETVALUE", "cursor"],
    ["catalog_rows", base + "&cursor=", "cursor"],
    ["catalog_rows", base + "&day=2026-09-20", "unknown_param"],
    ["catalog_rows", `as_of=bad&fp=${FP}`, "as_of"],
  ];
  for (const [res, s, reason] of cases) {
    const r = q(res, s);
    assert.deepEqual(r, { ok: false, reason }, `${res}?${s}`);
  }
  const other = encodeCursor("catalog_signals", ["kalos-x", "kd", 1]);
  assert.deepEqual(q("catalog_rows", `${base}&cursor=${other}`), { ok: false, reason: "cursor" }, "别的资源的游标");
  const mine = encodeCursor("catalog_rows", ["kalos-x"]);
  const ok = q("catalog_rows", `${base}&cursor=${mine}`);
  assert.ok(ok.ok && JSON.stringify(ok.query.cursor) === '["kalos-x"]');
});

test("rs_series_day 必须带 day：日历上真有的那天，落在 as_of 当天往前 92 天之内（共 93 天）", () => {
  const base = `as_of=2026-09-23T10:15Z&fp=${FP}`;
  for (const ok of ["2026-09-23", "2026-06-23", "2026-09-01"]) {
    const r = q("rs_series_day", `${base}&day=${ok}`);
    assert.ok(r.ok && r.query.day === ok, ok);
  }
  for (const bad of ["", "2026-06-22", "2026-09-24", "2026-02-30", "2026-13-01", "2026-00-10", "2026-09-00", "2026-08-32", "20260920", "2026-9-20"]) assert.deepEqual(q("rs_series_day", `${base}&day=${bad}`), { ok: false, reason: "day" }, bad);
  assert.deepEqual(q("rs_series_day", base), { ok: false, reason: "day" });
});

/* ---------- 游标 ---------- */

const KEY_SAMPLES: Record<RowResource, CursorKey> = {
  catalog_rows: ["goodshort-K10JEicNmxOWhQPwdg3zdw=="],
  catalog_signals: ["kalos-x", "kd", 2],
  catalog_posted: ["SD-000001"],
  catalog_accounts: ["acc-1"],
  rs_rows: ["6891a0c2"],
  rs_ids: ["691439b62925e01c310e6d01"],
  rs_clicks14: ["6891a0c2", "2026-09-20"],
  rs_bill_orders: ["2026-09-20", "691439b62925e01c310e6d01", ""],
  rs_series_day: ["6891a0c2"],
};

test("cursor：按资源主键编码成不透明的 base64url，往返不变（含空串主键、中文与补充平面字符）", () => {
  for (const r of ROW_RESOURCES) {
    const c = encodeCursor(r, KEY_SAMPLES[r]);
    assert.match(c, /^[A-Za-z0-9_-]+$/, r);
    assert.deepEqual(decodeCursor(r, c), KEY_SAMPLES[r], r);
  }
  const odd: CursorKey = ["短剧-" + String.fromCodePoint(0x1f600) + "-x"];
  assert.deepEqual(decodeCursor("catalog_rows", encodeCursor("catalog_rows", odd)), odd);
});

test("cursor：别的资源的、改过的、非规范编码的、形状或类型不对的、超长的一律拒绝", () => {
  const c = encodeCursor("catalog_signals", ["kalos-x", "kd", 1]);
  assert.equal(decodeCursor("catalog_rows", c), null);
  const raw = (v: unknown) => Buffer.from(JSON.stringify(v), "utf8").toString("base64url");
  const bad = [
    c + "A",
    c.slice(0, -1),
    c + "=",
    c.slice(0, 4) + "+" + c.slice(5),
    c.slice(0, 4) + "/" + c.slice(5),
    "",
    "!!!",
    raw({ r: "catalog_signals", k: ["kalos-x", "kd", "1"] }),
    raw({ r: "catalog_signals", k: ["kalos-x", "kd", 1.5] }),
    raw({ r: "catalog_signals", k: ["kalos-x", "kd"] }),
    raw({ r: "catalog_signals", k: ["kalos-x", "kd", 1, 2] }),
    raw({ r: "catalog_signals", k: ["kalos-x", null, 1] }),
    raw({ k: ["kalos-x", "kd", 1], r: "catalog_signals" }),
    raw({ r: "catalog_signals", k: ["kalos-x", "kd", 1], x: 1 }),
    raw(["catalog_signals", "kalos-x", "kd", 1]),
    Buffer.from("not json").toString("base64url"),
    "A".repeat(MAX_CURSOR_LENGTH + 1),
  ];
  for (const b of bad) assert.equal(decodeCursor("catalog_signals", b), null, b.slice(0, 60));
  assert.throws(() => encodeCursor("catalog_rows", ["x".repeat(5000)]), /catalog_rows/);
  assert.throws(() => encodeCursor("catalog_signals", ["a", "kd", "1"]), /catalog_signals/);
});

/* ---------- 按字节截页 ---------- */

const ENVELOPE = { ok: true, version: EXPORT_VERSION, resource: "catalog_rows", asOf: "2026-09-23T10:15:00.000Z", fingerprint: FP };

function entry(id: string, fill: string, repeat: number): PageEntry<Record<string, unknown>> {
  return { row: { row_key: id, blob: fill.repeat(repeat) }, key: [id] };
}

function bodyBytes(rows: unknown[], nextCursor: string | null): number {
  return Buffer.byteLength(JSON.stringify({ ...ENVELOPE, rows, nextCursor }), "utf8");
}

test("按字节截页：预算内按条数上限给；多取的那一行只用来判断有没有下一页，恰好取满不给游标", () => {
  const small = ["a", "b", "c"].map((id) => entry(id, "x", 10));
  const two = cutPageByBytes("catalog_rows", small, 2, ENVELOPE);
  assert.ok(two.ok);
  assert.deepEqual(two.ok && two.rows.map((r) => r.row_key), ["a", "b"]);
  assert.deepEqual(two.ok && decodeCursor("catalog_rows", two.nextCursor ?? ""), ["b"]);
  const exact = cutPageByBytes("catalog_rows", small.slice(0, 2), 2, ENVELOPE);
  assert.deepEqual(exact, { ok: true, rows: small.slice(0, 2).map((e) => e.row), nextCursor: null });
  assert.deepEqual(cutPageByBytes("catalog_rows", [], 2, ENVELOPE), { ok: true, rows: [], nextCursor: null });
});

test("按字节截页：累计超 3 MB 预算就截断（UTF-8 字节，不是字符数），游标指向本页最后一行，整页不超预算", () => {
  const cjk = ["a", "b", "c", "d"].map((id) => entry(id, "网", 350_000));
  const cut = cutPageByBytes("catalog_rows", cjk, 5000, ENVELOPE);
  assert.ok(cut.ok);
  if (!cut.ok) return;
  assert.equal(cut.rows.length, 2, "每行约 1.05 MB（UTF-8），3 行就超 3 MB");
  assert.deepEqual(decodeCursor("catalog_rows", cut.nextCursor ?? ""), ["b"]);
  assert.ok(bodyBytes(cut.rows, cut.nextCursor) <= PAGE_BYTE_BUDGET);
  const ascii = ["a", "b", "c", "d"].map((id) => entry(id, "x", 700_000));
  const cut2 = cutPageByBytes("catalog_rows", ascii, 5000, ENVELOPE);
  assert.ok(cut2.ok && cut2.rows.length === 4 && cut2.nextCursor === null);
  assert.ok(cut2.ok && bodyBytes(cut2.rows, cut2.nextCursor) <= PAGE_BYTE_BUDGET);
});

test("单行连同信封超 3 MB、不超 4 MB：这一页只给这一行；它排在别的行后面时先截在它前面", () => {
  const big = entry("big", "x", 3_500_000);
  const alone = cutPageByBytes("catalog_rows", [big, entry("z", "x", 10)], 5000, ENVELOPE);
  assert.ok(alone.ok);
  if (!alone.ok) return;
  assert.deepEqual(alone.rows.map((r) => r.row_key), ["big"]);
  assert.deepEqual(decodeCursor("catalog_rows", alone.nextCursor ?? ""), ["big"]);
  const size = bodyBytes(alone.rows, alone.nextCursor);
  assert.ok(size > PAGE_BYTE_BUDGET && size <= ROW_HARD_LIMIT, String(size));
  const last = cutPageByBytes("catalog_rows", [big], 5000, ENVELOPE);
  assert.deepEqual(last.ok && last.nextCursor, null);
  const before = cutPageByBytes("catalog_rows", [entry("a", "x", 10), big], 5000, ENVELOPE);
  assert.ok(before.ok);
  assert.deepEqual(before.ok && before.rows.map((r) => r.row_key), ["a"]);
  assert.deepEqual(before.ok && decodeCursor("catalog_rows", before.nextCursor ?? ""), ["a"]);
});

test("单行超过 4 MB 硬上限：返回 row_too_large，只带资源名与主键，不带字段值", () => {
  const huge = entry("huge", "SECRETVALUE", 400_000);
  const cut = cutPageByBytes("catalog_rows", [huge, entry("z", "x", 1)], 5000, ENVELOPE);
  assert.deepEqual(cut, { ok: false, error: "row_too_large", resource: "catalog_rows", key: ["huge"] });
  assert.ok(!JSON.stringify(cut).includes("SECRETVALUE"));
  const first = cutPageByBytes("catalog_rows", [entry("a", "x", 1), huge], 5000, ENVELOPE);
  assert.ok(first.ok && first.rows.length === 1, "排在后面的超大行先截在它前面，下一页再报 row_too_large");
  const signal = cutPageByBytes("catalog_signals", [{ row: { blob: "x".repeat(4_100_000) }, key: ["k", "kd", 0] }], 10, ENVELOPE);
  assert.deepEqual(signal, { ok: false, error: "row_too_large", resource: "catalog_signals", key: ["k", "kd", 0] });
});

test("预算按 4.1：3 MB 页预算、4 MB 单行硬上限，都低于 Vercel 的 4.5 MB 响应上限", () => {
  assert.equal(PAGE_BYTE_BUDGET, 3_000_000);
  assert.equal(ROW_HARD_LIMIT, 4_000_000);
  assert.ok(ROW_HARD_LIMIT < 4.5 * 1_000_000);
});
