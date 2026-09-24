import { describe, expect, it } from "@rstest/core";
import { sql } from "drizzle-orm";

import {
  cachedVersionDb,
  emptyLru,
  lruGet,
  lruPut,
  makeCacheCell,
  readVersionMeta,
  remember,
  rememberForVersion,
} from "@/server/pick-board/cache";
import { type Executor, type VersionScope } from "@/server/pick-board/db";

/**
 * A3: versions are immutable, so what a render reads from a version's meta
 * (rules, sources) and the candidate pool N are kept per version in a small
 * in-process LRU. The LRU is pure; the one mutable cell is injected.
 */

const V7: VersionScope = {
  schema: "pickm_v000007",
  asOf: "2026-09-23T22:15:00+00:00",
  versionId: 7,
  rules: null,
};
const V8: VersionScope = { ...V7, schema: "pickm_v000008", versionId: 8 };

function countingExecutor(rows: () => unknown[]) {
  const calls: number[] = [];
  const db: Executor = {
    execute: async <R>() => {
      calls.push(calls.length);
      return { rows: rows() as R[] };
    },
  };
  return { calls, db };
}

describe("pure LRU", () => {
  it("keeps at most capacity entries and evicts the least recently used", () => {
    let lru = emptyLru<number>(2);
    lru = lruPut(lru, "a", 1);
    lru = lruPut(lru, "b", 2);
    const read = lruGet(lru, "a");
    expect(read.value).toBe(1);
    lru = lruPut(read.lru, "c", 3);
    expect(lruGet(lru, "b").value).toBeUndefined();
    expect(lruGet(lru, "a").value).toBe(1);
    expect(lruGet(lru, "c").value).toBe(3);
  });

  it("never changes the LRU it is given", () => {
    const before = lruPut(emptyLru<number>(1), "a", 1);
    const snapshot = JSON.stringify(before);
    lruPut(before, "b", 2);
    lruGet(before, "a");
    expect(JSON.stringify(before)).toBe(snapshot);
    expect(Object.isFrozen(before)).toBe(true);
    expect(Object.isFrozen(before.entries)).toBe(true);
  });

  it("replaces a key in place of adding it twice", () => {
    const lru = lruPut(lruPut(emptyLru<number>(3), "a", 1), "a", 2);
    expect(lru.entries).toEqual([["a", 2]]);
  });

  it("refuses a capacity below one", () => {
    expect(() => emptyLru(0)).toThrow(RangeError);
  });
});

describe("remember", () => {
  it("loads once for concurrent callers of one key", async () => {
    const cell = makeCacheCell<Promise<number>>(4);
    let loads = 0;
    const load = async () => {
      loads += 1;
      return 42;
    };
    const values = await Promise.all([
      remember(cell, "k", load),
      remember(cell, "k", load),
    ]);
    expect(values).toEqual([42, 42]);
    expect(await remember(cell, "k", load)).toBe(42);
    expect(loads).toBe(1);
  });

  it("forgets a failed load, so the next call tries again", async () => {
    const cell = makeCacheCell<Promise<number>>(4);
    const boom = new Error("synthetic");
    await expect(
      remember(cell, "k", async () => Promise.reject(boom)),
    ).rejects.toBe(boom);
    expect(await remember(cell, "k", async () => 7)).toBe(7);
  });
});

describe("per-version reads", () => {
  it("rememberForVersion keys by the version schema", async () => {
    const cell = makeCacheCell<Promise<unknown>>(8);
    let scope = V7;
    const deps = { scope: () => scope, cell };
    let loads = 0;
    const pool = () =>
      rememberForVersion(
        "pool",
        async () => {
          loads += 1;
          return loads;
        },
        deps,
      );
    expect(await pool()).toBe(1);
    expect(await pool()).toBe(1);
    scope = V8;
    expect(await pool()).toBe(2);
    scope = V7;
    expect(await pool()).toBe(1);
  });

  it("readVersionMeta runs one query per version and key", async () => {
    const cell = makeCacheCell<Promise<unknown>>(8);
    const { calls, db } = countingExecutor(() => [{ value: { a: 1 } }]);
    const deps = { db: () => db, scope: () => V7, cell };
    expect(await readVersionMeta("rsCounts", deps)).toEqual({ a: 1 });
    expect(await readVersionMeta("rsCounts", deps)).toEqual({ a: 1 });
    expect(calls).toHaveLength(1);
    await readVersionMeta("sources", deps);
    expect(calls).toHaveLength(2);
  });

  it("readVersionMeta answers null for a key the version does not have", async () => {
    const cell = makeCacheCell<Promise<unknown>>(8);
    const { db } = countingExecutor(() => []);
    expect(
      await readVersionMeta("growthBaseline", {
        db: () => db,
        scope: () => V7,
        cell,
      }),
    ).toBeNull();
  });

  it("cachedVersionDb runs a given statement on a given version once", async () => {
    const cell = makeCacheCell<Promise<unknown>>(8);
    const seen: string[] = [];
    const versionDb = (schema: string): Executor => ({
      execute: async <R>() => {
        seen.push(schema);
        return { rows: [{ schema }] as R[] };
      },
    });
    const cached = cachedVersionDb(versionDb, cell);
    const query = sql`SELECT key, value FROM meta WHERE key IN ('rules', 'sources')`;
    const first = await cached("pickm_v000007").execute(query);
    const again = await cached("pickm_v000007").execute(query);
    await cached("pickm_v000008").execute(query);
    await cached("pickm_v000007").execute(sql`SELECT ${1}::int AS n`);
    expect(again).toBe(first);
    expect(seen).toEqual(["pickm_v000007", "pickm_v000008", "pickm_v000007"]);
  });
});
