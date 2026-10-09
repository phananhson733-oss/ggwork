/**
 * 剧目 URL slug。
 *
 * 【2026-09-01 改】原来的实现把标题折成 ASCII，非拉丁语种一律退化成 `d-<id>`：
 * 实测全库 30,824 部里有 12,961 部（42%）是这种不可读短码，而泰/日/韩/俄/保/阿/繁中
 * 恰恰是片库的大头。更要命的是接管域名后的实测数据——GSC 上近乎全部点击都落在
 * 一条西里尔文 URL 上（/bg/video-play/…/великият-и-могъщ-джин），
 * 也就是说【非拉丁标题正是流量所在】，把它们抹成 d-691439b6… 等于自断关键词。
 *
 * 现策略：保留原生文字。URL 里出现非 ASCII 会被浏览器百分号编码，这是 RFC 3987
 * 明确允许的，Google 也按解码后的文本理解——参照站（同域名前任）就是这么做的。
 *
 * 与参照站的差别有一处【是我们更对】：它把组合记号（Unicode Mark）一并删掉，
 * 于是泰语 "แม่บ้านจำเป็นของคุณ" 变成 "แม-บ-านจำเป-นของค"、印地语同样成串断裂。
 * 我们保留 \p{M}，泰/印地/阿拉伯/越南语的 slug 才是能读的词。
 * 复刻它那套缺陷的算法只存在于 `lib/legacy-url.ts`，且【仅用于匹配它的旧 URL】，
 * 不得用来生成我方 slug。
 */

/** 单个 slug 里保留的标题字符数上限（按码点算，不是 UTF-16 单元） */
const MAX_TITLE_CODEPOINTS = 60;

/** Unicode 组合记号。单独提出来是因为它没有 g 标志，可以安全地重复 test */
const MARK = /\p{M}/u;

/**
 * 标题 → slug 主体。
 *
 * 允许 \p{L}（字母）\p{N}（数字）\p{M}（组合记号），其余一律折成单个连字符。
 * 这个白名单顺带挡掉了所有会破坏路由的字符：`/` `?` `#` `%` `.` 都不在其中，
 * 所以 slug 永远只占一个路径段，不需要额外转义。
 */
function titleToSlug(title: string): string {
  const normalized = title
    // 先小写：土耳其语 İ 小写后是 i + U+0307，U+0307 属于 \p{M} 会被保留，
    // 不会像参照站那样断成 "i-stanbul"
    .toLowerCase()
    // 撇号直接删而不是折成连字符，"don't" → "dont" 比 "don-t" 更接近搜索词。
    // 【必须在 NFC 之前删】：撇号夹在基字与组合记号之间时会挡住合成，
    // 先 NFC 再删撇号会留下一个本可合成的分解序列，产出非 NFC 的 slug，
    // 而匹配端按 NFC 比较，就永远对不上（审计查出的 P3）
    .replace(/['’‘`]/g, "")
    .normalize("NFC")
    .replace(/[^\p{L}\p{N}\p{M}]+/gu, "-")
    .replace(/^-+|-+$/g, "");

  // 按码点切，直接 slice 会把星形平面字符（部分罕用汉字、emoji 残留）劈成孤立代理项，
  // 那会产出无法编码的 URL
  const chars = [...normalized];
  let end = Math.min(chars.length, MAX_TITLE_CODEPOINTS);

  /**
   * 切点落在字素内部时回退到上一个字素边界。
   *
   * 取前缀只可能丢掉尾部的组合记号，不会产生"没有基字的孤立记号"。
   * 但丢掉记号本身就是错的：泰语 "ก้" 被切成 "ก"、阿拉伯语丢掉元音符号，
   * 都会让末字变成另一个字甚至乱码——那正是参照站的缺陷，我们保留 \p{M}
   * 就是为了避免它。所以只要发现切点【后面紧跟着记号】，就说明切在字素中间，
   * 把这个残缺字素整个去掉。
   *
   * 【不要】简化成"去掉末尾所有 \p{M}"：那会把 "สวัสดี" 这种完整字素的
   * 正常声调/元音符号也削掉，等于亲手把参照站的 bug 抄回来。
   */
  if (end < chars.length && MARK.test(chars[end])) {
    while (end > 0 && MARK.test(chars[end - 1])) end--;
    if (end > 0) end--; // 连同它的基字一起去掉
  }

  return chars.slice(0, end).join("").replace(/-+$/g, "");
}

/**
 * 用【完整 book_id】做后缀，不用截断短码。
 *
 * 曾经取末 6 位，实测在 16,462 部片库上就已经出现 6 组 (locale, 末6位) 碰撞，
 * 直接撞爆 dramas_locale_slug_idx 唯一索引让整轮同步失败。
 * 生日碰撞随片库增长只会更频繁，而唯一索引是硬约束——
 * 概率性方案配硬约束就是定时炸弹。
 *
 * book_id 本身全局唯一，因此完整带上即可【从构造上保证】slug 唯一，无需任何碰撞处理。
 * 代价只是 URL 长了 18 个字符，对排名无实质影响。
 */
function idCode(bookId: string): string {
  const cleaned = bookId.replace(/[^a-zA-Z0-9]/g, "").toLowerCase();
  // 兜底串必须【至少 8 位】，否则 extractShortCode 的 /-([a-z0-9]{8,})$/ 认不出来，
  // slugMatchesBook 会把 buildSlug 自己生成的规范 slug 判为不匹配（审计查出的自洽性缺陷）。
  // 实际 book_id 是 24 位十六进制，走不到这里，但一个自相矛盾的函数对不该留着。
  return cleaned || "unknownid";
}

export function buildSlug(title: string, bookId: string): string {
  const titlePart = titleToSlug(title);
  const code = idCode(bookId);
  // 标题整条都是标点/emoji 时才会走到这里，实测极少
  return titlePart ? `${titlePart}-${code}` : `d-${code}`;
}

/**
 * 从 slug 反解 id 码，用于校验访问的 slug 是否匹配该剧（不匹配时 308 到规范 slug）。
 *
 * 后缀恒为 ASCII 小写字母数字，与标题部分是否非拉丁无关，所以这个正则不用改。
 */
export function extractShortCode(slug: string): string | null {
  const match = /-([a-z0-9]{8,})$/.exec(slug);
  return match ? match[1] : null;
}

export function slugMatchesBook(slug: string, bookId: string): boolean {
  return extractShortCode(slug) === idCode(bookId);
}
