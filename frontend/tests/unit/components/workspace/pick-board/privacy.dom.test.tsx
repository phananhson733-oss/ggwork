/**
 * What the board may never show (P3-4 test #1, #2 with critique B8): no pan
 * link, no extraction code, no money. The version's glossary describes the
 * old RealShort page and keeps its original wording (U9), so its subtree is
 * only checked for link targets, not for words.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";

import { AccountsTable } from "@/components/workspace/pick-board/accounts-table";
import { OrdersTable } from "@/components/workspace/pick-board/orders-table";
import {
  PostedNote,
  PostedTable,
} from "@/components/workspace/pick-board/posted-table";
import { RankTable } from "@/components/workspace/pick-board/rank-table";
import { ReelshortDetailView } from "@/components/workspace/pick-board/reelshort-detail";
import { ReelshortTable } from "@/components/workspace/pick-board/reelshort-table";
import { RowDetailView } from "@/components/workspace/pick-board/row-detail";
import { RowsTable } from "@/components/workspace/pick-board/rows-table";
import { RulesTab } from "@/components/workspace/pick-board/rules-tab";

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
  reelshortPickRow,
  request,
  rowDetail,
  signal,
} from "./fixtures";
import {
  GLOSSARY_SELECTOR,
  hrefs,
  panDomainPattern,
  readableText,
  withoutGlossary,
} from "./support";

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
const MONEY = /\$|USD/;

function board(): ReactNode {
  const req = request();
  const meta = rankMeta();
  const rows = [pickRow(), pickRow({ rowKey: "kalos-demo-3", hasPan: false })];
  return (
    <>
      <RowsTable rows={[...rows, reelshortPickRow()]} req={req} rules={rules} />
      {(["kd", "kw", "sm", "fh", "dbn"] as const).map((kind) => (
        <RankTable
          key={kind}
          rows={[rankRow({ signal: signal({ kind }) })]}
          req={request({ tab: "rank", rk: kind })}
          meta={meta}
          kind={kind}
          rules={rules}
        />
      ))}
      <RowDetailView detail={rowDetail()} req={req} rules={rules} />
      <ReelshortDetailView
        detail={reelshortDetail()}
        req={req}
        requestedId="demo0002"
        asOf={AS_OF}
        rules={rules}
      />
      <ReelshortTable rows={[observeRow()]} asOf={AS_OF} req={req} />
      <ReelshortTable rows={[observeRow()]} asOf={AS_OF} req={req} candidates />
      <OrdersTable
        rows={[
          billRow(),
          billRow({ bookId: "loose0001", canonicalId: null, sameDayClicks: 0 }),
        ]}
        totals={billTotals()}
        asOf={AS_OF}
        source={undefined}
        req={req}
      />
      <PostedTable rows={[postedRecord()]} links={postedLinks()} req={req} />
      <AccountsTable accounts={accounts()} />
    </>
  );
}

describe("nothing on the board links to a pan or shows a code or an amount", () => {
  it("renders no money, no extraction code and no pan link outside the glossary", () => {
    const { container } = render(
      <>
        {board()}
        <RulesTab rules={rules} />
      </>,
    );
    const text = readableText(withoutGlossary(container));
    expect(text).not.toMatch(MONEY);
    expect(text).not.toContain("提取码");
    expect(container.querySelector(GLOSSARY_SELECTOR)).not.toBeNull();
    const pan = panDomainPattern();
    expect(text).not.toMatch(pan);
    for (const href of hrefs(container)) expect(href).not.toMatch(pan);
  });

  it("keeps the glossary's own words but still only links out over https", () => {
    const { container } = render(<RulesTab rules={rules} />);
    const glossary = container.querySelector(GLOSSARY_SELECTOR);
    expect(glossary?.textContent).toContain("提取码");
    for (const href of hrefs(container))
      expect(href.startsWith("/") || href.startsWith("https://")).toBe(true);
  });

  it("says why pan links are gone above the glossary", () => {
    const { container } = render(<RulesTab rules={rules} />);
    const glossary = container.querySelector(GLOSSARY_SELECTOR);
    expect(glossary?.textContent).toContain(
      "以下词条来自 RealShort 选剧台；本页不显示网盘链接、提取码和分成金额，「分成对账」在本页叫「订单对账」",
    );
  });

  it("keeps the posted-pool note free of money", () => {
    const { container } = render(
      <PostedNote
        rules={rules}
        shown={3}
        stats={{
          total: 3,
          pubCount: 2,
          postsSum: 4,
          viewsSum: 100,
          metricAt: "2026-09-21",
          importedAt: AS_OF,
          accountCount: 2,
        }}
      />,
    );
    expect(readableText(container)).not.toMatch(MONEY);
  });
});

describe("the pan cell says whether a pan exists, never where", () => {
  it("rows table: has pan points to RealShort, no pan says so", () => {
    const { container } = render(
      <RowsTable
        rows={[pickRow(), pickRow({ rowKey: "kalos-demo-3", hasPan: false })]}
        req={request()}
        rules={rules}
      />,
    );
    const cells = Array.from(container.querySelectorAll("tbody tr")).map(
      (tr) => tr.lastElementChild?.textContent ?? "",
    );
    expect(cells[0]).toContain("有网盘");
    expect(cells[0]).toContain("到 RealShort 证据页查看");
    expect(cells[1]).toContain("无网盘");
    expect(cells[1]).not.toContain("到 RealShort 证据页查看");
    const link = container.querySelector(
      'a[href^="https://dramashortstv.com/admin/pick?tab=row&row="]',
    );
    expect(link?.getAttribute("href")).toBe(
      "https://dramashortstv.com/admin/pick?tab=row&row=kalos-demo-1",
    );
    expect(link?.getAttribute("target")).toBe("_blank");
  });

  it("row detail: the material fact follows hasPan", () => {
    const withPan = render(
      <RowDetailView detail={rowDetail()} req={request()} rules={rules} />,
    );
    expect(withPan.container.textContent).toContain(
      "有网盘（网盘信息不同步到本页，到 RealShort 证据页查看）",
    );
    cleanup();
    const without = render(
      <RowDetailView
        detail={rowDetail({ row: pickRow({ hasPan: false }) })}
        req={request()}
        rules={rules}
      />,
    );
    expect(without.container.textContent).toContain("这一行剧单没附网盘");
    expect(without.container.textContent).not.toContain("有网盘");
  });
});
