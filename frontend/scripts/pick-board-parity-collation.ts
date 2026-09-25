/**
 * parity 的 collation 放行（P4-3）：两边库的 datcollate 不同时，列表里先后对调的一对，只有同时满足下面三条才算
 * collation 造成的，否则照常报「顺序不同」，并点出第一对解释不了的（只写键）：
 * 1. 两项在同一种列表里，并且主排序键相同（ORDER BY 里排在剧名前面的列：证据日期、剧单日期、名次、周数、
 *    评级在 GRADES 里的位置、条数；账号列表没有主排序键）；
 * 2. RealShort 的先后正是 RealShort 的 collation 比出来的先后（剧名，再平台，再行键；语种计数只比语种名；
 *    账号比分组、名字、id）；
 * 3. 镜像的先后正是镜像的 collation 比出来的先后。
 * 2 与 3 同时成立，两种 collation 对这一对的判断必然相反。
 *
 * collation 的模型：C、POSIX、C.UTF-8、ucs_basic 按码点比（UTF-8 的字节序就是码点序）；其余按 Intl.Collator（ICU）
 * 近似 glibc，比出相等时再按码点比（PG 确定性 collation 的做法）。近似不准时只会多报，不会放过主排序错。
 * 剧场行的主排序键取自快照核心的 tiePrimary：第 50 行的并列组补全用的也是它，两处只有一份。
 *
 * 第 50 行的并列组超过上限、没补全时，少了 / 多了照样算差异；只有正在那一边第 50 行并列组里的行，原因后面才加
 * 「可能只是 collation 换行」的提示（cappedBoundary / cappedNote）。
 */
import {
  ROW_LIMIT,
  TIE_ROWS_MAX,
  isJsonObject,
  tiePrimary,
  tiesOf,
  type Json,
  type JsonObject,
} from "./pick-board-snapshot-core.rs";

/** 两边库的 datcollate；相同或有一边不知道时不放行任何先后差异 */
export type Collations = Readonly<{ rs: string; mirror: string }>;

type Compare = (a: string, b: string) => number;

const BYTE_ORDER = /^(?:c|posix|ucs_basic)(?:[.@].*)?$/i;

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

/** 剧场行：主排序键是 tiePrimary（剧场榜按榜的种类，选剧 / 全部剧库按缺省排序「证据时间」） */
function rowOrderKey(row: JsonObject): OrderKey | null {
  const primary = tiePrimary(row);
  if (primary === null) return null;
  const signal = row.signal;
  if (isJsonObject(signal)) {
    // 剧场榜：剧名、行键（queries-rank.ts dailySelect / rankOrder）
    const chain = texts(row, ["title", "rowKey"]);
    return chain
      ? { list: `rank:${JSON.stringify(signal.kind ?? null)}`, primary, chain }
      : null;
  }
  // 选剧 / 全部剧库：剧名、平台、行键（queries.ts orderBy）
  const chain = texts(row, ["title", "platform", "rowKey"]);
  return chain ? { list: "rows", primary, chain } : null;
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

type Pair = readonly [string, string];

/** RealShort 把 a 排在 b 前、镜像把 b 排在 a 前的这一对，是否由两边的 collation 解释 */
function explains(
  compare: Readonly<{ rs: Compare; mirror: Compare }>,
  [a, b]: Pair,
  items: OrderedItems,
): boolean {
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
    chainCompare(compare.rs, rsA.chain, rsB.chain) < 0 &&
    chainCompare(compare.mirror, mirrorB.chain, mirrorA.chain) < 0
  );
}

/**
 * 先后对调的各对里，第一对两边 collation 解释不了的（只有键）；每一对都解释得了时 null。
 * 两边 collation 相同或有一边不知道（null）时哪一对都解释不了，就是第一对。
 */
function unexplainedPair(
  collations: Collations | null,
  pairs: readonly Pair[],
  items: OrderedItems,
): Pair | null {
  if (!collations) return pairs[0] ?? null;
  const compare = {
    rs: collator(collations.rs),
    mirror: collator(collations.mirror),
  };
  return pairs.find((pair) => !explains(compare, pair, items)) ?? null;
}

/** RealShort 把 a 排在 b 前、镜像把 b 排在 a 前的每一对，是否都由两边的 collation 解释 */
export function collationExplains(
  collations: Collations | null,
  pairs: readonly Pair[],
  items: OrderedItems,
): boolean {
  if (!collations || pairs.length === 0) return false;
  return unexplainedPair(collations, pairs, items) === null;
}

/** 两边共有的项排得不一样时：explained 即 collation 放行；否则 reason 是给人看的原因，只写键 */
export type OrderVerdict =
  | Readonly<{ explained: true }>
  | Readonly<{ explained: false; reason: string }>;

/** 两边共有的项，RealShort 的先后对镜像的先后；先后相同时 null */
export function orderVerdict(
  collations: Collations | null,
  rsOrder: readonly string[],
  mirrorOrder: readonly string[],
  items: OrderedItems,
): OrderVerdict | null {
  const first = rsOrder.findIndex((key, i) => mirrorOrder[i] !== key);
  if (first < 0) return null;
  const pairs = inversions(rsOrder, mirrorOrder);
  if (collationExplains(collations, pairs, items)) return { explained: true };
  // 第一处错位的那一对可能正是 collation 解释得了的；再点出第一对解释不了的，人工核（SELECT '甲' < '乙'）就核它
  const pair = unexplainedPair(collations, pairs, items);
  const named = pair
    ? `；RealShort 把 ${pair[0]} 排在 ${pair[1]} 前面、镜像相反，collation 解释不了这一对`
    : "";
  const at = `第 ${first + 1} 项 RealShort 是 ${rsOrder[first]}，镜像是 ${mirrorOrder[first]}`;
  return { explained: false, reason: `顺序不同：${at}${named}` };
}

/* ---------------------------------------------------------------- 第 50 行的并列组没补全时 */

/** 有一边 ties.capped 的那一页，两边各自第 ROW_LIMIT 行的主排序键（tiePrimary） */
export type TieBoundary = Readonly<{
  rs: string | null;
  mirror: string | null;
}>;

function boundaryKey(page: JsonObject): string | null {
  const rows = Array.isArray(page.rows) ? (page.rows as readonly Json[]) : [];
  const row = rows[ROW_LIMIT - 1];
  return row === undefined ? null : tiePrimary(row);
}

/** 有一边第 50 行的并列组超过上限、没补全时，两边各自的边界键；否则 null */
export function cappedBoundary(
  rs: JsonObject,
  mirror: JsonObject,
): TieBoundary | null {
  const capped = [rs, mirror].some((page) => tiesOf(page)?.capped === true);
  return capped ? { rs: boundaryKey(rs), mirror: boundaryKey(mirror) } : null;
}

const CAPPED_NOTE = `（第 ${ROW_LIMIT} 行所在的并列组超过 ${TIE_ROWS_MAX} 行，没有补全，可能只是 collation 换行：按运行手册「LIMIT 边界上的换行」的人工核法核）`;

/** 少了 / 多了的这一项的主排序键正是那一边第 50 行的键时，原因后面加的提示；别的行（比如边界上面的）不加 */
export function cappedNote(
  boundary: TieBoundary | null,
  side: keyof TieBoundary,
  item: Json | undefined,
): string {
  const key = boundary?.[side] ?? null;
  const tied = key !== null && item !== undefined && tiePrimary(item) === key;
  return tied ? CAPPED_NOTE : "";
}
