// PORTED_FROM: realshort@816ca2e src/lib/observe/source-state.ts
// 本地改动：只读，来源状态取钉住版本的 meta.sources（原 observe_sources 表与写入函数都不搬），按版本缓存；
// 版本数据按 P2 的宽口径写入（各字段是任意标量），读出时归一：不认识的来源、不认识的状态、不是对象的项丢掉，
// 时间不是字符串的置空，details 不是对象的置 {}。
import "server-only";

import {
  OBSERVE_SOURCES,
  type ObserveSource,
  type SourceDetails,
  type SourceState,
} from "@/core/pick-board/source-types";

import { readVersionMeta } from "./cache";

/** 各采集来源在这个版本导出时的状态；版本里没有的来源不出现 */
export type ObserveSources = Partial<Record<ObserveSource, SourceState>>;

const STATUSES: readonly string[] = ["running", "success", "failed"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSource(key: string): key is ObserveSource {
  return (OBSERVE_SOURCES as readonly string[]).includes(key);
}

function stateOf(source: ObserveSource, raw: unknown): SourceState | null {
  if (!isRecord(raw)) return null;
  const { status, attemptedAt, completedAt, details } = raw;
  if (typeof status !== "string" || !STATUSES.includes(status)) return null;
  return {
    source,
    status: status as SourceState["status"],
    attemptedAt: typeof attemptedAt === "string" ? attemptedAt : "",
    completedAt: typeof completedAt === "string" ? completedAt : null,
    details: isRecord(details) ? (details as SourceDetails) : {},
  };
}

/**
 * meta.sources（或 resolveVersion 带回来的 board.sources）归一成页底要的形状。页面已经有 board.sources 时
 * 直接用它，不必再查一次。
 */
export function sourcesOf(raw: unknown): ObserveSources {
  if (!isRecord(raw)) return {};
  const entries = Object.entries(raw).flatMap(([key, value]) => {
    if (!isSource(key)) return [];
    const state = stateOf(key, value);
    return state ? [[key, state] as const] : [];
  });
  return Object.fromEntries(entries) as ObserveSources;
}

/** 钉住版本的来源状态（读一次 meta.sources，按版本缓存） */
export async function readSources(): Promise<ObserveSources> {
  return sourcesOf(await readVersionMeta("sources"));
}
