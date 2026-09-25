/**
 * pick-board-snapshot：RealShort 旧选剧台的 loaders 在一个镜像版本的时点上跑一遍，写成快照（P4-3，批判 A5）。
 * 工作台的 scripts/pick-board-parity.ts 拿它和镜像逐字段比。纯函数部分在 pick-board-snapshot-core.rs.ts。
 *
 * 【正本在工作台仓库，不进 RealShort 仓库、不开 PR】：往 RealShort 提交会触发它的生产部署，也会改变
 * fingerprint 里的 VERCEL_GIT_COMMIT_SHA。操作员把两个文件原名临时复制进 RealShort 检出的 scripts/ 下运行，
 * 跑完删掉；复制进去的时候不要跑 RealShort 的测试（tests/admin-contracts.test.ts 会数 scripts/ 下的文件）。
 *
 *   cd "$RS_REPO" && git fetch && git checkout --detach <版本 meta 的 sourceRevision>
 *   cp <工作台>/frontend/scripts/pick-board-snapshot.rs.ts <工作台>/frontend/scripts/pick-board-snapshot-core.rs.ts scripts/
 *   umask 077
 *   VERCEL_GIT_COMMIT_SHA=<sourceRevision> pnpm exec dotenv -e .env.local -- \
 *     pnpm exec tsx --conditions=react-server scripts/pick-board-snapshot.rs.ts \
 *     --as-of <版本 as_of，毫秒 ISO> --fp <版本 fingerprint> --out "$SCRATCH/snap.json" [--only <正则>]
 *   rm scripts/pick-board-snapshot.rs.ts scripts/pick-board-snapshot-core.rs.ts
 *
 * 只读 RealShort 生产 Neon（三个环境共用一条 DATABASE_URL）；用例串行，避开写库与同步时段（busyWindow），打印总耗时。
 * 取满 50 行的选剧 / 全部剧库（缺省排序）与剧场榜列表，还要往后翻页（每个用例至多多读 3 页），补全第 50 行所在的
 * 并列组（核心的 completeTies）。
 * 开头与结尾各核一次 fingerprint（export-v2 的 checkSource，内部读 readSourceSnapshot）：不一致就退出、不写文件。
 * 快照里没有网盘链接、提取码与任何金额：网盘与金额字段落盘前换成 STRIPPED，网盘只留 hasPan（与导出同一个判断）；
 * 其余文本值逐个过 RealShort 自己的 scrubPanText（导出清洗用的同一个，开跑前自检一次），认出的整串换成 SCRUBBED。
 * RealShort 的模块在 main 里按路径动态 import。
 */
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  ROW_LIMIT,
  SCRUBBED,
  SNAPSHOT_FORMAT,
  deriveCases,
  isJsonObject,
  parseSnapshotArgs,
  runCase,
  selectCases,
  staticCases,
  stripSensitive,
  tieStats,
  tieSummary,
  type BoardLoaders,
  type CaseRecord,
  type CaseRequest,
  type SnapshotArgs,
  type SnapshotCase,
  type SnapshotDoc,
} from "./pick-board-snapshot-core.rs";

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

/* ---------------------------------------------------------------- RealShort 的模块 */

type RsRequest = CaseRequest;

const RS_MODULES = {
  queries: "src/lib/pick/queries.ts",
  request: "src/lib/pick/request.ts",
  exportV2: "src/lib/pick/export-v2.ts",
  exportMap: "src/lib/pick/export-v2-map.ts",
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
  exportMap: ["scrubPanText"],
  db: ["getDb"],
} as const;

type RsFn = (...args: readonly unknown[]) => unknown;
/** 动态 import 进来、核过导出的 RealShort 模块；单测用假的 */
export type RealShort = {
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
  const names = Object.keys(RS_MODULES) as (keyof typeof RS_MODULES)[];
  const loaded = await Promise.all(names.map((name) => load(RS_MODULES[name])));
  const modules = Object.fromEntries(names.map((name, i) => [name, loaded[i]]));
  for (const [name, fns] of Object.entries(RS_EXPORTS)) {
    const mod = modules[name];
    const absent = fns.filter((fn: string) => typeof mod?.[fn] !== "function");
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

/** 一定该被认出来的网盘文本：RealShort 的清洗器认不出它，就不能拿它清快照 */
const PAN_SELF_TEST = "链接：https://pan.baidu.com/s/1AbCdEfG 提取码：ab12";

/** RealShort 自己的 scrubPanText（导出清洗用的同一个）包成「认不认得出网盘信息」；先自检一次 */
function panDetector(rs: RealShort): (text: string) => boolean {
  const detect = (text: string): boolean => {
    const result = rs.exportMap.scrubPanText(text);
    if (!isJsonObject(result) || typeof result.hits !== "number")
      throw new Stop(2, "RealShort 的 scrubPanText 返回的不是 {text, hits}");
    return result.hits > 0;
  };
  if (!detect(PAN_SELF_TEST))
    throw new Stop(2, "RealShort 的 scrubPanText 认不出网盘链接：不拿它清快照");
  return detect;
}

/* ---------------------------------------------------------------- 编排 */

async function readCollation(rs: RealShort): Promise<string | null> {
  const { sql } = await import("drizzle-orm");
  const { rows } = await (rs.db.getDb() as RsDb).execute(
    sql`SELECT datcollate::text AS c FROM pg_database WHERE datname = current_database()`,
  );
  const first = rows[0];
  return isJsonObject(first) && typeof first.c === "string" ? first.c : null;
}

async function checkSourceAt(
  rs: RealShort,
  fp: string,
  when: string,
  now: Date,
) {
  const result = await rs.exportV2.checkSource(
    fp,
    rs.exportV2.exportContext(now),
  );
  const problem = checkSourceProblem(result, fp);
  if (problem) throw new Stop(3, `${when}核对：${problem}；快照作废，不写文件`);
}

/** 跑用例要用的：loaders、落盘前的网盘判断、进度输出 */
type Runner<Req extends CaseRequest, Meta> = Readonly<{
  loaders: BoardLoaders<Req, Meta>;
  isPanText: (text: string) => boolean;
  progress: (line: string) => void;
}>;

async function runSerially<Req extends CaseRequest, Meta>(
  run: Runner<Req, Meta>,
  cases: readonly SnapshotCase[],
  done: readonly CaseRecord[],
): Promise<CaseRecord[]> {
  let records = [...done];
  for (const kase of cases) {
    const started = Date.now();
    const raw = await runCase(run.loaders, kase);
    const result = stripSensitive(raw, run.isPanText);
    const ms = Date.now() - started;
    records = [...records, { id: kase.id, query: kase.query, ms, result }];
    run.progress(`[${records.length}] ${kase.id} ${ms}ms`);
  }
  return records;
}

async function runAll<Req extends CaseRequest, Meta>(
  run: Runner<Req, Meta>,
  only: RegExp | null,
): Promise<CaseRecord[]> {
  const first = await runSerially(run, selectCases(staticCases(), only), []);
  const results = new Map(first.map((r) => [r.id, r.result]));
  return runSerially(run, selectCases(deriveCases(results), only), first);
}

/** 快照要的外部依赖；main 给真的，单测给假的 */
export type SnapshotDeps = Readonly<{
  now: () => Date;
  /** 核对检出并载入 RealShort 的模块 */
  loadRealShort: () => Promise<RealShort>;
  sourceRevision: string | null;
  /** 只在开头与结尾的核对都通过之后调一次 */
  write: (file: string, text: string) => void;
  progress: (line: string) => void;
}>;

/** 时段 → 载入 → 开头核对 → collation 与全部用例 → 结尾核对 → 写文件；任何一步不通过就抛 Stop、不写 */
export async function takeSnapshot(
  args: SnapshotArgs,
  deps: SnapshotDeps,
): Promise<SnapshotDoc> {
  const window = args.ignoreWindow ? null : busyWindow(deps.now());
  if (window)
    throw new Stop(
      2,
      `现在在「${window}」的时段里：避开后再跑，或加 --ignore-window`,
    );
  const rs = await deps.loadRealShort();
  const isPanText = panDetector(rs);
  const startedAt = deps.now();
  await checkSourceAt(rs, args.fp, "开头", startedAt);
  const collation = await readCollation(rs);
  const loaders = rsLoaders(rs, new Date(args.asOf));
  const cases = await runAll(
    { loaders, isPanText, progress: deps.progress },
    args.only,
  );
  await checkSourceAt(rs, args.fp, "结尾", deps.now());
  const finishedAt = deps.now();
  const doc: SnapshotDoc = {
    format: SNAPSHOT_FORMAT,
    asOf: args.asOf,
    fingerprint: args.fp,
    sourceRevision: deps.sourceRevision,
    collation,
    rowLimit: ROW_LIMIT,
    startedAt: startedAt.toISOString(),
    finishedAt: finishedAt.toISOString(),
    elapsedMs: finishedAt.getTime() - startedAt.getTime(),
    cases,
  };
  deps.write(args.out, `${JSON.stringify(doc)}\n`);
  return doc;
}

/* ---------------------------------------------------------------- main：只在 RealShort 检出里跑 */

async function main(): Promise<number> {
  const parsed = parseSnapshotArgs(process.argv.slice(2));
  if (!parsed.ok) throw new Stop(2, parsed.error);
  const { args } = parsed;
  // 先挡住：跑完几分钟的生产查询再发现写不了就白跑了
  if (existsSync(args.out)) throw new Stop(2, "--out 指的文件已经存在");
  const root = process.cwd();
  const doc = await takeSnapshot(args, {
    now: () => new Date(),
    loadRealShort: async () => {
      checkCheckout(root);
      return loadRealShort(root);
    },
    sourceRevision: commitSha(),
    // wx：不覆盖已有文件；0600：只有自己能读（快照里有 RealShort 的业务数据）
    write: (file, text) =>
      writeFileSync(file, text, { mode: 0o600, flag: "wx" }),
    progress: (line) => process.stderr.write(`${line}\n`),
  });
  const scrubbed = JSON.stringify(doc).split(SCRUBBED).length - 1;
  const ties = tieStats(doc.cases.map((c) => c.result));
  process.stdout.write(
    `快照：${doc.cases.length} 个用例，耗时 ${(doc.elapsedMs / 1000).toFixed(1)} 秒，` +
      `开头与结尾的 fingerprint 核对都通过；网盘信息清洗 ${scrubbed} 格；` +
      `第 ${ROW_LIMIT} 行所在的并列组：${tieSummary(ties)}；写到 ${args.out}\n`,
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
