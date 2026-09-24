/**
 * pick-board-snapshot 的纯函数部分（P4-3）：常量、参数、用例清单、结果归一与落盘前的清理、按页面分派 loaders。
 * 不连库、不读文件、不 import RealShort 与工作台的任何模块：它和 pick-board-snapshot.rs.ts 一起被临时复制进
 * RealShort 检出（见那个文件的开头），工作台的 pick-board-parity.ts 也用它重放同一批用例。
 * 常量是 request.ts 的副本，工作台单测钉住两边相同。
 */

/** 2：网盘与金额之外的文本也过 RealShort 自己的 scrubPanText，认出的整串换成 SCRUBBED */
export const SNAPSHOT_FORMAT = "pick-board-snapshot/2";
/** 每个用例的列表只留前 50 行（方案 P4-3） */
export const ROW_LIMIT = 50;
/** 落盘前替换网盘与金额字段的占位；parity 把它们当「被删字段」 */
export const STRIPPED = "[不进快照]";
/** 落盘前被 RealShort 的 scrubPanText 认出网盘信息的文本整串换成它；parity 把它对上镜像的清洗占位，按 meta.scrub 记数 */
export const SCRUBBED = "[快照清洗：网盘信息]";
export const GLOBALS_CASE_ID = "globals";

// prettier-ignore
export const PLATFORMS = ["reelshort", "dramabox", "shortmax", "flickreels", "flareflow", "kalos", "starshort",
  "goodshort", "moboreels", "touchshort"] as const;
// prettier-ignore
export const THEATER_BASES = ["kd", "kw", "qc", "qr", "sm", "smd", "mg", "fh", "sh", "gh", "gn", "ghh", "dbn"] as const;
export const RS_BASES = ["clk", "bill", "gsc"] as const;
// prettier-ignore
export const RS_RANKS = ["rs_rr", "rs_growth", "rs_cand", "rs_pc", "rs_clk", "rs_gsc", "rs_bill", "rs_ledger"] as const;
export const GRADES = ["SSS", "SS", "S", "A", "B", "C", "D"] as const;
/** 选剧 tab 的发布记录筛选，去掉「不限」 */
export const POSTED_FILTERS = ["no", "yes", "pool"] as const;
/** 发布记录 tab 的状态，去掉「全部」 */
export const POSTED_STATES = ["pub", "sched", "none", "nomatch"] as const;
const DAILY_RANKS = ["kd", "qc", "qr"] as const;
const GRADED_RANKS = ["sm", "mg"] as const;
/** 涨幅榜的缺省排序是 d7（rank.rs_growth 那个用例），另三种单列 */
const GROWTH_SORTS = ["d1", "dp1", "dp7"] as const;
const RS_ROW_PREFIX = "reelshort-";

export const PAN_KEYS: ReadonlySet<string> = new Set(["panUrl", "panPw"]);
/** 分成金额（USD）与推广值：旧页有、镜像没有，也不该出现在任何落盘文件里 */
// prettier-ignore
export const MONEY_KEYS: ReadonlySet<string> = new Set(["billUsd", "billUsd1", "billUsd7", "billUsd15", "billUsd30",
  "revenueUsd", "usd", "matchedUsd", "promotionValue"]);

// prettier-ignore
export type Json = null | boolean | number | string | readonly Json[] | { readonly [key: string]: Json };
export type JsonObject = Readonly<Record<string, Json>>;

export type SnapshotCase = Readonly<{ id: string; query: string }>;
export type CaseRecord = Readonly<{
  id: string;
  query: string;
  ms: number;
  result: Json;
}>;
export type SnapshotDoc = Readonly<{
  format: typeof SNAPSHOT_FORMAT;
  asOf: string;
  fingerprint: string;
  sourceRevision: string | null;
  /** 库的 datcollate：两边不同时，剧名排序的差异才进白名单 */
  collation: string | null;
  rowLimit: number;
  startedAt: string;
  finishedAt: string;
  elapsedMs: number;
  cases: readonly CaseRecord[];
}>;

export function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/* ---------------------------------------------------------------- 参数 */

export type Parsed<T> =
  | Readonly<{ ok: true; args: T }>
  | Readonly<{ ok: false; error: string }>;

function refuse(error: string): Readonly<{ ok: false; error: string }> {
  return { ok: false, error };
}

export type Flags = Readonly<{
  values: ReadonlyMap<string, string>;
  switches: ReadonlySet<string>;
}>;

/** 读 `--name value` 与开关；错误只点参数名，不回显值 */
export function readFlags(
  argv: readonly string[],
  valueFlags: ReadonlySet<string>,
  switchFlags: ReadonlySet<string>,
): Parsed<Flags> {
  const values = new Map<string, string>();
  const switches = new Set<string>();
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i] ?? "";
    if (switchFlags.has(flag)) {
      switches.add(flag);
      continue;
    }
    const value = argv[i + 1];
    if (!valueFlags.has(flag))
      return refuse(`不认识的参数 ${flag.slice(0, 40)}`);
    if (value === undefined || value.startsWith("--"))
      return refuse(`${flag} 缺值`);
    values.set(flag, value);
    i += 1;
  }
  return { ok: true, args: { values, switches } };
}

export type SnapshotArgs = Readonly<{
  asOf: string;
  fp: string;
  out: string;
  only: RegExp | null;
  ignoreWindow: boolean;
}>;

const AS_OF_ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const FINGERPRINT = /^[0-9a-f]{64}$/;

function compileOnly(raw: string | undefined): RegExp | null | false {
  if (raw === undefined) return null;
  try {
    return new RegExp(raw);
  } catch {
    return false;
  }
}

export function parseSnapshotArgs(
  argv: readonly string[],
): Parsed<SnapshotArgs> {
  const read = readFlags(
    argv,
    new Set(["--as-of", "--fp", "--out", "--only"]),
    new Set(["--ignore-window"]),
  );
  if (!read.ok) return read;
  const { values, switches } = read.args;
  const asOf = values.get("--as-of") ?? "";
  if (!AS_OF_ISO.test(asOf) || Number.isNaN(Date.parse(asOf)))
    return refuse("--as-of 应是版本的 as_of，毫秒 ISO（…T…Z）");
  const fp = values.get("--fp") ?? "";
  if (!FINGERPRINT.test(fp))
    return refuse("--fp 应是版本的 fingerprint（64 位小写 hex）");
  const out = values.get("--out");
  if (!out) return refuse("--out 缺值：快照写到哪个文件");
  const only = compileOnly(values.get("--only"));
  if (only === false) return refuse("--only 不是合法的正则");
  const ignoreWindow = switches.has("--ignore-window");
  return { ok: true, args: { asOf, fp, out, only, ignoreWindow } };
}

/* ---------------------------------------------------------------- 用例 */

function urlCase(id: string, params: Record<string, string>): SnapshotCase {
  return { id, query: new URLSearchParams(params).toString() };
}

function pickCases(): SnapshotCase[] {
  return [
    urlCase("pick", {}),
    urlCase("all", { tab: "all" }),
    ...PLATFORMS.map((p) => urlCase(`pick.platform.${p}`, { platform: p })),
    ...[...THEATER_BASES, ...RS_BASES].map((b) =>
      urlCase(`pick.basis.${b}`, { basis: b }),
    ),
    ...POSTED_FILTERS.map((f) => urlCase(`pick.posted.${f}`, { posted: f })),
    urlCase("pick.yt", { yt: "1" }),
    urlCase("pick.inuse", { inuse: "1" }),
    urlCase("pick.dated", { dated: "1" }),
    urlCase("pick.off", { off: "1" }),
  ];
}

function rankCases(): SnapshotCase[] {
  return [
    ...[...THEATER_BASES, ...RS_RANKS].map((rk) =>
      urlCase(`rank.${rk}`, { tab: "rank", rk }),
    ),
    ...GROWTH_SORTS.map((rs) =>
      urlCase(`rank.rs_growth.${rs}`, { tab: "rank", rk: "rs_growth", rs }),
    ),
  ];
}

/**
 * 不依赖数据的用例（方案 P4-3 的清单）：全局一个、选剧与全部剧库的默认视图、每个剧场、每种依据、三种发布筛选、
 * yt / inuse / dated / off、每张榜、涨幅榜的另三种排序、发布记录列表与四种状态。依赖数据的在 deriveCases。
 */
export function staticCases(): SnapshotCase[] {
  return [
    { id: GLOBALS_CASE_ID, query: "" },
    ...pickCases(),
    ...rankCases(),
    urlCase("posted", { tab: "posted" }),
    ...POSTED_STATES.map((pst) =>
      urlCase(`posted.${pst}`, { tab: "posted", pst }),
    ),
  ];
}

function at(value: Json | undefined, route: readonly (string | number)[]) {
  return route.reduce<Json | undefined>((node, step) => {
    if (typeof step === "number")
      return Array.isArray(node) ? (node as readonly Json[])[step] : undefined;
    return isJsonObject(node) ? node[step] : undefined;
  }, value);
}

function arrayAt(value: Json | undefined, route: readonly string[]): Json[] {
  const found = at(value, route);
  return Array.isArray(found) ? [...(found as readonly Json[])] : [];
}

function stringsOf(rows: readonly Json[], key: string): string[] {
  return rows.flatMap((row) => {
    const value = isJsonObject(row) ? row[key] : undefined;
    return typeof value === "string" && value !== "" ? [value] : [];
  });
}

function periodCases(results: ReadonlyMap<string, Json>): SnapshotCase[] {
  const days = DAILY_RANKS.flatMap((rk) => {
    const day = at(results.get(`rank.${rk}`), ["meta", "days", 1]);
    return typeof day === "string"
      ? [urlCase(`rank.${rk}.day2`, { tab: "rank", rk, day })]
      : [];
  });
  const week = at(results.get("rank.kw"), ["meta", "weeks", 1, "start"]);
  const weeks =
    typeof week === "string"
      ? [urlCase("rank.kw.week2", { tab: "rank", rk: "kw", week })]
      : [];
  return [...days, ...weeks];
}

/** 前两个值各开一个用例，id 以 1、2 结尾 */
function numbered(
  prefix: string,
  values: readonly string[],
  params: (value: string) => Record<string, string>,
): SnapshotCase[] {
  return values
    .slice(0, 2)
    .map((value, i) => urlCase(`${prefix}${i + 1}`, params(value)));
}

function gradeCases(results: ReadonlyMap<string, Json>): SnapshotCase[] {
  return GRADED_RANKS.flatMap((rk) => {
    const grades = at(results.get(`rank.${rk}`), ["meta", "grades"]);
    const present = GRADES.filter((g) => {
      const n = isJsonObject(grades) ? grades[g] : undefined;
      return typeof n === "number" && n > 0;
    });
    return numbered(`rank.${rk}.grade`, present, (grade) => ({
      tab: "rank",
      rk,
      grade,
    }));
  });
}

function evidenceCases(results: ReadonlyMap<string, Json>): SnapshotCase[] {
  const records = stringsOf(
    arrayAt(results.get("posted"), ["list", "rows"]),
    "sd",
  );
  const rows = arrayAt(results.get("pick"), ["page", "rows"]);
  const isRs = (row: Json) => isJsonObject(row) && row.platform === "reelshort";
  const theater = stringsOf(
    rows.filter((row) => !isRs(row)),
    "rowKey",
  );
  const fromRank = stringsOf(
    arrayAt(results.get("rank.rs_rr"), ["result", "rows"]),
    "id",
  ).map((id) => `${RS_ROW_PREFIX}${id}`);
  const reelshort = [
    ...new Set([...stringsOf(rows.filter(isRs), "rowKey"), ...fromRank]),
  ].filter((key) => key.startsWith(RS_ROW_PREFIX));
  const row = (key: string) => ({ tab: "row", row: key });
  return [
    ...numbered("posted.record", records, (sd) => ({ tab: "posted", sd })),
    ...numbered("row.theater", theater, row),
    ...numbered("row.reelshort", reelshort, row),
  ];
}

/**
 * 依赖数据的用例，取自已经跑完的用例结果：日榜第二天、周榜第二周、评级榜前两档、两条发布记录、
 * 两个剧场行与两个 ReelShort 行的证据页。parity 不再推导，照快照里的 query 原样重放。
 */
export function deriveCases(
  results: ReadonlyMap<string, Json>,
): SnapshotCase[] {
  return [
    ...periodCases(results),
    ...gradeCases(results),
    ...evidenceCases(results),
  ];
}

/** --only 只挑用例，globals 一直在 */
export function selectCases(
  cases: readonly SnapshotCase[],
  only: RegExp | null,
): SnapshotCase[] {
  return only
    ? cases.filter((c) => c.id === GLOBALS_CASE_ID || only.test(c.id))
    : [...cases];
}

/* ---------------------------------------------------------------- 结果的形状 */

function entriesToJson(entries: Iterable<[unknown, unknown]>): JsonObject {
  return Object.fromEntries(
    [...entries]
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => [String(k), toJson(v)]),
  );
}

/** loader 的返回值归一成 JSON：Date 写 ISO，Map 写对象，undefined 的键去掉，非有限数写成文字 */
export function toJson(value: unknown): Json {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number")
    return Number.isFinite(value) ? value : String(value);
  if (typeof value === "bigint") return value.toString();
  if (value instanceof Date)
    return Number.isNaN(value.getTime()) ? "Invalid Date" : value.toISOString();
  if (value instanceof Map)
    return entriesToJson(value as Map<unknown, unknown>);
  if (value instanceof Set) return [...(value as Set<unknown>)].map(toJson);
  if (Array.isArray(value)) return value.map(toJson);
  if (typeof value === "object") return entriesToJson(Object.entries(value));
  return null;
}

const PAN_LINK = /^https?:\/\//i;

/**
 * 落盘前去掉网盘链接、提取码与金额：
 * - 网盘与金额字段的值换成 STRIPPED，键留着（parity 据此认出「被删字段」）；有 panUrl 的对象补 hasPan，
 *   判断与导出的 has_pan 相同（export-v2.ts HAS_PAN_SQL：pan_url ~* '^https?://'）；
 * - 其余每个文本值（剧名、备注、payload.h 的格子、posts 的链接……）交给 isPanText（RealShort 的 scrubPanText），
 *   认出网盘信息的整串换成 SCRUBBED。对象的键是标识，不动。
 */
export function stripSensitive(
  value: Json,
  isPanText: (text: string) => boolean,
): Json {
  if (typeof value === "string") return isPanText(value) ? SCRUBBED : value;
  if (Array.isArray(value))
    return (value as readonly Json[]).map((v) => stripSensitive(v, isPanText));
  if (!isJsonObject(value)) return value;
  const entries = Object.entries(value).map(([key, child]): [string, Json] => [
    key,
    PAN_KEYS.has(key) || MONEY_KEYS.has(key)
      ? STRIPPED
      : stripSensitive(child, isPanText),
  ]);
  const url = value.panUrl;
  return "panUrl" in value
    ? Object.fromEntries([
        ...entries,
        ["hasPan", typeof url === "string" && PAN_LINK.test(url)],
      ])
    : Object.fromEntries(entries);
}

function trimRows(value: Json): Json {
  if (!isJsonObject(value) || !Array.isArray(value.rows)) return value;
  return {
    ...value,
    rows: (value.rows as readonly Json[]).slice(0, ROW_LIMIT),
  };
}

/* ---------------------------------------------------------------- 按页面的顺序调 loaders */

/** 两边 PickRequest 共有、分派用得到的字段 */
export type CaseRequest = Readonly<{
  tab: string;
  rank: string;
  sd: string;
  rowKey: string;
}>;

/** 页面用到的 loaders；RealShort 那边在闭包里带上 asOf，镜像那边在 withScriptScope 里 */
export interface BoardLoaders<Req extends CaseRequest, Meta> {
  parse(params: URLSearchParams): Req;
  isRsRank(rank: string): boolean;
  reelshortId(rowKey: string): string;
  pickRows(req: Req): Promise<unknown>;
  facets(req: Req): Promise<unknown>;
  rankMeta(req: Req): Promise<Meta>;
  rankRows(req: Req, meta: Meta): Promise<unknown>;
  rsRank(req: Req): Promise<unknown>;
  growthDiagnosis(req: Req): Promise<unknown>;
  postedList(req: Req): Promise<unknown>;
  postedRecord(sd: string): Promise<unknown>;
  postedStats(): Promise<unknown>;
  accounts(): Promise<unknown>;
  rowDetail(rowKey: string): Promise<unknown>;
  reelshortDetail(id: string): Promise<unknown>;
  freshness(): Promise<unknown>;
  sources(): Promise<unknown>;
}

async function runGlobals<Req extends CaseRequest, Meta>(
  loaders: BoardLoaders<Req, Meta>,
): Promise<Json> {
  const [freshness, sources, postedStats, accounts] = await Promise.all([
    loaders.freshness(),
    loaders.sources(),
    loaders.postedStats(),
    loaders.accounts(),
  ]);
  return toJson({ freshness, sources, postedStats, accounts });
}

async function runList<Req extends CaseRequest, Meta>(
  loaders: BoardLoaders<Req, Meta>,
  req: Req,
): Promise<Json> {
  const [page, facets] = await Promise.all([
    loaders.pickRows(req),
    loaders.facets(req),
  ]);
  return { page: trimRows(toJson(page)), facets: toJson(facets) };
}

function isEmptyRows(result: Json): boolean {
  return (
    isJsonObject(result) &&
    result.kind === "rows" &&
    Array.isArray(result.rows) &&
    result.rows.length === 0
  );
}

/** 页面的 RankView：先 meta；rs 榜的列表截前 50 行，订单对账整份（LIMIT 边界要比）；涨幅榜空了才诊断 */
async function runRank<Req extends CaseRequest, Meta>(
  loaders: BoardLoaders<Req, Meta>,
  req: Req,
): Promise<Json> {
  const meta = await loaders.rankMeta(req);
  if (!loaders.isRsRank(req.rank))
    return {
      meta: toJson(meta),
      page: trimRows(toJson(await loaders.rankRows(req, meta))),
    };
  const result = toJson(await loaders.rsRank(req));
  const rows = isJsonObject(result) && result.kind === "rows";
  const base = { meta: toJson(meta), result: rows ? trimRows(result) : result };
  if (req.rank !== "rs_growth" || !isEmptyRows(result)) return base;
  return { ...base, diagnosis: toJson(await loaders.growthDiagnosis(req)) };
}

async function runDetail<Req extends CaseRequest, Meta>(
  loaders: BoardLoaders<Req, Meta>,
  req: Req,
): Promise<Json> {
  if (!req.rowKey) return null;
  const id = loaders.reelshortId(req.rowKey);
  return toJson(
    id
      ? await loaders.reelshortDetail(id)
      : await loaders.rowDetail(req.rowKey),
  );
}

/** 一个用例：按页面（rs:src/app/admin/(protected)/pick/page.tsx）的分派调 loaders，结果归一成 JSON */
export async function runCase<Req extends CaseRequest, Meta>(
  loaders: BoardLoaders<Req, Meta>,
  kase: SnapshotCase,
): Promise<Json> {
  if (kase.id === GLOBALS_CASE_ID) return runGlobals(loaders);
  const req = loaders.parse(new URLSearchParams(kase.query));
  switch (req.tab) {
    case "pick":
    case "all":
      return runList(loaders, req);
    case "rank":
      return runRank(loaders, req);
    case "posted":
      return req.sd
        ? toJson(await loaders.postedRecord(req.sd))
        : { list: trimRows(toJson(await loaders.postedList(req))) };
    case "row":
      return runDetail(loaders, req);
    default:
      throw new Error(`用例 ${kase.id}：tab=${req.tab} 没有要比的数据`);
  }
}
