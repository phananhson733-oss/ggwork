/**
 * pick-board-snapshot 的纯函数部分（P4-3）：常量、参数、用例清单、结果归一与落盘前的清理、按页面分派 loaders、
 * 列表第 50 行所在并列组的补全。
 * 不连库、不读文件、不 import RealShort 与工作台的任何模块：它和 pick-board-snapshot.rs.ts 一起被临时复制进
 * RealShort 检出（见那个文件的开头），工作台的 pick-board-parity.ts 也用它重放同一批用例。
 * 常量是 request.ts 的副本，工作台单测钉住两边相同。
 */

/**
 * 2：网盘与金额之外的文本也过 RealShort 自己的 scrubPanText，认出的整串换成 SCRUBBED；
 * 3：列表截到 50 行后，补全第 50 行所在的并列组（见 completeTies）
 */
export const SNAPSHOT_FORMAT = "pick-board-snapshot/3";
/** 每个用例的列表只留前 50 行（方案 P4-3），再补第 50 行所在的并列组 */
export const ROW_LIMIT = 50;
/** 第 50 行之后最多补几行并列行；并列组更长时停下，记 ties.capped */
export const TIE_ROWS_MAX = 100;
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
/** 按日出榜的剧场榜（request.ts DAILY_RANKS） */
export const DAILY_RANKS = ["kd", "qc", "qr"] as const;
/** 按评级排的剧场榜（queries-rank.ts rankOrder 里的 sm、mg） */
export const GRADED_RANKS = ["sm", "mg"] as const;
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

function rowsOf(value: Json): readonly Json[] {
  return isJsonObject(value) && Array.isArray(value.rows)
    ? (value.rows as readonly Json[])
    : [];
}

function trimRows(value: Json): Json {
  if (!isJsonObject(value) || !Array.isArray(value.rows)) return value;
  return { ...value, rows: rowsOf(value).slice(0, ROW_LIMIT) };
}

/* ---------------------------------------------------------------- 第 50 行所在的并列组 */

function field(o: JsonObject, key: string): Json {
  return o[key] ?? null;
}

function isOneOf(list: readonly string[], value: string): boolean {
  return list.includes(value);
}

/** 评级在 GRADES 里的位置；不在里面的（空串、别的写法、null）一律 null，同 array_position(…) NULLS LAST */
function gradePosition(grade: Json): number | null {
  const i = (GRADES as readonly Json[]).indexOf(grade);
  return i < 0 ? null : i;
}

/** 剧场榜的 ORDER BY 里排在剧名前面的列（queries-rank.ts dailySelect / rankOrder） */
function rankPrimary(row: JsonObject, signal: JsonObject): Json[] {
  const kind = typeof signal.kind === "string" ? signal.kind : "";
  if (isOneOf(DAILY_RANKS, kind)) return [field(row, "dayRank")];
  if (kind === "kw")
    return [
      isJsonObject(signal.payload) ? field(signal.payload, "weeks") : null,
    ];
  if (isOneOf(GRADED_RANKS, kind))
    return [gradePosition(field(signal, "grade")), field(row, "listedOn")];
  return [field(signal, "evidenceOn"), field(row, "listedOn")];
}

/**
 * 一行的主排序键：ORDER BY 里排在剧名前面、与 collation 无关的那几列，写成 JSON 文本。两行相同即「并列」，
 * 并列行之间只按剧名、平台、行键排，两边库的 collation 不同就可能排得不同。
 * - 剧场榜的行（有 signal 对象）：日榜是当天名次；周榜是周数；评级榜是评级的位置与剧单日期；其余是证据日期与剧单日期；
 * - 选剧 / 全部剧库的行（有 rowKey 与 sourceTable）：缺省排序「证据时间」的证据日期与剧单日期（queries.ts orderBy）；
 * - 别的都是 null。
 * collation 放行（pick-board-parity-collation.ts）用的是同一个键。
 */
export function tiePrimary(row: Json): string | null {
  if (!isJsonObject(row)) return null;
  const signal = row.signal;
  if (isJsonObject(signal)) return JSON.stringify(rankPrimary(row, signal));
  if (!("rowKey" in row && "sourceTable" in row)) return null;
  return JSON.stringify([
    field(row, "latestEvidenceOn"),
    field(row, "listedOn"),
  ]);
}

/** 补全的结果：第 50 行之后补了几行；并列组还没完就到了上限时 capped */
export type Ties = Readonly<{ extra: number; capped: boolean }>;

type TieScan = Readonly<{
  appended: readonly Json[];
  /**
   * ended：在读到的行里碰到了键不同的行；capped：补满了 TIE_ROWS_MAX 行，并列一直到读到的最后一行（组正好
   * 100 行、下一行在没读的那页上时也算，偏保守）；null：这批行看完了，还要往后看
   */
  stop: "ended" | "capped" | null;
}>;

function scanTies(
  boundary: string,
  appended: readonly Json[],
  rows: readonly Json[],
): TieScan {
  const end = rows.findIndex((row) => tiePrimary(row) !== boundary);
  const tied = end < 0 ? rows : rows.slice(0, end);
  const room = TIE_ROWS_MAX - appended.length;
  const next = [...appended, ...tied.slice(0, room)];
  if (end >= 0 && tied.length <= room) return { appended: next, stop: "ended" };
  return {
    appended: next,
    stop: next.length === TIE_ROWS_MAX ? "capped" : null,
  };
}

/** 同一个 query，只换页码 */
function withPage(params: URLSearchParams, page: number): URLSearchParams {
  return new URLSearchParams([
    ...[...params.entries()].filter(([key]) => key !== "page"),
    ["page", String(page)],
  ]);
}

type LoadPage = (params: URLSearchParams) => Promise<unknown>;
type Collected = Ties & Readonly<{ rows: readonly Json[] }>;

function collected(scan: TieScan): Collected {
  const capped = scan.stop === "capped";
  return { rows: scan.appended, extra: scan.appended.length, capped };
}

/**
 * 从第 1 页的第 50 行之后接着收并列行：先用这一页里 50 行以外的，再从第 2 页起一页页往后翻，直到碰到键不同的行、
 * 没有更多行，或补满 TIE_ROWS_MAX 行时并列还在继续（capped）。每翻一页，要么停下，要么至少多补一行，所以一定会停：
 * size 50 时至多多读 3 页（第 2、3 页两页满页就补满上限；第 4 页只在 count 偏大、第 3 页不满却说还有时才读，读到的是空页），
 * size 100 时 1 页（同样情况 2 页）。
 */
async function collectTies(
  boundary: string,
  first: Json,
  params: URLSearchParams,
  load: LoadPage,
): Promise<Collected> {
  let scan = scanTies(boundary, [], rowsOf(first).slice(ROW_LIMIT));
  let source = first;
  let page = 1;
  while (
    scan.stop === null &&
    isJsonObject(source) &&
    source.hasMore === true
  ) {
    page += 1;
    source = toJson(await load(withPage(params, page)));
    const rows = rowsOf(source);
    // hasMore 说还有、这一页却是空的（RealShort 的 count 可能数进孤立的信号）：不再往后翻，按并列已结束算
    if (rows.length === 0) break;
    scan = scanTies(boundary, scan.appended, rows);
  }
  return collected(scan);
}

/**
 * 截到 ROW_LIMIT 行，再补全第 50 行所在的并列组（格式 3）。两边的主排序键与 collation 无关，数据相同时
 * 「比第 50 行靠前的行 + 整个并列组」在两边是同一组行；组内先后由 collation 放行，每一行都在两边、逐字段比。
 * 不满 50 行不补；第 50 行没有主排序键（tiePrimary 为 null）也不补。补过的页多一个 ties 字段，两边跑同一段代码，形状相同。
 * 只对第 1 页、每页不少于 50 行的请求调用（见 isFirstFullPage）。往后翻页走同一个 loader：只换 query 里的页码，
 * 经 loaders.parse 重新解析。
 */
async function completeTies(
  value: Json,
  params: URLSearchParams,
  load: LoadPage,
): Promise<Json> {
  if (!isJsonObject(value) || !Array.isArray(value.rows)) return value;
  const kept = rowsOf(value).slice(0, ROW_LIMIT);
  const last = kept.length === ROW_LIMIT ? kept[ROW_LIMIT - 1] : undefined;
  const boundary = last === undefined ? null : tiePrimary(last);
  if (boundary === null) return { ...value, rows: kept };
  const { rows, extra, capped } = await collectTies(
    boundary,
    value,
    params,
    load,
  );
  return { ...value, rows: [...kept, ...rows], ties: { extra, capped } };
}

/**
 * 只补第 1 页、每页不少于 ROW_LIMIT 行的请求：第 2 页起页头也是一条 LIMIT 边界（OFFSET），那里的并列组补不到；
 * size=20 时截断在第 20 行，不在第 50 行。这两种只截断，两边 collation 不同时边界上的换行照报。
 * 现有用例都不带 page、size、sort（单测钉住），碰不到这一条；以后加这样的用例要先补这里。
 */
function isFirstFullPage(req: CaseRequest): boolean {
  return req.page === 1 && req.size >= ROW_LIMIT;
}

/** 选剧 / 全部剧库只在缺省排序「证据时间」下补：collation 放行的顺序模型只认这一种排序 */
function isDefaultSort(params: URLSearchParams): boolean {
  const sort = params.get("sort");
  return sort === null || sort === "evidence";
}

export type TieStats = Readonly<{
  /** 取满 50 行、查了并列组的用例（页上有 ties 字段） */
  full: number;
  /** 其中真补了行的（extra > 0） */
  appended: number;
  rows: number;
  capped: number;
}>;

/** 一页的 ties 字段；没补过并列组的页（或不是一页）是 null */
export function tiesOf(page: Json | undefined): Ties | null {
  const found = isJsonObject(page) ? page.ties : null;
  return isJsonObject(found) && typeof found.extra === "number"
    ? { extra: found.extra, capped: found.capped === true }
    : null;
}

/** 用例结果里查过并列组的：几个用例、其中几个真补了行、共补几行、几个到了上限。只数数，不碰行 */
export function tieStats(results: readonly Json[]): TieStats {
  const ties = results.flatMap((result) => {
    const found = tiesOf(isJsonObject(result) ? result.page : null);
    return found ? [found] : [];
  });
  return {
    full: ties.length,
    appended: ties.filter((t) => t.extra > 0).length,
    rows: ties.reduce((sum, t) => sum + t.extra, 0),
    capped: ties.filter((t) => t.capped).length,
  };
}

/** 快照结尾与 parity 报告里的同一句 */
export function tieSummary(stats: TieStats): string {
  return (
    `${stats.full} 个用例取满 ${ROW_LIMIT} 行、查了并列组，其中 ${stats.appended} 个补了共 ${stats.rows} 行，` +
    `${stats.capped} 个超过 ${TIE_ROWS_MAX} 行没补全`
  );
}

/* ---------------------------------------------------------------- 按页面的顺序调 loaders */

/** 两边 PickRequest 共有、分派用得到的字段（page、size 决定补不补并列组） */
export type CaseRequest = Readonly<{
  tab: string;
  rank: string;
  sd: string;
  rowKey: string;
  page: number;
  size: number;
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

/** 选剧 / 全部剧库：第 1 页、缺省排序下补全第 50 行的并列组，其余只截 50 行 */
async function runList<Req extends CaseRequest, Meta>(
  loaders: BoardLoaders<Req, Meta>,
  req: Req,
  params: URLSearchParams,
): Promise<Json> {
  const [page, facets] = await Promise.all([
    loaders.pickRows(req),
    loaders.facets(req),
  ]);
  const rows =
    isDefaultSort(params) && isFirstFullPage(req)
      ? await completeTies(toJson(page), params, (next) =>
          loaders.pickRows(loaders.parse(next)),
        )
      : trimRows(toJson(page));
  return { page: rows, facets: toJson(facets) };
}

function isEmptyRows(result: Json): boolean {
  return (
    isJsonObject(result) &&
    result.kind === "rows" &&
    Array.isArray(result.rows) &&
    result.rows.length === 0
  );
}

/**
 * 页面的 RankView：先 meta；剧场榜截 50 行，第 1 页再补全第 50 行的并列组（往后翻页用同一个 meta）；
 * rs 榜的列表只截前 50 行（它们的排序不在 collation 放行的模型里），订单对账整份（LIMIT 边界要比）；
 * 涨幅榜空了才诊断
 */
async function runRank<Req extends CaseRequest, Meta>(
  loaders: BoardLoaders<Req, Meta>,
  req: Req,
  params: URLSearchParams,
): Promise<Json> {
  const meta = await loaders.rankMeta(req);
  if (!loaders.isRsRank(req.rank)) {
    const page = toJson(await loaders.rankRows(req, meta));
    const rows = isFirstFullPage(req)
      ? await completeTies(page, params, (next) =>
          loaders.rankRows(loaders.parse(next), meta),
        )
      : trimRows(page);
    return { meta: toJson(meta), page: rows };
  }
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
  const params = new URLSearchParams(kase.query);
  const req = loaders.parse(params);
  switch (req.tab) {
    case "pick":
    case "all":
      return runList(loaders, req, params);
    case "rank":
      return runRank(loaders, req, params);
    case "posted":
      // 发布记录只截 50 行：它的排序不在 collation 放行的模型里
      return req.sd
        ? toJson(await loaders.postedRecord(req.sd))
        : { list: trimRows(toJson(await loaders.postedList(req))) };
    case "row":
      return runDetail(loaders, req);
    default:
      throw new Error(`用例 ${kase.id}：tab=${req.tab} 没有要比的数据`);
  }
}
