import {writeCatalogAtomic} from "../runtime/catalog-write";
/**
 * 选剧台的入库：scripts/juyuantai/catalog.json + posted.json → catalog_rows / catalog_signals / catalog_posted。
 *
 *   pnpm catalog-import                # 全量替换写库
 *   pnpm catalog-import -- --dry       # 只匹配、只打印各表行数，不写库
 *   pnpm catalog-import -- --dir <目录>  # 两份 json 不在 scripts/juyuantai/ 时指定
 *   pnpm catalog-import -- --force-rows|--force-signals|--force-posted|--force-accounts
 *                                      # 放行某一张表的骤降保护（只放行指定的那张）
 *   pnpm catalog-import -- --stale-ok  # 允许用超过 6 小时前产出的 catalog.json / posted.json
 *
 * 前置：bash fetch.sh → python3 build.py → python3 posted.py（都在 scripts/juyuantai/，需要飞书用户登录，
 * 所以这条链路不挂 Cron，理由与 pnpm sync-editorial 相同）。
 * 设计见 docs/plans/2026-09-11-pick-station-design.md。
 *
 * 【全量替换，不是增量 upsert】：源头是一次完整快照，上一轮有、这一轮没有的行就是剧场下架或改名，
 * 增量会把它们永久留在表里而页面上看不出来。做法是带批次时间戳 upsert，全部成功后删掉旧批次；
 * 任一批写失败就抛错退出，旧数据原样留着（页面照常能开）。
 *
 * 【neon-http 没有交互式事务】，db.batch() 才是原子的；catalog_rows 40,640 行约 12 MB
 * 超出单次 HTTP 请求该有的体量，所以分 1,000 行一批 upsert；信号（2,492 行）与发布记录（71 行）
 * 各自一个 batch 原子替换。
 *
 * 【三张表凑不成一个原子写，所以在写之前守门】2026-09-11 生产事故：飞书选剧池两条记录的编号
 * 短暂重号（当时以为是自动编号没落地；2026-09-18 实测「剧ID」是手填 text 列，见 posted-checks.ts），一路走到数据库才抛 catalog_posted_pkey 冲突。那时 catalog_rows 已经提交完，留下
 * 「剧单是新的、发布记录是旧的」的半截状态；更麻烦的是服务端那道过期兜底看的是
 * catalog_rows.importedAt（src/app/api/health/route.ts），这种半截它根本不会告警。
 * neon-http 下补不出跨表事务，于是反过来做：凡是写之前能发现的，一律别走到写库那一步——
 * 见 preflight()，它查主键（空编号 / 形状 / 重复，三类一次全报，判定在 juyuantai/posted-checks.ts）、以及相对库里现有行数的骤降（空快照会把线上数据清掉，
 * 因为 writePosted 的 delete 是无条件执行的）。写完再对一次行数，半截状态当场抛错而不是静悄悄留着。
 *
 * 【写库阶段前后有忙标记 pick_catalog】（2026-09-23，feed v2 方案 4.3，P1-5）：第一次写之前 observe_sources 置 running，
 * 最后一次写与写后核对之后置 success，中途抛错置 failed（顺序在 lib/pick/catalog-import.ts 的 writeCatalogMarked，
 * tests/pick-import.test.ts 钉住）。feed v2 导出与带 fp 的 v1 见到 running 就回 503，不会把新行配上旧信号；
 * failed 或超过 45 分钟的 running 只做可见性（manifest 告警 catalog_import_incomplete、旧选剧台页底「剧单导入」一行），
 * 不拦截读取。恢复方式永远是整次重跑 pnpm catalog-import，不手工改 observe_sources。
 * 【上线顺序：先在库上执行 scripts/sql/observe-source-pick-catalog.sql，再部署这份代码】，否则 begin 撞 CHECK 约束，
 * 一行不写就退出。
 *
 * 【两次导入互斥】（2026-09-23 验收）：两个人先后跑 catalog-import 时，后一次的 begin 会顶替前一次的 attempt。
 * 每个写库 batch（catalog_rows 的每个分块、删旧批次、信号、发布记录与账号、最后的 success）第一句都用 sourceGuardSql
 * 核验并锁住本次的所有权（见 ownedBatch）：被顶替的那次，下一批整批回滚并抛错，不会把旧快照写回去；
 * 标 failed 只认自己的 attempt，不碰新的那次。
 */
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { lt, sql } from "drizzle-orm";
import type { BatchItem } from "drizzle-orm/batch";

import { getDb, getPool } from "@/db";
import { catalogAccounts, catalogPosted, catalogRows, catalogSignals } from "@/db/schema";
import { beginSource, failSource, sourceGuardSql, sourceSuccessSql, sourceSuperseded } from "@/lib/observe/source-state";
import type { SourceAttempt, SourceDetails } from "@/lib/observe/source-types";
import {
  type CanonicalDrama,
  matchRow,
  toAccountRows,
  toCatalogRow,
  toPostedRows,
  toSignalRows,
  type CatalogAccountInsert,
  type CatalogRowInsert,
  type CatalogSignalInsert,
  type MatchTables,
  type RawAccount,
  type RawCatalogRow,
  type RawPostedDrama,
} from "@/lib/pick/catalog-import";
import { publicCanonical } from "@/lib/queries";

import { postedKeyProblems } from "./juyuantai/posted-checks";

const BATCH = 1000;
const RETAIN_MOBO = process.env.PICK_SOURCE_RETAIN_MOBOREELS === "1";

function arg(name: string): string | null {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? (process.argv[i + 1] ?? null) : null;
}

function readJson<T>(dir: string, name: string): T {
  const path = join(dir, name);
  try {
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch (error) {
    throw new Error(
      `读不到 ${path}：先在 scripts/juyuantai/ 跑 bash fetch.sh && python3 build.py && python3 posted.py。` +
        `（${error instanceof Error ? error.message : String(error)}）`,
    );
  }
}

/** 与 scripts/juyuantai/match.ts 的三条查询逐字相同；正典片段引用不带别名的 dramas，所以不能起别名 */
async function loadMatchTables(): Promise<{ tables: MatchTables; canonicalDramas: CanonicalDrama[] }> {
  const db = getDb();
  const site = await db.execute(
    sql`select title_key, array_agg(distinct locale) as locales from dramas where detail_synced_at is not null group by title_key`,
  );
  const legacy = await db.execute(
    sql`select title_key, array_agg(distinct locale) as locales from legacy_urls group by title_key`,
  );
  const canon = await db.execute(
    sql`select title_key, locale, id, title from dramas where ${publicCanonical()} order by locale, id`,
  );
  const siteLocales = new Map<string, string[]>();
  for (const r of site.rows as { title_key: string; locales: string[] }[])
    siteLocales.set(r.title_key, r.locales);
  const legacyLocales = new Map<string, string[]>();
  for (const r of legacy.rows as { title_key: string; locales: string[] }[])
    legacyLocales.set(r.title_key, r.locales);
  const canonical = new Map<string, { id: string; locale: string }[]>();
  const canonicalDramas: CanonicalDrama[] = [];
  for (const r of canon.rows as { title_key: string; locale: string; id: string; title: string }[]) {
    const arr = canonical.get(r.title_key) ?? [];
    arr.push({ id: r.id, locale: r.locale });
    canonical.set(r.title_key, arr);
    canonicalDramas.push({ id: r.id, title: r.title, locale: r.locale });
  }
  return { tables: { siteLocales, canonical, legacyLocales }, canonicalDramas };
}

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/**
 * pick_catalog 的一个写库 batch：第一句 sourceGuardSql 核验并锁住本次 attempt 的所有权，锁一直持到这一批提交
 * （新的一次 begin 要等这批写完才能顶替）。已被顶替时那一句除零、整批回滚；读一次 attempt_id 确认是被顶替了，
 * 就换成说得清的错误抛出（原错误挂在 cause 上），这次导入就此停下。返回除核验那一句以外各语句的结果。
 */
async function ownedBatch(attempt: SourceAttempt, writes: BatchItem<"pg">[]): Promise<unknown[]> {
  const db = getDb();
  try {
    const results = await db.batch([db.execute(sourceGuardSql("pick_catalog", attempt)), ...writes] as unknown as Parameters<
      typeof db.batch
    >[0]);
    return (results as readonly unknown[]).slice(1);
  } catch (error) {
    if (await sourceSuperseded("pick_catalog", attempt).catch(() => false))
      throw new Error(
        `这次剧单导入已被新的一次导入顶替（observe_sources 的 pick_catalog 换了 attempt），这一批已整批回滚、不再往下写。` +
          `以新的那次为准：它跑完就好，不要为这次重跑。`,
        { cause: error },
      );
    throw error;
  }
}

async function writeRows(rows: CatalogRowInsert[], stamp: Date, attempt: SourceAttempt) {
  const db = getDb();
  const set = Object.fromEntries(
    Object.keys(rows[0])
      .filter((k) => k !== "rowKey")
      .map((k) => [k, sql.raw(`excluded.${camelToSnake(k)}`)]),
  );
  let done = 0;
  for (const part of chunk(rows.filter((r) => !RETAIN_MOBO || r.platform !== "moboreels"), BATCH)) {
    await ownedBatch(attempt, [
      db
        .insert(catalogRows)
        .values(part.map((r) => ({ ...r, importedAt: stamp })))
        .onConflictDoUpdate({
          target: catalogRows.rowKey,
          set: { ...set, importedAt: stamp },
        }),
    ]);
    done += part.length;
    process.stdout.write(`\r  catalog_rows ${done}/${rows.length}`);
  }
  process.stdout.write("\n");
  const [stale] = await ownedBatch(attempt, [
    db.delete(catalogRows).where(sql`${catalogRows.importedAt} < ${stamp} AND (${!RETAIN_MOBO} OR ${catalogRows.platform} <> 'moboreels')`).returning({ rowKey: catalogRows.rowKey }),
  ]);
  console.log(`  旧批次删除 ${(stale as {rowCount:number}).rowCount} 行`);
}

async function writeSignals(signals: CatalogSignalInsert[], attempt: SourceAttempt) {
  const db = getDb();
  const preserved = RETAIN_MOBO ? new Set((await db.execute<{row_key:string}>(sql`SELECT row_key FROM catalog_rows WHERE platform='moboreels'`)).rows.map((r)=>r.row_key)) : new Set<string>();
  const parts = chunk(signals.filter((s)=>!preserved.has(s.rowKey)), BATCH);
  /* 删旧 + 插新在一个 batch 里，页面看不到「一秒钟没有任何信号」的中间态；
     最后一句是安全网：行已经不在了的信号（理论上上面的全量替换后不会有） */
  await ownedBatch(attempt, [
    db.delete(catalogSignals).where(sql`${!RETAIN_MOBO} OR row_key NOT IN (SELECT row_key FROM catalog_rows WHERE platform='moboreels')`),
    ...parts.map((part) => db.insert(catalogSignals).values(part)),
    db.execute(sql`delete from catalog_signals where row_key not in (select row_key from catalog_rows)`),
  ]);
}

async function writePosted(posted: ReturnType<typeof toPostedRows>, accounts: CatalogAccountInsert[], attempt: SourceAttempt) {
  const db = getDb();
  /* 发布记录与账号台账来自同一份 posted.json，一个 batch 里一起换 */
  await ownedBatch(attempt, [
    db.delete(catalogPosted),
    ...(posted.length ? [db.insert(catalogPosted).values(posted)] : []),
    db.delete(catalogAccounts),
    ...(accounts.length ? [db.insert(catalogAccounts).values(accounts)] : []),
  ]);
}

/**
 * 写完对一次行数：三张表分三次提交，中途断了会留下半截状态，这里当场抛而不是静悄悄留着。
 * 它是忙标记里「最后一次写之后」的那一步：抛错时 pick_catalog 标 failed，通过才 success。
 */
async function verifyCounts(expected: { rows: number; signals: number; posted: number; accounts: number }): Promise<SourceDetails> {
  const db = getDb();
  const [counts] = (
    await db.execute(
      sql`select (select count(*) from catalog_rows) as rows, (select count(*) from catalog_signals) as signals,
                 (select count(*) from catalog_posted) as posted, (select count(*) from catalog_accounts) as accounts`,
    )
  ).rows as { rows: string; signals: string; posted: string; accounts: string }[];
  const mismatch: string[] = [];
  if (Number(counts.rows) !== expected.rows) mismatch.push(`catalog_rows 期望 ${expected.rows} 实际 ${counts.rows}`);
  if (Number(counts.posted) !== expected.posted)
    mismatch.push(`catalog_posted 期望 ${expected.posted} 实际 ${counts.posted}`);
  if (Number(counts.accounts) !== expected.accounts)
    mismatch.push(`catalog_accounts 期望 ${expected.accounts} 实际 ${counts.accounts}`);
  if (mismatch.length)
    throw new Error(
      `写完之后行数对不上（${mismatch.join("；")}）。三张表可能处于半截状态，` +
        `服务端那道过期兜底只看 catalog_rows.importedAt，不会替你发现。立刻重跑 pnpm catalog-import。`,
    );

  console.log(
    `写入完成：catalog_rows ${counts.rows} / catalog_signals ${counts.signals} / catalog_posted ${counts.posted} / catalog_accounts ${counts.accounts}`,
  );
  const bad =
    Number(counts.rows) !== expected.rows ||
    Number(counts.signals) !== expected.signals ||
    Number(counts.posted) !== expected.posted ||
    Number(counts.accounts) !== expected.accounts;
  if (bad) throw new Error("写入后行数与 json 不一致，检查上面四个数");
  return { rows: Number(counts.rows) };
}

/** 快照相对库里现有行数的下限：低于这个比例就认为源头残缺，不写库（按表的 --force-* 可越过） */
const SHRINK_GUARD = 0.5;
/** 输入文件最多允许多旧（小时）。挡的是「拿昨天的 catalog.json 重跑一遍」当成今天的数据 */
const MAX_INPUT_AGE_H = 6;

/** 挑出出现超过一次的键 */
function duplicates<T>(items: T[], key: (t: T) => string): string[] {
  const seen = new Map<string, number>();
  for (const it of items) {
    const k = key(it);
    seen.set(k, (seen.get(k) ?? 0) + 1);
  }
  return [...seen.entries()].filter(([, n]) => n > 1).map(([k]) => k);
}

/**
 * 写库前的守门。三张表不能凑成一个事务（见文件头），所以可预见的失败必须在这里拦住，
 * 而不是让它在写到一半时抛出来。抛错时旧数据一行没动，页面照常能开。
 */
async function preflight(
  input: {
    rows: CatalogRowInsert[];
    signals: CatalogSignalInsert[];
    postedRows: ReturnType<typeof toPostedRows>;
    accountRows: CatalogAccountInsert[];
    /** posted.json 里选剧池那张表的链接，只用来印进守门文案 */
    poolUrl?: string;
  },
  force: { rows: boolean; signals: boolean; posted: boolean; accounts: boolean },
) {
  const { rows, signals, postedRows, accountRows } = input;
  const dupRowKeys = duplicates(rows, (r) => r.rowKey);
  if (dupRowKeys.length)
    throw new Error(
      `row_key 有重复（${dupRowKeys.length} 个，如 ${dupRowKeys.slice(0, 5).join("、")}），build.py 的合并出了问题，不写库`,
    );

  /* 主键三类问题（空编号 / 形状不合法 / 重复）一次全报，判定在纯模块 juyuantai/posted-checks.ts。
     2026-09-18：空编号先抛把重复挡在后面，运营修完 4 条第二天再撞 3 条；「等自动编号落地」那句
     也被实测推翻——「剧ID」是手填 text 列，空着不会自己补 */
  const keyProblems = postedKeyProblems(postedRows, input.poolUrl);
  if (keyProblems.length) throw new Error(keyProblems.join("\n"));

  /* 骤降保护：源头返回空或残缺快照时，全量替换会把线上数据清掉，而链路照常 exit 0 */
  const db = getDb();
  const [cur] = (
    await db.execute(
      sql`select (select count(*) from catalog_rows) as rows,
                 (select count(*) from catalog_signals) as signals,
                 (select count(*) from catalog_posted) as posted,
                 (select count(*) from catalog_accounts) as accounts`,
    )
  ).rows as { rows: string; signals: string; posted: string; accounts: string }[];
  /* 每张表各有各的开关：放行剧单的骤降不应该顺带放行「发布记录变成 0 行」 */
  const guard = (name: string, flag: string, incoming: number, existing: number, forced: boolean) => {
    if (existing === 0 || forced) return; // 首次导入，或人已针对这张表确认
    if (incoming >= existing * SHRINK_GUARD) return;
    throw new Error(
      `${name} 这一轮只有 ${incoming} 行，库里现有 ${existing} 行，跌破 ${SHRINK_GUARD * 100}% 下限，不写库。` +
        `源头多半返回了空或残缺快照（飞书权限变化、分页回归、登录态半失效）。` +
        `确认这就是真实数据，就只放行这一张：pnpm catalog-import -- ${flag}`,
    );
  };
  guard("剧单 catalog_rows", "--force-rows", rows.length, Number(cur.rows), force.rows);
  /* writeSignals 的 delete 同样是无条件执行的，空信号快照一样会清表 */
  guard("信号 catalog_signals", "--force-signals", signals.length, Number(cur.signals), force.signals);
  guard("发布记录 catalog_posted", "--force-posted", postedRows.length, Number(cur.posted), force.posted);
  guard("账号台账 catalog_accounts", "--force-accounts", accountRows.length, Number(cur.accounts), force.accounts);
}

function camelToSnake(s: string): string {
  return s.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
}

async function main() {
  const dry = process.argv.includes("--dry");
  /* 骤降保护的人工越权，按表分开：放行剧单不应该顺带放行「发布记录变成 0 行」 */
  const force = {
    rows: process.argv.includes("--force-rows"),
    signals: process.argv.includes("--force-signals"),
    posted: process.argv.includes("--force-posted"),
    accounts: process.argv.includes("--force-accounts"),
  };
  const dir = arg("dir") ?? join(import.meta.dirname, "juyuantai");
  const catalog = readJson<{ built: string; rows: RawCatalogRow[] }>(dir, "catalog.json");
  const posted = readJson<{ dramas: RawPostedDrama[]; accounts?: RawAccount[]; nPosts?: number; poolUrl?: string }>(
    dir,
    "posted.json",
  );
  /* 输入必须是这一轮刚产出的。daily.sh 里任一步失败都会停，但人手动只跑 catalog-import 时，
     很容易拿着昨天的 json 又导一遍，而导入是全量替换，等于把旧快照重新发布一次 */
  if (!process.argv.includes("--stale-ok")) {
    for (const f of ["catalog.json", "posted.json"]) {
      const ageH = (Date.now() - statSync(join(dir, f)).mtimeMs) / 3_600_000;
      if (ageH > MAX_INPUT_AGE_H)
        throw new Error(
          `${f} 是 ${ageH.toFixed(1)} 小时前产出的（上限 ${MAX_INPUT_AGE_H} 小时），多半不是这一轮的数据，不写库。` +
            `正常应该整条跑 pnpm catalog-refresh；确实要用这份旧文件就加 --stale-ok。`,
        );
    }
  }

  console.log(
    `catalog.json 构建于 ${catalog.built}：${catalog.rows.length} 行；posted.json：${posted.dramas.length} 部、账号 ${(posted.accounts ?? []).length} 个`,
  );

  console.log("匹配本站在售 / 旧站收录（三条只读查询）…");
  const { tables, canonicalDramas } = await loadMatchTables();
  if (RETAIN_MOBO) catalog.rows = catalog.rows.filter((r)=>r.p!=="moboreels");
  const rows = catalog.rows.map((r) => toCatalogRow(r, matchRow(r, tables)));
  const signals = catalog.rows.flatMap(toSignalRows);
  if (RETAIN_MOBO) {
    const saved = await getDb().select().from(catalogRows).where(sql`platform='moboreels'`);
    if (!saved.length) throw new Error("No complete MoboReels snapshot is available to retain");
    rows.push(...saved.map(({importedAt: _at,...r})=>r));
    const savedSignals = await getDb().select().from(catalogSignals).where(sql`row_key IN (SELECT row_key FROM catalog_rows WHERE platform='moboreels')`);
    signals.push(...savedSignals.map((s)=>({...s,payload:s.payload as Record<string,unknown>})));
    catalog.rows.push(...saved.map((r)=>({k:r.rowKey,p:r.platform,src:r.sourceTable,t:r.title,cn:r.titleCn,lang:r.lang})));
  }
  const postedRows = toPostedRows(posted.dramas, catalog.rows, canonicalDramas);
  const accountRows = toAccountRows(posted.accounts ?? []);

  const inSite = rows.filter((r) => r.inSiteIds.length).length;
  const other = rows.filter((r) => r.siteOther).length;
  const legacy = rows.filter((r) => r.legacyOnly).length;
  const matchedPosted = postedRows.filter((p) => p.rowKeys.length || p.dramaIds.length).length;
  console.log(
    `  行 ${rows.length}（有信号 ${rows.filter((r) => r.hasSignal).length}，在售 ${inSite}，同名·他语种在售 ${other}，旧站收录 ${legacy}）` +
      `；信号 ${signals.length}；发布记录 ${postedRows.length} 部（对上剧库 ${matchedPosted} 部：剧场行 ${postedRows.filter((p) => p.rowKeys.length).length} / ReelShort ${postedRows.filter((p) => p.dramaIds.length).length}；已发 ${postedRows.filter((p) => p.postCount > 0).length} 部）；账号 ${accountRows.length} 个`,
  );
  /* 写之前守门：三张表不是一个事务，可预见的失败必须在这里拦住（见文件头） */
  await preflight({ rows, signals, postedRows, accountRows, poolUrl: posted.poolUrl }, force);
  if (dry) {
    console.log("--dry：不写库（预检已通过）");
    return;
  }

  const stamp = new Date();
  /* 三次写库与写后核对都在 pick_catalog 忙标记里（见文件头）：守门与 --dry 在上面，一行不写，不留标记 */
  await writeCatalogAtomic({
    begin: () => beginSource("pick_catalog"),
    writeRows: (attempt) => writeRows(rows, stamp, attempt),
    writeSignals: (attempt) => writeSignals(signals, attempt),
    writePosted: (attempt) => writePosted(postedRows, accountRows, attempt),
    verify: () =>
      verifyCounts({ rows: rows.length, signals: signals.length, posted: postedRows.length, accounts: accountRows.length }),
    /* success 也是一个写库 batch：被顶替时抛错，不会静悄悄更新 0 行就当作导入成功 */
    complete: async (attempt, details) => {
      await ownedBatch(attempt, [getDb().execute(sourceSuccessSql("pick_catalog", attempt, details))]);
    },
    fail: (attempt) => failSource("pick_catalog", attempt),
    log: (line) => console.error(line),
  });
}

main().catch((_error) => {
  console.error("Catalog import failed; previous committed snapshot preserved");
  process.exitCode = 1;
}).finally(() => getPool().end());
