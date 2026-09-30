// 工作台新建（简化版趋势雷达，2026-09-30 简化范围第 2、4 节）：资料页的「Google 趋势」tab。一张只读参考表：当晚任务
// 清单上的每部剧一行（gateway 的 GET /api/pick/obs/trends-table），各自在 Google 上近 30 天的日级走势、两段均值、
// 变化与标签。不进智能体，不改候选排序。横幅是 gateway 按批次判断的（采集漏跑、数据过期为红色）；表头写采集日期与
// 计划、有数据、未返回数据、未查到的部数；没有批次、清单为空都写成一句话。排序按变化或按入选顺序，走链接（ts=）。
// 同步、纯展示：均值与标签由 core/pick/trends-table.ts 算。
import Link from "next/link";

import { ObsBannerList } from "@/components/workspace/pick/obs-banner-list";
import { DATA_SOURCE_TRENDS } from "@/core/pick/obs-wording";
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
import { nightOutcomeText } from "@/core/pick/trends-table-wording";
import type { PickRequest } from "@/core/pick-board/request";

import { pickHref } from "../toolbar";

import { at, HEADING, LINK, MUTED, SECTION, TABLE, TH } from "./obs-parts";
import { TrendsTableRowView, type TableLine } from "./trends-table-row";
import { TableSources } from "./trends-table-sources";

const INTRO =
  "只读参考：每晚取我们榜单上与 ReelShort 收入靠前的一批剧，查它们在 Google 上全球近 30 天的日级搜索走势。不进智能体，不改候选排序。指数是每部剧相对自己 30 天峰值的值，不能拿来比较两部剧谁搜得多。";

const COLUMNS = [
  "剧",
  "入选依据",
  "走势（近 30 天）",
  "近 7 日均值",
  "前 7 日均值",
  "变化",
  "标签",
  "采集结果",
  "提示",
  "链接",
] as const;

const SORT_TEXT: Readonly<Record<TrendSort, string>> = {
  change: "按变化",
  order: "按入选顺序",
};

function Banners({ table }: { table: TrendsTable }) {
  if (table.banners.length === 0) return null;
  return (
    <div className="mb-3" data-trends-banners="true">
      <ObsBannerList
        banners={table.banners.map((b) => ({ ...b, channel: "trends" }))}
      />
    </div>
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
  return (
    <section className={SECTION} data-trends-header="true">
      <h2 className={HEADING}>
        {batch.target_date} 的趋势表（{nightOutcomeText(batch.outcome)}）
      </h2>
      <Counts batch={batch} />
      <p className={MUTED}>
        {at(batch.started_at)} 开始采集
        {batch.finished_at ? `，${at(batch.finished_at)} 结束` : ""}
        ；曲线的最后一个完整日是 {last}（UTC），之后的日子还不完整，不参与计算。
      </p>
      <TableSources batch={batch} />
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
    <div className="overflow-x-auto">
      <table className={TABLE}>
        <thead>
          <tr>
            {COLUMNS.map((name) => (
              <th key={name} scope="col" className={TH}>
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortRows(lines, req.trendsSort).map((line) => (
            <TrendsTableRowView
              key={line.row.unit}
              line={line}
              lastComplete={last}
            />
          ))}
        </tbody>
      </table>
    </div>
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
      没有返回值的日子不参与均值。
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
}: {
  table: TrendsTable;
  req: PickRequest;
}) {
  return (
    <div data-trends-view="table">
      <Banners table={table} />
      <p className={`${MUTED} mb-3 text-[13px]`}>{INTRO}</p>
      <Body table={table} req={req} />
      <Rules />
      <p className={MUTED}>{DATA_SOURCE_TRENDS}</p>
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
