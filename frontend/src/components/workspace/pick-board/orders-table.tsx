// PORTED_FROM: realshort@816ca2e src/components/admin/pick/bill-table.tsx
// 本地改动：重写成「订单对账」（原「分成对账」，★U27）：不显示金额、推广标识与分成比例；一行是 rs_bill_orders 里
// 同一天、同一部剧、同一推广类型合并后的一行；合计改成行数口径（原始行 / 合并后 / 订单 / 同日有出站）；
// 剧名在账单归得上正典 id 时链到它的证据页，归不上时只印账单随附的剧名；去掉上线日期与上线天数两列
// （rs_ids 没有 publish_at，book_id 也不一定是正典）；来源状态按版本的 as_of 判（原 :42、:50 没传 now）；页脚写明三点变化。
import { formatInt, formatObservedAt } from "@/core/pick-board/metrics";
import { reelshortRowKey, type PickRequest } from "@/core/pick-board/request";
import { sourceStatus, type SourceState } from "@/core/pick-board/source-types";
import type { BillRow, BillTotals } from "@/server/pick-board";

import { TD } from "./cells";
import { Num, Th, TitleCell } from "./reelshort-cells";
import { TableWrap, TR } from "./rows-table";
import { Empty, rowHref } from "./toolbar";

/**
 * 订单对账（榜单 tab 的 rs_ledger）。
 *
 * 【「站内同日有出站」必须带说明，它是启发式不是归因】：短链和推广口令是上游
 * 发给这个 CPS 账号的，贴在网站、群里、社媒回填的都是同一个值，上游本身
 * 分不出渠道。同日站内有出站只说明"时间和位置对得上"。
 */
function SourceLine({
  source,
  asOf,
}: {
  source: SourceState | undefined;
  asOf: Date;
}) {
  return (
    <p className="text-helper mb-3 text-sm leading-relaxed">
      账号订单来自上游账单（预估，不是结算）。{sourceStatus(source, asOf)}
      。最近成功采集：{formatObservedAt(source?.completedAt)}。请求区间：
      {source?.details.startDate ?? "未知"} 至{" "}
      {source?.details.endDate ?? "未知"}
      （上游日期，时区未确认）。明细只列有订单的行，本页不显示金额。
    </p>
  );
}

function TotalTiles({ totals }: { totals: BillTotals }) {
  const tiles = [
    { l: "原始账单行", v: formatInt(totals.rows), s: "有订单的上游账单行" },
    {
      l: "合并后行数",
      v: formatInt(totals.mergedRows),
      s: "同一天、同一部剧、同一推广类型合并为一行",
    },
    { l: "订单数", v: formatInt(totals.orders), s: "order_cnt 合计" },
    {
      l: "同日站内有出站",
      v: `合并行 ${formatInt(totals.mergedWithClicks)} / 原始行 ${formatInt(totals.rowsWithClicks)}`,
      s: "启发式，不是归因",
    },
  ];
  return (
    <div className="mb-3.5 grid grid-cols-2 gap-3 lg:grid-cols-4">
      {tiles.map((t) => (
        <div
          key={t.l}
          className="border-line bg-panel rounded-[12px] border px-4 py-3"
        >
          <div className="text-helper text-[14px]">{t.l}</div>
          <div className="my-1 text-[24px] leading-tight font-bold tabular-nums">
            {t.v}
          </div>
          <div className="text-helper text-[12px]">{t.s}</div>
        </div>
      ))}
    </div>
  );
}

function OrderTr({ r, req }: { r: BillRow; req: PickRequest }) {
  return (
    <tr className={TR}>
      <td className={`${TD} whitespace-nowrap tabular-nums`}>{r.billDate}</td>
      <TitleCell
        id={r.bookId}
        title={r.title}
        locale={r.locale}
        href={
          r.canonicalId === null
            ? undefined
            : rowHref(req, reelshortRowKey(r.canonicalId))
        }
      />
      <td className={`${TD} whitespace-nowrap`}>
        <span className="border-line rounded-full border px-2 py-0.5 text-[11px]">
          {r.promotionType}
        </span>
        {r.sourceRows > 1 ? (
          <span className="text-ink-dim ml-1.5 text-[11px]">
            合并 {formatInt(r.sourceRows)} 行
          </span>
        ) : null}
      </td>
      <Num>{formatInt(r.orderCnt)}</Num>
      <td className={`${TD} whitespace-nowrap`}>
        {r.sameDayClicks > 0 ? (
          <span className="bg-success-surface text-success-ink rounded-full px-2 py-0.5 text-[11px]">
            有 · {formatInt(r.sameDayClicks)} 次
          </span>
        ) : (
          <span className="text-ink-dim text-[11px]">—</span>
        )}
      </td>
    </tr>
  );
}

function OrdersHead() {
  return (
    <thead>
      <tr>
        <Th hint="上游账单里这一行标注的日期。上游时区未确认，所以它与最后一列用的 UTC 日不保证对齐。">
          日期
        </Th>
        <Th hint="账单归得上正典 id 时链到那部剧的证据页；归不上时只印账单随附的剧名。">
          剧
        </Th>
        <Th hint="上游 promotion_type：link_book 是剧集短链，code 是推广口令，link_app 是 App 短链。">
          推广类型
        </Th>
        <Th right hint="上游 order_cnt，合并后这一行的订单笔数。">
          订单数
        </Th>
        <Th hint="同组同语种资源、同一 UTC 日、排除已识别爬虫后站内出站 ≥ 1。这是启发式不是归因：推广标识分不出渠道，所以它只说明「那天站内也有人点了出去」，不能证明订单是本站带来的。">
          同日站内出站
        </Th>
      </tr>
    </thead>
  );
}

function Footnotes({ totals, shown }: { totals: BillTotals; shown: number }) {
  return (
    <>
      {totals.mergedRows > shown ? (
        <p className="text-warning-ink mt-2 text-[12px]">
          合并后共 {formatInt(totals.mergedRows)} 行有订单，这里只列了最近{" "}
          {formatInt(shown)} 行。上面四个数是全量合计，不是本页之和。
        </p>
      ) : null}
      <p className="text-ink-dim mt-2 max-w-[90ch] text-[12px]">
        排序：bill_date DESC, order_cnt DESC, book_id, promotion_type（RealShort
        原来按金额排，本页没有金额）；最多列 200
        行，按合并后的行计；没有「上线日期 / 上线天数」两列：账单上的 book_id
        不一定是正典 id，镜像里也没有上线日期。
        「同日站内出站」是启发式：短链与口令是上游发给这个 CPS
        账号的，贴在网站、群里、社媒的都是同一个值，上游本身分不出渠道；点击按
        UTC
        日期匹配，上游账单时区未确认，同日匹配不能证明订单来自本站，也不能由未匹配推断订单来自站外。
      </p>
    </>
  );
}

export function OrdersTable({
  rows,
  totals,
  asOf,
  source,
  req,
}: {
  rows: readonly BillRow[];
  /*
   * 【合计必须来自单独的全量查询，不能对 rows 求和】——rows 是合并后 LIMIT 200 之后的结果集。
   */
  totals: BillTotals;
  /** 版本的 as_of：来源状态是否过期按它判 */
  asOf: Date;
  /** meta.sources.bill；版本里没有时 undefined */
  source: SourceState | undefined;
  req: PickRequest;
}) {
  if (rows.length === 0)
    return (
      <Empty>
        当前没有已保存的订单明细。{sourceStatus(source, asOf)}
        ；这不证明账号历史订单为零。
      </Empty>
    );
  return (
    <>
      <SourceLine source={source} asOf={asOf} />
      <TotalTiles totals={totals} />
      <TableWrap>
        <table className="w-full min-w-[820px] border-collapse text-[13px]">
          <OrdersHead />
          <tbody>
            {rows.map((r) => (
              <OrderTr
                key={`${r.billDate}-${r.bookId}-${r.promotionType}`}
                r={r}
                req={req}
              />
            ))}
          </tbody>
        </table>
      </TableWrap>
      <Footnotes totals={totals} shown={rows.length} />
    </>
  );
}
