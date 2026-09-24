// PORTED_FROM: realshort@816ca2e src/lib/site.ts（:34 dramaPath）与 src/lib/pick/feed-map.ts（:198-199 rowRef）
// 本地改动：dramaPath 从站内相对路径改成 ReelShort 公开站的绝对地址（工作台不是那个站，页面上按外链、新标签打开）；
// locale 也编码一段。证据页链接的前缀与 feed-map.ts 的 rowRef 相同。

/** ReelShort 公开站（dramashortstv.com）；剧目页与 RealShort 选剧台都在它下面 */
export const REALSHORT_ORIGIN = "https://dramashortstv.com";

/** RealShort 选剧台单行证据页的前缀，后面接编码过的 row_key */
export const REALSHORT_ROW_URL = `${REALSHORT_ORIGIN}/admin/pick?tab=row&row=`;

/**
 * 剧目页的绝对地址。slug 保留标题原文（西里尔文、泰文、日文、阿拉伯文都有），进 URL 必须百分号编码；
 * 只编码 locale 与 slug 两段，斜杠是路径分隔符不能碰。
 */
export function dramaPath(locale: string, slug: string): string {
  return `${REALSHORT_ORIGIN}/${encodeURIComponent(locale)}/drama/${encodeURIComponent(slug)}`;
}

/** RealShort 选剧台里这一行的证据页（与 feed-map.ts 的 rowRef 同形） */
export function realshortRowUrl(rowKey: string): string {
  return REALSHORT_ROW_URL + encodeURIComponent(rowKey);
}
