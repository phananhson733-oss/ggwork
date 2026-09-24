// PORTED_FROM: realshort@816ca2e src/lib/pick/request.ts（:359-379，isRowKey 与 ROW_KEY_MAX）
// 本地改动：从 request.ts 拆成不 import 任何模块的文件。client 组件（候选卡的 identity 解码、证据页链接）只要这条判定，
// 引整个 request.ts 会把它和 metrics.ts 一起打进聊天页的包。request.ts 从这里转出，两边是同一个函数。

/** C0 控制字符与 DEL */
const CONTROL = /[\u0000-\u001f\u007f]/;

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
 * 列表里点剧名进证据页落「找不到这一行」，而页面照常 200。
 */
export function isRowKey(s: string): boolean {
  return (
    s.length > 0 && s.length <= ROW_KEY_MAX && /\S/.test(s) && !CONTROL.test(s)
  );
}
