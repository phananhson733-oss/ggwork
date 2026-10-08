/** Export only the displayed production batch; missing observations stay blank. */
import { formatChange } from "./trends-table";
import type { TrendStats } from "./trends-table";
import type { TrendsTableRow } from "./trends-table-schema";
import {
  basisText,
  fetchStatusText,
  RESULT_TEXT,
} from "./trends-table-wording";

function cell(value: string | number | null): string {
  let text = value === null ? "" : String(value);
  if (typeof value === "string" && /^(?:\s*[=+@-]|[\t\r\n])/.test(text))
    text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

export function trendsCsv(
  lines: readonly { row: TrendsTableRow; stats: TrendStats | null }[],
  date: string,
  last: string,
): string {
  const rows: (string | number | null)[][] = [
    [
      "采集批次",
      "市场",
      "完整日截止 UTC",
      "剧名",
      "平台",
      "语种",
      "查询词",
      "入选依据",
      "近7日均值",
      "前7日均值",
      "变化",
      "采集结果",
      "采集说明",
    ],
  ];
  for (const { row, stats } of lines)
    rows.push([
      date,
      row.geo || "全球",
      last,
      row.title,
      row.platform,
      row.language,
      row.term,
      row.basis.map(basisText).join("；"),
      stats?.recentMean ?? null,
      stats?.priorMean ?? null,
      stats?.changeTenths == null ? null : formatChange(stats.changeTenths),
      RESULT_TEXT[row.result],
      fetchStatusText(row.status),
    ]);
  return "\uFEFF" + rows.map((r) => r.map(cell).join(",")).join("\r\n");
}
