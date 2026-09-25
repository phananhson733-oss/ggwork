/**
 * Shared synthetic rows for the parity comparison tests (P4-3). Not a test
 * file: the pick-board-parity*.test.ts files import it.
 */
import {
  isRsRank,
  parsePickRequest,
  reelshortId,
  type PickRequest,
} from "@/core/pick-board/request";

import type {
  CompareContext,
  Finding,
} from "../../../scripts/pick-board-parity-compare";
import {
  STRIPPED,
  type BoardLoaders,
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

type Row = Record<string, Json>;
type TextCompare = (a: string, b: string) => number;

/** C: code point order (every title here is ASCII, so UTF-16 order is the same) */
export const C_ORDER: TextCompare = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const EN_US = new Intl.Collator("en-US");
/** en_US: ICU, the way the mirror's glibc en_US.UTF-8 orders these titles */
export const EN_US_ORDER: TextCompare = (a, b) => EN_US.compare(a, b);

function dateDesc(a: Json | undefined, b: Json | undefined): number {
  // DESC NULLS LAST: a missing date sorts after every date
  const x = typeof a === "string" ? a : "";
  const y = typeof b === "string" ? b : "";
  return x === y ? 0 : x > y ? -1 : 1;
}

function text(row: Row, key: string): string {
  const value = row[key];
  return typeof value === "string" ? value : "";
}

/** loadPickRows' default ("evidence") ORDER BY, with the text columns in `order` */
export function evidenceOrder(rows: readonly Row[], order: TextCompare): Row[] {
  return [...rows].sort(
    (a, b) =>
      dateDesc(a.latestEvidenceOn, b.latestEvidenceOn) ||
      dateDesc(a.listedOn, b.listedOn) ||
      order(text(a, "title"), text(b, "title")) ||
      order(text(a, "platform"), text(b, "platform")) ||
      order(text(a, "rowKey"), text(b, "rowKey")),
  );
}

const notHere = async (): Promise<never> => {
  throw new Error("this fake does not serve that loader");
};

/** The real request parsing; every loader throws until a test supplies it */
export function unusedLoaders<Meta>(): BoardLoaders<PickRequest, Meta> {
  return {
    parse: parsePickRequest,
    isRsRank,
    reelshortId,
    pickRows: notHere,
    facets: notHere,
    rankMeta: notHere,
    rankRows: notHere,
    rsRank: notHere,
    growthDiagnosis: notHere,
    postedList: notHere,
    postedRecord: notHere,
    postedStats: notHere,
    accounts: notHere,
    rowDetail: notHere,
    reelshortDetail: notHere,
    freshness: notHere,
    sources: notHere,
  };
}

/** One database's pick list: the rows in that database's collation, paged like loadPickRows */
export function listLoaders(
  rows: readonly Row[],
  order: TextCompare,
): BoardLoaders<PickRequest, null> {
  const sorted = evidenceOrder(rows, order);
  return {
    ...unusedLoaders<null>(),
    pickRows: async (req) => {
      const offset = (req.page - 1) * req.size;
      const page = sorted.slice(offset, offset + req.size);
      const total = sorted.length;
      return { rows: page, total, hasMore: offset + page.length < total };
    },
    facets: async () => ({}),
    rankMeta: async () => null,
  };
}
