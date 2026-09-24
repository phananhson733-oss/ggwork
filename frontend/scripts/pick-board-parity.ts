/**
 * pick-board-parity：在一个镜像版本上跑移植后的 loaders，与 RealShort 同一时刻的快照
 * （scripts/pick-board-snapshot.rs.ts 写的）逐字段比，打印白名单内外的差异（P4-3）。
 *
 *   cd frontend && umask 077
 *   PICK_MIRROR_READER_URL="$(cat "$SCRATCH/reader-url.txt")" \
 *   PICK_MIRROR_CA_PEM="$(cat "$SCRATCH/supabase-ca.pem")" \
 *     pnpm exec tsx --conditions=react-server scripts/pick-board-parity.ts --v <N> --snapshot "$SCRATCH/snap.json"
 *
 * 连接串与 CA 从权限 600 的文件经环境变量传入，不写在命令行上。走的是页面同一条读路径：db.ts 的读连接池
 * （CA 校验的 TLS、只读事务）、resolveBoard 与 withScriptScope。只读。
 * 先核对版本：快照的 fingerprint、as_of、sourceRevision 必须与 vN 的相同，否则退出 3。
 * 退出码：0 没有白名单外差异；1 有；2 参数、环境或读库出错；3 快照与版本对不上。
 * 比对规则见 pick-board-parity-compare.ts。
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { sql } from "drizzle-orm";
import { z } from "zod";

import {
  PLATFORMS,
  isRsRank,
  parsePickRequest,
  reelshortId,
  type PickRequest,
} from "@/core/pick-board/request";
import { resolveBoard } from "@/server/pick-board/cache";
import {
  getDb,
  resetMirrorPoolForTests,
  withScriptScope,
} from "@/server/pick-board/db";
import {
  loadFacets,
  loadFreshness,
  loadPickRows,
  loadRowDetail,
} from "@/server/pick-board/queries";
import {
  loadAccounts,
  loadPostedList,
  loadPostedRecord,
  loadPostedStats,
} from "@/server/pick-board/queries-posted";
import {
  loadGrowthDiagnosis,
  loadRankMeta,
  loadRankRows,
  loadRsRank,
  type RankMeta,
} from "@/server/pick-board/queries-rank";
import {
  loadObserveSources,
  loadReelshortDetail,
} from "@/server/pick-board/queries-reelshort";

import {
  RULE_LABELS,
  compareCase,
  merge,
  settleScrub,
  timestampMs,
  type CaseComparison,
  type CompareContext,
  type Finding,
  type RuleId,
  type ScrubUsage,
} from "./pick-board-parity-compare";
import {
  SNAPSHOT_FORMAT,
  readFlags,
  runCase,
  type BoardLoaders,
  type CaseRecord,
  type Json,
  type Parsed,
  type SnapshotDoc,
} from "./pick-board-snapshot-core.rs";

export type ParityArgs = Readonly<{ v: number; snapshot: string }>;

const VERSION_ID = /^[1-9][0-9]{0,5}$/;

export function parseParityArgs(argv: readonly string[]): Parsed<ParityArgs> {
  const read = readFlags(argv, new Set(["--v", "--snapshot"]), new Set());
  if (!read.ok) return read;
  const v = read.args.values.get("--v") ?? "";
  if (!VERSION_ID.test(v))
    return { ok: false, error: "--v 应是镜像版本号 1–999999" };
  const snapshot = read.args.values.get("--snapshot");
  if (!snapshot) return { ok: false, error: "--snapshot 缺值：快照文件" };
  return { ok: true, args: { v: Number(v), snapshot } };
}

const caseSchema = z
  .object({
    id: z.string().min(1).max(80),
    query: z.string().max(2000),
    ms: z.number().nonnegative(),
    result: z.unknown(),
  })
  .strict();

const docSchema = z
  .object({
    format: z.literal(SNAPSHOT_FORMAT),
    asOf: z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/),
    fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
    sourceRevision: z.string().nullable(),
    collation: z.string().nullable(),
    rowLimit: z.number().int().positive(),
    startedAt: z.string(),
    finishedAt: z.string(),
    elapsedMs: z.number().nonnegative(),
    cases: z.array(caseSchema).min(1),
  })
  .strict()
  .refine(
    (doc) => new Set(doc.cases.map((c) => c.id)).size === doc.cases.length,
    {
      message: "用例 id 重复",
    },
  );

/** 快照文件：外来数据，按 schema 校验；result 是 JSON.parse 的产物，本来就是 JSON */
export function readSnapshot(
  raw: string,
):
  | Readonly<{ ok: true; doc: SnapshotDoc }>
  | Readonly<{ ok: false; error: string }> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, error: "快照不是 JSON" };
  }
  const checked = docSchema.safeParse(parsed);
  if (!checked.success)
    return {
      ok: false,
      error: `快照格式不对：${checked.error.issues
        .slice(0, 3)
        .map((i) => `${i.path.join(".") || "（整体）"} ${i.message}`)
        .join("；")}`,
    };
  return { ok: true, doc: checked.data as SnapshotDoc };
}

export type VersionFacts = Readonly<{
  versionId: number;
  asOf: string;
  fingerprint: string | null;
  sourceRevision: string | null;
}>;

/** 快照必须是这个版本那一刻的：fingerprint、as_of（按毫秒）、sourceRevision 都相同 */
export function checkVersionMatch(
  doc: SnapshotDoc,
  version: VersionFacts,
): string | null {
  if (doc.fingerprint !== version.fingerprint)
    return `快照的 fingerprint 不是 v${version.versionId} 的`;
  const asOf = timestampMs(version.asOf);
  if (asOf === null || asOf !== timestampMs(doc.asOf))
    return `快照的 as_of 不是 v${version.versionId} 的 as_of`;
  if (doc.sourceRevision !== version.sourceRevision)
    return `快照的 sourceRevision 不是 v${version.versionId} 的（VERCEL_GIT_COMMIT_SHA 设错了？）`;
  return null;
}

/* ---------------------------------------------------------------- 汇总与打印 */

export type MirrorOutcome =
  | Readonly<{ ok: true; result: Json }>
  | Readonly<{ ok: false; error: string }>;

export type WhitelistSummary = Readonly<{
  rule: RuleId;
  label: string;
  count: number;
  examples: readonly string[];
}>;

export type ParityReport = Readonly<{
  caseCount: number;
  failures: readonly Finding[];
  whitelisted: readonly WhitelistSummary[];
  scrubUsage: readonly ScrubUsage[];
}>;

function compareOne(
  kase: Pick<CaseRecord, "id" | "result">,
  outcome: MirrorOutcome | undefined,
  ctx: CompareContext,
): CaseComparison {
  const at = { caseId: kase.id, path: "", rule: null };
  if (!outcome)
    return {
      findings: [{ ...at, what: "镜像这边没有跑这个用例" }],
      charges: [],
    };
  if (!outcome.ok)
    return {
      findings: [{ ...at, what: `镜像这边出错：${outcome.error}` }],
      charges: [],
    };
  return compareCase(kase.id, kase.result, outcome.result, ctx);
}

function summarize(findings: readonly Finding[]): WhitelistSummary[] {
  return (Object.keys(RULE_LABELS) as RuleId[]).flatMap((rule) => {
    const hits = findings.filter((f) => f.rule === rule);
    return hits.length
      ? [
          {
            rule,
            label: RULE_LABELS[rule],
            count: hits.length,
            examples: hits.slice(0, 3).map((f) => `${f.caseId} ${f.path}`),
          },
        ]
      : [];
  });
}

export function compareSnapshot(input: {
  rsCases: readonly Pick<CaseRecord, "id" | "result">[];
  mirror: ReadonlyMap<string, MirrorOutcome>;
  ctx: CompareContext;
}): ParityReport {
  const compared = merge(
    input.rsCases.map((kase) =>
      compareOne(kase, input.mirror.get(kase.id), input.ctx),
    ),
  );
  const scrub = settleScrub(compared.charges, input.ctx.scrub);
  const findings = [...compared.findings, ...scrub.findings];
  return {
    caseCount: input.rsCases.length,
    failures: findings.filter((f) => f.rule === null),
    whitelisted: summarize(findings),
    scrubUsage: scrub.usage,
  };
}

const MAX_PRINTED = 200;

function failureLine(f: Finding): string {
  const values =
    f.rs !== undefined || f.mirror !== undefined
      ? `（RealShort ${f.rs ?? "（无）"}；镜像 ${f.mirror ?? "（无）"}）`
      : "";
  return `  ${f.caseId} ${f.path || "（整个用例）"}：${f.what}${values}`;
}

export function formatReport(report: ParityReport): string[] {
  const shown = report.failures.slice(0, MAX_PRINTED).map(failureLine);
  const more = report.failures.length - shown.length;
  return [
    `用例 ${report.caseCount} 个`,
    `白名单外差异：${report.failures.length}`,
    ...shown,
    ...(more > 0 ? [`  ……另有 ${more} 条没有列出`] : []),
    `白名单内差异：${report.whitelisted.reduce((n, w) => n + w.count, 0)}`,
    ...report.whitelisted.map(
      (w) => `  ${w.label}：${w.count} 处（例：${w.examples.join("；")}）`,
    ),
    "清洗占位（观察到的格子 / meta.scrub 记的次数；快照只含每个用例的前 50 行）：",
    ...(report.scrubUsage.length
      ? report.scrubUsage.map((u) => `  ${u.key}：${u.observed} / ${u.budget}`)
      : ["  meta.scrub 为空"]),
  ];
}

/** 版本 platformRules 里本地 PLATFORMS 没有的剧场键，按版本里的顺序 */
export function platformGap(
  platformRules: Readonly<Record<string, unknown>>,
  platforms: readonly string[],
): string[] {
  return Object.keys(platformRules).filter((key) => !platforms.includes(key));
}

/* ---------------------------------------------------------------- main */

const mirrorLoaders: BoardLoaders<PickRequest, RankMeta> = {
  parse: (params) => parsePickRequest(params),
  isRsRank: (rank) => isRsRank(rank),
  reelshortId: (rowKey) => reelshortId(rowKey),
  pickRows: loadPickRows,
  facets: loadFacets,
  rankMeta: loadRankMeta,
  rankRows: loadRankRows,
  rsRank: async (req) => {
    if (!isRsRank(req.rank)) throw new Error("不是 ReelShort 的榜");
    return loadRsRank(req, req.rank);
  },
  growthDiagnosis: loadGrowthDiagnosis,
  postedList: loadPostedList,
  postedRecord: loadPostedRecord,
  postedStats: loadPostedStats,
  accounts: loadAccounts,
  rowDetail: loadRowDetail,
  reelshortDetail: loadReelshortDetail,
  freshness: loadFreshness,
  sources: loadObserveSources,
};

class Stop extends Error {
  constructor(
    readonly code: number,
    message: string,
  ) {
    super(message);
  }
}

const metaSchema = z.object({
  fingerprint: z.string().nullable().default(null),
  sourceRevision: z.string().nullable().default(null),
  scrub: z.record(z.number().int().nonnegative()).default({}),
});

async function readVersionFacts() {
  const { rows } = await getDb().execute<{ key: string; value: unknown }>(
    sql`SELECT key, value FROM meta WHERE key IN ('fingerprint', 'sourceRevision', 'scrub')`,
  );
  const meta = metaSchema.safeParse(
    Object.fromEntries(rows.map((r) => [r.key, r.value])),
  );
  if (!meta.success)
    throw new Stop(
      3,
      "版本 meta 的 fingerprint / sourceRevision / scrub 不是预期形状",
    );
  const collation = await getDb().execute<{ c: string }>(
    sql`SELECT datcollate::text AS c FROM pg_database WHERE datname = current_database()`,
  );
  return { ...meta.data, collation: collation.rows[0]?.c ?? null };
}

function errorLabel(error: unknown): string {
  if (!(error instanceof Error)) return "unknown";
  return `${error.name}${error.message ? `：${error.message.slice(0, 200)}` : ""}`;
}

async function runMirror(
  doc: SnapshotDoc,
): Promise<Map<string, MirrorOutcome>> {
  let outcomes = new Map<string, MirrorOutcome>();
  for (const kase of doc.cases) {
    const outcome: MirrorOutcome = await runCase(mirrorLoaders, kase).then(
      (result: Json) => ({ ok: true as const, result }),
      (error: unknown) => ({ ok: false as const, error: errorLabel(error) }),
    );
    outcomes = new Map([...outcomes, [kase.id, outcome]]);
  }
  return outcomes;
}

async function compareInScope(
  doc: SnapshotDoc,
  header: readonly string[],
  versionId: number,
  asOf: string,
): Promise<number> {
  const facts = await readVersionFacts();
  const mismatch = checkVersionMatch(doc, { ...facts, versionId, asOf });
  if (mismatch) throw new Stop(3, mismatch);
  const started = Date.now();
  const mirror = await runMirror(doc);
  const collationDiffers =
    doc.collation !== null &&
    facts.collation !== null &&
    doc.collation !== facts.collation;
  const report = compareSnapshot({
    rsCases: doc.cases,
    mirror,
    ctx: { scrub: facts.scrub, collationDiffers },
  });
  const lines = [
    ...header,
    `collation：RealShort ${doc.collation ?? "未知"}，镜像 ${facts.collation ?? "未知"}${collationDiffers ? "（不同：剧名排序进白名单）" : ""}`,
    `镜像这边耗时 ${((Date.now() - started) / 1000).toFixed(1)} 秒`,
    ...formatReport(report),
  ];
  process.stdout.write(`${lines.join("\n")}\n`);
  return report.failures.length ? 1 : 0;
}

async function parity(args: ParityArgs): Promise<number> {
  const read = readSnapshot(readFileSync(args.snapshot, "utf8"));
  if (!read.ok) throw new Stop(2, read.error);
  const { doc } = read;
  const board = await resolveBoard(args.v);
  if (board.state !== "ready" || board.scope.versionId !== args.v)
    throw new Stop(3, `镜像 v${args.v} 不存在、未发布、已清理或 reader 读不了`);
  const gap = platformGap(board.scope.rules.platformRules, PLATFORMS);
  const header = [
    `parity：镜像 v${args.v}（${board.scope.schema}，as_of ${board.scope.asOf}）对 RealShort 快照（${doc.cases.length} 个用例，RealShort 那边耗时 ${(doc.elapsedMs / 1000).toFixed(1)} 秒）`,
    `本地剧场键差集（版本 platformRules − PLATFORMS）：${gap.length ? gap.join("、") : "无"}`,
  ];
  return withScriptScope(board.scope, () =>
    compareInScope(doc, header, args.v, board.scope.asOf),
  );
}

async function main(): Promise<number> {
  const parsed = parseParityArgs(process.argv.slice(2));
  if (!parsed.ok) throw new Stop(2, parsed.error);
  try {
    return await parity(parsed.args);
  } finally {
    await resetMirrorPoolForTests();
  }
}

function reportFailure(error: unknown): number {
  const code = error instanceof Stop ? error.code : 2;
  const text = error instanceof Stop ? error.message : errorLabel(error);
  process.stderr.write(`pick-board-parity：${text}\n`);
  return code;
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
