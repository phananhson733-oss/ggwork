/**
 * Theater rules come from the version being read, never from a static copy
 * (P3-4 test #5): change a rule in the version and every place that shows it
 * changes with it.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import { Glossary } from "@/components/workspace/pick-board/glossary";
import { ReelshortDetailView } from "@/components/workspace/pick-board/reelshort-detail";
import { RowDetailView } from "@/components/workspace/pick-board/row-detail";
import { RowsTable } from "@/components/workspace/pick-board/rows-table";
import { RulesTab } from "@/components/workspace/pick-board/rules-tab";
import { Filters } from "@/components/workspace/pick-board/toolbar";

import {
  AS_OF,
  boardRules,
  pickRow,
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
    <a href={href} data-next-link="" data-prefetch={String(prefetch)} {...rest}>
      {children}
    </a>
  ),
}));

afterEach(cleanup);

const shortmaxRow = pickRow({
  rowKey: "shortmax-demo-2",
  platform: "shortmax",
  signals: [signal({ kind: "sm", grade: "SS", rank: null })],
});

const blocked = boardRules((raw) => {
  raw.platformRules.shortmax.yt = "no";
  raw.platformRules.shortmax.report = "改过的报备说明";
  raw.platformRules.shortmax.unban = "改过的解禁通道";
  raw.platformRules.shortmax.ytNote = "改过的 YouTube 说明";
});

function lastCell(container: HTMLElement): string {
  return (
    container.querySelector("tbody tr")?.lastElementChild?.textContent ?? ""
  );
}

describe("YouTube, report and unban follow the version", () => {
  it("rows table resource cell reads the version's YouTube rule", () => {
    const ok = render(
      <RowsTable rows={[shortmaxRow]} req={request()} rules={boardRules()} />,
    );
    expect(lastCell(ok.container)).toContain("YouTube 可发");
    cleanup();
    const no = render(
      <RowsTable rows={[shortmaxRow]} req={request()} rules={blocked} />,
    );
    expect(lastCell(no.container)).toContain("禁 YouTube");
    expect(lastCell(no.container)).not.toContain("YouTube 可发");
  });

  it("row detail facts read the version's rule", () => {
    const { container } = render(
      <RowDetailView
        detail={rowDetail({ row: shortmaxRow })}
        req={request()}
        rules={blocked}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("禁 YouTube");
    expect(text).toContain("改过的报备说明");
    expect(text).toContain("改过的解禁通道");
    expect(text).toContain("改过的 YouTube 说明");
  });

  it("a theater the version has no rule for shows the rule as unknown", () => {
    const missing = boardRules((raw) => {
      raw.platformRules = Object.fromEntries(
        Object.entries(raw.platformRules).filter(([key]) => key !== "shortmax"),
      ) as typeof raw.platformRules;
    });
    const { container } = render(
      <RowDetailView
        detail={rowDetail({ row: shortmaxRow })}
        req={request()}
        rules={missing}
      />,
    );
    expect(container.textContent).toContain("规则未知");
  });

  it("labels use the version's youtube and basis wording", () => {
    const renamed = boardRules((raw) => {
      raw.youtubeLabels.ok = "油管可发";
      raw.basisLabels.sm = "ShortMax 分级（新）";
    });
    const { container } = render(
      <RowsTable rows={[shortmaxRow]} req={request()} rules={renamed} />,
    );
    expect(lastCell(container)).toContain("油管可发");
    expect(
      container.querySelector('[title^="ShortMax 分级（新）"]'),
    ).not.toBeNull();
  });
});

describe("ReelShort detail reads the reelshort rule of the version", () => {
  it("material and settlement come from rules.platformRules.reelshort", () => {
    const edited = boardRules((raw) => {
      raw.platformRules.reelshort.material = "版本里的素材说明";
      raw.platformRules.reelshort.back = "版本里的结算说明";
    });
    const { container } = render(
      <ReelshortDetailView
        detail={reelshortDetail()}
        req={request()}
        requestedId="demo0001"
        asOf={AS_OF}
        rules={edited}
      />,
    );
    expect(container.textContent).toContain(
      "版本里的素材说明 · 版本里的结算说明",
    );
  });
});

describe("rules tab", () => {
  it("lists in-use theaters first, then the rest, including unknown ones", () => {
    const withNew = boardRules((raw) => {
      raw.platformRules = {
        ...raw.platformRules,
        newshort: {
          ...raw.platformRules.touchshort,
          key: "newshort",
          name: "NewShort",
        },
      };
    });
    const { container } = render(<RulesTab rules={withNew} />);
    const names = Array.from(
      container.querySelectorAll("tbody tr td:first-child > span:first-child"),
    ).map((s) => s.textContent);
    expect(names.slice(0, 5)).toEqual([
      "ReelShort",
      "DramaBox",
      "ShortMax",
      "FlickReels",
      "flareflow",
    ]);
    expect(names).toContain("NewShort");
    expect(names).toHaveLength(11);
    expect(container.textContent).toContain("本页不认识的剧场");
  });

  it("an internal doc is a board link, an https doc opens a new tab, an unsafe doc has no link", () => {
    const edited = boardRules((raw) => {
      raw.platformRules.touchshort.doc = "javascript:alert(1)";
    });
    const { container } = render(<RulesTab rules={edited} />);
    const reelshort = container.querySelector(
      'a[href^="/workspace/pick-data"]',
    );
    expect(reelshort?.getAttribute("href")).toBe(
      "/workspace/pick-data?tab=rank&rk=rs_rr&v=7",
    );
    expect(reelshort?.hasAttribute("data-next-link")).toBe(true);
    const external = container.querySelector(
      'a[href="https://example.feishu.cn/wiki/placeholder1"]',
    );
    expect(external?.getAttribute("target")).toBe("_blank");
    const rows = Array.from(container.querySelectorAll("tbody tr"));
    const touch = rows.find((r) => r.textContent?.includes("TouchShort"));
    expect(touch?.querySelectorAll("a")).toHaveLength(0);
  });

  it("column hints come from the version", () => {
    const edited = boardRules((raw) => {
      raw.ruleHints.结算 = "版本里的结算提示";
    });
    const { container } = render(<RulesTab rules={edited} />);
    expect(
      container.querySelector('[title="版本里的结算提示"]'),
    ).not.toBeNull();
  });
});

describe("filters", () => {
  it("in-use platform chips follow the version's inUse", () => {
    const reordered = boardRules((raw) => {
      raw.inUse = ["shortmax", "kalos"];
    });
    const { container } = render(
      <Filters
        req={request()}
        facets={{
          platforms: {},
          langs: [],
          bases: {},
          posted: { no: 0, yes: 0, pool: 0 },
        }}
        rules={reordered}
      />,
    );
    const chips = Array.from(container.querySelectorAll("a"))
      .map((a) => a.textContent ?? "")
      .filter((t) => /^(全部|ShortMax|KalosTV|ReelShort)/.test(t));
    expect(chips.slice(0, 3).map((t) => t.replace(/\d+$/, ""))).toEqual([
      "全部",
      "ShortMax",
      "KalosTV",
    ]);
  });
});

describe("glossary", () => {
  it("renders the groups it is given and filters them", () => {
    render(
      <Glossary
        groups={[
          {
            g: "第一组",
            d: "说明",
            items: [
              { t: "报备", d: "登记账号" },
              { t: "出站", d: "点去 App" },
            ],
          },
        ]}
      />,
    );
    expect(screen.getByText("报备")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "出站" },
    });
    expect(screen.queryByText("报备")).toBeNull();
    expect(screen.getByText("出站")).toBeTruthy();
  });
});
