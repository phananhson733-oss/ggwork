/**
 * 选剧工作台镜像导出（feed v2，`pick-export-v2`）的纯映射与清洗：资源与列白名单、jsonb 键白名单、网盘清洗与豁免清单、
 * 行映射、manifest 与 meta.* 的键白名单、fingerprint。查询参数、游标与按字节截页在 export-v2-page.ts（单向依赖本文件）。
 * 【不许 import 任何 server-only 模块】——tests/pick-export-v2.test.ts 直接加载；查库与拼 SQL 在 export-v2.ts（server-only）。
 *
 * 方案见 ggwork-deerflow 的 docs/plans/2026-09-23-supabase-pick-board-plan.md 第 3.3、4 节（第 13、14 节优先）。
 * 禁止字段（pan_url、pan_pw、promotion_value、分成 USD 与对账金额）的保障落在【输出】上（4.6）：
 * 1. 列白名单：每个资源输出哪些列、什么类型写在 RESOURCE_SPECS；signal payload 与帖子 jsonb 按键白名单挑；
 *    manifest 与 meta.* 每一层按 MANIFEST_SHAPE 挑键。RealShort 自己的 loader 读到禁止列没关系，禁止的是它们出现在响应里。
 * 2. 自由文本：scrubPanText 在原文或规范化副本里认出网盘链接或提取码，就把整个字符串换成「[网盘信息已移除]」。正则与规范化
 *    规则的唯一来源是 tests/fixtures/pan-scrub-cases.json，这里与它逐字相同（测试钉住），工作台的 Python 闸门也逐字取用、照同一流程做。
 *    豁免只在一行的顶层按名字点（SCRUB_EXEMPT_KEYS），嵌套层按路径点名（SCRUB_EXEMPT_PATHS），新字段默认清洗。
 * 3. 只有两个点名的派生：has_pan（布尔）与 bill_rank（整数名次），在 export-v2.ts 的 SQL 里算好（13.3）。
 */
import { createHash } from "node:crypto";

import { SORT_LABELS, SORTS as RS_SORTS } from "@/lib/observe/metrics";
import { OBSERVE_SOURCES } from "@/lib/observe/source-types";

import { LANG_LOC } from "./catalog-import";
import { GLOSSARY, RULE_HINTS } from "./glossary";
import { PLATFORM_RULES, POSTED_POOL_URL, YOUTUBE_LABEL } from "./platforms";
import { BASES, BASIS_DATE_LABEL, BASIS_LABELS, IN_USE, PLATFORMS, RANKS, RS_RANK_LABELS, RS_RANKS } from "./request";

export const EXPORT_VERSION = "pick-export-v2";

/** 输出里的 key 一律不许匹配它（方案 4.6 第 2 条逐字）；两个派生 has_pan、bill_rank 本来就不匹配 */
export const FORBIDDEN_NAME = /pan_|promotion_value|promotion_code|promotion_link|revenue_usd|bill_usd|usd/i;

type Json = Record<string, unknown>;

function isPlainObject(v: unknown): v is Json {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return false;
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

/** 深拷贝 JSON 形状的值：输出从不与入参共享可变结构 */
function copyJson(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(copyJson);
  if (isPlainObject(v)) return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, copyJson(x)]));
  return v;
}

/**
 * 同 copyJson，但任何层级上像禁止字段的键（FORBIDDEN_NAME）连同它的值一起丢掉。jsonb 的白名单只管第一层的键，
 * 值里嵌套的对象（payload.h[*][2]、帖子的 md 这类）没有白名单，只能按名字拦：与方案 4.6 第 2 条自检用的是同一个正则。
 */
function copyJsonWithoutForbidden(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(copyJsonWithoutForbidden);
  if (!isPlainObject(v)) return v;
  return Object.fromEntries(
    Object.entries(v)
      .filter(([k]) => !FORBIDDEN_NAME.test(k))
      .map(([k, x]) => [k, copyJsonWithoutForbidden(x)]),
  );
}

/* ---------------------------------------------------------------- 资源与列白名单 */

export const ROW_RESOURCES = [
  "catalog_rows",
  "catalog_signals",
  "catalog_posted",
  "catalog_accounts",
  "rs_rows",
  "rs_ids",
  "rs_clicks14",
  "rs_bill_orders",
  "rs_series_day",
] as const;
export type RowResource = (typeof ROW_RESOURCES)[number];
export const EXPORT_RESOURCES = ["manifest", ...ROW_RESOURCES] as const;
export type ExportResource = (typeof EXPORT_RESOURCES)[number];

export function isExportResource(v: string): v is ExportResource {
  return (EXPORT_RESOURCES as readonly string[]).includes(v);
}

/**
 * 列类型决定输出值怎么归一，类型不符就抛错、不猜：
 * - text / day 原样字符串。day 是 RealShort 的 YYYY-MM-DD 文本日期列，镜像照存文本（3.3），它多一个「豁免清洗」的身份；
 * - int / float 一律 number（count、rank() 这类 bigint 与 numeric 经 raw execute 以十进制字符串过来，也转掉）；
 * - bool 另认 PG 的文本形态 t / f；ts 一律 ISO 字符串（raw execute 回来的 `2026-09-01 06:54:13.83+00` 与 Date 都认）；
 * - text[] 只收字符串数组；json 只收 JSON 形状，并且必须在 JSON_KEYS 里登记了键白名单。
 */
const COLUMN_TYPES = ["text", "day", "int", "float", "bool", "ts", "text[]", "json"] as const;
export type ColumnType = (typeof COLUMN_TYPES)[number];

export interface ColumnSpec {
  readonly name: string;
  readonly type: ColumnType;
  readonly nullable: boolean;
}

export interface ResourceSpec {
  /** keyset 游标的主键列，按这个顺序 ORDER BY 与比较 */
  readonly key: readonly string[];
  /** 每页条数上限（4.4），也是 limit 的缺省值 */
  readonly maxLimit: number;
  /** 输出列，按这个顺序输出 */
  readonly columns: readonly ColumnSpec[];
}

/** "name:type"，可空写成 "name:type?" */
function cols(defs: readonly string[]): readonly ColumnSpec[] {
  return defs.map((d) => {
    const [name, raw = ""] = d.split(":");
    const nullable = raw.endsWith("?");
    const type = nullable ? raw.slice(0, -1) : raw;
    if (!(COLUMN_TYPES as readonly string[]).includes(type)) throw new Error(`export-v2：列 ${name} 的类型 ${type} 不认识`);
    return { name, type: type as ColumnType, nullable };
  });
}

/** 与 catalog_rows 同形的列：剧单行与 ReelShort 行共用，列序同方案 3.3 */
const CATALOG_SHAPED = [
  "row_key:text", "platform:text", "source_table:text", "title:text", "title_cn:text", "lang:text", "kind:text",
  "origin:text", "tags:text", "listed_on:day?", "episodes:int?", "pay_start:int?", "youtube:bool", "merged_rows:int",
  "off_on:day?", "reoff_note:text", "title_key:text", "in_site_ids:text[]", "legacy_only:bool", "site_other:bool",
  "has_signal:bool", "latest_evidence_on:day?",
];

/**
 * 每个资源的输出列（方案 3.3、4.4）。【明确不存在的列】不在这里就不会出现在响应里：
 * catalog_rows 没有 pan_url / pan_pw / creator（has_pan 是 SQL 里由 pan_url 算出的布尔）；
 * rs_rows 没有任何 bill_usd*、推广链接、promotion_code、group_key（bill_rank 是按分成排的整数名次）；
 * rs_bill_orders 没有 revenue_usd / promotion_value（同键的几个 promotion_value 合并，source_rows 记合并了几行原始行）。
 */
export const RESOURCE_SPECS: Readonly<Record<RowResource, ResourceSpec>> = {
  catalog_rows: {
    key: ["row_key"],
    maxLimit: 5000,
    columns: cols([...CATALOG_SHAPED, "imported_at:ts", "has_pan:bool"]),
  },
  catalog_signals: {
    key: ["row_key", "kind", "ord"],
    maxLimit: 5000,
    columns: cols(["row_key:text", "kind:text", "ord:int", "evidence_on:day?", "rank:int?", "grade:text", "note:text", "payload:json"]),
  },
  catalog_posted: {
    key: ["sd"],
    maxLimit: 1000,
    columns: cols([
      "sd:text", "feishu_record:text", "title:text", "title_key:text", "lang:text", "platform:text", "life:text",
      "scheduled:bool", "online_on:day?", "why:text", "note:text", "archived:bool", "post_count:int", "last_post_on:day?",
      "views_total:int", "sources:text[]", "cats:text[]", "who:text[]", "accounts:text[]", "created_on:day?",
      "updated_on:day?", "first_post_on:day?", "metric_at:day?", "sched_count:int", "views_count:int", "posts:json",
      "row_keys:text[]", "drama_ids:text[]", "imported_at:ts",
    ]),
  },
  catalog_accounts: {
    key: ["id"],
    maxLimit: 1000,
    columns: cols(["id:text", "name:text", "url:text", "grp:text", "form:text", "niche:text", "status:text", "fans:int?", "as_of:day?", "imported_at:ts"]),
  },
  rs_rows: {
    key: ["drama_id"],
    maxLimit: 2000,
    columns: cols([
      ...CATALOG_SHAPED,
      "has_pan:bool", "rs_clk:bool", "rs_bill:bool", "rs_gsc:bool", "rs_clk_on:day?", "rs_bill_on:day?", "rs_gsc_on:day?",
      "drama_id:text",
      "locale:text", "slug:text", "publish_at:ts?", "chapter_count:int", "pay_start_raw:int", "rr:float", "promoters_cnt:int",
      "metrics_valid:bool?", "synced_at:ts?", "search_impressions:int", "search_data_at:ts?", "detail_synced_at:ts?",
      "tag_list:text[]", "description:text",
      "baseline1_at:ts?", "baseline7_at:ts?", "baseline15_at:ts?", "rr1:float?", "p1:int?", "rr7:float?", "p7:int?",
      "rr15:float?", "p15:int?", "s1_rr:float?", "s1_p:int?", "s7_rr:float?", "s7_p:int?", "clicks7:int",
      "last_click_on:day?", "bill_orders:int", "last_bill_on:day?", "bill_rank:int?",
    ]),
  },
  rs_ids: {
    key: ["id"],
    maxLimit: 10000,
    columns: cols(["id:text", "canonical_id:text?", "locale:text", "slug:text", "title:text", "chapter_count:int", "pay_start:int", "is_public_canonical:bool"]),
  },
  rs_clicks14: {
    key: ["drama_id", "day"],
    maxLimit: 20000,
    columns: cols(["drama_id:text", "day:day", "human:int", "bot:int"]),
  },
  rs_bill_orders: {
    key: ["bill_date", "book_id", "promotion_type"],
    maxLimit: 5000,
    columns: cols(["bill_date:day", "book_id:text", "promotion_type:text", "canonical_id:text?", "book_title:text", "order_cnt:int", "source_rows:int", "same_day_clicks:int"]),
  },
  rs_series_day: {
    key: ["drama_id"],
    maxLimit: 40000,
    columns: cols(["drama_id:text", "revenue_cents:float", "promoters_cnt:int"]),
  },
};

/** catalog_signals.payload 的键白名单（3.3）：导入时 `...rest` 会收下 build.py 输出的任何未知键，这里只挑认识的 */
export const SIGNAL_PAYLOAD_KEYS = ["d", "w", "weeks", "best", "days", "first", "h", "qy", "pid"] as const;
/** catalog_posted.posts 每条帖子的键白名单：scripts/juyuantai/posted.py 写出的全部键（RawPost 加 how） */
export const POSTED_POST_KEYS = ["d", "acct", "st", "views", "likes", "favs", "cmts", "shares", "md", "url", "note", "how", "pid"] as const;

/** jsonb 列的键白名单：object = 一个对象挑键；list = 对象数组逐个挑键。新加的 json 列没登记，映射时直接抛错 */
const JSON_KEYS: Readonly<Record<string, { kind: "object" | "list"; keys: readonly string[] }>> = {
  "catalog_signals.payload": { kind: "object", keys: SIGNAL_PAYLOAD_KEYS },
  "catalog_posted.posts": { kind: "list", keys: POSTED_POST_KEYS },
};

/* ---------------------------------------------------------------- 网盘清洗 */

/**
 * 3.3 的豁免清单：标识与 id 数组、v1 的 source_id / detail_url / source_ref，以及各资源的日期列。
 * 【只在一行的顶层按名字生效】jsonb 与数组里嵌套的键（payload.h[*][2] 里的 id、day 这类）不认名字、照常清洗，
 * 嵌套层要豁免只能在 SCRUB_EXEMPT_PATHS 按路径点名；不在这两处的文本一律清洗，新字段默认清洗。
 * 改写这些字段会破坏主键与链接：row_key `goodshort-K10JEicNmxOWhQPwdg3zdw==` 就会被宽松的提取码正则命中。
 */
const IDENTITY_KEYS = [
  "row_key", "sd", "feishu_record", "id", "drama_id", "canonical_id", "book_id", "slug", "title_key",
  "in_site_ids", "row_keys", "drama_ids", "source_id", "detail_url", "source_ref",
];
const DATE_KEYS = [
  "listed_on", "off_on", "latest_evidence_on", "imported_at", "evidence_on", "online_on", "last_post_on", "created_on",
  "updated_on", "first_post_on", "metric_at", "as_of", "rs_clk_on", "rs_bill_on", "rs_gsc_on", "publish_at", "synced_at",
  "search_data_at", "detail_synced_at", "baseline1_at", "baseline7_at", "baseline15_at", "last_click_on", "last_bill_on",
  "day", "bill_date", "listed_at", "observed_at",
];
export const SCRUB_EXEMPT_KEYS: ReadonlySet<string> = new Set([...IDENTITY_KEYS, ...DATE_KEYS]);

/**
 * 嵌套层的豁免，按相对一行根的路径点名（数组下标写 [*]）。v2 各资源的嵌套只有 jsonb 与 text[]，没有要豁免的；
 * v1 行（feed-map.ts 的 FeedRow）里有三处：signals[*].source_ref（由 row_key 拼成的后台链接，3.3 点名豁免）、
 * signals[*].observed_at 与 posted.last_post_on（日期）。
 */
export const SCRUB_EXEMPT_PATHS: ReadonlySet<string> = new Set(["signals[*].source_ref", "signals[*].observed_at", "posted.last_post_on"]);

export const PAN_SCRUB_REPLACEMENT = "[网盘信息已移除]";

/**
 * 网盘域名：方案 4.6 的清单，2026-09-23 验收补上 yun.baidu.com、pan.xunlei.com、115cdn.com、移动云盘两个域名与
 * 123 云盘的三个分享域名；2026-09-24 第二十二轮补上微云、坚果云、PikPak、联通云盘、城通、蓝奏云优享、小飞机网盘，第二十三轮补上
 * 蓝奏云的短域名（lanzn、lanzv、lanzx 这类 lanz 加一个字母）、文叔叔、奶牛快传、360 安全云盘；第二十四轮按审计给的国内分享服务
 * 清单一次补齐各家的短域名与别名（UC fast.uc.cn、115 anxia.com、123 云盘 123952.com、城通的 400gb.com 等镜像、小飞机、文叔叔 wss.cc、
 * 奶牛 c-t.work、360 的旧域名）、曲奇云盘、MuseTransfer、钛盘、AirPortal、轻松传、联想 Filez 与企业网盘、新浪微盘、威盘，以及已停运的
 * 金山快盘、华为网盘、搜狐网盘、联通旧入口。通用短链（url.cn、t.cn、dwz.cn）可能跳向网盘也可能跳向正常内容，不收。
 */
export const PAN_DOMAINS_SOURCE =
  "(?:pan\\.baidu\\.com|yun\\.baidu\\.com|pan\\.quark\\.cn|aliyundrive\\.com|alipan\\.com|115\\.com|115cdn\\.com|123pan\\.(?:com|cn)|" +
  "123684\\.com|123865\\.com|123912\\.com|lanzou[a-z]{0,2}\\.com|drive\\.uc\\.cn|cloud\\.189\\.cn|pan\\.xunlei\\.com|caiyun\\.139\\.com|yun\\.139\\.com|" +
  "weiyun\\.com|jianguoyun\\.com|mypikpak\\.com|pan\\.wo\\.cn|ctfile\\.com|ilanzou\\.com|feijipan\\.com|" +
  "lanz[a-z]\\.com|wenshushu\\.cn|cowtransfer\\.com|yunpan\\.360\\.cn|" +
  "fast\\.uc\\.cn|anxia\\.com|123952\\.com|400gb\\.com|pipipan\\.com|545c\\.com|90pan\\.com|089u\\.com|474b\\.com|" +
  "t00y\\.com|306t\\.com|47ks\\.com|4765\\.com|77tj\\.com|feijix\\.com|fjpan\\.com|wss\\.cc|c-t\\.work|yunpan\\.cn|" +
  "yunpan\\.com|pan\\.360\\.cn|quqi\\.com|musetransfer\\.com|tmp\\.link|airportal\\.cn|airportal\\.link|easychuan\\.cn|" +
  "filez\\.com|box\\.lenovo\\.com|vdisk\\.weibo\\.com|v\\.disk\\.weibo\\.com|vdisk\\.cn|kuaipan\\.cn|dbank\\.com|" +
  "dbank\\.vmall\\.com|pan\\.sohu\\.net|fhrl\\.wostore\\.cn)";
/**
 * 原文上的网盘链接检测：域名大小写不敏感。只问有没有，不定位要换掉的那一段（命中就整串替换，见 scrubPanText），
 * 所以 scheme、子域名、路径与 query 都不用写进来。
 * - 左边界只挡 ASCII 字母数字（abc115.com 不算）：紧贴「-」「.」「_」的无协议链接（资源-pan.baidu.com/s/…、
 *   资源1-pan.baidu.com/s/… 这类带编号的写法）照样命中。代价是 example-alipan.com、my-115.com 这类连字符接网盘域名的
 *   别家主机也算（宁可多清：挡住「字母数字 + -」就漏了带编号的分享链接）；
 * - 右边界挡住 115.community、pan.baidu.com-x 这类更长的主机名。
 * 与 tests/fixtures/pan-scrub-cases.json 的 patterns.url.source 逐字相同。
 */
export const PAN_URL_SOURCE = "(?<![A-Za-z0-9])" + PAN_DOMAINS_SOURCE + "(?![A-Za-z0-9-])";

/**
 * 提取码：只认六个关键字（提取码、提取碼、访问码、訪問碼、密码、密碼）加显式分隔符（: ： =），码是 4–8 位字母数字，
 * 两侧不能再接字母数字。不收单独的 code、pwd（真实剧名 Code Name Reaper II、Cheat Code Champion 与英文简介的
 * secret code that 都会被误伤）；「密码学」这类不带分隔符的词也不收。
 * 关键字、分隔符、码值之间可以夹 Unicode 的全部空白字符（White_Space）：方案原文写的是 \s*，这里逐个写成显式的字符类
 * （Python 的 \s 多认 U+001C–U+001F、JS 的多认 U+FEFF，两边会不一致）。U+2028、U+2029、U+0085 这类换行 NFKC 不改，
 * 不写进来就漏（提取码:<U+2028>ab12）。空隙里也可以夹 HTML 标签（PAN_CODE_GAP）：解码出来的 <ab12> 本身就像标签，
 * 靠删标签把 <b> 挪开会连码值一起删掉（提取码：<b>&lt;ab12&gt;</b>），不删又被 <b> 隔开。标签的 {0,64} 按码点数，两条正则都带 u 标志。
 * 与 tests/fixtures/pan-scrub-cases.json 的 patterns.code.source 逐字相同。
 */
/**
 * 规范化副本删掉的 HTML 标签（<b>、</a>）与注释（服务端渲染在相邻文本之间插的 <!-- -->），也是提取码里可以夹的标签；
 * 与夹具的 normalize.tags 逐字相同。标签名的首字母后面至多 64 项：一项是一个不是 < > = 的字符，或者一个 =；
 * = 后面（可隔至多 64 个 HTML 空白）是一对引号括起来的至多 64 个字符的，连着这对引号算同一项，引号里可以有 < >
 * （<span title="下载 > 复制">）；= 后面的引号在这个长度里没有收尾的、以及别处的引号都当普通字符，标签照旧到第一个 > 收尾
 * （<a title=it's>、<a href=">）。= 这一项要么连着完整的引号串、要么不连，同一段文本只有一种拆法，失败时不会回溯成指数级。
 * 都按码点数：TAG_RE 与两条提取码正则都带 u 标志，与 Python 一致。
 */
const PAN_TAG_QUOTED = `[\\t\\n\\f\\r ]{0,64}(?:"[^"]{0,64}"|'[^']{0,64}')`;
const PAN_TAG_ITEM = `(?:[^<>=]|=(?:${PAN_TAG_QUOTED}|(?!${PAN_TAG_QUOTED})))`;
const PAN_TAG_ELEMENT = `<\\/?[A-Za-z]${PAN_TAG_ITEM}{0,64}>`;
export const PAN_TAG_SOURCE = `(?:${PAN_TAG_ELEMENT}|<!--[^<>]{0,64}-->)`;
/** 六个关键字；字与字之间也可以夹标签（提取<b>码</b>：），不靠删标签把关键字接上 */
const PAN_CODE_KEYWORDS = ["提取码", "提取碼", "访问码", "訪問碼", "密码", "密碼"];
/** 六个关键字用到的字：提取码碼访问訪問密 */
const PAN_CODE_KEYWORD_CHARS = [...new Set(PAN_CODE_KEYWORDS.join(""))].join("");
/**
 * 提取码正则里跨过的标签（关键字字间、关键字与分隔符之间、分隔符与码值之间）：同 PAN_TAG_SOURCE，只是 = 后面当成一项的完整引号串
 * （里面可以有 < >）不能有六个关键字用到的字（PAN_CODE_KEYWORD_CHARS），标签里别的字符（包括这样的引号串匹配不上时逐个走的引号与
 * 引号里的字，<i title="提"> 照样跨过）不能有 < > = 与「码」「碼」，注释不能有 < > 与「码」「碼」；代码围栏的语言标记同样不能有「码」「碼」。
 * 起点都在关键字用到的字上（每个关键字从它的第一个字起查）。引号串里可以有 < >，从关键字前几个字起头的起点接得上后面的标签，
 * 一路跨过去找下一个字（<a x='提<a x="'> 重复很多次），所以当成一项的引号串里一个都不能有；标签别处、注释里不能有 <，从那里起头的起点
 * 后面只能紧跟关键字的下一个字，没有「码」「碼」凑不齐，停在它的标签里。围栏语言标记里起头的起点能沿着紧跟的一串标签走出
 * 这一行（```x提<b⏎>），但那串标签里的起点都停在各自的标签里，一串标签只有它一个能走远。这样一个起点扫过的范围里没有能走远的
 * 别的起点，整段检测随长度线性：重复很多次的 <i title="提取码：">、<i title="密码⏎>、<a x='提<a x="'> 都不会让每个起点都扫到结尾。
 * 引号串里写了这些字的（提取码：<span title="复制提取码">ab12、包着只读输入框的 <span title="复制提取码"><input value="ab12">），
 * 由 keyless 副本（标签留着、里面关键字用到的字换成 U+FFFD，见 PAN_PROBE_COPIES）照查；关键字本身只写在标签属性里、后面又隔着这样的标签的
 * （<i title="提取码："><span title="复制提取码">ab12）两边都认不出，是构造出来的组合。
 */
const PAN_GAP_TAG_QUOTED = `[\\t\\n\\f\\r ]{0,64}(?:"[^"${PAN_CODE_KEYWORD_CHARS}]{0,64}"|'[^'${PAN_CODE_KEYWORD_CHARS}]{0,64}')`;
const PAN_GAP_TAG_ITEM = `(?:[^<>=码碼]|=(?:${PAN_GAP_TAG_QUOTED}|(?!${PAN_GAP_TAG_QUOTED})))`;
const PAN_GAP_TAG_SOURCE = `(?:<\\/?[A-Za-z]${PAN_GAP_TAG_ITEM}{0,64}>|<!--[^<>码碼]{0,64}-->)`;
const PAN_CODE_KEYWORD = `(?:${PAN_CODE_KEYWORDS.map((word) => [...word].join(`(?:${PAN_GAP_TAG_SOURCE})*`)).join("|")})`;
const PAN_CODE_SPACE_CHARS = "\\t\\n\\v\\f\\r \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PAN_CODE_SPACE = `[${PAN_CODE_SPACE_CHARS}]`;
const PAN_CODE_LINE_SPACE = "[\\t \\u00a0\\u1680\\u2000-\\u200a\\u202f\\u205f\\u3000]";
const PAN_CODE_NEWLINE_CHARS = "\\n\\v\\f\\r\\u0085\\u2028\\u2029";
/**
 * 分隔符后面、码值前面可以夹的符号，逐段写出（JS 与 Python 逐字一致）：Unicode 的全部空白、ASCII 标点（< 除外，它归标签与
 * 尖括号那一支）、Latin-1 符号、间距修饰符号（˖ ˗ ˏ ˋ）、藏文的装饰括号（༺ ༻ ༼ ༽）、通用标点（— … • 零宽字符）、符号用的
 * 组合附加符号（键帽的 U+20E3）、字母式符号、罗马数字、箭头、数学符号、杂项技术符号、带圈字母数字（① ⑴ ⒈）、制表符、方块、
 * 几何图形（▪ ●）、杂项符号（★ ☞）、Dingbats（✅ ➡）、补充箭头与数学符号、补充标点、CJK 标点（、。【】「」）、带圈 CJK（㈠）、
 * 爪哇文的装饰括号（꧁ ꧂）、变体选择符、竖排与小写形式、全角标点、emoji（U+1F000–U+1FAFF，字面字符，所以两条提取码正则
 * 必须带 u）。不在里面的：
 * - ASCII 与全角的字母数字、汉字（字母式符号、罗马数字、带圈数字、上标这类兼容字符在里面）：码值前面有文字的
 *   （提取码：见评论区 ab12）不认；其他文字的字母即使拿来装饰（ღ ๑ ʚ ɞ）也不在里面；
 * - & 与 \（连同全角与小写形式）：&nbsp; &quot; &#12345; \u7a0d 里的字母数字不当成码值，副本解开实体、删掉 \ 之后照查；
 * - 货币符号（$ ¢ £ ¤ ¥、U+20A0–U+20CF、﹩ ＄ ￠ ￡ ￥ ￦）：没有人在码值前面写货币符号，剧目备注里却常有价格（爱情密码：￥1999元整季授权）；
 *   ₨ 在副本里 NFKC 成 Rs，爱情密码：₨1999 仍整串替换。
 */
export const PAN_CODE_SYMBOL_SOURCE = "[\\u0009-\\u000d\\u0020-\\u0023\\u0025\\u0027-\\u002f\\u003a-\\u003b\\u003d-\\u0040\\u005b\\u005d-\\u0060\\u007b-\\u007e\\u0085\\u00a0-\\u00a1\\u00a6-\\u00bf\\u00d7\\u00f7\\u02c2-\\u02df\\u02e5-\\u02ff\\u0f3a-\\u0f3d\\u1680\\u2000-\\u206f\\u20d0-\\u20ff\\u2100-\\u2bff\\u2e00-\\u2e7f\\u3000-\\u303f\\u3200-\\u32ff\\ua9c1-\\ua9c2\\ufe00-\\ufe0f\\ufe30-\\ufe5f\\ufe61-\\ufe67\\ufe6a-\\ufe6f\\ufeff\\uff01-\\uff03\\uff05\\uff07-\\uff0f\\uff1a-\\uff20\\uff3b\\uff3d-\\uff40\\uff5b-\\uff65\\uffe2-\\uffe4\\uffe8-\\uffee🀀-🫿]";
/** 列表编号：1–9 位数字、一到四个中文数字或一个 ASCII 字母，后面跟 . ) 、 ． ）之一（1. 12) 1、 一、 a.；(1) （1） 由符号与编号拼成） */
const PAN_CODE_LIST_NUMBER = "(?:[0-9]{1,9}|[一二三四五六七八九十百千零两]{1,4}|[A-Za-z])[.)、．）]";
/** 键帽数字：一个 ASCII 数字、可选的 U+FE0F、U+20E3（1️⃣ 1⃣）；#️⃣ *️⃣ 全是符号 */
const PAN_CODE_KEYCAP = "[0-9]\\ufe0f?\\u20e3";
/** 列表编号或键帽数字；紧跟在 ` 或 ~ 后面的不认，归围栏的语言标记（```a.b） */
const PAN_CODE_NUMBERING = `(?<![\`~])(?:${PAN_CODE_LIST_NUMBER}|${PAN_CODE_KEYCAP})`;
/**
 * 代码围栏：``` 或 ~~~ 一串连着到行尾的语言标记（```txt、~~~plaintext、``` txt、```{.txt}），不要求在行首，引用与列表里的
 * 围栏（> ```txt、- ~~~txt）照样跨过去。` 与 ~ 本身是符号，所以围栏只从一串的第一个起算（前面不是同一种符号）；语言标记
 * 三种起法互斥：紧跟一个 ASCII 字母数字；隔行内空白再接字母数字；可隔行内空白的 { {. {# {= 再接字母数字；从这个字母数字起
 * 到行尾至多 64 个字符，里面不能有同一种围栏符号（同一行里闭合的行内代码 ```Unforgettable``` 不是围栏，不跨过去把下一行的年份当码）
 * 与「码」「碼」（见 PAN_GAP_TAG_SOURCE）。后两种起法的字母数字若起头一个列表编号或键帽数字（``` a.、```{.a.}），围栏不认，由符号与编号拆；
 * 紧跟的那种由围栏拆，编号不认（PAN_CODE_NUMBERING）。没有语言标记的围栏（```⏎）全是符号。
 */
const PAN_CODE_FENCE_START = `(?:${PAN_CODE_LINE_SPACE}*\\{[.#=]?(?!${PAN_CODE_NUMBERING})|${PAN_CODE_LINE_SPACE}+(?!${PAN_CODE_NUMBERING})|)[A-Za-z0-9]`;
const PAN_CODE_FENCE = `(?:(?<!\`)\`{3,}${PAN_CODE_FENCE_START}[^${PAN_CODE_NEWLINE_CHARS}\`码碼]{0,63}|(?<!~)~{3,}${PAN_CODE_FENCE_START}[^${PAN_CODE_NEWLINE_CHARS}~码碼]{0,63})(?![^${PAN_CODE_NEWLINE_CHARS}])`;
/** 任务复选框里打了勾的 [x] [X]（[ ] 本身都是符号） */
const PAN_CODE_CHECKBOX = "\\[[xX]\\]";
/**
 * 关键字与分隔符之间：只有空白、HTML 标签、换行后行首的引用 >（> 提取码⏎> ：）与表格竖线 |（提取码<b>：</b>、| 提取码 |：）。
 * 剧名里的「密码」后面常接书名号与冒号（《致命密码》：2024年上映），这里夹别的符号会把剧名当成提取码，所以【提取码】：这类
 * 关键字外包括号的写法不认；分隔符因此也只有一种认法。
 * 分隔符与码值之间（PAN_CODE_GAP）：符号、HTML 标签与注释（提取码：<b>ab12</b>、提取码：<b>&lt;ab12&gt;</b> 解出的 <b><ab12>，
 * 标签不用靠删掉才能把码值接上）、代码围栏、列表编号、键帽数字、复选框。括号与引号（【ab12】、"ab12"）、指示符号（👉 🔑 →）、
 * Markdown 与纯文本的排版（> 引用、- • 列表、# 标题、=== 下划线、| 表格与 | --- | 分隔行、==高亮==）都是符号，照样跨过去。
 * 同一段文本只有一种拆法：符号不含 ASCII 字母数字与 <，标签以 < 起头，编号、键帽、复选框里有必需的 ASCII 字母数字；围栏与
 * 符号都能从 ` ~ 起头，围栏只从一串的第一个起算，语言标记起头的那个字母数字只有一条路能拆（见 PAN_CODE_FENCE）。
 */
const PAN_CODE_QUOTE_LINE = `[${PAN_CODE_NEWLINE_CHARS}](?:${PAN_CODE_LINE_SPACE}*>)+`;
const PAN_CODE_GAP_BEFORE = `(?:${PAN_CODE_SPACE}|${PAN_GAP_TAG_SOURCE}|${PAN_CODE_QUOTE_LINE}|\\|)*`;
const PAN_CODE_GAP = `(?:${PAN_CODE_SYMBOL_SOURCE}|${PAN_GAP_TAG_SOURCE}|${PAN_CODE_FENCE}|${PAN_CODE_NUMBERING}|${PAN_CODE_CHECKBOX})*`;
const PAN_CODE_LEAD = `${PAN_CODE_KEYWORD}${PAN_CODE_GAP_BEFORE}[:：=]`;
/**
 * ASCII 的尖括号单独一支（PAN_CODE_ANGLE_SOURCE），按 HTML 标签的写法分开标签与码值：< 后面先是空白的一定不是标签
 * （< ab12 >）；< 后面紧跟码值的，码值后面是 />（可隔空白），或者从 < 起是一个完整的标签（PAN_TAG_SOURCE）、码值后面接
 * 「空白 + 属性名 + =」的是标签（<span class="hint">、<span />、<span @click="x">、<span 数据=提示>：属性名是除空白、引号、
 * < > / = 以外的至多 64 个字符），其余当成码（<ab12>、<ab12/cd34>、实体解出来不闭合的 <ab12、<ab12 id=1，<ab12 下载>）。
 * 裸标签 <strong>、布尔属性 <input disabled> 与码值分不开，当成码；反过来码值后面紧跟赋值属性、又是完整标签的（<ab12 备注=x>）
 * 与带属性的标签分不开，当成标签。
 */
/** 码值：4–8 位字母数字，右边不能再接字母数字（9 位以上不是码） */
const PAN_CODE_VALUE = "[A-Za-z0-9]{4,8}(?![A-Za-z0-9])";
/**
 * 正文里的码值：同 PAN_CODE_VALUE，只是字与字之间可以夹跨过的标签与注释（PAN_GAP_TAG_SOURCE），合计 4–8 个字母数字：富文本把码拆成几段
 * （提取码：<span class="code">ab</span><span class="code">12</span><a href="/download">Download</a>，删标签会粘成 ab12Download）。
 * 最后一个字右边不能再接字母数字，接标签算边界；ab<b>cdefghij 合计 10 个，不是码。
 */
export const PAN_CODE_TEXT_VALUE_SOURCE = `[A-Za-z0-9](?:(?:${PAN_GAP_TAG_SOURCE})*[A-Za-z0-9]){3,7}(?![A-Za-z0-9])`;
/** 标签里的一个赋值属性：空白、属性名（除空白、引号、< > / = 以外的至多 64 个字符）、可选的空白、= */
const PAN_HTML_ATTRIBUTE = `${PAN_CODE_SPACE}+[^${PAN_CODE_SPACE_CHARS}"'<>\\/=]{1,64}${PAN_CODE_SPACE}*=`;
/** 从 < 起是一个完整的标签，4–8 位的标签名后面接赋值属性 */
const PAN_HTML_TAG_WITH_ATTRIBUTE = `(?=${PAN_TAG_ELEMENT})<[A-Za-z0-9]{4,8}${PAN_HTML_ATTRIBUTE}`;
export const PAN_CODE_ANGLE_SOURCE = `(?:<${PAN_CODE_SPACE}${PAN_CODE_GAP}${PAN_CODE_VALUE}|(?!${PAN_HTML_TAG_WITH_ATTRIBUTE})<${PAN_CODE_VALUE}(?!${PAN_CODE_SPACE}*\\/>))`;
/** 标签按属性拆：属性名是除空白、引号、< > / = 以外的至多 64 个字符，取尽 */
const PAN_HTML_NAME = `[^${PAN_CODE_SPACE_CHARS}"'<>\\/=]{1,64}(?![^${PAN_CODE_SPACE_CHARS}"'<>\\/=])`;
/** 属性值：一对引号括起来的至多 64 个字符；或者不以完整引号串开头、除空白与 < > 以外的至多 64 个字符，取尽（placeholder=/value=ab12 整个是值） */
const PAN_HTML_QUOTED_VALUE = `(?:"[^"]{0,64}"|'[^']{0,64}')`;
const PAN_HTML_UNQUOTED_VALUE = `(?!${PAN_HTML_QUOTED_VALUE})[^${PAN_CODE_SPACE_CHARS}<>]{1,64}(?![^${PAN_CODE_SPACE_CHARS}<>])`;
const PAN_HTML_ANY_ATTRIBUTE = `${PAN_HTML_NAME}(?:${PAN_CODE_SPACE}*=${PAN_CODE_SPACE}*(?:${PAN_HTML_QUOTED_VALUE}|${PAN_HTML_UNQUOTED_VALUE}))?`;
/** 标签名、属性之间：空白或 /（引号值后面紧跟下一个属性时为空） */
const PAN_HTML_BETWEEN = `(?:${PAN_CODE_SPACE}|\\/)*`;
/**
 * 码值放在标签的 value 属性里（只读输入框给人复制：提取码：<input type="text" value="ab12" readonly>）：分隔符后面的标签里
 * 有一个 value 属性（名字不分大小写），属性值是 4–8 位字母数字，可带引号。标签按属性拆（标签名取尽，前面至多 32 个属性），
 * value 必须是一个完整的属性名：data-value、引号串或别的属性值里的「value=」（placeholder=/value=ab12）都不算。
 */
const PAN_HTML_VALUE_PREFIX = `<[A-Za-z][^${PAN_CODE_SPACE_CHARS}\\/<>]{0,64}(?![^${PAN_CODE_SPACE_CHARS}\\/<>])(?:${PAN_HTML_BETWEEN}${PAN_HTML_ANY_ATTRIBUTE}){0,32}?${PAN_HTML_BETWEEN}[Vv][Aa][Ll][Uu][Ee]${PAN_CODE_SPACE}*=${PAN_CODE_SPACE}*(?:["']${PAN_CODE_SPACE}*)?`;
export const PAN_CODE_VALUE_ATTRIBUTE_SOURCE = PAN_HTML_VALUE_PREFIX + PAN_CODE_VALUE;
/**
 * value 那份规范化副本删标签时（normalize.tag_value）：从 < 起能按上面 value 那一支匹配上的标签，不删成空串，换成第 1 组的码值、
 * 两边各加一个空格（免得和前后的字母数字粘成更长的一串：<input value="ab12">cd 不成 ab12cd）。只认 value 属性：把裸标签也换成字
 * 会把关键字与分隔符之间的 <span> 变成「提取码 span :」，反而认不出（尖括号里的码值由 keyless 副本认）。
 */
export const PAN_TAG_VALUE_SOURCE = `${PAN_HTML_VALUE_PREFIX}(${PAN_CODE_VALUE})`;
/**
 * keyless 那份规范化副本删标签时（normalize.tag_keyword、normalize.tag_keyword_mask）：标签与注释留着，里面六个关键字用到的字逐个换成 U+FFFD。
 * 不直接删：删字会拼出原文没有的码值与域名（value="访问2024" 成 value="2024"，pan.提baidu.com 成 pan.baidu.com）；U+FFFD 不是字母数字，
 * 后面的规范化步骤也不动它，长度不变，下一遍标签照旧这样拆。
 */
export const PAN_TAG_KEYWORD_SOURCE = `[${PAN_CODE_KEYWORD_CHARS}]`;
export const PAN_TAG_KEYWORD_MASK = "\ufffd";
export const PAN_CODE_DETECT_SOURCE =
  PAN_CODE_LEAD +
  PAN_CODE_GAP +
  `(?:${PAN_CODE_ANGLE_SOURCE}|${PAN_CODE_VALUE_ATTRIBUTE_SOURCE}|${PAN_CODE_TEXT_VALUE_SOURCE})`;
export const PAN_CODE_SOURCE = "(?<![A-Za-z0-9])" + PAN_CODE_DETECT_SOURCE;

/**
 * 规范化副本把夹在两个 ASCII 字母数字之间的「。」（U+3002）换成「.」：中文输入法打出来的 pan。baidu。com。半角的 ｡ 与竖排的 ︒ 经 NFKC 也成「。」；
 * 句末的「。」两边不是字母数字，不动，中文正文的副本不会因为它变样。与夹具的 normalize.dots 逐字相同。
 */
export const PAN_DOTS_SOURCE = "(?<=[A-Za-z0-9])\\u3002(?=[A-Za-z0-9])";
/** 规范化副本先删掉的零宽字符；与夹具的 normalize.strip 逐字相同 */
export const PAN_ZERO_WIDTH_SOURCE = "[\\u200b-\\u200d\\u2060\\ufeff]";
/** 规范化副本解码的 %XX（只解十六进制值在 0x20–0x7E 的）；与夹具的 normalize.percent 逐字相同 */
export const PAN_PERCENT_SOURCE = "%([0-9A-Fa-f]{2})";
/**
 * 规范化副本解开的 HTML 实体（夹具 normalize.entities.pattern）：第 1 组十进制、第 2 组十六进制码点，第 3 组名字。
 * 从左到右扫一遍，解出来的不再解（&amp;#46; 只成 &#46;）。数字引用的分号可有可无（HTML 照样解：pan&#46baidu），
 * 数字取完整的一串（&#0000000046; 也是「.」），去掉前导 0 后十进制超过 PAN_ENTITY_DEC_DIGITS 位、十六进制超过
 * PAN_ENTITY_HEX_DIGITS 位的一定不是合法码点，原样留下，不去把超长的数字转成整数。
 */
export const PAN_ENTITY_SOURCE = "&(?:#([0-9]+);?|#[xX]([0-9A-Fa-f]+);?|([A-Za-z]{2,8});)";
export const PAN_ENTITY_DEC_DIGITS = 7;
export const PAN_ENTITY_HEX_DIGITS = 6;
/** 解开的实体名字（夹具 normalize.entities.names，大小写敏感）。用 Map：&toString; 不能查到原型链上 */
export const PAN_ENTITY_NAMES: ReadonlyMap<string, string> = new Map([
  ["nbsp", "\u00a0"],
  ["amp", "&"],
  ["lt", "<"],
  ["gt", ">"],
  ["quot", '"'],
  ["apos", "'"],
  ["colon", ":"],
  ["sol", "/"],
  ["quest", "?"],
  ["equals", "="],
  ["period", "."],
  ["num", "#"],
  ["percnt", "%"],
]);
/** 规范化副本删掉的 Markdown 与转义标记（[…](…/s/1AbCd\\_Ef)、https:\\/\\/…、**ab12**）；与夹具的 normalize.markup 逐字相同 */
export const PAN_MARKUP_SOURCE = "[\\\\*_`~]";

/** 规范化副本有变化的遍数上限（夹具 limits.probe_passes）：超过就整串替换，深层嵌套的编码不会变成平方级的扫描 */
export const PAN_PROBE_PASSES = 16;

/** 原文上的两条检测：只问有没有，不带 g（带 g 的 test 会记住 lastIndex） */
const PAN_URL_RE = new RegExp(PAN_URL_SOURCE, "i");
const PAN_CODE_RE = new RegExp(PAN_CODE_SOURCE, "u");
/**
 * 规范化副本上的检测（夹具 normalize.detect），只问有没有命中、不带 g。规范化自己会在域名与关键字旁边造出字母数字
 * （①ｐａｎ．ｂａｉｄｕ．ｃｏｍ 成 1pan.baidu.com），带边界就漏了：URL 只查域名清单本身，提取码去掉左边界
 * （右边界与 4–8 位留着：9 位以上不是码）。代价是含全角这类字符的文本里出现网盘域名的子串也整串替换，宁可多清。
 */
const PAN_URL_DETECT = new RegExp(PAN_DOMAINS_SOURCE, "i");
const PAN_CODE_DETECT = new RegExp(PAN_CODE_DETECT_SOURCE, "u");
const DOTS_RE = new RegExp(PAN_DOTS_SOURCE, "g");
const ZERO_WIDTH_RE = new RegExp(PAN_ZERO_WIDTH_SOURCE, "g");
const PERCENT_RE = new RegExp(PAN_PERCENT_SOURCE, "g");
const TAG_RE = new RegExp(PAN_TAG_SOURCE, "gu");
const TAG_VALUE_RE = new RegExp(`^(?:${PAN_TAG_VALUE_SOURCE})`, "u");
const TAG_KEYWORD_RE = new RegExp(PAN_TAG_KEYWORD_SOURCE, "gu");
const ENTITY_RE = new RegExp(PAN_ENTITY_SOURCE, "g");
const MARKUP_RE = new RegExp(PAN_MARKUP_SOURCE, "g");

/**
 * JS 侧预筛：清洗（含规范化兜底）的超集，一个都不含的字符串直接放过，65 MB 的导出文本大多走这一支。
 * 每个命中都要在规范化副本里有「.」（网盘域名里有）或「码/碼」（六个关键字都以它结尾）。副本里的「.」只可能来自原文的「.」、
 * %2E（原文要有 % 或 NFKC 成 % 的 ％ ﹪）、HTML 实体（&#46;、&period;，原文要有 & 或 NFKC 成 & 的 ＆ ﹠），或 NFKC 后带「.」的字符
 * （․ ‥ … ⒈–⒛ ㏂ ㏇ ㏘ ︙ ︰ ﹒ ． 🄀），或 dots 那一步换掉的「。」（原文要有「。」或 NFKC 成它的 ｡ ︒）；「码」「碼」同样可以写成实体（原文要有 &），没有哪个码点 NFKC 成它们。
 * 删标签、删 Markdown 标记只删字符，造不出这些。副本解码超限（整串替换）要连着变十几遍，只有解码（原文要有 & 或 %）与嵌套的
 * 标签（<b<b…>>，一遍删一层；原文要有 < 或 NFKC 成 < 的 ＜ ﹤）做得到，它们也在里面。
 * tests/pick-export-v2.test.ts 逐码点核对这张表没有漏。
 */
export const MAYBE_PAN = new RegExp(
  "[.%&<码碼\\u3002\\uff61\\ufe12\\uff1c\\ufe64\\ufe60\\uff06\\u2024-\\u2026\\u2488-\\u249b\\u33c2\\u33c7\\u33d8\\ufe19\\ufe30\\ufe52\\ufe6a\\uff05\\uff0e]|\\ud83c\\udd00",
);

/**
 * 预筛在库里要多认的非 ASCII 字符（码点区间，十六进制）：规范化副本能从它们身上认出网盘记号，含它们的行都得挑出来。
 * - ％ ﹪（NFKC 成 %）与零宽字符（副本先删掉它们，p 与 an.baidu.com 就拼上了）；
 * - NFKC 后正好是一个「.」的 ․ ﹒ ．（… ‥ 成两三个连着的点，拼不出域名里两侧都是字母数字的那个点，不收）；
 * - NFKC 后含 ASCII 字母或数字的：全角字母数字、①、Ⅱ、℃、㎝、ﬁ、𝐚、🄰 这类，按区块取整（比逐个码点宽，只会多挑不会漏挑；
 *   全角标点 ff1a–ff20 除 ＜ 外不在里面，中文的「：」不会让整行都成候选）；
 * - NFKC 成副本会解开或删掉的 & \\ * _ ` ~ < 的：＆ ﹠ ＼ ﹨ ＊ ﹡ ＿ ︳ ︴ ﹍–﹏ ｀ ` ～ ＜ ﹤。
 */
const PREFILTER_EXTRA =
  "ff05 fe6a 200b-200d 2060 feff 2024 fe52 ff0e ff06 fe60 ff3c fe68 ff0a fe61 ff3f fe33-fe34 fe4d-fe4f ff40 1fef ff5e ff1c fe64 aa b2-b3 b9-ba bc-be 132-133 13f-140 149 17f 1c4-1cc 1f1-1f3 2b0-2b8 2e1-2e3 " +
  "1d2c-1dbf 1e9a 2070-209c 20a8 2100-2189 2460-24ea 2c7c-2c7d 3250-325f 32b1-32cf 3358-33ff a7f2-a7f4 fb00-fb06 ff10-ff19 " +
  "ff21-ff3a ff41-ff5a 107a5 1ccd6-1ccf9 1d400-1d7ff 1f100-1f16c 1f190 1fbf0-1fbf9";

/** "a b-c" → 方括号里的字面字符与区间（PG 在 UTF8 库、JS 带 u 标志时都按码点读） */
function codePointClass(spec: string): string {
  return spec
    .split(" ")
    .map((r) => r.split("-").map((h) => String.fromCodePoint(parseInt(h, 16))).join("-"))
    .join("");
}

/**
 * 网盘预筛：清洗（含规范化兜底）的超集，给 manifest 的 meta.scrub 在库里先挑出【可能】命中的行（PG 的 `~*`），再逐行过
 * toExportRow 精确计数，不用为了计数把 65 MB 整个再读一遍。
 * - 原文里的网盘域名都含其中一个 ASCII 记号（pan. / yun. / yundr / 115. / 115cdn / 123684 / 123865 / 123912 / lanz / uc.cn /
 *   189.cn / pikpak / ctfile / wenshushu / cowtransfer，以及第二十四轮补的各家短域名与别名的记号；pipipan.com、yunpan.cn、kuaipan.cn 这类里有 pan.），六个提取码关键字都含「码」或「碼」（访问、訪問也点名收下）。记号里的点写成 [.。｡︒]：dots 那一步把字母数字之间的「。」
 *   换成「.」（pan。baidu。com），不写进来就得把含句号的中文正文整行挑出来。ASCII 记号只用字母、数字、这个点与 |：PG 的 `~*` 与
 *   JS 的 i 标志在 ASCII 上大小写不敏感的意思相同；
 * - 规范化兜底能认出来而原文没有记号的写法，原文里一定有下面之一：% 或 &（解出任意字符）、零宽字符、\\ * _ ` ~ <
 *   （副本删掉它们，两边的字母就拼上了：pan\\.baidu、pan<b>.</b>baidu），或 PREFILTER_EXTRA 里 NFKC 成字母、数字、点的字符。
 *   最后一个字符类把它们都收下。
 * tests/pick-export-v2.test.ts 按 PAN_URL_SOURCE 的每个域名、夹具正例与逐码点核对钉住「超集」；库内测试钉住 meta.scrub 等于逐页命中之和。
 */
export const PAN_PREFILTER =
  "pan[.。｡︒]|yun[.。｡︒]|yundr|115[.。｡︒]|115cdn|123684|123865|123912|lanz|uc[.。｡︒]cn|189[.。｡︒]cn|pikpak|ctfile|wenshushu|cowtransfer|anxia|123952|400gb|545c|089u|474b|t00y|306t|47ks|4765[.。｡︒]com|77tj|feijix|wss[.。｡︒]cc|t[.。｡︒]work|quqi|musetransfer|tmp[.。｡︒]link|airportal|easychuan|filez|lenovo|disk[.。｡︒]weibo|vdisk|dbank|wostore|码|碼|访问|訪問|" +
  `[%&\\\\*_\`~<${codePointClass(PREFILTER_EXTRA)}]`;

function decodeEntity(whole: string, dec?: string, hex?: string, name?: string): string {
  if (name !== undefined) return PAN_ENTITY_NAMES.get(name) ?? whole;
  const digits = (dec ?? hex ?? "").replace(/^0+/, "");
  if (digits.length > (dec !== undefined ? PAN_ENTITY_DEC_DIGITS : PAN_ENTITY_HEX_DIGITS)) return whole;
  const cp = digits ? parseInt(digits, dec !== undefined ? 10 : 16) : 0;
  return cp >= 1 && cp <= 0x10ffff && (cp < 0xd800 || cp > 0xdfff) ? String.fromCodePoint(cp) : whole;
}

function decodePercent(whole: string, hex: string): string {
  const code = parseInt(hex, 16);
  return code >= 0x20 && code <= 0x7e ? String.fromCharCode(code) : whole;
}

/**
 * 规范化副本一遍里的七步，按夹具 normalize.order：解 HTML 实体 → NFKC → 字母数字之间的「。」换成「.」 → 删零宽字符 →
 * 删 Markdown 与转义标记 → 删 HTML 标签 → 解一层 %XX。
 * 删标签会连着标签里的字一起删掉，放在删标记之后：提取码：\\<ab12\\> 先删掉 \\ 成 <ab12>，那一步就查到了。
 */
const PROBE_STEPS: readonly (readonly [name: string, apply: (text: string) => string])[] = [
  ["entities", (t) => t.replace(ENTITY_RE, decodeEntity)],
  ["form", (t) => t.normalize("NFKC")],
  ["dots", (t) => t.replace(DOTS_RE, ".")],
  ["strip", (t) => t.replace(ZERO_WIDTH_RE, "")],
  ["markup", (t) => t.replace(MARKUP_RE, "")],
  ["tags", (t) => t.replace(TAG_RE, "")],
  ["percent", (t) => t.replace(PERCENT_RE, decodePercent)],
];
/** 夹具 normalize.order */
export const PAN_PROBE_ORDER: readonly string[] = PROBE_STEPS.map(([name]) => name);
/**
 * 夹具 normalize.copies：规范化副本做四份，只有删标签那一步不同（见 scrubPanText）。
 * - delete：删成空串（pan<b>.</b>baidu.com 隔着标签拼起来）；
 * - keep：不删（删标签会连着属性一起删掉，<a href="https://pan%2Ebaidu%2Ecom/…"> 里的链接在解码之前就没了）；
 * - value：从 < 起能按 PAN_TAG_VALUE_SOURCE 匹配上的标签换成「空格 + 码值 + 空格」，其余删成空串（容器的引号串里有关键字用到的字、
 *   里面是只读输入框：提取码：<span title="复制提取码"><input value="ab12"></span>）。它不能代替 delete：插进来的码值会把
 *   pan<span value="1234">.</span>baidu.com 这类隔着标签拼起来的写法隔开；
 * - keyless：标签与注释留着，里面关键字用到的字换成 U+FFFD（PAN_TAG_KEYWORD_SOURCE、PAN_TAG_KEYWORD_MASK）。提取码正则为了线性跨不过
 *   引号串里有这些字的标签（见 PAN_GAP_TAG_SOURCE），换掉以后照原文一样跨过去，码值的左右边界也不变：提示容器包着的正文码值紧挨着别的控件
 *   （…>ab12</span><a>Download</a>，删标签会粘成 ab12Download）、尖括号里的码值（…>&lt;ab12&gt;</span>，删标签会连码值一起删掉）。
 */
export const PAN_PROBE_COPIES = ["delete", "keep", "value", "keyless"] as const;
export type PanProbeCopy = (typeof PAN_PROBE_COPIES)[number];
function keepTagValue(tag: string): string {
  const value = TAG_VALUE_RE.exec(tag)?.[1];
  return value === undefined ? "" : ` ${value} `;
}
function maskTagKeywords(tag: string): string {
  return tag.replace(TAG_KEYWORD_RE, PAN_TAG_KEYWORD_MASK);
}
const COPY_STEPS: Readonly<Record<PanProbeCopy, typeof PROBE_STEPS>> = {
  delete: PROBE_STEPS,
  keep: PROBE_STEPS.filter(([name]) => name !== "tags"),
  value: PROBE_STEPS.map(([name, apply]) => [name, name === "tags" ? (t: string) => t.replace(TAG_RE, keepTagValue) : apply] as const),
  keyless: PROBE_STEPS.map(([name, apply]) => [name, name === "tags" ? (t: string) => t.replace(TAG_RE, maskTagKeywords) : apply] as const),
};

/** 规范化副本的一遍；seen 收到每一步做完、文本有变化时的结果与那一步的名字 */
function probePass(text: string, steps: typeof PROBE_STEPS, seen?: (step: string, name: string) => void): string {
  return steps.reduce((t, [name, apply]) => {
    const next = apply(t);
    if (seen && next !== t) seen(next, name);
    return next;
  }, text);
}

/**
 * 规范化副本（只用于检测、不输出）：probePass 整遍重复到不再变化，一步解出来的东西下一遍接着处理
 * （&#8203; 解成零宽字符再删掉，&lt;b&gt; 解成标签再删掉，%3Cb%3E、&amp;#46;、%252E 这类多层编码逐层解开）。
 * 有变化的遍数超过 PAN_PROBE_PASSES 就返回 null，调用方整串替换。seen 见 probePass（每一步的中间结果）。
 * copy 是四份副本之一（PAN_PROBE_COPIES），默认 delete。
 */
export function panProbeText(text: string, seen?: (step: string, name: string) => void, copy: PanProbeCopy = "delete"): string | null {
  const steps = COPY_STEPS[copy];
  let out = text;
  for (let pass = 1; ; pass += 1) {
    const next = probePass(out, steps, seen);
    if (next === out) return out;
    if (pass > PAN_PROBE_PASSES) return null;
    out = next;
  }
}

/**
 * 网盘清洗，流程与共享夹具的 about 相同（工作台的 Python 闸门照做）：字符串里只要认出任何网盘片段，就整个换成一个
 * 占位符、hits 记 1；认不出就原样返回、hits 0。不做局部替换：局部替换只盖住片段的一部分时，剩下的尾巴（分享路径、
 * ?pwd=、码值）没了域名与关键字，再也认不出来（2026-09-24 验收十三轮里，每一轮的漏清都是这一类）。宁可多清。
 * 认出 = 下面任一条：
 * 1. 原文上 PAN_URL_RE（网盘域名，带左右边界）或 PAN_CODE_RE（关键字 + 分隔符 + 码值，带左边界）命中；
 * 2. 规范化副本（panProbeText）的每一步做完、文本有变化并且与原文不同时，不带边界的检测（PAN_URL_DETECT / PAN_CODE_DETECT）
 *    命中：全角、URL 编码、零宽字符、HTML 实体与标签、Markdown 转义、中文句号绕过了原文上的正则；
 * 3. 规范化副本解码超限（深层嵌套的编码）。
 * 副本做四份（PAN_PROBE_COPIES）：delete 删标签、keep 不删、value 删标签时输入框换成码值、keyless 把标签里关键字用到的字换成 U+FFFD。
 * delete 的删标签一次都没改动文本时，四份逐步相同，后三份不用再做。
 * 占位符里没有 MAYBE_PAN 认的字符，清过的文本再清一次原样、hits 0。
 */
const LATER_COPIES = PAN_PROBE_COPIES.filter((copy) => copy !== "delete");
export function scrubPanText(text: string): { text: string; hits: number } {
  if (!MAYBE_PAN.test(text)) return { text, hits: 0 };
  const whole = { text: PAN_SCRUB_REPLACEMENT, hits: 1 };
  if (PAN_URL_RE.test(text) || PAN_CODE_RE.test(text)) return whole;
  const first = probeDetects(text, "delete");
  if (first.detected) return whole;
  if (first.tagsRemoved && LATER_COPIES.some((copy) => probeDetects(text, copy).detected)) return whole;
  return { text, hits: 0 };
}

/** 一份规范化副本上的检测：命中或超限都算 detected；tagsRemoved 记下删标签那一步有没有改动过文本 */
function probeDetects(text: string, copy: PanProbeCopy): { detected: boolean; tagsRemoved: boolean } {
  let detected = false;
  let tagsRemoved = false;
  const probe = panProbeText(
    text,
    (step, name) => {
      if (name === "tags") tagsRemoved = true;
      if (!detected && step !== text && (PAN_URL_DETECT.test(step) || PAN_CODE_DETECT.test(step))) detected = true;
    },
    copy,
  );
  return { detected: probe === null || detected, tagsRemoved };
}

/** 各字段路径被清洗的次数（资源名.字段，数组下标一律写成 [*]），进 manifest 的 meta.scrub */
export type ScrubCounts = Readonly<Record<string, number>>;

export function addScrubCounts(a: ScrubCounts, b: ScrubCounts): ScrubCounts {
  return Object.entries(b).reduce<Record<string, number>>((acc, [k, n]) => ({ ...acc, [k]: (acc[k] ?? 0) + n }), { ...a });
}

/**
 * 递归清洗一个值里的全部文本叶子：字符串、数组元素、对象里每个字符串叶子（含 jsonb 的 payload.h[*][2]、posts[].url、
 * who[] 这类）。value 是一行：它顶层的键按 SCRUB_EXEMPT_KEYS 的名字豁免，嵌套的键只按 SCRUB_EXEMPT_PATHS 的路径豁免，
 * 豁免的整棵子树原样保留。返回新值，不改入参；path 是命中计数的前缀（通常是资源名）。
 */
export function scrubAllText<T>(value: T, path = ""): { value: T; hits: ScrubCounts } {
  const hits: Record<string, number> = {};
  /** at：命中计数的键（带前缀）；rel：相对这一行根的路径，判嵌套豁免用（根是空串） */
  const walk = (v: unknown, at: string, rel: string): unknown => {
    if (typeof v === "string") {
      const r = scrubPanText(v);
      if (r.hits > 0) hits[at] = (hits[at] ?? 0) + r.hits;
      return r.text;
    }
    if (Array.isArray(v)) return v.map((x) => walk(x, `${at}[*]`, `${rel}[*]`));
    if (!isPlainObject(v)) return v;
    return Object.fromEntries(
      Object.entries(v).map(([k, x]) => {
        const key = rel ? `${rel}.${k}` : k;
        const exempt = rel ? SCRUB_EXEMPT_PATHS.has(key) : SCRUB_EXEMPT_KEYS.has(k);
        return [k, exempt ? copyJson(x) : walk(x, at ? `${at}.${k}` : k, key)];
      }),
    );
  };
  return { value: walk(value, path, "") as T, hits };
}

/* ---------------------------------------------------------------- 行映射 */

export type KeyValue = string | number;
export type CursorKey = readonly KeyValue[];

export interface ExportRow {
  /** 只含白名单列、已归一、已清洗的一行 */
  row: Json;
  /** 游标主键，取自清洗【之前】的值：主键列本来就豁免清洗，这里再多一层保险，游标不受清洗影响 */
  key: CursorKey;
  hits: ScrubCounts;
}

function toInt(v: unknown): number | null {
  if (typeof v === "number") return Number.isSafeInteger(v) ? v : null;
  if (typeof v === "bigint") return v >= BigInt(Number.MIN_SAFE_INTEGER) && v <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(v) : null;
  if (typeof v === "string" && /^-?[0-9]{1,16}$/.test(v)) return Number.isSafeInteger(Number(v)) ? Number(v) : null;
  return null;
}

function toFloat(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && /^-?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$/.test(v)) return Number.isFinite(Number(v)) ? Number(v) : null;
  return null;
}

function toBool(v: unknown): boolean | null {
  if (typeof v === "boolean") return v;
  if (v === "t" || v === "true") return true;
  if (v === "f" || v === "false") return false;
  return null;
}

/** timestamptz 归一成 ISO：PG 文本形态的 `+00` 不是合法 ISO 偏移，补成 `+00:00`（同 observe/queries 的 toTimestamp） */
function toIsoTimestamp(v: unknown): string | null {
  const d = v instanceof Date ? v : typeof v === "string" ? new Date(v.replace(" ", "T").replace(/([+-][0-9]{2})$/, "$1:00")) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toISOString() : null;
}

function isJsonValue(v: unknown): boolean {
  if (v === null || typeof v === "string" || typeof v === "boolean") return true;
  if (typeof v === "number") return Number.isFinite(v);
  if (Array.isArray(v)) return v.every(isJsonValue);
  return isPlainObject(v) && Object.values(v).every(isJsonValue);
}

/** 第一层按白名单挑键；值往下递归拷贝时，任何层级上像禁止字段的键都丢掉 */
function pickKeys(v: Json, keys: readonly string[]): Json {
  return Object.fromEntries(keys.filter((k) => v[k] !== undefined).map((k) => [k, copyJsonWithoutForbidden(v[k])]));
}

function whitelistJson(where: string, v: unknown): unknown {
  const rule = JSON_KEYS[where];
  if (!rule || !isJsonValue(v)) return undefined;
  if (rule.kind === "object") return isPlainObject(v) ? pickKeys(v, rule.keys) : undefined;
  return Array.isArray(v) && v.every(isPlainObject) ? v.map((x) => pickKeys(x as Json, rule.keys)) : undefined;
}

function normalizeValue(resource: RowResource, col: ColumnSpec, v: unknown): unknown {
  const where = `${resource}.${col.name}`;
  if (v === null) {
    if (col.nullable) return null;
    throw new Error(`export-v2：${where} 不可空`);
  }
  const out = (() => {
    switch (col.type) {
      case "text":
      case "day":
        return typeof v === "string" ? v : undefined;
      case "int":
        return toInt(v) ?? undefined;
      case "float":
        return toFloat(v) ?? undefined;
      case "bool":
        return toBool(v) ?? undefined;
      case "ts":
        return toIsoTimestamp(v) ?? undefined;
      case "text[]":
        return Array.isArray(v) && v.every((x) => typeof x === "string") ? [...v] : undefined;
      case "json":
        return whitelistJson(where, v);
    }
  })();
  /* 错误里只写资源与列名，不写值：route 把它当 read_failed，日志里也不会带出字段内容 */
  if (out === undefined) throw new Error(`export-v2：${where} 的值不是 ${col.type}`);
  return out;
}

/**
 * 查询结果的一行 → 输出行：只挑白名单列（禁止列与未知列一律丢掉），按列类型归一，再整行清洗。
 * 白名单列缺失或为 undefined 直接抛错：那是 export-v2.ts 的 SQL 漏了列，不能静默补 null。
 */
export function toExportRow(resource: RowResource, raw: Readonly<Json>): ExportRow {
  const spec = RESOURCE_SPECS[resource];
  const picked = Object.fromEntries(
    spec.columns.map((c) => {
      if (raw[c.name] === undefined) throw new Error(`export-v2：${resource} 缺列 ${c.name}`);
      return [c.name, normalizeValue(resource, c, raw[c.name])];
    }),
  );
  const key = spec.key.map((k) => picked[k] as KeyValue);
  const { value, hits } = scrubAllText(picked, resource);
  return { row: value, key, hits };
}

/* ---------------------------------------------------------------- manifest 与 meta 的键白名单 */

/**
 * 形状：scalar = 字符串 / 有限数 / 布尔 / null（Date 转 ISO），塞对象或数组就抛错；
 * object = 只挑 fields 里的键（partial 时缺了就不输出，否则缺了抛错）；array = 逐个按 of；
 * record = 键在 keys 里的才输出（keys 为 null 时收任意键，但像禁止字段的键照样丢掉）。
 */
export type Shape =
  | { readonly kind: "scalar" }
  | { readonly kind: "object"; readonly fields: Readonly<Record<string, Shape>>; readonly partial: boolean }
  | { readonly kind: "array"; readonly of: Shape }
  | { readonly kind: "record"; readonly of: Shape; readonly keys: readonly string[] | null };

const SCALAR: Shape = { kind: "scalar" };
const obj = (fields: Record<string, Shape>, partial = false): Shape => ({ kind: "object", fields, partial });
const flat = (names: readonly string[], partial = false): Shape => obj(Object.fromEntries(names.map((n) => [n, SCALAR])), partial);
const arr = (of: Shape): Shape => ({ kind: "array", of });
const rec = (of: Shape, keys: readonly string[] | null = null): Shape => ({ kind: "record", of, keys });

function pickScalar(v: unknown, fail: () => never): unknown {
  if (v === null || typeof v === "string" || typeof v === "boolean") return v;
  if (typeof v === "number") return Number.isFinite(v) ? v : fail();
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? fail() : v.toISOString();
  return fail();
}

/** 按形状挑键并复制；形状不符抛错，错误里只有路径、没有值 */
export function pickShape(shape: Shape, value: unknown, path = "$"): unknown {
  const fail = (at = path): never => {
    throw new Error(`export-v2：${at} 的形状不符`);
  };
  switch (shape.kind) {
    case "scalar":
      return pickScalar(value, fail);
    case "array":
      return Array.isArray(value) ? value.map((x, i) => pickShape(shape.of, x, `${path}[${i}]`)) : fail();
    case "object": {
      if (!isPlainObject(value)) return fail();
      const missing = Object.keys(shape.fields).find((k) => value[k] === undefined);
      if (missing !== undefined && !shape.partial) return fail(`${path}.${missing}`);
      const present = Object.entries(shape.fields).filter(([k]) => value[k] !== undefined);
      return Object.fromEntries(present.map(([k, s]) => [k, pickShape(s, value[k], `${path}.${k}`)]));
    }
    case "record": {
      if (!isPlainObject(value)) return fail();
      const keep = Object.keys(value).filter((k) => (shape.keys ? shape.keys.includes(k) : !FORBIDDEN_NAME.test(k)));
      return Object.fromEntries(keep.map((k) => [k, pickShape(shape.of, value[k], `${path}.${k}`)]));
    }
  }
}

/** 形状里出现的全部键名（object 的字段名与 record 的固定键），白名单自检用 */
export function shapeKeyNames(shape: Shape): string[] {
  switch (shape.kind) {
    case "scalar":
      return [];
    case "array":
      return shapeKeyNames(shape.of);
    case "object":
      return Object.entries(shape.fields).flatMap(([k, s]) => [k, ...shapeKeyNames(s)]);
    case "record":
      return [...(shape.keys ?? []), ...shapeKeyNames(shape.of)];
  }
}

/** meta.sources 收哪些来源：observe_sources 的全部取值（ReelShort 的四个，加 P1-5 的剧单导入），与 ObserveSource 同一份 */
export const EXPORT_SOURCES = OBSERVE_SOURCES;
/** meta.sources.*.details 的键：SourceDetails 的全部键（只有口径与计数；ratio 是分成比例，规则表与术语表里本来就写着 50%） */
export const SOURCE_DETAIL_KEYS = [
  "startDate", "endDate", "timezone", "dataState", "rows", "expectedRows", "unresolvedPages", "unmatchedQueries",
  "pageRows", "queryRows", "truncated", "partial", "scope", "ratio", "billPeriod", "termsFetchedAt",
] as const;

const FACETS = obj({
  platforms: rec(SCALAR, PLATFORMS),
  langs: arr(flat(["lang", "n"])),
  bases: rec(SCALAR, BASES),
  posted: flat(["pool", "yes", "no"]),
});

/** control.ledger 只有 {rows, orders}：rows 是有订单的原始账单行数（= rsCounts.ledger = sum(source_rows)），不用 loadBillTotals */
const LEDGER = flat(["rows", "orders"]);

/** meta.rules：规则常量整份导出（4.5），镜像的规则跟着版本走；它的摘要进 fingerprint（4.3） */
export const RULES_SHAPE = obj({
  platformRules: rec(flat(["key", "name", "doc", "updated", "back", "report", "yt", "ytNote", "tag", "unban", "material", "signals"]), PLATFORMS),
  inUse: arr(SCALAR),
  basisLabels: rec(SCALAR, BASES),
  basisDateLabels: rec(SCALAR, BASES),
  rsRankLabels: rec(SCALAR, RS_RANKS),
  youtubeLabels: rec(SCALAR, ["ok", "only", "warn", "no"]),
  glossary: arr(obj({ g: SCALAR, d: SCALAR, items: arr(flat(["t", "a", "ask", "d", "h"], true)) })),
  ruleHints: rec(SCALAR),
  langLoc: rec(SCALAR),
  postedPoolUrl: SCALAR,
  sortLabels: rec(SCALAR, RS_SORTS),
});

export const META_SHAPE = obj({
  freshness: flat(["importedAt", "rows", "withSignal", "signals", "posted", "rsCanonical", "rsCandidates", "rsSyncedAt"]),
  rsCounts: flat(["all", "cand", "growthD1", "growthD7", "growthDp1", "growthDp7", "pc", "clk", "gsc", "bill", "ledger"]),
  growthBaseline: rec(flat(["baselineDay", "baselineSnapshot", "earliestVerifiedOn"]), ["1", "7"]),
  sources: rec(
    obj({ source: SCALAR, status: SCALAR, attemptedAt: SCALAR, completedAt: SCALAR, details: flat(SOURCE_DETAIL_KEYS, true) }),
    EXPORT_SOURCES,
  ),
  rules: RULES_SHAPE,
  control: obj({
    facetsPick: FACETS,
    facetsAll: FACETS,
    rankCounts: rec(SCALAR, RANKS),
    postedStats: flat(["total", "pubCount", "postsSum", "viewsSum", "metricAt", "importedAt", "accountCount"]),
    postedStates: flat(["pub", "sched", "none", "nomatch"]),
    ledger: LEDGER,
  }),
  scrub: rec(SCALAR),
  warnings: arr(flat(["code", "source", "status", "attemptedAt"])),
});

/** manifest 这一「行」（4.5）：counts 是每个资源在 as_of 的精确行数，按天的曲线资源另记在 snapshotDays */
export const MANIFEST_SHAPE = obj({
  version: SCALAR,
  asOf: SCALAR,
  fingerprint: SCALAR,
  sourceRevision: SCALAR,
  counts: rec(SCALAR, ROW_RESOURCES.filter((r) => r !== "rs_series_day")),
  latestSnapshot: SCALAR,
  snapshotDays: arr(flat(["day", "rows"])),
  meta: META_SHAPE,
});

export function pickManifest(value: unknown): Json {
  return pickShape(MANIFEST_SHAPE, value) as Json;
}

/**
 * manifest 输出前的最后一步（loadExportManifest 只经这里）：先按键白名单挑（pickManifest），再把 meta 里的文本过同一个
 * scrubAllText（同一份豁免规则）。语种、来源 details 的说明、告警这类值来自库里的数据或 RealShort 函数的返回，同样可能带网盘信息。
 * 不清洗的：meta.scrub（计数本身，值是整数、键是字段路径），以及 meta 之外的 fingerprint、版本号、asOf、sourceRevision、
 * counts、latestSnapshot、snapshotDays（标识、日期与计数）。键的顺序与 pickManifest 相同。
 * hits 是 meta 文本的命中（键以 manifest.meta. 开头），只随这一页返回，不进 meta.scrub（那里数的是行资源）。
 */
export function finalizeManifest(value: unknown): { manifest: Json; hits: ScrubCounts } {
  const picked = pickManifest(value);
  const meta = picked.meta as Json;
  const { scrub, ...text } = meta;
  const cleaned = scrubAllText(text, "manifest.meta");
  const out = Object.fromEntries(Object.keys(meta).map((k) => [k, k === "scrub" ? scrub : cleaned.value[k]]));
  return { manifest: { ...picked, meta: out }, hits: cleaned.hits };
}

export function mapLedger(value: unknown): { rows: number; orders: number } {
  return pickShape(LEDGER, value, "$.meta.control.ledger") as { rows: number; orders: number };
}

export function buildRulesMeta(): Json {
  const rules = {
    platformRules: PLATFORM_RULES,
    inUse: IN_USE,
    basisLabels: BASIS_LABELS,
    basisDateLabels: BASIS_DATE_LABEL,
    rsRankLabels: RS_RANK_LABELS,
    youtubeLabels: YOUTUBE_LABEL,
    glossary: GLOSSARY,
    ruleHints: RULE_HINTS,
    langLoc: LANG_LOC,
    postedPoolUrl: POSTED_POOL_URL,
    sortLabels: SORT_LABELS,
  };
  return pickShape(RULES_SHAPE, rules, "$.meta.rules") as Json;
}

/* ---------------------------------------------------------------- fingerprint */

/** 规范化 JSON：对象键按码元序排好、没有空白；undefined 的键丢掉，Date 转 ISO，非有限数与其它类型抛错 */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("export-v2：canonicalJson 不收非有限数");
    return JSON.stringify(value);
  }
  if (value instanceof Date) return JSON.stringify(value.toISOString());
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (!isPlainObject(value)) throw new Error("export-v2：canonicalJson 不收这种值");
  const keys = Object.keys(value).filter((k) => value[k] !== undefined).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(",")}}`;
}

function sha256Hex(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

/** meta.rules 的摘要：RealShort 发版改了规则，fingerprint 随之改变，导出途中发版就会 409（4.3） */
export function rulesDigest(rules: Json = buildRulesMeta()): string {
  return sha256Hex(canonicalJson(rules));
}

/**
 * fingerprint = sha256(规范化 JSON)：库内各表的聚合（4.3 的表，由 export-v2.ts 读）、构建 SHA（本地为 null）、规则摘要，
 * 外加版本号（形状升版本时旧 fp 自然作废）。每页先读数据、后算它，再与请求的 fp 比较。
 */
export function exportFingerprint(parts: { aggregates: unknown; buildSha: string | null; rulesDigest: string }): string {
  return sha256Hex(canonicalJson({ version: EXPORT_VERSION, aggregates: parts.aggregates, buildSha: parts.buildSha, rulesDigest: parts.rulesDigest }));
}
