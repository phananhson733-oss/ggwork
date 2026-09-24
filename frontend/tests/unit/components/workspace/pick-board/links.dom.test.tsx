/**
 * Links and forms stay on this board and on the version being read (P3-4
 * test #3, #4, with critique B9 and U10 / ★38): every internal link is
 * /workspace/pick-data with v, every other link is https, next/link never
 * prefetches, and the three GET forms carry v.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";

import { AccountsTable } from "@/components/workspace/pick-board/accounts-table";
import { OrdersTable } from "@/components/workspace/pick-board/orders-table";
import { PostsTable } from "@/components/workspace/pick-board/posted-record";
import {
  PostedFilters,
  PostedNote,
  PostedTable,
} from "@/components/workspace/pick-board/posted-table";
import { RankFilters } from "@/components/workspace/pick-board/rank-filters";
import { RankTable } from "@/components/workspace/pick-board/rank-table";
import { ReelshortDetailView } from "@/components/workspace/pick-board/reelshort-detail";
import { ReelshortTable } from "@/components/workspace/pick-board/reelshort-table";
import { RowDetailView } from "@/components/workspace/pick-board/row-detail";
import { RowsTable } from "@/components/workspace/pick-board/rows-table";
import { RulesTab } from "@/components/workspace/pick-board/rules-tab";
import {
  Filters,
  OutOfRange,
  Pager,
  Tabs,
} from "@/components/workspace/pick-board/toolbar";

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
} from "./fixtures";
import { PINNED_BOARD_LINK, hrefs } from "./support";

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
    <a href={href} data-next-link="" data-prefetch={String(prefetch)} {...rest}>
      {children}
    </a>
  ),
}));

afterEach(cleanup);

const rules = boardRules();
const FACETS = {
  platforms: { kalos: 3, reelshort: 2 },
  langs: [{ lang: "英语", n: 5 }],
  bases: { kd: 2 },
  posted: { no: 1, yes: 2, pool: 3 },
};

function everything(): ReactNode {
  const req = request();
  const meta = rankMeta();
  return (
    <>
      <Tabs req={request({ tab: "row", row: "kalos-demo-1" })} counts={{}} />
      <Filters req={req} facets={FACETS} rules={rules} />
      <RowsTable
        rows={[pickRow(), reelshortPickRow()]}
        req={req}
        rules={rules}
      />
      <Pager req={request({ page: "2" })} hasMore count={1} total={500} />
      <OutOfRange req={request({ page: "9" })} total={120} />
      <RankFilters
        req={request({ tab: "rank", rk: "kd" })}
        meta={meta}
        rules={rules}
      />
      <RankTable
        rows={[rankRow()]}
        req={req}
        meta={meta}
        kind="kd"
        rules={rules}
      />
      <RowDetailView detail={rowDetail()} req={req} rules={rules} />
      <ReelshortDetailView
        detail={reelshortDetail()}
        req={req}
        requestedId="demo0001"
        asOf={AS_OF}
        rules={rules}
      />
      <ReelshortTable rows={[observeRow()]} asOf={AS_OF} req={req} />
      <OrdersTable
        rows={[billRow(), billRow({ bookId: "loose0001", canonicalId: null })]}
        totals={billTotals()}
        asOf={AS_OF}
        source={undefined}
        req={req}
      />
      <PostedFilters
        req={request({ tab: "posted" })}
        counts={{ "": 3, pub: 1, sched: 1, none: 1, nomatch: 0 }}
      />
      <PostedTable rows={[postedRecord()]} links={postedLinks()} req={req} />
      <AccountsTable accounts={accounts()} />
      <RulesTab rules={rules} />
    </>
  );
}

describe("links stay on the pinned version or leave over https", () => {
  it("every href is a pinned board link or an https link", () => {
    const { container } = render(everything());
    const all = hrefs(container);
    expect(all.length).toBeGreaterThan(40);
    for (const href of all)
      expect(PINNED_BOARD_LINK.test(href) || href.startsWith("https://")).toBe(
        true,
      );
  });

  it("next/link is only used for board links and never prefetches", () => {
    const { container } = render(everything());
    const links = Array.from(container.querySelectorAll("a[data-next-link]"));
    expect(links.length).toBeGreaterThan(20);
    for (const a of links) {
      expect(a.getAttribute("data-prefetch")).toBe("false");
      expect(a.getAttribute("href")).toMatch(PINNED_BOARD_LINK);
    }
  });

  it("links out of the board open in a new tab without an opener", () => {
    const { container } = render(everything());
    const external = Array.from(container.querySelectorAll("a")).filter((a) =>
      (a.getAttribute("href") ?? "").startsWith("https://"),
    );
    expect(external.length).toBeGreaterThan(5);
    for (const a of external) {
      expect(a.getAttribute("target")).toBe("_blank");
      expect(a.getAttribute("rel")).toContain("noopener");
      expect(a.hasAttribute("data-next-link")).toBe(false);
    }
  });

  it("public drama pages are absolute ReelShort addresses", () => {
    const { container } = render(
      <ReelshortTable rows={[observeRow()]} asOf={AS_OF} req={request()} />,
    );
    const page = container.querySelector(
      'a[href="https://dramashortstv.com/en/drama/demo-heir"]',
    );
    expect(page?.getAttribute("rel")).toBe("noopener");
    expect(page?.getAttribute("target")).toBe("_blank");
  });
});

describe("urls from the data are links only when they are https", () => {
  it("account urls: https is a link, anything else is text", () => {
    const { container } = render(<AccountsTable accounts={accounts()} />);
    const rows = Array.from(container.querySelectorAll("tbody tr"));
    expect(rows[0]?.querySelector("a")?.getAttribute("href")).toBe(
      "https://social.example.com/@demo",
    );
    expect(rows[1]?.querySelector("a")).toBeNull();
    expect(rows[1]?.textContent).toContain("Scrubbed Channel");
    expect(rows[2]?.querySelector("a")).toBeNull();
    expect(rows[2]?.textContent).toContain("Plain Channel");
  });

  it("post urls: https is a link, a scrubbed or http url is plain text", () => {
    const { container } = render(<PostsTable posts={postedRecord().posts} />);
    const cells = Array.from(container.querySelectorAll("tbody tr")).map(
      (tr) => tr.lastElementChild,
    );
    expect(cells[0]?.querySelector("a")?.getAttribute("href")).toBe(
      "https://video.example.com/p/1",
    );
    expect(cells[1]?.querySelector("a")).toBeNull();
    expect(cells[1]?.textContent).toBe("[网盘信息已移除]");
    expect(cells[2]?.querySelector("a")).toBeNull();
    expect(cells[2]?.textContent).toBe("http://video.example.com/p/3");
  });

  it("the posted pool link follows the version rules and disappears when empty", () => {
    const stats = {
      total: 1,
      pubCount: 1,
      postsSum: 1,
      viewsSum: 1,
      metricAt: null,
      importedAt: null,
      accountCount: 1,
    };
    const withPool = render(
      <PostedNote rules={rules} stats={stats} shown={1} />,
    );
    expect(
      withPool.container.querySelector(`a[href="${rules.postedPoolUrl}"]`),
    ).not.toBeNull();
    cleanup();
    const empty = boardRules((raw) => {
      raw.postedPoolUrl = "javascript:alert(1)";
    });
    const without = render(
      <PostedNote rules={empty} stats={stats} shown={1} />,
    );
    expect(without.container.querySelector("a")).toBeNull();
    expect(without.container.textContent).toContain("选剧池");
  });
});

describe("the three GET forms submit to the board with v", () => {
  it("search, earlier-period and posted-search forms carry the version", () => {
    const { container } = render(
      <>
        <Filters req={request()} facets={FACETS} rules={rules} />
        <RankFilters
          req={request({ tab: "rank", rk: "kd" })}
          meta={rankMeta()}
          rules={rules}
        />
        <PostedFilters
          req={request({ tab: "posted" })}
          counts={{ "": 3, pub: 1, sched: 1, none: 1, nomatch: 0 }}
        />
      </>,
    );
    const forms = Array.from(container.querySelectorAll("form"));
    expect(forms).toHaveLength(3);
    for (const form of forms) {
      expect(form.getAttribute("action")).toBe("/workspace/pick-data");
      expect(form.getAttribute("method")).toBe("get");
      const v = form.querySelectorAll('input[type="hidden"][name="v"]');
      expect(v).toHaveLength(1);
      expect(v[0]?.getAttribute("value")).toBe("7");
    }
  });

  it("forms leave v out when the request has none", () => {
    const { container } = render(
      <Filters req={{ ...request(), v: null }} facets={FACETS} rules={rules} />,
    );
    expect(container.querySelector('input[name="v"]')).toBeNull();
  });
});

describe("tabs", () => {
  it("adds the sync and imports tab and drops the replay result from tab links", () => {
    const { container } = render(
      <Tabs
        req={request({ result: "0123456789abcdef0123456789abcdef" })}
        counts={{ pick: 3 }}
      />,
    );
    const imports = Array.from(container.querySelectorAll("a")).find(
      (a) => a.textContent === "同步与导入",
    );
    expect(imports?.getAttribute("href")).toBe(
      "/workspace/pick-data?tab=imports&v=7",
    );
    for (const href of hrefs(container)) expect(href).not.toContain("result=");
  });
});
