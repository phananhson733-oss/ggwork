/**
 * Shared synthetic rows for the parity comparison tests (P4-3). Not a test
 * file: the pick-board-parity*.test.ts files import it.
 */
import type {
  CompareContext,
  Finding,
} from "../../../scripts/pick-board-parity-compare";
import {
  STRIPPED,
  type Json,
} from "../../../scripts/pick-board-snapshot-core.rs";

export const PLAIN: CompareContext = { scrub: {}, collations: null };
export const PAN_TEXT = "资源 https://pan.example/s/1AbC 提取码：zz99";

export function failures(findings: readonly Finding[]): Finding[] {
  return findings.filter((f) => f.rule === null);
}

export function rules(findings: readonly Finding[]): string[] {
  return [...new Set(findings.flatMap((f) => (f.rule ? [f.rule] : [])))].sort();
}

export function pickRow(over: Record<string, Json> = {}): Record<string, Json> {
  return {
    rowKey: "kalos-a",
    platform: "kalos",
    sourceTable: "kalos_rows",
    title: "剧名甲",
    hasPan: true,
    clicks: 0,
    signals: [],
    posted: [],
    ...over,
  };
}

export function rsRow(over: Record<string, Json> = {}): Record<string, Json> {
  return { ...pickRow(over), panUrl: STRIPPED, panPw: STRIPPED };
}

export function list(rows: Json[]): Json {
  return { page: { rows, total: rows.length, hasMore: false }, facets: {} };
}
