// PORTED_FROM: realshort@816ca2e src/app/admin/(protected)/pick/page.tsx
// 本地改动：RankView / GrowthEmpty 拆成同步的 RankBody（取数在页面）；三种榜各一个小组件；BillTable 换成 OrdersTable；
// now 换成版本的 as_of；涨幅榜空态的排序名取版本规则 rules.sortLabels，规则经 ctx.rules 传给 RankFilters 与 RankTable。
import Link from "next/link";

import {
  Empty,
  OutOfRange,
  Pager,
  pickHref,
} from "@/components/workspace/pick-board/toolbar";
import type { GrowthEmptyReason } from "@/core/pick-board/growth-diagnosis";
import type { Sort as RsSort } from "@/core/pick-board/metrics";
import type { PickRequest } from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import type { GrowthDiagnosis, RankMeta } from "@/server/pick-board";

import { OrdersTable } from "../orders-table";
import { RankFilters } from "../rank-filters";
import { RankTable, rankNote } from "../rank-table";
import { ReelshortTable } from "../reelshort-table";

import type { BoardContext, RankBoard, RankData } from "./board-data";

const NOTE = "text-helper mb-3 text-sm leading-[1.65]";
const LINK = "text-link hover:underline";
const GROWTH_SORTS = ["d1", "d7", "dp1", "dp7"] as const;

type Theater = Extract<RankBoard, { kind: "theater" }>;
type RsRows = Extract<RankBoard, { kind: "rows" }>;
type Ledger = Extract<RankBoard, { kind: "ledger" }>;
type Props<B> = Readonly<{
  board: B;
  meta: RankMeta;
  req: PickRequest;
  ctx: BoardContext;
}>;

function whyEmpty(
  d: GrowthDiagnosis,
  label: string,
): Record<GrowthEmptyReason, string> {
  const since = `按「${label}」要比 ${d.baselineDay} 的快照`;
  return {
    filters_empty: `当前筛选（语种 / 上线分桶 / 搜索）下没有能按「${label}」比较的剧；不加筛选时有。`,
    no_verified_snapshot: `保留的快照里还没有已校验的，暂不能按「${label}」排名。`,
    baseline_before_first_verified_snapshot: `${since}，保留的第一个已校验快照是 ${d.earliestVerifiedOn ?? "—"}${d.earliestPossibleOn ? `，最早 ${d.earliestPossibleOn} 起可能有数` : ""}。`,
    baseline_snapshot_missing: `${since}，那天没有快照，今天算不出。`,
    baseline_snapshot_unverified: `${since}，那天的快照未校验，今天算不出。`,
    no_comparable_rows: `${d.baselineDay} 的已校验快照在，但当前没有能对上的剧，原因未知。`,
  };
}

/**
 * 涨幅榜空态：按 loadGrowthDiagnosis 的原因说清为什么是空的。
 * 出口链接上的可比数是全库口径，只在没加筛选时印。
 */
function GrowthEmpty({
  req,
  meta,
  sort,
  d,
  rules,
}: {
  req: PickRequest;
  meta: RankMeta;
  sort: RsSort;
  d: GrowthDiagnosis;
  rules: BoardRules;
}) {
  const alts = GROWTH_SORTS.filter(
    (s) => s !== sort && meta.growthCounts[s] > 0,
  );
  return (
    <Empty>
      {whyEmpty(d, rules.sortLabels[sort])[d.reason]}
      {d.reason === "filters_empty" ? (
        <Link
          prefetch={false}
          href={pickHref(req, { rsLocale: "", rsBucket: null, q: "" })}
          className={`ml-2 ${LINK}`}
        >
          去掉筛选
        </Link>
      ) : null}
      {alts.length ? (
        <span className="ml-2 inline-flex flex-wrap gap-3">
          {alts.map((s) => (
            <Link
              prefetch={false}
              key={s}
              href={pickHref(req, { rsSort: s })}
              className={LINK}
            >
              换「{rules.sortLabels[s]}」
              {d.filtered
                ? ""
                : `（${meta.growthCounts[s].toLocaleString("en-US")} 部可比）`}
            </Link>
          ))}
        </span>
      ) : null}
    </Empty>
  );
}

function TheaterRank({ board, meta, req, ctx }: Props<Theater>) {
  const { rows, total, hasMore } = board.page;
  return (
    <>
      <RankFilters req={req} meta={meta} rules={ctx.rules} />
      <p className={NOTE}>
        {rankNote(board.rank, meta)} 榜单 tab
        一律含已下架的行（行上标「下架」）：那一天的榜是历史事实。
      </p>
      {rows.length === 0 ? (
        total > 0 ? (
          <OutOfRange req={req} total={total} />
        ) : (
          <Empty>这个榜单在当前选择下没有记录。</Empty>
        )
      ) : (
        <>
          <RankTable
            rows={rows}
            req={req}
            meta={meta}
            kind={board.rank}
            rules={ctx.rules}
          />
          <Pager
            req={req}
            hasMore={hasMore}
            count={rows.length}
            total={total}
          />
        </>
      )}
    </>
  );
}

function LedgerRank({ board, meta, req, ctx }: Props<Ledger>) {
  return (
    <>
      <RankFilters req={req} meta={meta} rules={ctx.rules} />
      <p className={NOTE}>
        {rankNote(board.rank, meta, req.rsBucket !== null)}
      </p>
      <OrdersTable
        rows={board.rows}
        totals={board.totals}
        asOf={ctx.asOf}
        source={board.source}
        req={req}
      />
    </>
  );
}

function RsRank({ board, meta, req, ctx }: Props<RsRows>) {
  const { rows, total, hasMore, sort, diagnosis } = board;
  const growth = board.rank === "rs_growth";
  const offset = (req.page - 1) * req.size;
  const table = (
    <>
      <ReelshortTable
        rows={rows}
        asOf={ctx.asOf}
        req={req}
        rank={growth}
        sort={sort}
        candidates={board.rank === "rs_cand"}
        offset={growth ? 0 : offset}
      />
      {rows.length > 0 && !growth ? (
        <Pager req={req} hasMore={hasMore} count={rows.length} total={total} />
      ) : null}
    </>
  );
  return (
    <>
      <RankFilters req={req} meta={meta} rules={ctx.rules} />
      <p className={NOTE}>
        {rankNote(board.rank, meta, req.rsBucket !== null)}
      </p>
      {rows.length === 0 && total !== null && total > 0 ? (
        <OutOfRange req={req} total={total} />
      ) : diagnosis ? (
        <GrowthEmpty
          req={req}
          meta={meta}
          sort={sort}
          d={diagnosis}
          rules={ctx.rules}
        />
      ) : (
        table
      )}
    </>
  );
}

export function RankBody({
  data,
  req,
  ctx,
}: {
  data: RankData;
  req: PickRequest;
  ctx: BoardContext;
}) {
  const { board, meta } = data;
  if (board.kind === "theater")
    return <TheaterRank board={board} meta={meta} req={req} ctx={ctx} />;
  if (board.kind === "ledger")
    return <LedgerRank board={board} meta={meta} req={req} ctx={ctx} />;
  return <RsRank board={board} meta={meta} req={req} ctx={ctx} />;
}
