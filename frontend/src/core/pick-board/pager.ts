// PORTED_FROM: realshort@816ca2e src/components/ui/pager.tsx（:27 pageSequence）
// 本地改动：只摘纯函数 pageSequence 成 .ts 纯模块；Pager 组件本身不搬（toolbar 自己渲染分页按钮，只用这个序列）。
/**
 * 数字分页器。分类页与 blog 列表共用。
 *
 * 页码序列固定为「首页 + 当前页两侧各一页 + 末页」，中间用省略号补齐，
 * 所以无论总页数是 7 还是 326，渲染出来的按钮数量都是常数——
 * 片库上万部时，把页码全列出来会生成一条几百个链接的行。
 *
 * 省略号是 span 不是 link：它没有目标页，做成可点元素会误导。
 */

/**
 * 返回要渲染的页码序列，省略处用 null 占位。
 *
 * `span` 是当前页两侧各露几页。公开站用默认的 1（一行放得下、也够用）；
 * 后台观测台传 4，于是第 1 页看到的是 1..10——那张表默认每页 10 行、
 * 常有几千页，人要能一眼跳到前十页里的任意一页。
 *
 * 【不要为了后台再抄一份】两份实现迟早给出不同的序列，而这种偏差
 * 在两边各自看时都很正常。
 */
export function pageSequence(
  current: number,
  total: number,
  span = 1,
): ReadonlyArray<number | null> {
  // 阈值跟着 span 走：能全列就全列，省得出现只省掉一页的省略号
  if (total <= 2 * span + 5) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }

  const pages = new Set<number>([1, total, current]);
  for (let d = 1; d <= span; d++) {
    if (current - d > 1) pages.add(current - d);
    if (current + d < total) pages.add(current + d);
  }
  // 靠近两端时补齐，避免出现 "1 … 2 3" 这种省略号只省掉一页的怪样子
  if (current <= span + 2) for (let n = 2; n <= 2 * span + 2; n++) pages.add(n);
  if (current >= total - span - 1)
    for (let n = total - 2 * span - 1; n <= total - 1; n++) pages.add(n);

  const sorted = [...pages]
    .filter((n) => n >= 1 && n <= total)
    .sort((a, b) => a - b);

  const out: (number | null)[] = [];
  let previous = 0;
  for (const page of sorted) {
    if (previous && page - previous > 1) out.push(null);
    out.push(page);
    previous = page;
  }
  return out;
}
