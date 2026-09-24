import "server-only";

import { sql, type SQL } from "drizzle-orm";
import { PgDialect } from "drizzle-orm/pg-core";

import { buildBoardRules, type BoardRules } from "@/core/pick-board/rules";

import {
  boardScope,
  controlDb,
  getDb,
  versionDb,
  type Executor,
  type QueryRows,
  type VersionScope,
} from "./db";
import { resolveVersion, type BoardVersion } from "./version";

/**
 * 进程内、按版本的小缓存（批判 A3）。
 *
 * 列表页一次渲染约 13 条查询、峰值 7 条并发（RealShort 的并发结构原样保留），而读连接池每实例只有 3 条。
 * 一个 pickm_vNNNNNN 发布之后内容不再变，版本号也不复用，所以「这个版本的 meta.rules / meta.sources /
 * rsCounts / growthBaseline / freshness」与「这个版本的候选池 N」读一次就能一直用：每次渲染少 2–4 条往返。
 * 只缓存「只取决于版本 schema」的东西；取决于请求（筛选、页码、as_of 往后挪）的查询一律不缓存。
 * pick_mirror.series 在发布后还会折叠截断，不是版本的一部分，也不缓存。
 *
 * LRU 是纯函数（旧的不动，返回新的）；唯一的可变容器是注入进来的 CacheCell，生产上是模块级的一个。
 * 一个版本被清理后它的条目不会再被读到（选哪个版本每次都实时查 pick_mirror.versions），随 LRU 自然淘汰。
 */

/** 最近使用的在末尾 */
export type Lru<V> = Readonly<{
  capacity: number;
  entries: readonly (readonly [string, V])[];
}>;

function frozen<V>(
  capacity: number,
  entries: (readonly [string, V])[],
): Lru<V> {
  return Object.freeze({ capacity, entries: Object.freeze(entries) });
}

export function emptyLru<V>(capacity: number): Lru<V> {
  if (!Number.isInteger(capacity) || capacity < 1)
    throw new RangeError("LRU capacity must be a positive integer");
  return frozen<V>(capacity, []);
}

function peek<V>(lru: Lru<V>, key: string): V | undefined {
  return lru.entries.find(([k]) => k === key)?.[1];
}

/** 取值，并把它挪到最近使用的位置 */
export function lruGet<V>(
  lru: Lru<V>,
  key: string,
): Readonly<{ value: V | undefined; lru: Lru<V> }> {
  const hit = lru.entries.find(([k]) => k === key);
  if (!hit) return { value: undefined, lru };
  const rest = lru.entries.filter(([k]) => k !== key);
  return { value: hit[1], lru: frozen(lru.capacity, [...rest, hit]) };
}

/** 放进去；满了就丢最久没用的 */
export function lruPut<V>(lru: Lru<V>, key: string, value: V): Lru<V> {
  const rest = lru.entries.filter(([k]) => k !== key);
  const kept = rest.slice(Math.max(0, rest.length - (lru.capacity - 1)));
  return frozen(lru.capacity, [...kept, [key, value] as const]);
}

function lruDelete<V>(lru: Lru<V>, key: string): Lru<V> {
  return frozen(
    lru.capacity,
    lru.entries.filter(([k]) => k !== key),
  );
}

/** 进程边界上唯一的可变容器：只有 remember 换它里面的 LRU */
export type CacheCell<V> = { lru: Lru<V> };

export function makeCacheCell<V>(capacity: number): CacheCell<V> {
  return { lru: emptyLru<V>(capacity) };
}

/**
 * 同一个键只 load 一次：存的是 Promise，并发的调用者拿到同一个。load 失败时把这个键忘掉，
 * 下一次重新读（失败不进缓存）。
 */
export async function remember<V>(
  cell: CacheCell<Promise<V>>,
  key: string,
  load: () => Promise<V>,
): Promise<V> {
  const read = lruGet(cell.lru, key);
  cell.lru = read.lru;
  if (read.value) return read.value;
  const pending = load();
  cell.lru = lruPut(cell.lru, key, pending);
  try {
    return await pending;
  } catch (error) {
    if (peek(cell.lru, key) === pending) cell.lru = lruDelete(cell.lru, key);
    throw error;
  }
}

/** 一个版本约 7 个键（resolve 的 meta、四个 meta 键、候选池），当前版本加几个钉住的版本 */
const VERSION_CACHE_ENTRIES = 64;

let versionCache = makeCacheCell<Promise<unknown>>(VERSION_CACHE_ENTRIES);

/** 测试钩子：换一个空缓存 */
export function resetVersionCacheForTests(): void {
  versionCache = makeCacheCell<Promise<unknown>>(VERSION_CACHE_ENTRIES);
}

export type VersionCacheDeps = Readonly<{
  scope: () => VersionScope<unknown>;
  cell: CacheCell<Promise<unknown>>;
}>;

const defaultCacheDeps = (): VersionCacheDeps => ({
  scope: boardScope,
  cell: versionCache,
});

/** 钉住的版本上、只取决于版本的一次读：按版本 schema 记住 */
export async function rememberForVersion<V>(
  what: string,
  load: () => Promise<V>,
  deps: VersionCacheDeps = defaultCacheDeps(),
): Promise<V> {
  const key = `${deps.scope().schema}|${what}`;
  return remember(deps.cell as CacheCell<Promise<V>>, key, load);
}

/** 页面与查询读到的 meta 键（驼峰，P2 原样写入） */
export type MetaKey =
  | "freshness"
  | "rsCounts"
  | "growthBaseline"
  | "sources"
  | "latestSnapshot";

export type MetaDeps = VersionCacheDeps & Readonly<{ db: () => Executor }>;

/** 钉住版本的一个 meta 值（原样的 JSON）；版本里没有这个键时是 null */
export async function readVersionMeta(
  key: MetaKey,
  deps: MetaDeps = { ...defaultCacheDeps(), db: getDb },
): Promise<unknown> {
  return rememberForVersion(
    `meta:${key}`,
    async () => {
      const { rows } = await deps.db().execute<{
        value: unknown;
      }>(sql`SELECT value FROM meta WHERE key = ${key}`);
      return rows[0]?.value ?? null;
    },
    deps,
  );
}

const dialect = new PgDialect();

function statementKey(query: SQL): string {
  const { sql: text, params } = dialect.sqlToQuery(query);
  return `${text}\u0000${JSON.stringify(params)}`;
}

/**
 * versionDb 的缓存版：同一个版本 schema 上同一条语句（文本加参数）只跑一次。版本表不可变，所以结果可以一直用；
 * 给 resolveVersion 第二步读 meta.rules / meta.sources 用。
 */
export function cachedVersionDb(
  readVersion: (schema: string) => Executor,
  cell: CacheCell<Promise<unknown>> = versionCache,
): (schema: string) => Executor {
  return (schema) => {
    const db = readVersion(schema);
    return Object.freeze({
      execute: async <R = Record<string, unknown>>(query: SQL) =>
        remember(
          cell as CacheCell<Promise<QueryRows<R>>>,
          `${schema}|sql:${statementKey(query)}`,
          () => db.execute<R>(query),
        ),
    });
  };
}

/**
 * 页面用的版本解析：resolveVersion 加版本规则构造，第二步（meta.rules / meta.sources）走按版本的缓存。
 * 第一步（选哪个版本）每次都实时查。缓存命中时第二步不再碰版本 schema，所以「两步之间版本被清理」
 * 要到后面的查询才会以 MirrorVersionGone 出现，视图按「该版本刚被清理」处理（P3-5）。
 */
export async function resolveBoard(
  v: number | null,
): Promise<BoardVersion<BoardRules>> {
  return resolveVersion(v, buildBoardRules, {
    controlDb,
    versionDb: cachedVersionDb(versionDb),
  });
}
