// PORTED_FROM: realshort@816ca2e src/lib/observe/source-state.ts
// 本地改动：只读，来源状态取钉住版本的 meta.sources（原 observe_sources 表与写入函数都不搬）。
// P3-3a 只有类型与签名，函数体由 P3-3 补。
import "server-only";

import {
  type ObserveSource,
  type SourceState,
} from "@/core/pick-board/source-types";

/** 各采集来源在这个版本导出时的状态；版本里没有的来源不出现 */
export type ObserveSources = Partial<Record<ObserveSource, SourceState>>;

export async function readSources(): Promise<ObserveSources> {
  throw new Error("pick-board: readSources is not implemented yet (P3-3)");
}
