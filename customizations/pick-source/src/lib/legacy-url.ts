/**
 * 承接域名前任 dramashortstv.com 的旧 URL。
 *
 * 这个域名之前跑着另一套站，Google 已收录 39,562 条剧目 URL，
 * GSC 实测近 4 天 7,050 次点击、平均排名 3.8。域名切过来以后这些 URL
 * 会直接打到我们身上——不接就是 39,562 个 404，等于把已有排名全部退回。
 *
 * 旧站的 URL 形状（实测自它的 drama-detail-sitemap.xml 与线上页面）：
 *
 *   /{loc}/detail/{id}/{slug}       剧目页；英文【无 loc 前缀】
 *   /{loc}/video-play/{id}/{slug}   播放页；它的 canonical 指回 detail，
 *                                   URL 里【不带集号】，分集是客户端状态
 *
 * `{id}` 是它自己的自增主键（7870、38000…），与 CPS 的 book_id 无关，
 * 我们【无法】用它反查，所以匹配一律走 `{slug}`——它由标题派生，
 * 而我们和它拿的是同一份 CPS 标题。实测 39,562 条里 75.8% 能命中我方某一行。
 */

/**
 * 旧站的 locale 代码 → 我方 locale。
 *
 * 只列不一致的四个：`in` 是 Java/旧 ISO 的印尼语代码，`fil` 与 `tl` 是
 * 菲律宾语的两种写法，中文它按地区分 zh-TW / zh-CN 而我们按字形分 zh-hant / zh。
 * 其余 17 个（th ja es pt fr ko de tr ar ro it pl ru vi bg cs hi）两边相同。
 */
/**
 * 用 Map 而不是对象字面量：对象会继承 Object.prototype，
 * 于是 `ALIASES["__proto__"]` / `["constructor"]` 会返回原型上的东西，
 * 让一个声明返回 string 的函数吐出一个对象。段名直接来自 URL，
 * 爬虫拼这种路径是常态（审计查出的类型契约缺陷）。
 */
const LOCALE_ALIASES = new Map<string, string>([
  ["in", "id"],
  ["zh-tw", "zh-hant"],
  ["zh-cn", "zh"],
  ["fil", "tl"],
]);

/**
 * 把旧站的 locale 段映射成我方 locale。
 *
 * 别名表里有就换掉，没有就原样小写返回——旧站 22 个语种里有 18 个和我方一致，
 * 原样返回正是这 18 个的正确行为。**这里不做合法性校验**：
 * 调用方 `redirectLegacy()` 会用 `isSupportedLocale()` 把认不出来的挡掉，
 * 校验放两处会漂移。
 *
 * 大小写不敏感：sitemap 里写的是 `zh-TW`，但外链和爬虫会出现 `zh-tw`。
 */
export function mapLegacyLocale(segment: string): string {
  const lower = segment.toLowerCase();
  return LOCALE_ALIASES.get(lower) ?? lower;
}

/**
 * 复刻旧站的标题 → slug 算法，【仅用于匹配它的旧 URL】。
 *
 * 与 `lib/slug.ts` 的差别只有一处，但很关键：它把 Unicode 组合记号（\p{M}）
 * 一并当作分隔符，于是泰语 "แม่บ้านจำเป็นของคุณ" 被切成
 * "แม-บ-านจำเป-นของค"、印地语与阿拉伯语同样成串断裂。
 * 这是它的缺陷，我方 slug 不复刻（见 slug.ts 注释），
 * 但要命中它已被收录的 URL，这里必须【一模一样地错】。
 *
 * 另外它不做长度截断（实测最长 85 码点），所以这里也不能截。
 */
export function legacyTitleKey(title: string): string {
  return title
    .toLowerCase()
    .normalize("NFC")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * 把旧 URL 的 slug 段归一成查表用的 key。
 *
 * 旧 slug 本身就是上面那套算法的产物，理论上原样即可，但外链会带上
 * 大小写变体、尾部斜杠、重复连字符，所以再过一遍同样的归一化，
 * 保证「同一部剧的各种写法」都落到同一个 key。
 */
export function legacySlugKey(slug: string): string {
  return legacyTitleKey(decodeSlugSegment(slug));
}

/** 路由参数在 Next 里已解码过一次，但外链可能是双重编码，这里容错解一次 */
function decodeSlugSegment(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    // 非法百分号序列（爬虫经常这样）——原样用，交给归一化去掉怪字符
    return raw;
  }
}
