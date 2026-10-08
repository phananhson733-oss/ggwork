import { EditingEntry } from "@/components/workspace/editing/editing-entry";
// Native Trends evidence row: production basis, same-batch curve, comparison, result and verification guidance.
// Missing/partial observations keep their existing semantics; no imported pilot scores or fallback curves.
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

import { LINK, MUTED, TD } from "./trends-table-parts";

export type TableLine = Readonly<{
  order: number;
  row: TrendsTableRow;
  /** null: the row has no series with data this night */
  stats: TrendStats | null;
}>;

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
      <EditingEntry title={row.title} />
      {meta ? <div className={MUTED}>{meta}</div> : null}
      <div className="text-helper mt-1 text-xs">市场：{row.geo || "全球"}</div>
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
        <>
          <div>{basisText(row.basis[0]!)}</div>
          {row.basis.length > 1 ? (
            <details className="mt-1">
              <summary className={`${LINK} cursor-pointer text-xs`}>
                另 {row.basis.length - 1} 条来源
              </summary>
              {row.basis.slice(1).map((b, i) => (
                <div key={i} className={MUTED}>
                  {basisText(b)}
                </div>
              ))}
            </details>
          ) : null}
        </>
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
      <td className={`${TD} min-w-40`}>
        {stats && row.series ? (
          <>
            <TrendsSpark
              points={sparkPoints(row.series, lastComplete)}
              title={`${row.term} 的近 30 天走势`}
            />
            <div className="text-helper mt-1 text-xs">
              完整日截至 {lastComplete}
            </div>
            <div className="text-helper mt-1 text-xs">
              近 7 日返回 {stats.recentDays}/7 天 · 非零 {stats.nonZeroRecent}{" "}
              天
            </div>
          </>
        ) : (
          <span className={MUTED}>—</span>
        )}
      </td>
      <td className={`${TD} tabular-nums`}>
        <div className="text-ink-1 font-semibold" data-trends-change="true">
          {formatChange(stats?.changeTenths ?? null)}
        </div>
        <div data-trends-label={stats?.label ?? ""} className="mt-1">
          {stats ? (
            <span className={LABEL_TONE[stats.label]}>
              {TREND_LABEL_TEXT[stats.label]}
            </span>
          ) : (
            <span className={MUTED}>—</span>
          )}
        </div>
        <details className="mt-1 text-xs">
          <summary className={`${LINK} cursor-pointer`}>两段均值</summary>
          <div>近 7 日均值：{formatMean(stats?.recentMean ?? null)}</div>
          <div>前 7 日均值：{formatMean(stats?.priorMean ?? null)}</div>
          {stats ? (
            <div className={MUTED}>
              实际返回 {stats.recentDays} / {stats.priorDays}{" "}
              个完整日；缺失日不补零。
            </div>
          ) : null}
        </details>
      </td>
      <Result row={row} />
      <td className={`${TD} min-w-32`}>
        <p className="text-helper mb-2 text-xs" data-trends-advice="true">
          {row.result !== "data"
            ? "先核对采集状态，暂不据此判断趋势。"
            : stats?.label === "too_little"
              ? "有效观测不足，先核对查询词与日期。"
              : "先核对同名剧与窗口，再作为选剧参考。"}
        </p>
        {isShortTerm(row.term) ? (
          <span className="text-warning-ink">{SHORT_TERM_HINT}</span>
        ) : null}

        <div className="mt-2">
          <ExternalLink
            href={trendsExploreUrl(row.term, row.geo, row.time_range)}
            className={LINK}
            rel="noopener noreferrer"
          >
            在 Google Trends 打开
          </ExternalLink>
        </div>
      </td>
    </tr>
  );
}
