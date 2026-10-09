/**
 * 选剧台（/admin/pick）的入参校验与常量。【不许 import 任何 server-only 模块】——
 * `node --test` 直接加载（tests/pick-request.test.ts），同 lib/observe/metrics.ts。
 *
 * 所有会进 SQL 的枚举值都是白名单：排序键进 ORDER BY，那个位置没有参数化占位符，
 * 字符串透传就是注入点，而页面照样 200（与观测台 SORTS 同一条理由）。
 *
 * 2026-09-11 观测台并入这一页（用户：「观测台和选剧台 2 合 1」）：ReelShort 是第十个剧场，
 * 它的候选条件是三种依据、它的七张榜加分成对账在榜单 tab 里。ReelShort 那一侧的白名单
 * （排序 / 上线分桶）沿用 lib/observe/metrics.ts 的，不另抄一份。
 */
import {
  BUCKETS,
  GROWTH_LIMIT,
  SORTS as RS_SORTS,
  type Bucket,
  type Sort as RsSort,
} from "@/lib/observe/metrics";

export type { Bucket, RsSort };
export { BUCKETS, RS_SORTS };
/** 涨幅榜只认三种增量排序；榜的行数上限与观测台同一个常量（纯模块，组件与 node --test 都能直接 import） */
export const GROWTH_SORTS: readonly RsSort[] = ["d1", "d7", "dp1", "dp7"];
export { GROWTH_LIMIT };

export const TABS = ["pick", "all", "row", "rank", "posted", "rules"] as const;
export type Tab = (typeof TABS)[number];

/** 证据页是从哪个列表 tab 进来的；「返回列表」与顶部 tab 按它回去，默认选剧 */
export const LIST_TABS = ["pick", "all", "rank", "posted"] as const;
export type ListTab = (typeof LIST_TABS)[number];

/** 证据时间 / 剧单日期 / 剧名 */
export const SORTS = ["evidence", "listed", "title"] as const;
export type Sort = (typeof SORTS)[number];

/**
 * 剧场剧单的信号种类（catalog_signals.kind），与 build.py 的 sig.s 一一对应。
 * 顺序就是 chips 的显示顺序：先榜单、再评级、再清单、最后备注。
 * `qc / qr` 是鹊娱汇聚台的两张跨剧场榜（7 日转化率 / 7 日总收入 Top25，每日 10:30 更新，2026-09-12 接入），
 * 形状与 KalosTV 日榜相同（payload.h 是 [日期, 名次] 的历史），所以榜单 tab 的日榜逻辑对三种通用（DAILY_RANKS）。
 */
export const THEATER_BASES = [
  "kd",
  "kw",
  "qc",
  "qr",
  "sm",
  "smd",
  "mg",
  "fh",
  "sh",
  "gh",
  "gn",
  "ghh",
  "dbn",
] as const;
export type TheaterBasis = (typeof THEATER_BASES)[number];

/** 按日出榜、payload.h 存 [日期, 名次, 备注?] 历史的种类：榜单 tab 按「那一天」看，证据页展开逐日名次 */
export const DAILY_RANKS: ReadonlySet<string> = new Set<TheaterBasis>(["kd", "qc", "qr"]);
export function isDailyRank(kind: string): boolean {
  return DAILY_RANKS.has(kind);
}

/** 周榜的一周：week 是剧场的周标签（不带年份，跨年会重名），start 是周起日期 YYYY-MM-DD，才是这一周的身份 */
export interface WeekOption {
  week: string;
  start: string;
}
/** 请求里的日 / 周怎么落到实际取的那一期：latest = 没指定；exact = 按日期对上；label = 旧周标签唯一对上；ambiguous = 周标签对上不同年份的几周、取其中最近的；missing = 没有这一期、回落到最近一期 */
export type PeriodResolution = "latest" | "exact" | "label" | "ambiguous" | "missing";
const WEEK_START = /^\d{4}-\d{2}-\d{2}$/;

/**
 * 周热门「哪一周」按周起日期识别（问答审计 P1-6）：周标签不带年份，「4.20–4.26」在 2025 与 2026 各有一周，
 * 按标签分组会让更早那周从周列表里消失，按标签过滤会把两年的行混进同一张榜。
 * weeks 新的在前；raw 是 parsePickRequest 清洗后的 week（周起日期，或旧书签里的周标签）
 */
export function resolveWeek(weeks: readonly WeekOption[], raw: string): { start: string; how: PeriodResolution } {
  const latest = weeks[0]?.start ?? "";
  if (!raw) return { start: latest, how: "latest" };
  if (WEEK_START.test(raw)) return weeks.some((w) => w.start === raw) ? { start: raw, how: "exact" } : { start: latest, how: "missing" };
  const hits = weeks.filter((w) => w.week === raw);
  if (hits.length === 0) return { start: latest, how: "missing" };
  return { start: hits[0].start, how: hits.length === 1 ? "label" : "ambiguous" };
}

/** 日榜「哪一天」：days 新的在前，不存在的日子回落到最近一天并标 missing（原来静默回落） */
export function resolveDay(days: readonly string[], raw: string): { day: string; how: PeriodResolution } {
  if (!raw) return { day: days[0] ?? "", how: "latest" };
  return days.includes(raw) ? { day: raw, how: "exact" } : { day: days[0] ?? "", how: "missing" };
}

/** 周 chips 的显示文字：周标签；同一个标签对应不止一周时补上年份，否则两枚 chip 长得一样 */
export function weekText(weeks: readonly WeekOption[]): (start: string) => string {
  const count = new Map<string, number>();
  for (const w of weeks) count.set(w.week, (count.get(w.week) ?? 0) + 1);
  return (start) => {
    const w = weeks.find((x) => x.start === start);
    if (!w) return start;
    return (count.get(w.week) ?? 0) > 1 ? `${w.week}（${start.slice(0, 4)}）` : w.week;
  };
}

/**
 * ReelShort 的三个候选条件。它们不在 catalog_signals 里，查询时从
 * outbound_clicks / cps_bill_daily / dramas.search_impressions 现算（与原观测台候选清单同口径）。
 */
export const RS_BASES = ["clk", "bill", "gsc"] as const;
export type RsBasis = (typeof RS_BASES)[number];

/** 依据（信号种类）。空串 = 任一信号 */
export const BASES = [...THEATER_BASES, ...RS_BASES] as const;
export type Basis = (typeof BASES)[number];

export function isRsBasis(b: string): b is RsBasis {
  return (RS_BASES as readonly string[]).includes(b);
}

export const BASIS_LABELS: Record<Basis, string> = {
  kd: "KalosTV 日榜",
  kw: "KalosTV 周热门",
  qc: "鹊娱 7 日转化率 Top25",
  qr: "鹊娱 7 日总收入 Top25",
  sm: "ShortMax 评级",
  smd: "ShortMax 每日推荐",
  mg: "MoboReels 评级",
  fh: "FlickReels 爆款剧单",
  sh: "StarShort 高充值剧单",
  gh: "GoodShort 爆款推荐",
  gn: "GoodShort 重点推荐",
  ghh: "GoodShort 历史高充值",
  dbn: "DramaBox 运营备注",
  clk: "ReelShort 7 天有出站",
  bill: "ReelShort 有预估订单",
  gsc: "ReelShort 搜索匹配",
};

/** 这一类证据的日期叫什么；没有自己日期的种类写「日期未知」 */
export const BASIS_DATE_LABEL: Record<Basis, string> = {
  kd: "榜单日期",
  kw: "周起",
  qc: "榜单日期",
  qr: "榜单日期",
  sm: "",
  smd: "",
  mg: "",
  fh: "入榜日期",
  sh: "",
  gh: "推荐日期",
  gn: "推荐日期",
  ghh: "表内日期（含义未核）",
  dbn: "",
  clk: "最近过滤后出站",
  bill: "最近账单",
  gsc: "采集",
};

/**
 * 十个剧场的平台键：九个剧单剧场与 build.py 的 p 一致，`reelshort` 是本站自己的 CPS 账号
 * （行来自 dramas 表，不在剧单里）。在用的五个排前面。
 */
export const PLATFORMS = [
  "reelshort",
  "dramabox",
  "shortmax",
  "flickreels",
  "flareflow",
  "kalos",
  "starshort",
  "goodshort",
  "moboreels",
  "touchshort",
] as const;
export type Platform = (typeof PLATFORMS)[number];

export const PLATFORM_LABELS: Record<Platform, string> = {
  reelshort: "ReelShort",
  dramabox: "DramaBox",
  shortmax: "ShortMax",
  flickreels: "FlickReels",
  flareflow: "flareflow",
  kalos: "KalosTV",
  starshort: "StarShort",
  goodshort: "GoodShort",
  moboreels: "MoboReels",
  touchshort: "TouchShort",
};

/** 运营在用的剧场（用户 2026-09-10 定）：这几个排在 chips 前面，规则表标「在用」 */
export const IN_USE: readonly Platform[] = ["reelshort", "dramabox", "shortmax", "flickreels", "flareflow"];

/** ReelShort 行的行键前缀：`reelshort-<book_id>`，与 artifact 的 `'reelshort-'+id` 相同 */
export const RS_ROW_PREFIX = "reelshort-";
const BOOK_ID = /^[a-z0-9]{6,40}$/i;

export function reelshortRowKey(id: string): string {
  return `${RS_ROW_PREFIX}${id}`;
}

/** 是 ReelShort 行就返回 book_id，否则空串（形状不像 book_id 的也当不是） */
export function reelshortId(rowKey: string): string {
  if (!rowKey.startsWith(RS_ROW_PREFIX)) return "";
  const id = rowKey.slice(RS_ROW_PREFIX.length);
  return BOOK_ID.test(id) ? id : "";
}

/**
 * 榜单 tab 一次只看一张榜。剧场榜的键就是信号种类；ReelShort 的七张榜加分成对账以 `rs_` 开头
 * （原观测台的总览 / 候选 / 涨幅榜 / 对账，2026-09-11 并进来）。默认 KalosTV 日榜。
 */
export const RS_RANKS = [
  "rs_rr",
  "rs_growth",
  "rs_cand",
  "rs_pc",
  "rs_clk",
  "rs_gsc",
  "rs_bill",
  "rs_ledger",
] as const;
export type RsRank = (typeof RS_RANKS)[number];

export const RS_RANK_LABELS: Record<RsRank, string> = {
  rs_rr: "ReelShort 30 天指标",
  rs_growth: "ReelShort 涨幅榜",
  rs_cand: "ReelShort 候选清单",
  rs_pc: "ReelShort 推广人数",
  rs_clk: "ReelShort 7 天出站",
  rs_gsc: "ReelShort 搜索需求",
  rs_bill: "ReelShort 预估分成",
  rs_ledger: "ReelShort 分成对账",
};

export const RANKS = [...THEATER_BASES, ...RS_RANKS] as const;
export type RankKey = (typeof RANKS)[number];

export const RANK_LABELS: Record<RankKey, string> = { ...BASIS_LABELS, ...RS_RANK_LABELS };

export function isRsRank(k: string): k is RsRank {
  return (RS_RANKS as readonly string[]).includes(k);
}

export const RANK_DEFAULT: RankKey = "kd";

/** 评级榜（ShortMax / MoboReels）的档位，SSS 最高；顺序就是排序与 chips 顺序 */
export const GRADES = ["SSS", "SS", "S", "A", "B", "C", "D"] as const;
export type Grade = (typeof GRADES)[number];

/** 发布记录 tab 的状态筛选：全部 / 已发 / 已排期未发 / 未排期 / 剧库未对上 */
export const POSTED_STATES = ["", "pub", "sched", "none", "nomatch"] as const;
export type PostedState = (typeof POSTED_STATES)[number];

export const POSTED_STATE_LABELS: Record<PostedState, string> = {
  "": "全部",
  pub: "已发",
  sched: "已排期未发",
  none: "未排期",
  nomatch: "剧库未对上",
};

/** 发布记录筛选：不限 / 未发过 / 发过 / 在选剧池 */
export const POSTED_FILTERS = ["", "no", "yes", "pool"] as const;
export type PostedFilter = (typeof POSTED_FILTERS)[number];

export const POSTED_LABELS: Record<PostedFilter, string> = {
  "": "不限",
  no: "未发过",
  yes: "发过",
  pool: "在选剧池",
};

/** 每页行数白名单。`?size=100000` 会让一次请求把全表搬回来，而这一页没有速率限制 */
export const PAGE_SIZES = [20, 50, 100, 200] as const;
export const PAGE_SIZE = 50;

/** 页码上限。防的是 `?page=999999999` 变成一次巨大的 OFFSET 全表扫 */
export const MAX_PAGE = 500;

/** 搜索词最长；再长的搜索词不是人打的 */
export const MAX_QUERY = 80;

export interface PickRequest {
  tab: Tab;
  sort: Sort;
  /** 剧场过滤，空串 = 全部 */
  platform: Platform | "";
  /** 语种过滤（剧单里的中文名，如「英语」；ReelShort 行按 LANG_LOC 反查成同一套中文名），空串 = 全部 */
  lang: string;
  /** 依据过滤，空串 = 任一信号 */
  basis: Basis | "";
  posted: PostedFilter;
  /** 含已下架的行 */
  withOff: boolean;
  /** 全部剧库 tab：只看有信号的行（默认含仅剧单收录的行） */
  signalOnly: boolean;
  /** 选剧 tab：含仅剧单收录的行（默认只看有信号 / 有候选条件的行）；artifact 的 wide */
  wide: boolean;
  /** 条件行：只留 YouTube 能发的行（禁 YouTube 的剧场去掉；限剧单的剧场只留在 YouTube 剧单上的行） */
  youtubeOk: boolean;
  /** 条件行：只看最近一条证据有日期的行 */
  datedOnly: boolean;
  /** 「收起其他剧场」：只看在用的五个 */
  inUseOnly: boolean;
  page: number;
  size: number;
  q: string;
  /** 证据页要看的 row_key */
  rowKey: string;
  /**
   * 证据页的来源 tab（`from=`）。只在 tab=row 时有意义：没有它，从全部剧库 / 榜单 / 发布记录点进
   * 证据页再「返回列表」，落到的是选剧 tab 的另一张表（/qa 2026-09-11 ISSUE-001）。
   */
  from: ListTab;
  /** 榜单 tab：看哪张榜 */
  rank: RankKey;
  /** 榜单 tab：日榜看哪一天（YYYY-MM-DD，空 = 最近一天） */
  day: string;
  /** 榜单 tab：周热门看哪一周（剧场的周标签，如 8.10–8.16，空 = 最近一周） */
  week: string;
  /** 榜单 tab：评级榜只看某一档 */
  grade: Grade | "";
  /** ReelShort 榜：排序（涨幅榜 / 候选清单才有 chips；别的榜各自固定） */
  rsSort: RsSort;
  /** ReelShort 榜：语种码过滤（en / zh-hant），空串 = 全部 */
  rsLocale: string;
  /** ReelShort 榜：上线时段分桶，null = 全部 */
  rsBucket: Bucket | null;
  /** 发布记录 tab：状态筛选 */
  postedState: PostedState;
  /** 发布记录单条记录页要看的选剧池编号（SD-000001） */
  sd: string;
}

function pick<T extends string>(
  value: string,
  allowed: readonly T[],
  fallback: T,
): T {
  return (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

/**
 * 语种是剧单里的中文名（英语 / 繁体中文……），参与的是参数化的 WHERE，
 * 但仍然只收「像语种名」的短串：最多 8 个字，只许字母与汉字。
 */
function cleanLang(raw: string): string {
  const s = raw.trim();
  return /^[\p{L}]{1,8}$/u.test(s) ? s : "";
}

/** ReelShort 榜的语种码：2–7 位 ASCII 语言标签（与 lib/observe/metrics.ts 同一条规则） */
function cleanLocale(raw: string): string {
  const s = raw.trim();
  return /^[a-z]{2}(-[a-z]{2,4})?$/i.test(s) ? s.toLowerCase() : "";
}

/** C0 控制字符与 DEL；三处校验共用 */
const CONTROL = /[\u0000-\u001f\u007f]/;
const CONTROL_ALL = /[\u0000-\u001f\u007f]/g;

/** 行键长度上限（实测最长 73） */
export const ROW_KEY_MAX = 120;

/**
 * row_key 只做参数化的等值查询，所以不限字符集：剧场给了 id 的行键是「平台-id」，
 * 而 id 可以是 base64（GoodShort 的 `mqk++n/L+Wf/xDC0G43CRQ==`）或带汉字
 * （ShortMax 的 `845227（已设置定时）`），实测 4,846 行不是纯 slug。
 * 只拒绝空串、全空白、控制字符与超长。
 *
 * 【不 trim】：首尾空格是键的一部分。行键是剧场 id 原样拼的，2026-09-13 只读实测 catalog_rows 有 19 个
 * ShortMax 行键带尾随空格（`shortmax-856049 `，12 个在选剧 tab 默认可见）。trim 之后是另一个键、等值查询永远查不到：
 * 列表里点剧名进证据页落「找不到这一行」，问答的 get_catalog_row 同样找不到，而页面照常 200。
 * 证据页、问答工具（rowKeyArg）与问答正文的 row: 链接（lib/ask/markdown.ts）三处共用这一条判定。
 */
export function isRowKey(s: string): boolean {
  return s.length > 0 && s.length <= ROW_KEY_MAX && /\S/.test(s) && !CONTROL.test(s);
}

function cleanRowKey(raw: string): string {
  return isRowKey(raw) ? raw : "";
}

/** 日榜的日期只收规范的 YYYY-MM-DD；它进的是 jsonb 的字符串比较，不是 date 类型，形状错了只会查空 */
function cleanDay(raw: string): string {
  const s = raw.trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : "";
}

/** 周：周起日期 YYYY-MM-DD，或剧场自己写的周标签（8.10–8.16 这种带 en dash 的，旧书签里的写法）；只拒控制字符与超长，是哪一周由 resolveWeek 对着有榜的周判 */
function cleanWeek(raw: string): string {
  const s = raw.trim();
  return s.length > 0 && s.length <= 24 && !CONTROL.test(s) ? s : "";
}

/** 选剧池编号形如 SD-000001；posted.py 原样保留运营填的值，所以只限形状不限前缀 */
function cleanSd(raw: string): string {
  const s = raw.trim();
  return /^[A-Za-z0-9_-]{1,40}$/.test(s) ? s : "";
}

export function parsePickRequest(
  params: URLSearchParams | Record<string, string | string[] | undefined>,
): PickRequest {
  const get = (key: string): string => {
    if (params instanceof URLSearchParams) return params.get(key) ?? "";
    const raw = params[key];
    return (Array.isArray(raw) ? raw[0] : raw) ?? "";
  };
  const pageRaw = Number.parseInt(get("page"), 10);
  const sizeRaw = Number.parseInt(get("size"), 10);
  const tab = pick(get("tab"), TABS, "pick");
  const bucketRaw = get("bk");
  return {
    tab,
    sort: pick(get("sort"), SORTS, "evidence"),
    platform: pick(get("platform"), [...PLATFORMS, ""] as const, ""),
    lang: cleanLang(get("lang")),
    basis: pick(get("basis"), [...BASES, ""] as const, ""),
    posted: pick(get("posted"), POSTED_FILTERS, ""),
    withOff: get("off") === "1",
    signalOnly: get("sig") === "1",
    wide: get("w") === "1",
    youtubeOk: get("yt") === "1",
    datedOnly: get("dated") === "1",
    inUseOnly: get("inuse") === "1",
    page:
      Number.isFinite(pageRaw) && pageRaw >= 1
        ? Math.min(pageRaw, MAX_PAGE)
        : 1,
    size: (PAGE_SIZES as readonly number[]).includes(sizeRaw)
      ? sizeRaw
      : PAGE_SIZE,
    /* 去掉控制字符：它们进不了 ILIKE 的语义，只会让日志里出现看不见的东西 */
    q: get("q").replace(CONTROL_ALL, "").trim().slice(0, MAX_QUERY),
    rowKey: cleanRowKey(get("row")),
    from: pick(get("from"), LIST_TABS, "pick"),
    rank: pick(get("rk"), RANKS, RANK_DEFAULT),
    day: cleanDay(get("day")),
    week: cleanWeek(get("week")),
    grade: pick(get("grade"), [...GRADES, ""] as const, ""),
    rsSort: pick(get("rs"), RS_SORTS, "rr"),
    rsLocale: cleanLocale(get("rl")),
    rsBucket: (BUCKETS as readonly string[]).includes(bucketRaw) ? (bucketRaw as Bucket) : null,
    postedState: pick(get("pst"), POSTED_STATES, ""),
    sd: cleanSd(get("sd")),
  };
}

/** 把请求写回查询串；只写非默认值，地址才短得能发给同事 */
export function pickQuery(
  req: PickRequest,
  patch: Partial<PickRequest> = {},
): string {
  const r = { ...req, ...patch };
  const p = new URLSearchParams();
  if (r.tab !== "pick") p.set("tab", r.tab);
  if (r.sort !== "evidence") p.set("sort", r.sort);
  if (r.platform) p.set("platform", r.platform);
  if (r.lang) p.set("lang", r.lang);
  if (r.basis) p.set("basis", r.basis);
  if (r.posted) p.set("posted", r.posted);
  if (r.withOff) p.set("off", "1");
  if (r.signalOnly) p.set("sig", "1");
  if (r.wide) p.set("w", "1");
  if (r.youtubeOk) p.set("yt", "1");
  if (r.datedOnly) p.set("dated", "1");
  if (r.inUseOnly) p.set("inuse", "1");
  if (r.q) p.set("q", r.q);
  if (r.size !== PAGE_SIZE) p.set("size", String(r.size));
  if (r.page > 1) p.set("page", String(r.page));
  if (r.tab === "row" && r.rowKey) p.set("row", r.rowKey);
  if (r.tab === "row" && r.from !== "pick") p.set("from", r.from);
  /* 榜单 / 发布记录自己的参数只在那两个 tab 下写；证据页是从它们进来的也要带着，返回时才回得到同一张表 */
  const origin = r.tab === "row" ? r.from : r.tab;
  if (origin === "rank") {
    if (r.rank !== RANK_DEFAULT) p.set("rk", r.rank);
    if (isRsRank(r.rank)) {
      if (r.rsSort !== "rr") p.set("rs", r.rsSort);
      if (r.rsLocale) p.set("rl", r.rsLocale);
      if (r.rsBucket) p.set("bk", r.rsBucket);
    } else {
      if (r.day) p.set("day", r.day);
      if (r.week) p.set("week", r.week);
      if (r.grade) p.set("grade", r.grade);
    }
  }
  if (origin === "posted") {
    if (r.postedState) p.set("pst", r.postedState);
    if (r.sd) p.set("sd", r.sd);
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** 鹊娱剧库：没有单剧直达地址，只能把剧名带到列表页上（见 scripts/juyuantai/README.md） */
export const QUEYU_INDEX = "https://cps-distribution.zwnet.cn/promotion/index";

export function queyuHref(title: string): string {
  return `${QUEYU_INDEX}?title=${encodeURIComponent(title)}`;
}
