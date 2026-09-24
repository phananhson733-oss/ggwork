/**
 * pick-board-parity 的比对核心（P4-3）：RealShort 快照与镜像同一用例的结果逐字段比，差异分成白名单内与白名单外。
 * 纯函数，不连库；scripts/pick-board-parity.ts 负责跑镜像那边的 loaders 并打印。
 *
 * 白名单（合成稿 P4-3，按批判 B16 删掉 clicks 的 0 与 null）：
 * - 被删字段：分成金额（USD）、推广值、订单对账的上线日期；
 * - 网盘单元格：panUrl / panPw 不比，只比 hasPan（快照按导出的 has_pan 判断补上）；
 * - 对账合并：RealShort 的原始账单行按（账单日, book_id, 推广类型）合并后再比，镜像多出 canonicalId 与合计的行数口径；
 *   证据页订单明细的 sameDayClicks 在 RealShort 恒为 0；
 * - 账单明细的顺序与 LIMIT 边界：订单对账与证据页订单明细不比顺序，截断那一天的行与合计不强求一致；
 * - 清洗占位「[网盘信息已移除]」：只在 meta.scrub 记过的字段路径上放行，按格子去重后不得超过它记的次数；
 * - payload、posts 里导出键白名单之外的键；
 * - 「分成」改名「订单」；
 * - timestamptz 按毫秒比；
 * - 两边库的 collation 不同时，剧场行列表与语种计数里文字不同的两项之间的先后。
 * bill_rank 排序（ReelShort 预估分成榜）不进白名单：镜像按 bill_rank 排，与 RealShort 的顺序必须相同。
 *
 * 打印出来的值：RealShort 的文字一律只给长度与摘要（原文可能带网盘信息），网盘与金额字段两边都不给值。
 */
import { createHash } from "node:crypto";

import {
  MONEY_KEYS,
  PAN_KEYS,
  isJsonObject,
  type Json,
  type JsonObject,
} from "./pick-board-snapshot.rs";

export const PAN_SCRUB_REPLACEMENT = "[网盘信息已移除]";
/** 导出的 jsonb 键白名单（rs:src/lib/pick/export-v2-map.ts:195-197；工作台 contracts.py 同一份） */
// prettier-ignore
export const SIGNAL_PAYLOAD_KEYS = ["d", "w", "weeks", "best", "days", "first", "h", "qy", "pid"] as const;
// prettier-ignore
export const POSTED_POST_KEYS = ["d", "acct", "st", "views", "likes", "favs", "cmts", "shares", "md", "url", "note",
  "how", "pid"] as const;

const TOTALS_EXTRA: ReadonlySet<string> = new Set([
  "mergedRows",
  "mergedWithClicks",
  "rowsWithClicks",
]);
/** 订单对账截 200 行（按合并后的行；RealShort 按原始行），证据页订单明细截 50 行 */
const BILL_LIMITS: Readonly<Record<string, number>> = {
  "result.rows": 200,
  bill: 50,
};

export type RuleId =
  | "deleted-field"
  | "pan-cell"
  | "ledger-merge"
  | "bill-order-limit"
  | "scrub"
  | "payload-key"
  | "rename"
  | "timestamptz-ms"
  | "collation";

export const RULE_LABELS: Readonly<Record<RuleId, string>> = {
  "deleted-field": "被删字段（USD、推广值、订单对账的上线日期）",
  "pan-cell": "网盘单元格（只比 hasPan）",
  "ledger-merge": "对账合并",
  "bill-order-limit": "账单明细的顺序与 LIMIT 边界",
  scrub: "清洗占位（meta.scrub 的路径与次数）",
  "payload-key": "payload、posts 里白名单之外的键",
  rename: "「分成」改名「订单」",
  "timestamptz-ms": "timestamptz 按毫秒比",
  collation: "collation 不同时的剧名排序",
};

export type Finding = Readonly<{
  caseId: string;
  path: string;
  /** null = 白名单外 */
  rule: RuleId | null;
  what: string;
  rs?: string;
  mirror?: string;
}>;

/** 一处清洗占位记到哪个 meta.scrub 路径；cell 是去重用的格子（同一行同一字段在几个用例里出现只算一次） */
export type ScrubCharge = Readonly<{
  key: string;
  cell: string;
  caseId: string;
  path: string;
}>;

export type CaseComparison = Readonly<{
  findings: Finding[];
  charges: ScrubCharge[];
}>;

export type CompareContext = Readonly<{
  /** 版本 meta.scrub：字段路径 → 清洗次数 */
  scrub: Readonly<Record<string, number>>;
  collationDiffers: boolean;
}>;

type EntityKind =
  | "catalogRow"
  | "rsPickRow"
  | "linkedRow"
  | "signal"
  | "posted"
  | "bill"
  | "observeRow"
  | "account"
  | "rsId"
  | "billTotals";

/** 比到哪儿了：path 给人看（数组项写键），generic 数组下标写 [*]；rel / cell 相对最近的一行数据（entity） */
type Walk = Readonly<{
  caseId: string;
  ctx: CompareContext;
  path: string;
  generic: string;
  entity: EntityKind | null;
  rel: string;
  anchor: string;
  cell: string;
  /** 榜单行 dayNote 对应的信号格子（去重用），见 dayNoteCell */
  dayCell: string | null;
}>;

const EMPTY: CaseComparison = { findings: [], charges: [] };

export function merge(results: readonly CaseComparison[]): CaseComparison {
  return {
    findings: results.flatMap((r) => r.findings),
    charges: results.flatMap((r) => r.charges),
  };
}

function join(base: string, key: string): string {
  return base === "" ? key : `${base}.${key}`;
}

function step(w: Walk, key: string): Walk {
  return {
    ...w,
    path: join(w.path, key),
    generic: join(w.generic, key),
    rel: join(w.rel, key),
    cell: join(w.cell, key),
  };
}

function stepIndex(w: Walk, label: string): Walk {
  return {
    ...w,
    path: `${w.path}[${label}]`,
    generic: `${w.generic}[*]`,
    rel: `${w.rel}[*]`,
    cell: `${w.cell}[${label}]`,
  };
}

/* ---------------------------------------------------------------- 显示（不回显 RealShort 的文字） */

function shortHash(text: string): string {
  return createHash("sha256").update(text).digest("hex").slice(0, 8);
}

function showShape(value: Json | undefined): string {
  if (value === undefined) return "（无）";
  if (Array.isArray(value)) return `‹数组 ${value.length} 项›`;
  if (isJsonObject(value)) return "‹对象›";
  return JSON.stringify(value);
}

function showRs(value: Json | undefined): string {
  return typeof value === "string"
    ? `‹文本 ${[...value].length} 字 #${shortHash(value)}›`
    : showShape(value);
}

function showMirror(value: Json | undefined): string {
  if (typeof value !== "string") return showShape(value);
  const chars = [...value];
  return JSON.stringify(
    chars.length > 60 ? `${chars.slice(0, 60).join("")}…` : value,
  );
}

function isSensitive(w: Walk): boolean {
  const leaf = /([A-Za-z0-9_]+)(?:\[\*\])*$/.exec(w.generic)?.[1] ?? "";
  return PAN_KEYS.has(leaf) || MONEY_KEYS.has(leaf);
}

function fail(w: Walk, what: string, rs?: Json, mirror?: Json): CaseComparison {
  const values = isSensitive(w)
    ? {}
    : {
        ...(rs === undefined ? {} : { rs: showRs(rs) }),
        ...(mirror === undefined ? {} : { mirror: showMirror(mirror) }),
      };
  return {
    findings: [{ caseId: w.caseId, path: w.path, rule: null, what, ...values }],
    charges: [],
  };
}

function allow(w: Walk, rule: RuleId, what: string): CaseComparison {
  return {
    findings: [{ caseId: w.caseId, path: w.path, rule, what }],
    charges: [],
  };
}

/* ---------------------------------------------------------------- 一行数据是什么（决定清洗路径与去重） */

function text(o: JsonObject, key: string): string | null {
  const value = o[key];
  return typeof value === "string" ? value : null;
}

/** 标量写成文字；对象与数组写 JSON（身份键里不该出现，出现了也不会是 [object Object]） */
function label(value: Json | undefined): string {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : JSON.stringify(value ?? null);
}

function billKey(o: JsonObject): string {
  return `${text(o, "billDate")}|${text(o, "bookId")}|${text(o, "promotionType") ?? ""}`;
}

function classifyRow(o: JsonObject): [EntityKind, string] | null {
  const rowKey = text(o, "rowKey");
  if (rowKey === null) return null;
  if (!("sourceTable" in o)) return ["linkedRow", `row:${rowKey}`];
  return o.platform === "reelshort" && rowKey.startsWith("reelshort-")
    ? ["rsPickRow", `drama:${rowKey.slice("reelshort-".length)}`]
    : ["catalogRow", `row:${rowKey}`];
}

function classifyIdentified(o: JsonObject): [EntityKind, string] | null {
  const id = text(o, "id");
  if (id === null) return null;
  if ("revenueCents" in o) return ["observeRow", `drama:${id}`];
  if ("grp" in o) return ["account", `acct:${id}`];
  return "slug" in o ? ["rsId", `rsid:${id}`] : null;
}

/** [实体种类, 去重锚点]；不是一行数据时 null */
function classify(o: JsonObject, parent: string): [EntityKind, string] | null {
  const row = classifyRow(o);
  if (row) return row;
  if ("kind" in o && "ord" in o && "payload" in o)
    return ["signal", `${parent}/sig:${label(o.kind)}#${label(o.ord)}`];
  const sd = text(o, "sd");
  if (sd !== null) return ["posted", `sd:${sd}`];
  if (text(o, "billDate") !== null && text(o, "bookId") !== null)
    return ["bill", `bill:${billKey(o)}`];
  const identified = classifyIdentified(o);
  if (identified) return identified;
  return "orders" in o && ("usd" in o || "mergedRows" in o)
    ? ["billTotals", parent]
    : null;
}

/** 榜单行的 dayNote 是所选那天在信号 payload.h 里的备注：找到那一项，与信号列表里的同一格记成一处 */
function dayNoteCell(o: JsonObject, anchor: string): string | null {
  const signal = o.signal;
  const payload = isJsonObject(signal) ? signal.payload : null;
  const h = isJsonObject(payload) && Array.isArray(payload.h) ? payload.h : [];
  const i = (h as readonly Json[]).findIndex(
    (e) => Array.isArray(e) && e[1] === o.dayRank && e[2] === o.dayNote,
  );
  if (!isJsonObject(signal) || i < 0) return null;
  return `${anchor}/sig:${label(signal.kind)}#${label(signal.ord)}|payload.h[${i}][2]`;
}

function enter(w: Walk, rs: JsonObject): Walk {
  const found = classify(rs, w.anchor);
  if (!found) return w;
  const [entity, anchor] = found;
  const dayCell = entity === "catalogRow" ? dayNoteCell(rs, anchor) : null;
  return { ...w, entity, anchor, rel: "", cell: "", dayCell };
}

const RESOURCES: Readonly<Record<EntityKind, readonly string[]>> = {
  catalogRow: ["catalog_rows"],
  rsPickRow: ["rs_rows"],
  linkedRow: ["catalog_rows"],
  signal: ["catalog_signals"],
  posted: ["catalog_posted"],
  bill: ["rs_ids"],
  observeRow: ["rs_rows"],
  account: ["catalog_accounts"],
  rsId: ["rs_ids"],
  billTotals: [],
};

function snakePath(rel: string): string {
  return rel.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
}

/** 这个格子在导出里的字段路径（meta.scrub 的键），可能有几个候选 */
function exportPaths(w: Walk): string[] {
  if (!w.entity) return [];
  const snake = snakePath(w.rel);
  if (w.entity === "observeRow")
    return [`rs_rows.${snake.replace(/^tags(?=\[|$)/, "tag_list")}`];
  if (w.entity === "bill" && snake === "title")
    return ["rs_ids.title", "rs_bill_orders.book_title"];
  return RESOURCES[w.entity].map((resource) => `${resource}.${snake}`);
}

function scrubTarget(w: Walk): { paths: string[]; cell: string } {
  if (w.entity === "catalogRow" && w.rel === "dayNote")
    return {
      paths: ["catalog_signals.payload.h[*][*]"],
      cell: w.dayCell ?? `${w.anchor}|dayNote`,
    };
  return { paths: exportPaths(w), cell: `${w.anchor}|${w.cell}` };
}

function chargeScrub(w: Walk, rs: string): CaseComparison {
  const { paths, cell } = scrubTarget(w);
  const key = paths.find((p) => (w.ctx.scrub[p] ?? 0) > 0);
  if (!key)
    return fail(
      w,
      "清洗占位落在 meta.scrub 没有记的字段路径上",
      rs,
      PAN_SCRUB_REPLACEMENT,
    );
  return {
    findings: [
      { caseId: w.caseId, path: w.path, rule: "scrub", what: `记到 ${key}` },
    ],
    charges: [{ key, cell, caseId: w.caseId, path: w.path }],
  };
}

/* ---------------------------------------------------------------- 标量 */

const TIMESTAMP =
  /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}(?::?\d{2})?)$/;

/** timestamptz 的文本（PG 的 `+00`、ISO 的 `Z`）读成毫秒；多出的小数位截掉，与 JS Date 一样 */
export function timestampMs(value: string): number | null {
  const m = TIMESTAMP.exec(value);
  if (!m) return null;
  const [, day, time, frac = "", zone = "Z"] = m;
  const offset =
    zone === "Z"
      ? "Z"
      : `${zone.slice(0, 3)}:${zone.slice(3).replace(":", "").padEnd(2, "0")}`;
  const ms = Date.parse(
    `${day}T${time}.${frac.padEnd(3, "0").slice(0, 3)}${offset}`,
  );
  return Number.isNaN(ms) ? null : ms;
}

function sameMillisecond(a: string, b: string): boolean {
  const left = timestampMs(a);
  return left !== null && left === timestampMs(b);
}

function compareStrings(
  w: Walk,
  rs: string,
  mirror: string,
): CaseComparison | null {
  if (mirror === PAN_SCRUB_REPLACEMENT) return chargeScrub(w, rs);
  if (rs.includes("分成") && rs.replaceAll("分成", "订单") === mirror)
    return allow(w, "rename", "「分成」改名「订单」");
  if (sameMillisecond(rs, mirror))
    return allow(w, "timestamptz-ms", "同一毫秒");
  return null;
}

function compareScalar(w: Walk, rs: Json, mirror: Json): CaseComparison {
  if (rs === mirror) return EMPTY;
  const strings =
    typeof rs === "string" && typeof mirror === "string"
      ? compareStrings(w, rs, mirror)
      : null;
  if (strings) return strings;
  if (w.generic === "bill[*].sameDayClicks" && rs === 0)
    return allow(
      w,
      "ledger-merge",
      "RealShort 证据页的订单明细 sameDayClicks 恒为 0",
    );
  if (w.generic === "billTruncated" && rs === true && mirror === false)
    return allow(
      w,
      "bill-order-limit",
      "RealShort 按原始行截 50，合并后镜像没到 50 行",
    );
  return fail(w, "值不同", rs, mirror);
}

/* ---------------------------------------------------------------- 对象 */

function rsOnlyRule(w: Walk, key: string): RuleId | null {
  if (PAN_KEYS.has(key)) return "pan-cell";
  if (MONEY_KEYS.has(key)) return "deleted-field";
  if (w.entity === "bill" && w.rel === "" && key === "publishAt")
    return "deleted-field";
  const payload = w.entity === "signal" && w.rel === "payload";
  if (payload && !(SIGNAL_PAYLOAD_KEYS as readonly string[]).includes(key))
    return "payload-key";
  const post = w.entity === "posted" && w.rel === "posts[*]";
  if (post && !(POSTED_POST_KEYS as readonly string[]).includes(key))
    return "payload-key";
  return null;
}

function mirrorOnlyRule(w: Walk, key: string): RuleId | null {
  if (w.entity === "bill" && w.rel === "" && key === "canonicalId")
    return "ledger-merge";
  return w.entity === "billTotals" && w.rel === "" && TOTALS_EXTRA.has(key)
    ? "ledger-merge"
    : null;
}

function compareKey(
  w: Walk,
  key: string,
  rs: JsonObject,
  mirror: JsonObject,
): CaseComparison {
  const child = step(w, key);
  const inRs = Object.hasOwn(rs, key);
  const inMirror = Object.hasOwn(mirror, key);
  if (inRs && inMirror)
    return compareValue(child, rs[key] ?? null, mirror[key] ?? null);
  const rule = inRs ? rsOnlyRule(w, key) : mirrorOnlyRule(w, key);
  if (rule)
    return allow(
      child,
      rule,
      inRs ? "RealShort 有、镜像没有" : "镜像有、RealShort 没有",
    );
  return inRs
    ? fail(child, "镜像缺这个字段", rs[key])
    : fail(child, "镜像多出这个字段", undefined, mirror[key]);
}

function compareObjects(
  w: Walk,
  rs: JsonObject,
  mirror: JsonObject,
): CaseComparison {
  const inner = enter(w, rs);
  const keys = [...new Set([...Object.keys(rs), ...Object.keys(mirror)])];
  return merge(keys.map((key) => compareKey(inner, key, rs, mirror)));
}

/* ---------------------------------------------------------------- 数组 */

/** 列表项的身份：行键、sd、账单键、信号、id、日、周起日、语种 */
function itemKey(item: Json): string | null {
  if (!isJsonObject(item)) return null;
  for (const key of ["rowKey", "sd"]) {
    const value = text(item, key);
    if (value !== null) return value;
  }
  if (text(item, "billDate") !== null && text(item, "bookId") !== null)
    return billKey(item);
  if ("kind" in item && "ord" in item)
    return `${label(item.kind)}#${label(item.ord)}`;
  const id = text(item, "id") ?? text(item, "day");
  if (id !== null) return id;
  if ("week" in item && text(item, "start") !== null)
    return text(item, "start");
  return "n" in item ? text(item, "lang") : null;
}

function keysOf(items: readonly Json[]): string[] | null {
  const keys = items.map(itemKey);
  if (keys.some((k) => k === null)) return null;
  const strings = keys as string[];
  return new Set(strings).size === strings.length ? strings : null;
}

/** 可以因 collation 放行先后的列表：剧场行比剧名，语种计数比语种名 */
function collationField(item: Json | undefined): string | null {
  if (!isJsonObject(item)) return null;
  if ("rowKey" in item && "sourceTable" in item) return "title";
  return "lang" in item && "n" in item ? "lang" : null;
}

function inversions(before: readonly string[], after: readonly string[]) {
  const position = new Map(after.map((key, i) => [key, i]));
  return before.flatMap((a, i) =>
    before
      .slice(i + 1)
      .filter((b) => (position.get(b) ?? 0) < (position.get(a) ?? 0))
      .map((b): [string, string] => [a, b]),
  );
}

function collationExplains(
  w: Walk,
  pairs: readonly [string, string][],
  items: ReadonlyMap<string, Json>,
): boolean {
  if (!w.ctx.collationDiffers) return false;
  return pairs.every(([a, b]) => {
    const left = items.get(a);
    const right = items.get(b);
    const field = collationField(left);
    if (!field || field !== collationField(right)) return false;
    const x = isJsonObject(left) ? left[field] : null;
    const y = isJsonObject(right) ? right[field] : null;
    return typeof x === "string" && typeof y === "string" && x !== y;
  });
}

function compareOrder(
  w: Walk,
  rsOrder: readonly string[],
  mirrorOrder: readonly string[],
  items: ReadonlyMap<string, Json>,
): CaseComparison {
  const first = rsOrder.findIndex((key, i) => mirrorOrder[i] !== key);
  if (first < 0) return EMPTY;
  if (collationExplains(w, inversions(rsOrder, mirrorOrder), items))
    return allow(w, "collation", "先后不同的两项只差在剧名 / 语种名的排序上");
  return fail(
    w,
    `顺序不同：第 ${first + 1} 项 RealShort 是 ${rsOrder[first]}，镜像是 ${mirrorOrder[first]}`,
  );
}

function compareKeyed(
  w: Walk,
  rs: readonly Json[],
  mirror: readonly Json[],
  rsKeys: readonly string[],
  mirrorKeys: readonly string[],
): CaseComparison {
  const rsBy = new Map(rsKeys.map((k, i) => [k, rs[i] ?? null]));
  const mirrorBy = new Map(mirrorKeys.map((k, i) => [k, mirror[i] ?? null]));
  const missing = rsKeys
    .filter((k) => !mirrorBy.has(k))
    .map((k) => fail(stepIndex(w, k), "镜像少了这一项"));
  const extra = mirrorKeys
    .filter((k) => !rsBy.has(k))
    .map((k) => fail(stepIndex(w, k), "镜像多了这一项"));
  const common = rsKeys.filter((k) => mirrorBy.has(k));
  const order = compareOrder(
    w,
    common,
    mirrorKeys.filter((k) => rsBy.has(k)),
    rsBy,
  );
  const items = common.map((k) =>
    compareValue(stepIndex(w, k), rsBy.get(k) ?? null, mirrorBy.get(k) ?? null),
  );
  return merge([...missing, ...extra, order, ...items]);
}

function compareIndexed(
  w: Walk,
  rs: readonly Json[],
  mirror: readonly Json[],
): CaseComparison {
  const length =
    rs.length === mirror.length
      ? EMPTY
      : fail(
          w,
          `长度不同：RealShort ${rs.length} 项，镜像 ${mirror.length} 项`,
        );
  const items = rs
    .slice(0, mirror.length)
    .map((item, i) =>
      compareValue(stepIndex(w, String(i)), item, mirror[i] ?? null),
    );
  return merge([length, ...items]);
}

/* ---------------------------------------------------------------- 账单 */

function isBill(item: Json): item is JsonObject {
  return (
    isJsonObject(item) &&
    typeof item.billDate === "string" &&
    typeof item.bookId === "string"
  );
}

function asNumber(value: Json | undefined): number {
  return typeof value === "number" ? value : 0;
}

/** RealShort 的原始账单行按导出的键合并（rs_bill_orders：同键 order_cnt 求和、source_rows 计行数） */
export function mergeBills(rows: readonly JsonObject[]): JsonObject[] {
  const keys = [...new Set(rows.map(billKey))];
  return keys.map((key) => {
    const group = rows.filter((row) => billKey(row) === key);
    return {
      ...group[0],
      orderCnt: group.reduce((sum, row) => sum + asNumber(row.orderCnt), 0),
      sourceRows: group.length,
      sameDayClicks: Math.max(
        ...group.map((row) => asNumber(row.sameDayClicks)),
      ),
    };
  });
}

function earliest(rows: readonly JsonObject[]): string {
  return rows.map((row) => text(row, "billDate") ?? "").sort()[0] ?? "";
}

/** 截断那一天：两边哪边取满了 LIMIT，那边最早的账单日往前就可能不全；取较晚的那个 */
function cutDay(
  rsRaw: readonly JsonObject[],
  mirror: readonly JsonObject[],
  limit: number,
): string | null {
  const days = [
    rsRaw.length >= limit ? earliest(rsRaw) : null,
    mirror.length >= limit ? earliest(mirror) : null,
  ].filter((d): d is string => d !== null);
  return days.length ? ([...days].sort().at(-1) ?? null) : null;
}

const BOUNDARY_FIELDS = /\.(?:orderCnt|sourceRows)$/;

function compareBillItem(
  w: Walk,
  key: string,
  pair: readonly [JsonObject | undefined, JsonObject | undefined],
  cut: string | null,
): CaseComparison {
  const child = stepIndex(w, key);
  const [rs, mirror] = pair;
  const day = key.slice(0, 10);
  const edge = cut !== null && day <= cut;
  if (!rs || !mirror)
    return edge
      ? allow(child, "bill-order-limit", "LIMIT 截断那天或更早，一边有一边没有")
      : fail(child, rs ? "镜像少了这一项" : "镜像多了这一项");
  const result = compareValue(child, rs, mirror);
  if (day !== cut) return result;
  const findings = result.findings.map((f) =>
    f.rule === null && BOUNDARY_FIELDS.test(f.path)
      ? {
          caseId: f.caseId,
          path: f.path,
          rule: "bill-order-limit" as const,
          what: "LIMIT 截断那天的合计可能不全",
        }
      : f,
  );
  return { findings, charges: result.charges };
}

function compareBills(
  w: Walk,
  rsRaw: readonly JsonObject[],
  mirror: readonly JsonObject[],
): CaseComparison {
  const limit = BILL_LIMITS[w.generic] ?? Number.POSITIVE_INFINITY;
  const merged = mergeBills(rsRaw);
  const cut = cutDay(rsRaw, mirror, limit);
  const rsBy = new Map(merged.map((row) => [billKey(row), row]));
  const mirrorBy = new Map(mirror.map((row) => [billKey(row), row]));
  const keys = [...new Set([...rsBy.keys(), ...mirrorBy.keys()])];
  const items = keys.map((key) =>
    compareBillItem(w, key, [rsBy.get(key), mirrorBy.get(key)], cut),
  );
  const rsOrder = [...rsBy.keys()].filter((k) => mirrorBy.has(k));
  const mirrorOrder = [...mirrorBy.keys()].filter((k) => rsBy.has(k));
  const reordered = rsOrder.some((k, i) => mirrorOrder[i] !== k)
    ? allow(
        w,
        "bill-order-limit",
        "账单明细不比顺序（镜像按订单数，RealShort 按金额）",
      )
    : EMPTY;
  const mergedNote =
    merged.length < rsRaw.length
      ? allow(
          w,
          "ledger-merge",
          `${rsRaw.length} 条原始账单行合并成 ${merged.length} 行`,
        )
      : EMPTY;
  return merge([...items, reordered, mergedNote]);
}

function compareArrays(
  w: Walk,
  rs: readonly Json[],
  mirror: readonly Json[],
): CaseComparison {
  const all = [...rs, ...mirror];
  if (all.length > 0 && all.every(isBill))
    return compareBills(w, rs as JsonObject[], mirror as JsonObject[]);
  const rsKeys = keysOf(rs);
  const mirrorKeys = keysOf(mirror);
  return rsKeys && mirrorKeys
    ? compareKeyed(w, rs, mirror, rsKeys, mirrorKeys)
    : compareIndexed(w, rs, mirror);
}

function isComposite(value: Json): boolean {
  return typeof value === "object" && value !== null;
}

function compareValue(w: Walk, rs: Json, mirror: Json): CaseComparison {
  if (Array.isArray(rs) && Array.isArray(mirror))
    return compareArrays(w, rs as readonly Json[], mirror as readonly Json[]);
  if (isJsonObject(rs) && isJsonObject(mirror))
    return compareObjects(w, rs, mirror);
  if (isComposite(rs) || isComposite(mirror))
    return fail(w, "类型不同", rs, mirror);
  return compareScalar(w, rs, mirror);
}

/** 一个用例：RealShort 快照里的结果对镜像同一 query 的结果 */
export function compareCase(
  caseId: string,
  rs: Json,
  mirror: Json,
  ctx: CompareContext,
): CaseComparison {
  const root: Walk = {
    caseId,
    ctx,
    path: "",
    generic: "",
    entity: null,
    rel: "",
    anchor: "",
    cell: "",
    dayCell: null,
  };
  return compareValue(root, rs, mirror);
}

/* ---------------------------------------------------------------- 清洗次数 */

export type ScrubUsage = Readonly<{
  key: string;
  observed: number;
  budget: number;
}>;

/** 清洗占位按格子去重后逐路径计数，不得超过 meta.scrub 记的次数 */
export function settleScrub(
  charges: readonly ScrubCharge[],
  scrub: Readonly<Record<string, number>>,
): { findings: Finding[]; usage: ScrubUsage[] } {
  const keys = [
    ...new Set([...Object.keys(scrub), ...charges.map((c) => c.key)]),
  ].sort();
  const usage = keys.map((key) => ({
    key,
    observed: new Set(charges.filter((c) => c.key === key).map((c) => c.cell))
      .size,
    budget: scrub[key] ?? 0,
  }));
  const findings = usage
    .filter((u) => u.observed > u.budget)
    .map((u) => ({
      caseId: charges.find((c) => c.key === u.key)?.caseId ?? "",
      path: u.key,
      rule: null,
      what: `清洗占位 ${u.observed} 处，超过 meta.scrub 记的 ${u.budget} 次`,
    }));
  return { findings, usage };
}
