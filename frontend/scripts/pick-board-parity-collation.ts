/**
 * parity 的 collation 放行（P4-3）：两边库的 datcollate 不同时，列表里先后对调的一对，只有同时满足下面三条才算
 * collation 造成的，否则照常报「顺序不同」：
 * 1. 两项在同一种列表里，并且主排序键相同（ORDER BY 里排在剧名前面的列：证据日期、剧单日期、名次、周数、评级、条数；
 *    账号列表没有主排序键）；
 * 2. RealShort 的先后正是 RealShort 的 collation 比出来的先后（剧名，再平台，再行键；语种计数只比语种名；
 *    账号比分组、名字、id）；
 * 3. 镜像的先后正是镜像的 collation 比出来的先后。
 * 2 与 3 同时成立，两种 collation 对这一对的判断必然相反。
 *
 * collation 的模型：C、POSIX、C.UTF-8、ucs_basic 按码点比（UTF-8 的字节序就是码点序）；其余按 Intl.Collator（ICU）
 * 近似 glibc，比出相等时再按码点比（PG 确定性 collation 的做法）。近似不准时只会多报，不会放过主排序错。
 */
import { isDailyRank } from "@/core/pick-board/request";

import {
  isJsonObject,
  type Json,
  type JsonObject,
} from "./pick-board-snapshot-core.rs";

/** 两边库的 datcollate；相同或有一边不知道时不放行任何先后差异 */
export type Collations = Readonly<{ rs: string; mirror: string }>;

type Compare = (a: string, b: string) => number;

const BYTE_ORDER = /^(?:c|posix|ucs_basic)(?:[.@].*)?$/i;
const GRADED_RANKS: ReadonlySet<string> = new Set(["sm", "mg"]);

export function codePointCompare(a: string, b: string): number {
  const x = [...a];
  const y = [...b];
  const length = Math.min(x.length, y.length);
  for (let i = 0; i < length; i += 1) {
    const d = (x[i]?.codePointAt(0) ?? 0) - (y[i]?.codePointAt(0) ?? 0);
    if (d !== 0) return d;
  }
  return x.length - y.length;
}

function localeCollator(name: string): Intl.Collator {
  const tag = (name.split(/[.@]/)[0] ?? "").replaceAll("_", "-");
  try {
    return new Intl.Collator(tag || "en");
  } catch {
    return new Intl.Collator("en");
  }
}

/** 一个 datcollate 名字对应的比较函数 */
export function collator(name: string): Compare {
  if (BYTE_ORDER.test(name)) return codePointCompare;
  const intl = localeCollator(name);
  return (a, b) => intl.compare(a, b) || codePointCompare(a, b);
}

/** 这一项在列表里怎么排：list 是列表种类，primary 是剧名前的排序键，chain 是之后逐个比的文字 */
type OrderKey = Readonly<{
  list: string;
  primary: string;
  chain: readonly string[];
}>;

function value(o: JsonObject, key: string): Json {
  return o[key] ?? null;
}

function texts(o: JsonObject, keys: readonly string[]): string[] | null {
  const found = keys.map((key) => o[key]);
  return found.every((v): v is string => typeof v === "string") ? found : null;
}

/** 剧场榜的 ORDER BY 里排在剧名前面的列（queries-rank.ts rankOrder / dailySelect） */
function rankPrimary(row: JsonObject, signal: JsonObject): Json[] {
  const kind = typeof signal.kind === "string" ? signal.kind : "";
  if (isDailyRank(kind)) return [value(row, "dayRank")];
  if (kind === "kw")
    return [
      isJsonObject(signal.payload) ? value(signal.payload, "weeks") : null,
    ];
  if (GRADED_RANKS.has(kind))
    return [value(signal, "grade"), value(row, "listedOn")];
  return [value(signal, "evidenceOn"), value(row, "listedOn")];
}

function rowOrderKey(row: JsonObject): OrderKey | null {
  const signal = row.signal;
  if (isJsonObject(signal)) {
    const chain = texts(row, ["title", "rowKey"]);
    return chain
      ? {
          list: `rank:${JSON.stringify(signal.kind ?? null)}`,
          primary: JSON.stringify(rankPrimary(row, signal)),
          chain,
        }
      : null;
  }
  // 选剧 / 全部剧库的缺省排序「证据时间」（queries.ts orderBy）
  const chain = texts(row, ["title", "platform", "rowKey"]);
  const primary = [value(row, "latestEvidenceOn"), value(row, "listedOn")];
  return chain
    ? { list: "rows", primary: JSON.stringify(primary), chain }
    : null;
}

function orderKey(item: Json | undefined): OrderKey | null {
  if (!isJsonObject(item)) return null;
  if ("rowKey" in item && "sourceTable" in item) return rowOrderKey(item);
  if ("grp" in item && "name" in item && "id" in item) {
    // 账号：ORDER BY grp, name, id（queries-posted.ts loadAccounts），三列都是文字，没有主排序键
    const chain = texts(item, ["grp", "name", "id"]);
    return chain ? { list: "accounts", primary: "", chain } : null;
  }
  if (!("lang" in item && "n" in item)) return null;
  // 语种计数：ORDER BY n DESC, k ASC
  const chain = texts(item, ["lang"]);
  return chain
    ? { list: "langs", primary: JSON.stringify([value(item, "n")]), chain }
    : null;
}

function chainCompare(
  compare: Compare,
  a: readonly string[],
  b: readonly string[],
): number {
  const length = Math.min(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    const d = compare(a[i] ?? "", b[i] ?? "");
    if (d !== 0) return d;
  }
  return 0;
}

/** before 里 a 在 b 前、after 里 b 在 a 前的每一对 [a, b] */
export function inversions(
  before: readonly string[],
  after: readonly string[],
): [string, string][] {
  const position = new Map(after.map((key, i) => [key, i]));
  return before.flatMap((a, i) =>
    before
      .slice(i + 1)
      .filter((b) => (position.get(b) ?? 0) < (position.get(a) ?? 0))
      .map((b): [string, string] => [a, b]),
  );
}

export type OrderedItems = Readonly<{
  rs: ReadonlyMap<string, Json>;
  mirror: ReadonlyMap<string, Json>;
}>;

/** RealShort 把 a 排在 b 前、镜像把 b 排在 a 前的每一对，是否都由两边的 collation 解释 */
export function collationExplains(
  collations: Collations | null,
  pairs: readonly (readonly [string, string])[],
  items: OrderedItems,
): boolean {
  if (!collations || pairs.length === 0) return false;
  const rsCompare = collator(collations.rs);
  const mirrorCompare = collator(collations.mirror);
  return pairs.every(([a, b]) => {
    const [rsA, rsB, mirrorA, mirrorB] = [
      orderKey(items.rs.get(a)),
      orderKey(items.rs.get(b)),
      orderKey(items.mirror.get(a)),
      orderKey(items.mirror.get(b)),
    ];
    if (!rsA || !rsB || !mirrorA || !mirrorB) return false;
    const lists = new Set([rsA.list, rsB.list, mirrorA.list, mirrorB.list]);
    if (lists.size !== 1) return false;
    if (rsA.primary !== rsB.primary || mirrorA.primary !== mirrorB.primary)
      return false;
    return (
      chainCompare(rsCompare, rsA.chain, rsB.chain) < 0 &&
      chainCompare(mirrorCompare, mirrorB.chain, mirrorA.chain) < 0
    );
  });
}
