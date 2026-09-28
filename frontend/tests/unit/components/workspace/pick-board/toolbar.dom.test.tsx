/**
 * The toolbar's rewritten pieces (review of P3-4): the other-theaters toggle
 * now reads the version's inUse, the search form carries every non-default
 * filter as a hidden field, and the search word stays behind when a tab
 * searches another table.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";

import { Filters, Tabs } from "@/components/workspace/pick-board/toolbar";

import { boardRules, request } from "./fixtures";

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
/* inUse in the fixture: reelshort, dramabox, shortmax, flickreels, flareflow */
const FACETS = {
  platforms: { reelshort: 1, shortmax: 2, kalos: 3, goodshort: 4 },
  langs: [],
  bases: {},
  posted: { no: 0, yes: 0, pool: 0 },
};

function renderFilters(params: Record<string, string>): HTMLElement {
  return render(<Filters req={request(params)} facets={FACETS} rules={rules} />)
    .container;
}

function linkByText(root: Element, text: RegExp): HTMLAnchorElement | null {
  return (
    Array.from(root.querySelectorAll("a")).find((a) =>
      text.test(a.textContent ?? ""),
    ) ?? null
  );
}

function query(href: string | null | undefined): URLSearchParams {
  return new URLSearchParams((href ?? "").split("?")[1] ?? "");
}

describe("the other-theaters toggle", () => {
  it("expanded by default: the other theaters show and the toggle collapses them", () => {
    const root = renderFilters({});
    expect(linkByText(root, /^KalosTV/)).not.toBeNull();
    const toggle = linkByText(root, /^收起其他剧场/);
    expect(toggle).not.toBeNull();
    expect(query(toggle?.getAttribute("href")).get("inuse")).toBe("1");
  });

  it("collapsed: the other theaters hide behind one chip that counts them and expands", () => {
    const root = renderFilters({ inuse: "1" });
    expect(linkByText(root, /^KalosTV/)).toBeNull();
    expect(linkByText(root, /^ShortMax/)).not.toBeNull();
    const toggle = linkByText(root, /^其他剧场 5 个/);
    expect(toggle?.textContent).toBe("其他剧场 5 个7");
    expect(query(toggle?.getAttribute("href")).has("inuse")).toBe(false);
  });

  it("collapsing while another theater is picked clears the pick", () => {
    const cases: Record<string, string>[] = [
      { platform: "kalos" },
      { platform: "kalos", inuse: "1" },
    ];
    for (const params of cases) {
      const root = renderFilters(params);
      expect(linkByText(root, /^KalosTV/)).not.toBeNull();
      const href = linkByText(root, /^收起其他剧场/)?.getAttribute("href");
      expect(query(href).get("inuse")).toBe("1");
      expect(query(href).has("platform")).toBe(false);
      cleanup();
    }
  });

  it("collapsing keeps an in-use theater pick", () => {
    const root = renderFilters({ platform: "shortmax" });
    const href = linkByText(root, /^收起其他剧场/)?.getAttribute("href");
    expect(query(href).get("platform")).toBe("shortmax");
  });

  it("the in-use list comes from the version", () => {
    const root = render(
      <Filters
        req={request({ inuse: "1" })}
        facets={FACETS}
        rules={boardRules((raw) => {
          raw.inUse = ["kalos"];
        })}
      />,
    ).container;
    expect(linkByText(root, /^KalosTV/)).not.toBeNull();
    expect(linkByText(root, /^ShortMax/)).toBeNull();
    expect(linkByText(root, /^其他剧场 9 个/)).not.toBeNull();
  });
});

describe("the search form", () => {
  it("carries every non-default filter and the version as hidden fields", () => {
    const params = {
      tab: "all",
      sort: "title",
      platform: "kalos",
      lang: "英语",
      basis: "kd",
      posted: "yes",
      off: "1",
      sig: "1",
      w: "1",
      yt: "1",
      dated: "1",
      inuse: "1",
    };
    const form = renderFilters(params).querySelector("form");
    const hidden = Array.from(
      form?.querySelectorAll('input[type="hidden"]') ?? [],
    ).map((input) => [input.getAttribute("name"), input.getAttribute("value")]);
    expect(Object.fromEntries(hidden)).toEqual({ ...params, v: "7" });
    expect(hidden).toHaveLength(Object.keys(params).length + 1);
  });

  it("carries only the version when every filter is at its default", () => {
    const form = renderFilters({}).querySelector("form");
    const names = Array.from(
      form?.querySelectorAll('input[type="hidden"]') ?? [],
    ).map((input) => input.getAttribute("name"));
    expect(names).toEqual(["v"]);
  });
});

describe("the search word across tabs", () => {
  function tabHref(root: Element, label: string): URLSearchParams {
    const link = Array.from(root.querySelectorAll("a")).find(
      (a) => a.textContent === label,
    );
    return query(link?.getAttribute("href"));
  }

  it("stays on the catalog tabs and is dropped where another table is searched", () => {
    const root = render(<Tabs req={request({ q: "bride" })} />).container;
    expect(tabHref(root, "选剧").get("q")).toBe("bride");
    expect(tabHref(root, "全部剧库").get("q")).toBe("bride");
    expect(tabHref(root, "榜单").has("q")).toBe(false);
    expect(tabHref(root, "发布记录").has("q")).toBe(false);
    expect(tabHref(root, "剧场规则").has("q")).toBe(false);
    expect(tabHref(root, "同步与导入").has("q")).toBe(false);
  });
});
