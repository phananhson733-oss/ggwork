/**
 * feed v2（`pick-export-v2`）路由用的纯函数：查询参数、keyset 游标、按字节截页（方案 4.1、4.4），以及查询层交给路由的结果类型。
 * 【不许 import 任何 server-only 模块】——tests/pick-export-v2-page.test.ts 直接加载（纯模块的 import 链由 tests/pick-export-v2.test.ts 扫）。
 * 资源、列与主键的定义在 export-v2-map.ts，这里单向依赖它。
 */
import {
  RESOURCE_SPECS,
  isExportResource,
  type CursorKey,
  type ExportResource,
  type RowResource,
  type ScrubCounts,
} from "./export-v2-map";

/* ---------------------------------------------------------------- 请求参数 */

/** as_of 最多比 now 早 30 分钟（4.1）：工作台等完 busy 才选 as_of，整次拉取受 900 秒总时限约束，不放宽 */
export const AS_OF_MAX_AGE_MS = 30 * 60_000;
/** rs_series_day 的 day 最早到 as_of 当天往前 92 天（共 93 天，同 manifest 的 snapshotDays 与镜像的曲线保留下限） */
export const SERIES_DAY_SPAN = 93;

const AS_OF_RE = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})(?::00(?:\.000)?)?Z$/;
const DAY_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
const FP_RE = /^[0-9a-f]{64}$/;

/** 精确到分钟的 UTC 时间（秒必须是 0），必须不晚于 now、不早于 now − 30 分钟；不合格返回 null */
export function parseAsOf(raw: string | null, now: Date): Date | null {
  const m = raw ? AS_OF_RE.exec(raw) : null;
  if (!m) return null;
  const iso = `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:00.000Z`;
  const t = Date.parse(iso);
  /* V8 会把 2 月 30 日、24:00 顺延成别的时刻照常返回，所以要求往返后原样相等 */
  if (Number.isNaN(t) || new Date(t).toISOString() !== iso) return null;
  return t <= now.getTime() && t >= now.getTime() - AS_OF_MAX_AGE_MS ? new Date(t) : null;
}

/** manifest 给出的 fingerprint：64 位小写十六进制 */
export function parseFingerprint(raw: string | null): string | null {
  return raw && FP_RE.test(raw) ? raw : null;
}

function realDay(raw: string): boolean {
  if (!DAY_RE.test(raw)) return false;
  /* 13 月、0 日这类形状对、日历上没有的日子是 Invalid Date，toISOString 会抛；同 parseAsOf 先判 NaN */
  const t = Date.parse(`${raw}T00:00:00.000Z`);
  return !Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === raw;
}

function parseSeriesDay(raw: string | null, asOf: Date): string | null {
  if (!raw || !realDay(raw)) return null;
  const last = asOf.toISOString().slice(0, 10);
  const first = new Date(Date.parse(`${last}T00:00:00.000Z`) - (SERIES_DAY_SPAN - 1) * 86_400_000).toISOString().slice(0, 10);
  return raw >= first && raw <= last ? raw : null;
}

function parseLimit(raw: string | null, max: number): number | null {
  if (raw === null) return max;
  if (!/^[0-9]{1,6}$/.test(raw)) return null;
  const n = Number(raw);
  return n >= 1 && n <= max ? n : null;
}

export interface ExportQuery {
  resource: ExportResource;
  asOf: Date;
  fp: string | null;
  cursor: CursorKey | null;
  limit: number;
  day: string | null;
}

/** 拒绝原因只是一个固定的词（resource / unknown_param / duplicate_param / as_of / fp / cursor / limit / day），不回显参数值 */
export type ExportQueryResult = { ok: true; query: ExportQuery } | { ok: false; reason: string };

function allowedParams(resource: ExportResource): readonly string[] {
  if (resource === "manifest") return ["as_of"];
  return resource === "rs_series_day" ? ["as_of", "fp", "cursor", "limit", "day"] : ["as_of", "fp", "cursor", "limit"];
}

/** 参数名只许是本资源认识的，每个最多出现一次 */
function paramsProblem(resource: ExportResource, params: URLSearchParams): string | null {
  const allowed = allowedParams(resource);
  for (const k of new Set(params.keys())) {
    if (!allowed.includes(k)) return "unknown_param";
    if (params.getAll(k).length > 1) return "duplicate_param";
  }
  return null;
}

/**
 * v2 路由的查询参数（4.1）。manifest 只收 as_of（它负责算出 fp）；其它资源必须带 manifest 给的 fp，
 * cursor 只认本资源的规范编码，limit 缺省取该资源的上限；rs_series_day 另须 day。now 由调用方传入。
 */
export function parseExportQuery(resource: string, params: URLSearchParams, now: Date): ExportQueryResult {
  const bad = (reason: string): ExportQueryResult => ({ ok: false, reason });
  if (!isExportResource(resource)) return bad("resource");
  const problem = paramsProblem(resource, params);
  if (problem) return bad(problem);
  const asOf = parseAsOf(params.get("as_of"), now);
  if (!asOf) return bad("as_of");
  if (resource === "manifest") return { ok: true, query: { resource, asOf, fp: null, cursor: null, limit: 1, day: null } };
  const fp = parseFingerprint(params.get("fp"));
  if (!fp) return bad("fp");
  const rawCursor = params.get("cursor");
  const cursor = rawCursor === null ? null : decodeCursor(resource, rawCursor);
  if (rawCursor !== null && !cursor) return bad("cursor");
  const limit = parseLimit(params.get("limit"), RESOURCE_SPECS[resource].maxLimit);
  if (limit === null) return bad("limit");
  const day = resource === "rs_series_day" ? parseSeriesDay(params.get("day"), asOf) : null;
  if (resource === "rs_series_day" && !day) return bad("day");
  return { ok: true, query: { resource, asOf, fp, cursor, limit, day } };
}

/* ---------------------------------------------------------------- 查询层的结果（路由按 status 回 HTTP） */

/**
 * 来源核对的两种失败（方案 4.3）：fingerprint 与 fp 不同是 409，有来源正在写是 503（带 Retry-After 的秒数）。
 * v2 的每一页与 v1 带 fp 的每一页都可能返回它们。
 */
export type SourceFailure = { status: 409; error: "source_changed" } | { status: 503; error: "source_busy"; retryAfter: number };

/** 单行连同信封超过 4 MB 硬上限（4.1）：只带资源名与主键，不带字段值 */
export type ExportFailure = SourceFailure | { status: 500; error: "row_too_large"; resource: RowResource; key: CursorKey };

export interface ExportBody {
  ok: true;
  version: string;
  resource: string;
  asOf: string;
  fingerprint: string;
  rows: Record<string, unknown>[];
  nextCursor: string | null;
}

/**
 * 200 另带本页的清洗命中数（不进正文）：行资源各页之和等于 manifest 的 meta.scrub，库内测试据此核对；
 * manifest 这一页的是它 meta 文本的命中（键以 manifest.meta. 开头），不计入 meta.scrub。
 */
export type ExportResult = { status: 200; body: ExportBody; hits: ScrubCounts } | ExportFailure;

/* ---------------------------------------------------------------- 游标 */

export const MAX_CURSOR_LENGTH = 4096;
/** 游标里单个文本主键的上限（码元）：现有最长的 row_key 不到 200 */
const MAX_KEY_TEXT = 1024;

function validKey(resource: RowResource, key: unknown): key is CursorKey {
  const spec = RESOURCE_SPECS[resource];
  if (!Array.isArray(key) || key.length !== spec.key.length) return false;
  return spec.key.every((name, i) => {
    const type = spec.columns.find((c) => c.name === name)?.type;
    const v: unknown = key[i];
    return type === "int" ? Number.isSafeInteger(v) : typeof v === "string" && v.length <= MAX_KEY_TEXT;
  });
}

/** 本页最后一行的主键 → 不透明的 base64url；主键不合法或编码超长直接抛错，不发出一个解不回来的游标 */
export function encodeCursor(resource: RowResource, key: CursorKey): string {
  if (!validKey(resource, key)) throw new Error(`export-v2：${resource} 的游标主键不合法`);
  const out = Buffer.from(JSON.stringify({ r: resource, k: key }), "utf8").toString("base64url");
  if (out.length > MAX_CURSOR_LENGTH) throw new Error(`export-v2：${resource} 的游标超长`);
  return out;
}

/** 只认本资源、形状与类型都对、且是规范编码（重新编码后逐字相同）的游标；其余一律 null */
export function decodeCursor(resource: RowResource, raw: string): CursorKey | null {
  if (!raw || raw.length > MAX_CURSOR_LENGTH || !/^[A-Za-z0-9_-]+$/.test(raw)) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(raw, "base64url").toString("utf8"));
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const { r, k } = parsed as { r?: unknown; k?: unknown };
  if (r !== resource || !validKey(resource, k)) return null;
  return encodeCursor(resource, k) === raw ? [...k] : null;
}

/* ---------------------------------------------------------------- 按字节截页 */

/** 一页连同信封的 UTF-8 序列化预算（4.1），低于 Vercel 4.5 MB 的响应体上限 */
export const PAGE_BYTE_BUDGET = 3_000_000;
/** 单行连同信封的硬上限：超过它的行返回 500 row_too_large（4.1） */
export const ROW_HARD_LIMIT = 4_000_000;

export interface PageEntry<T> {
  row: T;
  key: CursorKey;
}

export type PageCut<T> =
  | { ok: true; rows: T[]; nextCursor: string | null }
  | { ok: false; error: "row_too_large"; resource: RowResource; key: CursorKey };

function utf8Bytes(text: string): number {
  return Buffer.byteLength(text, "utf8");
}

/**
 * 按条数与字节截一页。调用方按主键升序多取一行（limit + 1），多出来的只用来判断后面还有没有。
 * - 每页至少 1 行：第一行连同信封超过 3 MB 预算、但不超过 4 MB 时，这一页只给它；
 * - 第一行连同信封超过 4 MB：返回 row_too_large，只带资源名与主键，不带字段值；
 * - 其余行累计到超预算前为止。信封按「rows 为空、nextCursor 取最长」算，所以整页一定不超预算。
 * 排在后面的超大行不会让本页失败：本页截在它前面，下一页从它开始再判。
 */
export function cutPageByBytes<T>(
  resource: RowResource,
  entries: readonly PageEntry<T>[],
  limit: number,
  envelope: Readonly<Record<string, unknown>>,
): PageCut<T> {
  const base = utf8Bytes(JSON.stringify({ ...envelope, rows: [], nextCursor: "x".repeat(MAX_CURSOR_LENGTH) }));
  const max = Math.min(entries.length, limit);
  let total = base;
  let taken = 0;
  for (; taken < max; taken += 1) {
    const size = utf8Bytes(JSON.stringify(entries[taken].row)) + (taken > 0 ? 1 : 0);
    if (taken === 0 && base + size > ROW_HARD_LIMIT) return { ok: false, error: "row_too_large", resource, key: entries[0].key };
    if (taken > 0 && total + size > PAGE_BYTE_BUDGET) break;
    total += size;
  }
  const more = taken < entries.length && taken > 0;
  return {
    ok: true,
    rows: entries.slice(0, taken).map((e) => e.row),
    nextCursor: more ? encodeCursor(resource, entries[taken - 1].key) : null,
  };
}
