/**
 * Orders instead of money (P3-4 test #6, #7 with critique B8, ★U26, ★U27):
 * the ReelShort pill counts orders, the ledger is called 订单对账 in the rank
 * filters and the rank table, and the orders table shows counts only.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";

import { ReelshortPills } from "@/components/workspace/pick-board/cells";
import { OrdersTable } from "@/components/workspace/pick-board/orders-table";
import { RankFilters } from "@/components/workspace/pick-board/rank-filters";
import {
  RankTable,
  rankNote,
} from "@/components/workspace/pick-board/rank-table";
import { ReelshortDetailView } from "@/components/workspace/pick-board/reelshort-detail";
import { ReelshortTable } from "@/components/workspace/pick-board/reelshort-table";
import { RS_RANKS } from "@/core/pick-board/request";

import {
  AS_OF,
  billRow,
  billTotals,
  boardRules,
  observeRow,
  rankMeta,
  rankRow,
  reelshortDetail,
  request,
} from "./fixtures";
import { readableText } from "./support";

rs.mock("next/link", () => ({
  default: ({
    href,
    prefetch,
    children,
    ...rest
  }: {
    href: string;
    prefetch?: boolean;
    children: ReactNode;
  }) => (
    <a href={href} data-prefetch={String(prefetch)} {...rest}>
      {children}
    </a>
  ),
}));

afterEach(cleanup);

const rules = boardRules();

describe("the ReelShort order pill", () => {
  it("shows the order count, not an amount", () => {
    const { container } = render(
      <ReelshortPills rs={observeRow({ billOrders: 3 })} />,
    );
    const text = readableText(container);
    expect(text).toContain("订单 3 笔");
    expect(text).not.toMatch(/\$|USD|分成 /);
  });

  it("is absent when there are no orders", () => {
    const { container } = render(
      <ReelshortPills rs={observeRow({ billOrders: 0 })} />,
    );
    expect(container.textContent).not.toContain("订单");
  });
});

describe("the ledger is 订单对账 on the rank filters and the rank table", () => {
  it("rank filters label the ledger chip from the version rules", () => {
    const { container } = render(
      <RankFilters
        req={request({ tab: "rank", rk: "rs_ledger" })}
        meta={rankMeta()}
        rules={rules}
      />,
    );
    const text = readableText(container);
    expect(text).toContain("订单对账");
    expect(text).not.toContain("分成对账");
  });

  it("rank notes: ledger is 订单对账, bill ranks by bill_rank, none mention money", () => {
    const meta = rankMeta();
    expect(rankNote("rs_ledger", meta)).toContain("订单对账");
    expect(rankNote("rs_bill", meta)).toContain(
      "按 RealShort 导出的预估分成名次（bill_rank）排序，本页不显示金额",
    );
    for (const rank of RS_RANKS) {
      const note = rankNote(rank, meta);
      expect(note).not.toContain("分成对账");
      expect(note).not.toMatch(/\$|USD/);
    }
  });

  it("the rank table itself never says 分成对账", () => {
    const { container } = render(
      <RankTable
        rows={[rankRow()]}
        req={request({ tab: "rank" })}
        meta={rankMeta()}
        kind="kd"
        rules={rules}
      />,
    );
    expect(readableText(container)).not.toContain("分成对账");
  });

  it("rank chips and rs sort chips use the version's labels", () => {
    const renamed = boardRules((raw) => {
      raw.rsRankLabels.rs_pc = "ReelShort 推广者（新）";
      raw.sortLabels.d7 = "七天变化（新）";
      raw.basisLabels.kd = "KalosTV 日榜（新）";
    });
    const { container } = render(
      <RankFilters
        req={request({ tab: "rank", rk: "rs_growth" })}
        meta={rankMeta({ counts: { kd: 1, rs_pc: 2 } })}
        rules={renamed}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("推广者（新）");
    expect(text).toContain("七天变化（新）");
    expect(text).toContain("KalosTV 日榜（新）");
  });
});

describe("orders table", () => {
  it("lists date, drama, promotion type, orders and same-day clicks", () => {
    const { container } = render(
      <OrdersTable
        rows={[
          billRow(),
          billRow({
            bookId: "loose0001",
            canonicalId: null,
            title: "Loose Book",
            sameDayClicks: 0,
          }),
        ]}
        totals={billTotals()}
        asOf={AS_OF}
        source={undefined}
        req={request({ tab: "rank", rk: "rs_ledger" })}
      />,
    );
    const heads = Array.from(container.querySelectorAll("thead th")).map(
      (th) => th.textContent,
    );
    expect(heads).toEqual(["日期", "剧", "推广类型", "订单数", "同日站内出站"]);
    const [first, second] = Array.from(container.querySelectorAll("tbody tr"));
    expect(first?.querySelector("a")?.getAttribute("href")).toContain(
      "row=reelshort-demo0001",
    );
    expect(first?.textContent).toContain("有 · 4 次");
    expect(second?.querySelector("a")).toBeNull();
    expect(second?.textContent).toContain("Loose Book");
    expect(second?.textContent).toContain("—");
  });

  it("totals count rows, merged rows, orders and same-day matches", () => {
    const { container } = render(
      <OrdersTable
        rows={[billRow()]}
        totals={billTotals()}
        asOf={AS_OF}
        source={undefined}
        req={request()}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("原始账单行");
    expect(text).toContain("12");
    expect(text).toContain("合并后");
    expect(text).toContain("8");
    expect(text).toContain("订单数");
    expect(text).toContain("20");
    expect(text).toContain("合并行 3 / 原始行 5");
    expect(text).toContain(
      "bill_date DESC, order_cnt DESC, book_id, promotion_type",
    );
    expect(text).toMatch(/上线日期/);
  });

  it("warns about the 200-row cut only when merged rows outnumber the rows shown", () => {
    const whole = render(
      <OrdersTable
        rows={[billRow()]}
        totals={billTotals({ rows: 12, mergedRows: 1 })}
        asOf={AS_OF}
        source={undefined}
        req={request()}
      />,
    );
    expect(whole.container.textContent).not.toContain("只列了最近");
    cleanup();
    const cut = render(
      <OrdersTable
        rows={[billRow()]}
        totals={billTotals({ rows: 12, mergedRows: 8 })}
        asOf={AS_OF}
        source={undefined}
        req={request()}
      />,
    );
    expect(cut.container.textContent).toContain(
      "合并后共 8 行有订单，这里只列了最近 1 行",
    );
  });

  it("an empty ledger explains itself against the version time", () => {
    const { container } = render(
      <OrdersTable
        rows={[]}
        totals={billTotals({ rows: 0, mergedRows: 0, orders: 0 })}
        asOf={AS_OF}
        source={{
          source: "bill",
          status: "success",
          attemptedAt: "2026-09-24T03:00:00Z",
          completedAt: "2026-09-24T03:10:00Z",
          details: {},
        }}
        req={request()}
      />,
    );
    expect(container.textContent).toContain("最近采集完成");
  });
});

describe("ReelShort tables and detail keep orders and drop money", () => {
  it("the ReelShort table has no money column group and an orders column", () => {
    const { container } = render(
      <ReelshortTable rows={[observeRow()]} asOf={AS_OF} req={request()} />,
    );
    const text = readableText(container);
    expect(text).not.toContain("我方分成");
    expect(text).not.toMatch(/\$|USD/);
    const heads = Array.from(
      container.querySelectorAll("thead tr:last-child th"),
    ).map((th) => th.textContent);
    expect(heads).toContain("订单");
    expect(container.querySelectorAll("thead tr:first-child th")).toHaveLength(
      4,
    );
  });

  it("the two header rows span the same columns as a body row", () => {
    for (const candidates of [false, true]) {
      const { container } = render(
        <ReelshortTable
          rows={[observeRow()]}
          asOf={AS_OF}
          req={request()}
          candidates={candidates}
        />,
      );
      const [groups, heads] = Array.from(
        container.querySelectorAll("thead tr"),
      );
      const spanned = Array.from(groups?.querySelectorAll("th") ?? []).reduce(
        (sum, th) => sum + Number(th.getAttribute("colspan") ?? "1"),
        0,
      );
      const columns = heads?.querySelectorAll("th").length ?? 0;
      expect(spanned).toBe(columns);
      expect(
        container.querySelectorAll("tbody tr:first-child td"),
      ).toHaveLength(columns);
      cleanup();
    }
  });

  it("the id line under a title is dim and shows its hint as help", () => {
    const { container } = render(
      <ReelshortTable rows={[observeRow()]} asOf={AS_OF} req={request()} />,
    );
    const idLine = Array.from(
      container.querySelectorAll("tbody td div[title]"),
    ).find((div) => div.textContent === "en · demo0001");
    expect(idLine?.getAttribute("title")).toContain("当前采集");
    expect(idLine?.classList.contains("text-ink-dim")).toBe(true);
    expect(idLine?.classList.contains("cursor-help")).toBe(true);
  });

  it("the detail lists orders with merged source rows and marks truncation", () => {
    const { container } = render(
      <ReelshortDetailView
        detail={reelshortDetail({ billTruncated: true })}
        req={request()}
        requestedId="demo0002"
        asOf={AS_OF}
        rules={rules}
      />,
    );
    const text = readableText(container);
    expect(text).toContain("订单明细（最近 50 行）");
    expect(text).toContain("合并的原始行");
    expect(text).toContain("更早的订单日没有列出");
    expect(text).not.toMatch(/\$|USD|预估 USD|推广标识/);
    expect(text).toContain(
      "已定位到同组同语种正典资源；查询的原始 ID：demo0002",
    );
  });
});
