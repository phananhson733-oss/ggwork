/**
 * The four RealShort /qa findings of 2026-09-11 (rs:tests/pick-qa.regression-2)
 * checked on the rendered components (P3-4 test #8): out-of-range pages give
 * a way back, every header cell has a scope, and the "earlier" select must be
 * chosen before submitting. The source-shape half of those checks lives in
 * tests/unit/core/pick-board/qa.regression-2.test.ts.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";

import { AccountsTable } from "@/components/workspace/pick-board/accounts-table";
import { OrdersTable } from "@/components/workspace/pick-board/orders-table";
import { PostedTable } from "@/components/workspace/pick-board/posted-table";
import { RankFilters } from "@/components/workspace/pick-board/rank-filters";
import { RankTable } from "@/components/workspace/pick-board/rank-table";
import { ReelshortDetailView } from "@/components/workspace/pick-board/reelshort-detail";
import { ReelshortTable } from "@/components/workspace/pick-board/reelshort-table";
import { RowDetailView } from "@/components/workspace/pick-board/row-detail";
import { RowsTable } from "@/components/workspace/pick-board/rows-table";
import { RulesTab } from "@/components/workspace/pick-board/rules-tab";
import { OutOfRange, Pager } from "@/components/workspace/pick-board/toolbar";

import {
  AS_OF,
  accounts,
  billRow,
  billTotals,
  boardRules,
  observeRow,
  pickRow,
  postedLinks,
  postedRecord,
  rankMeta,
  rankRow,
  reelshortDetail,
  request,
  rowDetail,
  signal,
} from "./fixtures";

rs.mock("next/navigation", () => ({
  usePathname: () => "/workspace/pick-data",
  useSearchParams: () => new URLSearchParams("v=7&tab=rows"),
}));

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

describe("ISSUE-003: a page past the end offers the first and the last page", () => {
  it("links to page 1 and to the last page, pinned to the version", () => {
    const { container } = render(
      <OutOfRange req={request({ page: "9" })} total={120} />,
    );
    expect(container.textContent).toContain("第 9 页不存在");
    const links = Array.from(container.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(links).toEqual([
      "/workspace/pick-data?v=7",
      "/workspace/pick-data?page=3&v=7",
    ]);
  });

  it("page size links go back to page 1", () => {
    const { container } = render(
      <Pager req={request({ page: "5" })} hasMore count={50} total={400} />,
    );
    const sizes = Array.from(container.querySelectorAll("a"))
      .filter((a) => /^(20|100|200)$/.test(a.textContent ?? ""))
      .map((a) => a.getAttribute("href") ?? "");
    expect(sizes).toHaveLength(3);
    for (const href of sizes) expect(href).not.toContain("page=");
  });
});

describe("ISSUE-005: every header cell has a scope", () => {
  it("col for column heads, colgroup for group heads", () => {
    const req = request();
    const meta = rankMeta();
    const { container } = render(
      <>
        <RowsTable rows={[pickRow()]} req={req} rules={rules} />
        {(["kd", "kw", "sm", "gn"] as const).map((kind) => (
          <RankTable
            key={kind}
            rows={[rankRow({ signal: signal({ kind }) })]}
            req={req}
            meta={meta}
            kind={kind}
            rules={rules}
          />
        ))}
        <ReelshortTable
          rows={[observeRow()]}
          asOf={AS_OF}
          req={req}
          candidates
        />
        <OrdersTable
          rows={[billRow()]}
          totals={billTotals()}
          asOf={AS_OF}
          source={undefined}
          req={req}
        />
        <RowDetailView detail={rowDetail()} req={req} rules={rules} />
        <ReelshortDetailView
          detail={reelshortDetail()}
          req={req}
          requestedId="demo0001"
          asOf={AS_OF}
          rules={rules}
        />
        <PostedTable rows={[postedRecord()]} links={postedLinks()} req={req} />
        <AccountsTable accounts={accounts()} />
        <RulesTab rules={rules} />
      </>,
    );
    const heads = Array.from(container.querySelectorAll("th"));
    expect(heads.length).toBeGreaterThanOrEqual(30);
    for (const th of heads)
      expect(["col", "colgroup"]).toContain(th.getAttribute("scope"));
  });
});

describe("ISSUE-006: the earlier-period select is required", () => {
  it("day select: required, with an empty placeholder first", () => {
    const { container } = render(
      <RankFilters
        req={request({ tab: "rank", rk: "kd" })}
        meta={rankMeta()}
        rules={rules}
      />,
    );
    const select = container.querySelector("select");
    expect(select?.required).toBe(true);
    const first = select?.querySelector("option");
    expect(first?.getAttribute("value")).toBe("");
    expect(first?.textContent).toBe("更早…");
  });
});
