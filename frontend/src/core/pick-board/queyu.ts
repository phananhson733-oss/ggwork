// PORTED_FROM: realshort@816ca2e src/lib/pick/request.ts（:497-502，QUEYU_INDEX 与 queyuHref）
// 本地改动：从 request.ts 拆成不 import 任何模块的文件，client 组件 queyu-button 引它时不带进 request.ts 与 metrics.ts。

/** 鹊娱剧库：没有单剧直达地址，只能把剧名带到列表页上 */
export const QUEYU_INDEX = "https://cps-distribution.zwnet.cn/promotion/index";

export function queyuHref(title: string): string {
  return `${QUEYU_INDEX}?title=${encodeURIComponent(title)}`;
}
