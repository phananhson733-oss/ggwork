// Production Trends tab: native theme, compact batch evidence and progressive details.
// Server computes the existing statistics; a small client island filters/exports these same rows without fetching.
import Link from "next/link";
import type { ReactNode } from "react";

import { orderObsBanners } from "@/components/workspace/pick/obs-banner-list";
import { obsBannerText } from "@/core/pick/obs-status";
import {
  TREND_LABEL_TEXT,
  TREND_RULES,
  lastCompleteDay,
  sortRows,
  trendStats,
  type TrendSort,
} from "@/core/pick/trends-table";
import type {
  TrendsTable,
  TrendsTableBatch,
} from "@/core/pick/trends-table-schema";
import { DATA_SOURCE_TRENDS } from "@/core/pick/trends-table-wording";
import { nightOutcomeText } from "@/core/pick/trends-table-wording";
import type { PickRequest } from "@/core/pick-board/request";

import { pickHref } from "../toolbar";
import { TrendsTableExplorer } from "../trends-table-explorer";

import { at, HEADING, LINK, MUTED, SECTION } from "./trends-table-parts";
import type { TableLine } from "./trends-table-row";
import { TableSources } from "./trends-table-sources";

const INTRO =
  "只读参考：每晚取我们榜单上与 ReelShort 收入靠前的一批剧，查它们在 Google 上全球近 30 天的日级搜索走势。不进智能体，不改候选排序。指数是每部剧相对自己 30 天峰值的值，不能拿来比较两部剧谁搜得多。";

const SORT_TEXT: Readonly<Record<TrendSort, string>> = {
  change: "按变化",
  order: "按入选顺序",
};

function Banners({ table }: { table: TrendsTable }) {
  if (table.banners.length === 0) return null;
  const ordered = orderObsBanners(
    table.banners.map((b) => ({ ...b, channel: "trends" as const })),
  );
  const first = ordered[0]!;
  const tone =
    first.level === "red"
      ? "border-danger-line bg-danger-surface text-danger-ink"
      : first.level === "warn"
        ? "border-warning-line bg-warning-surface text-warning-ink"
        : "border-line bg-info-surface text-info-ink";
  return (
    <aside
      className={`mb-3 rounded-lg border px-3 py-2 text-[13px] ${tone}`}
      role={first.level === "red" ? "alert" : "status"}
      data-trends-banners="true"
    >
      <p>{obsBannerText(first.code)}</p>
      {ordered.length > 1 ? (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs">
            查看其它状态提示（{ordered.length - 1}）
          </summary>
          {ordered.slice(1).map((b) => (
            <p key={b.code} className="mt-1">
              {obsBannerText(b.code)}
            </p>
          ))}
        </details>
      ) : null}
    </aside>
  );
}

function Counts({ batch }: { batch: TrendsTableBatch }) {
  const c = batch.counts;
  return (
    <p data-trends-counts="true">
      这晚计划查 {c.planned} 部：有数据 {c.data} 部，Google 未返回数据{" "}
      {c.no_data} 部，这晚未查到 {c.not_fetched} 部
      {c.pending > 0 ? `，还在查 ${c.pending} 部` : ""}。
    </p>
  );
}

function Header({ batch, last }: { batch: TrendsTableBatch; last: string }) {
  const recovery = batch.daily_recovery;
  const qualified = recovery
    ? Math.min(
        3,
        recovery.qualified_nights + (recovery.qualified === true ? 1 : 0),
      )
    : 0;
  return (
    <section className={SECTION} data-trends-header="true">
      <h2 className={HEADING}>
        {batch.target_date} 的趋势表（
        {!batch.collecting &&
        ["withheld", "published"].includes(batch.outcome) &&
        (batch.counts.not_fetched > 0 || batch.counts.pending > 0)
          ? "已结束，未采完整"
          : nightOutcomeText(batch.outcome, batch.collecting)}
        ）
      </h2>
      <Counts batch={batch} />
      <details className="mt-2">
        <summary className={`${LINK} cursor-pointer text-xs`}>
          批次来源与恢复记录
        </summary>
        {recovery ? (
          <p data-trends-recovery="true">
            恢复验证：本晚目标 {recovery.target} 部，已通过 {qualified}/3
            个有效夜晚。
            {recovery.qualified === null
              ? "本晚尚未完成资格核验。"
              : recovery.qualified
                ? "本晚通过资格核验。"
                : "本晚未通过资格核验，不据此升级阶段。"}
            {qualified === 3
              ? "后续维持每天最多 100 部；停止提示仍然优先。"
              : "只有完整且有真实请求与原始结果的夜晚才计入。"}
          </p>
        ) : null}
        <p className={MUTED}>
          {at(batch.started_at)} 开始采集
          {batch.finished_at ? `，${at(batch.finished_at)} 结束` : ""}
          ；曲线的最后一个完整日是 {last}
          （UTC），之后的日子还不完整，不参与计算。
        </p>
        <TableSources batch={batch} />
      </details>
    </section>
  );
}

function SortLinks({ req }: { req: PickRequest }) {
  return (
    <p className="mb-2 text-[13px]" data-trends-sort={req.trendsSort}>
      排序：
      {(Object.keys(SORT_TEXT) as TrendSort[]).map((sort) =>
        sort === req.trendsSort ? (
          <span key={sort} className="text-ink-1 mr-3 font-semibold">
            {SORT_TEXT[sort]}
          </span>
        ) : (
          <Link
            key={sort}
            prefetch={false}
            href={pickHref(req, { trendsSort: sort })}
            className={`${LINK} mr-3`}
          >
            {SORT_TEXT[sort]}
          </Link>
        ),
      )}
    </p>
  );
}

function Rows({
  table,
  last,
  req,
}: {
  table: TrendsTable;
  last: string;
  req: PickRequest;
}) {
  const lines: TableLine[] = table.rows.map((row) => ({
    order: row.order,
    row,
    stats:
      row.result === "data" && row.series ? trendStats(row.series, last) : null,
  }));
  return (
    <TrendsTableExplorer
      key={table.batch!.batch_id}
      lines={sortRows(lines, req.trendsSort)}
      last={last}
      date={table.batch!.target_date}
    />
  );
}

function Rules() {
  const r = TREND_RULES;
  const t = TREND_LABEL_TEXT;
  return (
    <p className={MUTED} data-trends-rules="true">
      标签只描述两段均值的关系：近 {r.windowDays} 个完整日里不为 0 的天数少于{" "}
      {r.minNonZeroDays} 天为「{t.too_little}」；前 {r.windowDays} 日均值为
      0、近 {r.windowDays} 日大于 0 为「{t.new}」；变化不低于 +{r.changePercent}
      % 为「{t.rising}」，不高于 −{r.changePercent}% 为「
      {t.falling}」，其余「{t.flat}」。按这个顺序判，先命中的为准；Google
      没有返回值的日子不参与均值。变化按一位小数向零截断显示，所以「
      {t.flat}」的行不会显示成 ±{r.changePercent}.0%；「{t.too_little}
      」先判，它的变化可以超过 ±{r.changePercent}%。
    </p>
  );
}

function Body({ table, req }: { table: TrendsTable; req: PickRequest }) {
  const batch = table.batch;
  if (batch === null)
    return (
      <p className={SECTION} data-trends-empty="batch">
        还没有趋势表：每晚的采集（UTC 17:30 起）第一次跑完后，这里才有数据。
      </p>
    );
  const last = lastCompleteDay(batch.window_end);
  return (
    <>
      <Header batch={batch} last={last} />
      {table.rows.length === 0 ? (
        <p className={SECTION} data-trends-empty="rows">
          这晚的任务清单是空的，没有要查的剧。
        </p>
      ) : (
        <section className={SECTION} data-trends-section="rows">
          <SortLinks req={req} />
          <Rows table={table} last={last} req={req} />
          {table.truncated ? (
            <p className={MUTED}>
              只列出清单的前 {table.row_limit} 部；表头的部数是列出的这些。
            </p>
          ) : null}
        </section>
      )}
    </>
  );
}

export function TrendsTableView({
  table,
  req,
  preview,
}: {
  table: TrendsTable;
  req: PickRequest;
  preview?: ReactNode;
}) {
  return (
    <div data-trends-view="table">
      <Banners table={table} />
      <p className={`${MUTED} mb-3 text-[13px]`}>
        先看入选依据，再核对走势。数据来自生产采集批次，缺失值不补零；不改变选剧排序。
      </p>
      {table.batch === null && preview ? (
        preview
      ) : (
        <Body table={table} req={req} />
      )}
      {table.batch !== null ? (
        <>
          <details className={`${SECTION} mt-3`}>
            <summary className={`${LINK} cursor-pointer`}>
              数据来源与计算口径
            </summary>
            <p className="mt-2">{INTRO}</p>
            <Rules />
            <p className={MUTED}>{DATA_SOURCE_TRENDS}</p>
            <p className={MUTED}>
              本视图未接入 GSC；不使用本地 US 历史样本填补生产缺失值。
            </p>
          </details>
        </>
      ) : null}
    </div>
  );
}

/** The gateway could not answer (503, a timeout, an answer this page cannot read): one line, the tab shell stays. */
export function TrendsTableUnavailable() {
  return (
    <p
      role="alert"
      className="border-warning-line bg-warning-surface text-warning-ink mb-4 rounded-lg border px-4 py-3 text-[13px] leading-[1.65]"
      data-trends-unavailable="true"
    >
      趋势表暂时读不了，稍后刷新再试。
    </p>
  );
}
