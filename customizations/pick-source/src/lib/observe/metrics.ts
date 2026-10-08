/**
 * 观测台的纯计算与入参校验。【不许 import 任何 server-only 模块】——
 * 这个文件要能被 `node --test` 直接加载，测的才是行为而不是"代码长这样"
 * （同 lib/sitemap-request.ts / lib/gsc-request.ts 的既定做法）。
 */

/**
 * 每页行数的可选档位。人自己在页脚切。
 *
 * 【必须是白名单，不能收任意整数】`?size=100000` 会让一次请求把全库搬回来，
 * 而这一页是公开路由后面的后台页、没有速率限制。上界 200 是原来的固定值。
 */
export const PAGE_SIZES = [10, 20, 50, 100, 200] as const;

/**
 * 默认每页行数。
 *
 * 【默认 10 不是 200】：这张表一行 14 列、剧名两行，200 行要滚十几屏，
 * 而人来观测台是按某个指标看头部那几部，不是通读全库。
 * 顺带把一次 MISS 的数据量降到二十分之一。
 */
export const PAGE_SIZE = 10;

/** 涨幅榜取前多少名 */
export const GROWTH_LIMIT = 50;

/** 单剧曲线看多少天，与快照保留期一致 */
export const SERIES_DAYS = 90;

export const TABS = ["all", "cand", "growth", "drama", "bill"] as const;
export type Tab = (typeof TABS)[number];

/**
 * 排序白名单。
 *
 * 【这是 SQL 拼接的唯一入口，必须是枚举而不是字符串透传】：排序键最终会进
 * ORDER BY 子句，参数化占位符在那个位置不成立，所以只能靠白名单。
 * 放开成 `ORDER BY ${req.sort}` 就是一个可读任意表的注入点，而页面照样 200。
 */
export const SORTS = [
  "rr",
  "d1",
  "d7",
  "dp1",
  "dp7",
  "promoters",
  "publish",
  "bill",
  "eff",
  "gsc",
  "clicks",
] as const;
export type Sort = (typeof SORTS)[number];

/** 上线时段分桶。参考站那套「0-7 天 / 8-30 天」用的就是这个轴。 */
export const BUCKETS = ["0-7", "8-30", "31-90", "91-365", "366+"] as const;
export type Bucket = (typeof BUCKETS)[number];

export interface ObserveRequest {
  tab: Tab;
  sort: Sort;
  /** 语种过滤，空串表示不过滤 */
  locale: string;
  /** 上线时段过滤，null 表示不过滤 */
  bucket: Bucket | null;
  /** 第几页，从 1 开始 */
  page: number;
  /** 每页行数，取值来自 PAGE_SIZES */
  size: number;
  /** 搜索词（剧名或 book_id），已裁剪 */
  q: string;
  /** 单剧页要看的 book_id */
  dramaId: string;
}

/** 页码上限。防的是 `?page=999999999` 变成一次巨大的 OFFSET 全表扫。 */
export const MAX_PAGE = 500;

function pick<T extends string>(
  value: string | null | undefined,
  allowed: readonly T[],
  fallback: T,
): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

/**
 * 把 URL 参数收成一个已校验的请求对象。
 *
 * 【坏参数一律降级，不 404】：这是内部看板，`?sort=xxx` 打成错别字应该
 * 回到默认排序，而不是把一个能用的页面变成错误页。同 genre 那条
 * 「坏的分页参数降级到第 1 页、不断页」的既有约定。
 */
export function parseObserveRequest(
  params: URLSearchParams | Record<string, string | string[] | undefined>,
): ObserveRequest {
  const get = (key: string): string => {
    if (params instanceof URLSearchParams) return params.get(key) ?? "";
    const raw = params[key];
    return (Array.isArray(raw) ? raw[0] : raw) ?? "";
  };

  const pageRaw = Number.parseInt(get("page"), 10);
  const page =
    Number.isFinite(pageRaw) && pageRaw >= 1 ? Math.min(pageRaw, MAX_PAGE) : 1;

  const sizeRaw = Number.parseInt(get("size"), 10);
  const bucketRaw = get("bucket");
  return {
    tab: pick(get("tab"), TABS, "all"),
    sort: pick(get("sort"), SORTS, "rr"),
    /* 语种只收 2–7 位的 ASCII 语言标签，它也会进查询参数（是参数化的，但没必要放宽） */
    locale: /^[a-z]{2}(-[a-z]{2,4})?$/i.test(get("locale"))
      ? get("locale").toLowerCase()
      : "",
    bucket: BUCKETS.includes(bucketRaw as Bucket)
      ? (bucketRaw as Bucket)
      : null,
    page,
    /* 不在档位表里就退回默认，同「坏参数一律降级不 404」 */
    size: (PAGE_SIZES as readonly number[]).includes(sizeRaw)
      ? sizeRaw
      : PAGE_SIZE,
    q: get("q").trim().slice(0, 120),
    dramaId: /^[a-z0-9]{6,40}$/i.test(get("drama")) ? get("drama") : "",
  };
}

/**
 * 上线多少天。
 *
 * 【publish_at 为空时返回 null，绝不当成 0 天】：库里现在 31,950 条全是空的
 * （详情同步曾经漏写这一列，已修，但要 53 天才轮完全库）。
 * 把"不知道"渲染成"第 0 天"会让整张表看起来全是新剧。
 */
export function ageInDays(publishAt: Date | null, now: Date): number | null {
  if (!publishAt) return null;
  const days = Math.floor(
    (Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) -
      Date.UTC(
        publishAt.getUTCFullYear(),
        publishAt.getUTCMonth(),
        publishAt.getUTCDate(),
      )) /
      86_400_000,
  );
  return days < 0 ? 0 : days;
}

/** 上线时段分桶。日期未知时返回 null，由调用方渲染成"未知"。 */
export function lifecycleBucket(age: number | null): Bucket | null {
  if (age === null) return null;
  if (age <= 7) return "0-7";
  if (age <= 30) return "8-30";
  if (age <= 90) return "31-90";
  if (age <= 365) return "91-365";
  return "366+";
}

/**
 * 相邻快照的差值。
 *
 * 【缺快照返回 null，不返回 0】：0 的意思是"量过了，没变"，
 * null 的意思是"那天没有快照，算不出来"。两者在页面上必须长得不一样——
 * 混成 0 会让一部正在涨的新剧看起来是死的，而这正是观测台要发现的那类剧。
 */
export function delta(
  current: number,
  previous: number | null | undefined,
): number | null {
  if (previous === null || previous === undefined) return null;
  return current - previous;
}

/** Format numeric values without assigning a currency. */
export function formatUsd(value: number, digits = 2): string {
  return value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatInt(value: number): string {
  return value.toLocaleString("en-US");
}

/** 上线时段的中文标签。null 是"日期未知"，不是"很久以前"。 */
export function bucketLabel(bucket: Bucket | null): string {
  switch (bucket) {
    case "0-7":
      return "0–7 天";
    case "8-30":
      return "8–30 天";
    case "31-90":
      return "31–90 天";
    case "91-365":
      return "91 天–1 年";
    case "366+":
      return "1 年以上";
    default:
      return "未知";
  }
}

export const TAB_LABELS: Record<Tab, string> = {
  all: "总览",
  cand: "候选清单",
  growth: "涨幅榜",
  drama: "单剧",
  bill: "分成对账",
};

export const SORT_LABELS: Record<Sort, string> = {
  rr: "30 天销售指标",
  d1: "较昨日净变化",
  d7: "较 7 天前净变化",
  dp1: "推广人数较昨日变化",
  dp7: "推广人数 7 天变化",
  promoters: "推广人数",
  publish: "上线日期",
  bill: "预估分成",
  eff: "销售指标 / 推广人数",
  gsc: "搜索需求信号",
  clicks: "7 天出站",
};

/**
 * 免费边界内有多少集。算不出来时返回 null，由调用方渲染成「?」。
 *
 * 【两种 null 不能合并成 0】：0 是「一集都不免费」（payStart=1 的剧真实存在），
 * null 是「上游这两个字段对不上，我们不知道」。渲染成 0 就是替上游
 * 编了一个免费内容承诺，而那正是这一格存在的理由——见 episodeAvailability。
 */
export function freeEpisodeBoundary(
  chapterCount: number,
  payStart: number,
): number | null {
  if (chapterCount <= 0) return null;
  if (payStart < 0 || payStart > chapterCount + 1) return null;
  return payStart === 0 ? chapterCount : payStart - 1;
}

/** 仅解释有效边界，不替上游冲突字段生成免费内容承诺。 */
export function episodeAvailability(
  chapterCount: number,
  payStart: number,
): string {
  if (chapterCount <= 0) return "总集数未取得，免费范围待核验";
  if (payStart < 0 || payStart > chapterCount + 1)
    return "字段冲突，免费范围待核验";
  return `前 ${payStart === 0 ? chapterCount : payStart - 1} 集在免费边界内`;
}

/** 涨幅榜这一行按所选增量有没有基线值：d1 / dp1 看昨天那行快照，d7 / dp7 看 7 天前那行（与 observe/queries.ts 的 comparableOnly 同一张对照） */
export function comparisonValue(
  row: {
    revenueCents1: number | null;
    revenueCents7: number | null;
    promotersCnt1: number | null;
    promotersCnt7: number | null;
  },
  sort: Sort,
): number | null {
  switch (sort) {
    case "d1":
      return row.revenueCents1;
    case "dp1":
      return row.promotersCnt1;
    case "dp7":
      return row.promotersCnt7;
    default:
      return row.revenueCents7;
  }
}

export function candidateReasons(row: {
  searchImpressions: number;
  clicks7: number;
  billOrders: number;
}): string[] {
  return [
    row.searchImpressions > 0 && "已保存搜索匹配",
    row.clicks7 > 0 && "近 7 天有出站",
    row.billOrders > 0 && "有预估订单",
  ].filter((s): s is string => Boolean(s));
}

export function formatObservedAt(
  value: Date | string | null | undefined,
): string {
  if (!value) return "未保留采集时间";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "采集时间未知"
    : `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}
