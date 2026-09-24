/**
 * pick-board-snapshot：RealShort 旧选剧台的 loaders 在一个镜像版本的时点上跑一遍，写成快照（P4-3，批判 A5）。
 * 工作台的 scripts/pick-board-parity.ts 拿它和镜像逐字段比。
 *
 * 【正本在工作台仓库，不进 RealShort 仓库、不开 PR】：往 RealShort 提交会触发它的生产部署，也会改变
 * fingerprint 里的 VERCEL_GIT_COMMIT_SHA。操作员临时复制进 RealShort 检出的 scripts/ 下运行，跑完删掉；
 * 复制进去的时候不要跑 RealShort 的测试（tests/admin-contracts.test.ts 会数 scripts/ 下的文件）。
 *
 *   cd "$RS_REPO" && git fetch && git checkout --detach <版本 meta 的 sourceRevision>
 *   cp <工作台>/frontend/scripts/pick-board-snapshot.rs.ts scripts/pick-board-snapshot.ts
 *   umask 077
 *   VERCEL_GIT_COMMIT_SHA=<sourceRevision> pnpm exec dotenv -e .env.local -- \
 *     pnpm exec tsx --conditions=react-server scripts/pick-board-snapshot.ts \
 *     --as-of <版本 as_of，毫秒 ISO> --fp <版本 fingerprint> --out "$SCRATCH/snap.json" [--only <正则>]
 *   rm scripts/pick-board-snapshot.ts
 *
 * 只读 RealShort 生产 Neon（三个环境共用一条 DATABASE_URL）；用例串行，避开写库与同步时段（busyWindow），打印总耗时。
 * 开头与结尾各核一次 fingerprint（export-v2 的 checkSource，内部读 readSourceSnapshot）：不一致就退出、不写文件。
 * 快照里没有网盘链接、提取码与任何金额：落盘前换成 STRIPPED，网盘只留 hasPan（与导出同一个判断）。
 * 本文件自成一体：常量是 request.ts 的副本（工作台单测钉住两边相同），RealShort 的模块在 main 里按路径动态 import。
 */
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const SNAPSHOT_FORMAT = "pick-board-snapshot/1";
/** 每个用例的列表只留前 50 行（方案 P4-3） */
export const ROW_LIMIT = 50;
/** 落盘前替换网盘与金额字段的占位；parity 把它们当「被删字段」 */
export const STRIPPED = "[不进快照]";
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
 * 落盘前去掉网盘链接、提取码与金额：值换成 STRIPPED，键留着（parity 据此认出「被删字段」）。
 * 有 panUrl 的对象补 hasPan，判断与导出的 has_pan 相同（export-v2.ts HAS_PAN_SQL：pan_url ~* '^https?://'）。
 */
export function stripSensitive(value: Json): Json {
  if (Array.isArray(value))
    return (value as readonly Json[]).map(stripSensitive);
  if (!isJsonObject(value)) return value;
  const entries = Object.entries(value).map(([key, child]): [string, Json] => [
    key,
    PAN_KEYS.has(key) || MONEY_KEYS.has(key) ? STRIPPED : stripSensitive(child),
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

/* ---------------------------------------------------------------- 守卫 */

const DAY_MINUTES = 24 * 60;
/**
 * RealShort vercel.json 里写库的 cron 与工作台镜像同步（北京时间 11:40、23:40）：
 * [名字, 开始的 UTC 分钟, 之前留几分钟, 之后留几分钟]
 */
// prettier-ignore
const BUSY_WINDOWS: readonly (readonly [string, number, number, number])[] = [
  ["RealShort sync（00:00 UTC）", 0, 15, 45], ["RealShort sync（06:00 UTC）", 360, 15, 45],
  ["RealShort sync（12:00 UTC）", 720, 15, 45], ["RealShort sync（18:00 UTC）", 1080, 15, 45],
  ["RealShort GSC 采集（05:17 UTC）", 317, 10, 30],
  ["工作台镜像同步（03:40 UTC）", 220, 10, 40], ["工作台镜像同步（15:40 UTC）", 940, 10, 40],
];

/** now 落在哪个写库或同步时段里；不在任何时段里是 null */
export function busyWindow(now: Date): string | null {
  const minute = now.getUTCHours() * 60 + now.getUTCMinutes();
  const hit = BUSY_WINDOWS.find(([, start, before, after]) => {
    const since = (minute - start + DAY_MINUTES) % DAY_MINUTES;
    return since <= after || since >= DAY_MINUTES - before;
  });
  return hit ? hit[0] : null;
}

/** checkSource 的返回（{fingerprint, problem}）是否就是 --fp 的那一刻；是则 null，否则说明 */
export function checkSourceProblem(result: unknown, fp: string): string | null {
  if (!isJsonObject(result) || typeof result.fingerprint !== "string")
    return "checkSource 的返回不是 {fingerprint, problem}";
  const { problem } = result;
  if (isJsonObject(problem)) {
    const status = typeof problem.status === "number" ? problem.status : "?";
    return `来源核对不通过：HTTP ${status}（409 = fingerprint 变了，503 = 有来源正在写）`;
  }
  if (problem !== null && problem !== undefined)
    return "checkSource 的 problem 不是预期形状";
  return result.fingerprint === fp
    ? null
    : "RealShort 此刻的 fingerprint 不是 --fp：VERCEL_GIT_COMMIT_SHA 没设对，或规则、来源数据变了";
}

/**
 * fingerprint 含 VERCEL_GIT_COMMIT_SHA 与规则摘要：设了它，检出就必须是同一个 commit（版本的 sourceRevision）。
 * 没设只对得上 sourceRevision 为 null 的版本（本地导出的），由 fingerprint 核对决定。
 */
export function checkHead(
  head: string,
  sha: string | undefined,
): string | null {
  if (!sha?.trim()) return null;
  return head.trim() === sha.trim()
    ? null
    : "RealShort 检出的 HEAD 不是 VERCEL_GIT_COMMIT_SHA：先 git checkout 版本的 sourceRevision";
}

/* ---------------------------------------------------------------- main：只在 RealShort 检出里跑 */

type RsRequest = CaseRequest;

const RS_MODULES = {
  queries: "src/lib/pick/queries.ts",
  request: "src/lib/pick/request.ts",
  exportV2: "src/lib/pick/export-v2.ts",
  db: "src/db/index.ts",
} as const;

/** 用到的导出；缺哪个就不是 816ca2e 系的选剧台 */
// prettier-ignore
const RS_EXPORTS = {
  queries: ["loadPickRows", "loadFacets", "loadFreshness", "loadRankMeta", "loadRankRows", "loadGrowthDiagnosis",
    "loadRsRank", "loadPostedList", "loadPostedRecord", "loadPostedStats", "loadAccounts", "loadRowDetail",
    "loadReelshortDetail", "loadObserveSources"],
  request: ["parsePickRequest", "isRsRank", "reelshortId"],
  exportV2: ["exportContext", "checkSource"],
  db: ["getDb"],
} as const;

type RsFn = (...args: readonly unknown[]) => unknown;
type RealShort = {
  readonly [K in keyof typeof RS_EXPORTS]: Readonly<
    Record<(typeof RS_EXPORTS)[K][number], RsFn>
  >;
};
type RsDb = { execute(query: unknown): Promise<{ rows: unknown[] }> };

export class Stop extends Error {
  constructor(
    readonly code: number,
    message: string,
  ) {
    super(message);
  }
}

function commitSha(): string | null {
  const sha = process.env.VERCEL_GIT_COMMIT_SHA?.trim() ?? "";
  return sha === "" ? null : sha;
}

function checkCheckout(root: string): void {
  const pkgFile = path.join(root, "package.json");
  const pkg: unknown = existsSync(pkgFile)
    ? JSON.parse(readFileSync(pkgFile, "utf8"))
    : null;
  if (!isJsonObject(pkg) || pkg.name !== "realshort")
    throw new Stop(2, "当前目录不是 RealShort 检出");
  const missing = Object.values(RS_MODULES).filter(
    (file) => !existsSync(path.join(root, file)),
  );
  if (missing.length)
    throw new Stop(2, `RealShort 检出缺少 ${missing.join("、")}`);
  const sha = commitSha();
  // 没设：只对得上 sourceRevision 为 null 的版本，交给开头的 fingerprint 核对
  if (!sha) return;
  const head = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: root,
    encoding: "utf8",
  });
  const problem = checkHead(head, sha);
  if (problem) throw new Stop(2, problem);
}

async function loadRealShort(root: string): Promise<RealShort> {
  const load = async (file: string): Promise<Record<string, unknown>> =>
    (await import(pathToFileURL(path.join(root, file)).href)) as Record<
      string,
      unknown
    >;
  const [queries, request, exportV2, db] = await Promise.all(
    Object.values(RS_MODULES).map(load),
  );
  const modules = { queries, request, exportV2, db };
  for (const [name, names] of Object.entries(RS_EXPORTS)) {
    const mod = modules[name as keyof RealShort];
    const absent = names.filter(
      (fn: string) => typeof mod?.[fn] !== "function",
    );
    if (absent.length)
      throw new Stop(
        2,
        `RealShort 的 ${name} 缺少 ${absent.join("、")}：不是 816ca2e 系的选剧台`,
      );
  }
  return modules as unknown as RealShort;
}

function rsLoaders(
  rs: RealShort,
  asOf: Date,
): BoardLoaders<RsRequest, unknown> {
  const q = rs.queries;
  return {
    parse: (params) => rs.request.parsePickRequest(params) as RsRequest,
    isRsRank: (rank) => rs.request.isRsRank(rank) === true,
    reelshortId: (rowKey) => String(rs.request.reelshortId(rowKey)),
    pickRows: async (req) => q.loadPickRows(req, asOf),
    facets: async (req) => q.loadFacets(req, asOf),
    rankMeta: async (req) => q.loadRankMeta(req, asOf),
    rankRows: async (req, meta) => q.loadRankRows(req, meta),
    rsRank: async (req) => q.loadRsRank(req, req.rank, asOf),
    growthDiagnosis: async (req) => q.loadGrowthDiagnosis(req, asOf),
    // 下面几个读的表没有时间维度（方案 P4-3），原样调用
    postedList: async (req) => q.loadPostedList(req),
    postedRecord: async (sd) => q.loadPostedRecord(sd),
    postedStats: async () => q.loadPostedStats(),
    accounts: async () => q.loadAccounts(),
    rowDetail: async (rowKey) => q.loadRowDetail(rowKey),
    reelshortDetail: async (id) => q.loadReelshortDetail(id, asOf),
    freshness: async () => q.loadFreshness(asOf),
    sources: async () => q.loadObserveSources(),
  };
}

async function readCollation(rs: RealShort): Promise<string | null> {
  const { sql } = await import("drizzle-orm");
  const { rows } = await (rs.db.getDb() as RsDb).execute(
    sql`SELECT datcollate::text AS c FROM pg_database WHERE datname = current_database()`,
  );
  const first = rows[0];
  return isJsonObject(first) && typeof first.c === "string" ? first.c : null;
}

async function checkSourceAt(rs: RealShort, fp: string, when: string) {
  const result = await rs.exportV2.checkSource(
    fp,
    rs.exportV2.exportContext(new Date()),
  );
  const problem = checkSourceProblem(result, fp);
  if (problem) throw new Stop(3, `${when}核对：${problem}；快照作废，不写文件`);
}

async function runSerially<Req extends CaseRequest, Meta>(
  loaders: BoardLoaders<Req, Meta>,
  cases: readonly SnapshotCase[],
  done: readonly CaseRecord[],
): Promise<CaseRecord[]> {
  let records = [...done];
  for (const kase of cases) {
    const started = Date.now();
    const result = stripSensitive(await runCase(loaders, kase));
    const ms = Date.now() - started;
    records = [...records, { id: kase.id, query: kase.query, ms, result }];
    process.stderr.write(`[${records.length}] ${kase.id} ${ms}ms\n`);
  }
  return records;
}

async function runAll<Req extends CaseRequest, Meta>(
  loaders: BoardLoaders<Req, Meta>,
  only: RegExp | null,
): Promise<CaseRecord[]> {
  const first = await runSerially(
    loaders,
    selectCases(staticCases(), only),
    [],
  );
  const results = new Map(first.map((r) => [r.id, r.result]));
  return runSerially(loaders, selectCases(deriveCases(results), only), first);
}

async function snapshot(args: SnapshotArgs): Promise<SnapshotDoc> {
  const window = args.ignoreWindow ? null : busyWindow(new Date());
  if (window)
    throw new Stop(
      2,
      `现在在「${window}」的时段里：避开后再跑，或加 --ignore-window`,
    );
  const root = process.cwd();
  checkCheckout(root);
  const rs = await loadRealShort(root);
  const startedAt = new Date();
  await checkSourceAt(rs, args.fp, "开头");
  const collation = await readCollation(rs);
  const cases = await runAll(rsLoaders(rs, new Date(args.asOf)), args.only);
  await checkSourceAt(rs, args.fp, "结尾");
  const finishedAt = new Date();
  return {
    format: SNAPSHOT_FORMAT,
    asOf: args.asOf,
    fingerprint: args.fp,
    sourceRevision: commitSha(),
    collation,
    rowLimit: ROW_LIMIT,
    startedAt: startedAt.toISOString(),
    finishedAt: finishedAt.toISOString(),
    elapsedMs: finishedAt.getTime() - startedAt.getTime(),
    cases,
  };
}

async function main(): Promise<number> {
  const parsed = parseSnapshotArgs(process.argv.slice(2));
  if (!parsed.ok) throw new Stop(2, parsed.error);
  const doc = await snapshot(parsed.args);
  // wx：不覆盖已有文件；0600：只有自己能读（快照里有 RealShort 的业务数据）
  writeFileSync(parsed.args.out, `${JSON.stringify(doc)}\n`, {
    mode: 0o600,
    flag: "wx",
  });
  process.stdout.write(
    `快照：${doc.cases.length} 个用例，耗时 ${(doc.elapsedMs / 1000).toFixed(1)} 秒，` +
      `开头与结尾的 fingerprint 核对都通过；写到 ${parsed.args.out}\n`,
  );
  return 0;
}

function reportFailure(error: unknown): number {
  if (error instanceof Stop) {
    process.stderr.write(`pick-board-snapshot：${error.message}\n`);
    return error.code;
  }
  const name = error instanceof Error ? error.name : "unknown";
  const message = error instanceof Error ? error.message.slice(0, 300) : "";
  process.stderr.write(`pick-board-snapshot 失败：${name} ${message}\n`);
  return 1;
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main().then(
    (code) => {
      process.exitCode = code;
    },
    (error: unknown) => {
      process.exitCode = reportFailure(error);
    },
  );
}
