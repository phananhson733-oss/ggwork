// 工作台新建（简化版趋势雷达，2026-09-30）：趋势表的一行。列与顺序见简化范围第 2 节：剧、入选依据、走势、近 7 日
// 与前 7 日均值、变化、标签、采集结果、提示、链接。均值、变化与标签只在「有数据」的行上算（trends-table.ts；全是 0 的曲线
// 也算有数据，它是 Google 的相对指数，不是搜索量为 0）；
// 别的行这几列写「—」，走势写采集结果，不拿别的夜晚的曲线顶替。同步、纯展示。
import {
  SHORT_TERM_HINT,
  TREND_LABEL_TEXT,
  formatChange,
  formatMean,
  isShortTerm,
  sparkPoints,
  trendsExploreUrl,
  type TrendStats,
} from "@/core/pick/trends-table";
import type { TrendsTableRow } from "@/core/pick/trends-table-schema";
import {
  RESULT_TEXT,
  basisText,
  fetchStatusText,
} from "@/core/pick/trends-table-wording";

import { ExternalLink } from "../links";
import { TrendsSpark } from "../trends-spark";

import { LINK, MUTED, TD } from "./obs-parts";

export type TableLine = Readonly<{
  order: number;
  row: TrendsTableRow;
  /** null: the row has no series with data this night */
  stats: TrendStats | null;
}>;

const NUMBER = `${TD} text-right tabular-nums`;
const LABEL_TONE: Readonly<Record<TrendStats["label"], string>> = {
  rising: "text-success-ink font-semibold",
  new: "text-success-ink font-semibold",
  falling: "text-danger-ink",
  flat: "text-ink-2",
  too_little: "text-ink-dim",
};

function Drama({ row }: { row: TrendsTableRow }) {
  const meta = [row.platform, row.language].filter(Boolean).join(" · ");
  return (
    <td className={TD}>
      <div className="text-ink-1 font-semibold">{row.title}</div>
      {meta ? <div className={MUTED}>{meta}</div> : null}
      {row.term !== row.title ? (
        <div className={MUTED}>查询词：{row.term}</div>
      ) : null}
    </td>
  );
}

function Basis({ row }: { row: TrendsTableRow }) {
  return (
    <td className={TD}>
      {row.basis.length === 0 ? (
        <span className={MUTED}>—</span>
      ) : (
        row.basis.map((b, i) => <div key={i}>{basisText(b)}</div>)
      )}
    </td>
  );
}

function Result({ row }: { row: TrendsTableRow }) {
  const detail = row.status === "ok" ? "" : fetchStatusText(row.status);
  return (
    <td className={TD} data-trends-result={row.result}>
      {RESULT_TEXT[row.result]}
      {detail ? <div className={MUTED}>{detail}</div> : null}
    </td>
  );
}

export function TrendsTableRowView({
  line,
  lastComplete,
}: {
  line: TableLine;
  lastComplete: string;
}) {
  const { row, stats } = line;
  return (
    <tr data-trends-row={row.order}>
      <Drama row={row} />
      <Basis row={row} />
      <td className={TD}>
        {stats && row.series ? (
          <TrendsSpark
            points={sparkPoints(row.series, lastComplete)}
            title={`${row.term} 的近 30 天走势`}
          />
        ) : (
          <span className={MUTED}>—</span>
        )}
      </td>
      <td className={NUMBER}>{formatMean(stats?.recentMean ?? null)}</td>
      <td className={NUMBER}>{formatMean(stats?.priorMean ?? null)}</td>
      <td className={NUMBER}>{formatChange(stats?.changeTenths ?? null)}</td>
      <td className={TD} data-trends-label={stats?.label ?? ""}>
        {stats ? (
          <span className={LABEL_TONE[stats.label]}>
            {TREND_LABEL_TEXT[stats.label]}
          </span>
        ) : (
          <span className={MUTED}>—</span>
        )}
      </td>
      <Result row={row} />
      <td className={TD}>
        {isShortTerm(row.term) ? (
          <span className="text-warning-ink">{SHORT_TERM_HINT}</span>
        ) : null}
      </td>
      <td className={TD}>
        <ExternalLink
          href={trendsExploreUrl(row.term, row.geo, row.time_range)}
          className={LINK}
          rel="noopener noreferrer"
        >
          在 Google Trends 打开
        </ExternalLink>
      </td>
    </tr>
  );
}
