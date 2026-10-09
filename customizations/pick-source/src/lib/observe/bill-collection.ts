import type { CpsBillItem } from "@/lib/cps/types";

/** 收齐并验证后交给调用方原子写入；不以部分页面冒充完成。 */
export async function collectBillPages(
  fetchPage: (
    page: number,
  ) => Promise<{ list: readonly CpsBillItem[]; total: number }>,
  { pageSize = 40, maxPages = 50 } = {},
): Promise<CpsBillItem[]> {
  const rows: CpsBillItem[] = [];
  const seen = new Set<string>();
  let expected: number | undefined;
  for (let page = 1; page <= maxPages; page++) {
    const result = await fetchPage(page);
    if (
      !Number.isInteger(result.total) ||
      result.total < 0 ||
      (expected !== undefined && expected !== result.total)
    )
      throw new Error("收益分页总数变化，采集不完整");
    expected = result.total;
    for (const item of result.list) {
      if (
        !/^\d{4}-\d{2}-\d{2}$/.test(item.date) ||
        Number.isNaN(Date.parse(item.date)) ||
        new Date(item.date).toISOString().slice(0, 10) !== item.date
      )
        throw new Error("收益日期无效");
      if (
        !Number.isFinite(item.total_revenue) ||
        !Number.isInteger(item.order_cnt) ||
        item.order_cnt < 0
      )
        throw new Error("收益金额或订单数无效");
      if (!item.book_id) throw new Error("收益资源 ID 缺失");
      const key = JSON.stringify([
        item.date,
        item.book_id,
        item.promotion_type,
        item.promotion_value,
      ]);
      if (seen.has(key)) throw new Error("收益分页包含重复记录");
      seen.add(key);
      rows.push(item);
    }
    if (rows.length === expected) return rows;
    if (rows.length > expected || result.list.length < pageSize)
      throw new Error("收益分页不完整");
  }
  throw new Error("收益达到分页上限，采集不完整");
}
