/**
 * The simplified radar's table (simplified scope 2026-09-30, sections 2 and 4): one row per drama on the night's task
 * list, the header's counts, the two sorts, the empty states, and a row the night did not get showing "这晚未查到"
 * with no curve. The rows are the backend's fixture (a real stable night) with controlled series where a label is
 * checked.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";

import {
  TrendsTableUnavailable,
  TrendsTableView,
} from "@/components/workspace/pick-board/views/trends-table-view";
import { forbiddenIn } from "@/core/pick/obs-format";
import type {
  TrendsTable,
  TrendsTableRow,
} from "@/core/pick/trends-table-schema";
import { trendsTableSchema } from "@/core/pick/trends-table-schema";
import { parsePickRequest } from "@/core/pick-board/request";

import fixture from "../../../../core/pick/fixtures/backend-trends-table.json";

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

const BASE: TrendsTable = trendsTableSchema.parse(fixture);
const WINDOW_END = "2026-09-25T00:00:00.000000+00:00";

it("shows stored recovery progress without counting an unfinished night", () => {
  if (!BASE.batch) throw new Error("fixture requires batch");
  render(
    <TrendsTableView
      req={parsePickRequest({ tab: "trends" })}
      table={{
        ...BASE,
        batch: {
          ...BASE.batch,
          daily_recovery: {
            since: "2026-09-25",
            target: 30,
            qualified_nights: 1,
            qualified: null,
          },
        },
      }}
    />,
  );
  expect(
    screen.getByText(/恢复验证：本晚目标 30 部，已通过 1\/3 个有效夜晚/),
  ).toBeTruthy();
  expect(screen.getByText(/本晚尚未完成资格核验/)).toBeTruthy();
});

/** 14 complete days up to 09-24 (prior 7, recent 7), then the partial 09-25. */
function series(
  prior: number,
  recent: number,
): NonNullable<TrendsTableRow["series"]> {
  const days = Array.from({ length: 15 }, (_, i) =>
    new Date(Date.UTC(2026, 8, 11 + i)).toISOString().slice(0, 10),
  );
  return days.map((date, i) => ({
    date,
    value: i < 7 ? prior : i < 14 ? recent : 100,
    partial: i === 14,
  }));
}

function row(patch: Partial<TrendsTableRow>): TrendsTableRow {
  const first = BASE.rows[0];
  if (!first) throw new Error("the fixture has rows");
  return { ...first, ...patch };
}

function table(rows: TrendsTableRow[], patch: Partial<TrendsTable> = {}) {
  const batch = BASE.batch;
  if (!batch) throw new Error("the fixture has a batch");
  return {
    ...BASE,
    batch: { ...batch, window_end: WINDOW_END },
    rows,
    ...patch,
  };
}

const ROWS: TrendsTableRow[] = [
  row({
    order: 1,
    unit: "u1",
    title: "Flat Bride",
    term: "Flat Bride",
    series: series(10, 10),
  }),
  row({
    order: 2,
    unit: "u2",
    title: "Rising Heir",
    term: "Rising Heir",
    series: series(10, 20),
  }),
  row({
    order: 3,
    unit: "u3",
    title: "Lost Night",
    term: "Lost Night",
    result: "not_fetched",
    status: "rate_limited",
    series: null,
  }),
  row({
    order: 4,
    unit: "u4",
    title: "Stay",
    term: "Stay",
    series: series(0, 30),
  }),
  row({
    order: 5,
    unit: "u5",
    title: "Quiet Queen Returns",
    term: "Quiet Queen Returns",
    result: "no_data",
    status: "no_data",
    series: null,
  }),
  row({
    order: 6,
    unit: "u6",
    title: "Zero Curve Nights",
    term: "Zero Curve Nights",
    status: "ok_zero",
    series: series(0, 0),
  }),
];

function show(t: TrendsTable, params: Record<string, string> = {}) {
  const req = parsePickRequest({ tab: "trends", ...params });
  return render(<TrendsTableView table={t} req={req} />).container;
}

function titles(root: HTMLElement): string[] {
  return Array.from(root.querySelectorAll("tbody tr")).map(
    (tr) => tr.querySelector("td .font-semibold")?.textContent ?? "",
  );
}

function rowOf(root: HTMLElement, order: number): HTMLElement {
  const tr = root.querySelector<HTMLElement>(`tr[data-trends-row="${order}"]`);
  if (!tr) throw new Error(`no row ${order}`);
  return tr;
}

describe("the table", () => {
  it("has the scope's ten columns, each a column header", () => {
    const root = show(table(ROWS));
    const heads = Array.from(root.querySelectorAll("thead th"));
    expect(heads.map((th) => th.textContent)).toEqual([
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
    ]);
    for (const th of heads) expect(th.getAttribute("scope")).toBe("col");
  });

  it("sorts by change by default: new, then rising to falling, then rows without data", () => {
    expect(titles(show(table(ROWS)))).toEqual([
      "Stay",
      "Rising Heir",
      "Flat Bride",
      "Zero Curve Nights",
      "Lost Night",
      "Quiet Queen Returns",
    ]);
  });

  it("sorts by our own pick order on ts=order, and links each sort", () => {
    const root = show(table(ROWS), { ts: "order" });
    expect(titles(root)).toEqual([
      "Flat Bride",
      "Rising Heir",
      "Lost Night",
      "Stay",
      "Quiet Queen Returns",
      "Zero Curve Nights",
    ]);
    const change = screen.getByRole("link", { name: "按变化" });
    expect(change.getAttribute("href")).toBe("/workspace/pick-data?tab=trends");
    expect(change.getAttribute("data-prefetch")).toBe("false");
  });

  it("writes the means, the change and the label of a row with data", () => {
    const root = show(table(ROWS));
    const cells = within(rowOf(root, 2)).getAllByRole("cell");
    expect(cells.map((td) => td.textContent).slice(3, 7)).toEqual([
      "20.0",
      "10.0",
      "+100.0%",
      "上升",
    ]);
    expect(
      rowOf(root, 2).querySelector('[data-trends-spark="line"]'),
    ).toBeTruthy();
    expect(
      rowOf(root, 4).querySelector('[data-trends-label="new"]'),
    ).toBeTruthy();
    expect(
      rowOf(root, 1).querySelector('[data-trends-label="flat"]'),
    ).toBeTruthy();
  });

  it("shows a drama the night did not get as not fetched, with no curve and no numbers", () => {
    const root = show(table(ROWS));
    const lost = rowOf(root, 3);
    expect(
      lost.querySelector('[data-trends-result="not_fetched"]')?.textContent,
    ).toBe("这晚未查到被限流（429）");
    expect(lost.querySelector("svg")).toBeNull();
    const cells = within(lost).getAllByRole("cell");
    expect(cells.map((td) => td.textContent).slice(2, 7)).toEqual([
      "—",
      "—",
      "—",
      "—",
      "—",
    ]);
  });

  it("keeps Google's empty answer apart from a failed one", () => {
    const quiet = rowOf(show(table(ROWS)), 5);
    expect(
      quiet.querySelector('[data-trends-result="no_data"]')?.textContent,
    ).toBe("Google 未返回数据Google 没有返回曲线");
    expect(quiet.querySelector("svg")).toBeNull();
  });

  it("draws a curve of zeros as data: Google's index, too little to judge, never 'no data'", () => {
    const zero = rowOf(show(table(ROWS)), 6);
    expect(zero.querySelector('[data-trends-result="data"]')?.textContent).toBe(
      "有数据Google 返回的近 30 天曲线全是 0",
    );
    expect(zero.querySelector('[data-trends-spark="line"]')).toBeTruthy();
    expect(
      zero.querySelector('[data-trends-label="too_little"]')?.textContent,
    ).toBe("数据太少");
  });

  it("states the rules in their order, and promises nothing from the change alone", () => {
    const rules = show(table(ROWS)).querySelector("[data-trends-rules]");
    const text = rules?.textContent ?? "";
    expect(text).toContain("先命中的为准");
    expect(text).toContain("「持平」的行不会显示成 ±25.0%");
    expect(text).not.toContain("一定");
  });

  it("hints at a short title and links the term on Google Trends", () => {
    const root = show(table(ROWS));
    expect(
      within(rowOf(root, 4)).getByText(/搜索热度可能不属于这部剧/),
    ).toBeTruthy();
    expect(
      within(rowOf(root, 5)).queryByText(/搜索热度可能不属于这部剧/),
    ).toBeNull();
    const link = within(rowOf(root, 2)).getByRole("link", {
      name: "在 Google Trends 打开",
    });
    expect(link.getAttribute("href")).toBe(
      "https://trends.google.com/trends/explore?date=today%201-m&q=Rising%20Heir",
    );
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("writes each basis: the board, its issue and the rank", () => {
    const root = show(table(ROWS));
    expect(
      within(rowOf(root, 1)).getByText(
        "鹊娱 7 日转化率榜 2026-09-24 期第 1 名",
      ),
    ).toBeTruthy();
  });

  it("never calls something unobserved zero", () => {
    const root = show(table(ROWS));
    expect(forbiddenIn(root.textContent ?? "")).toEqual([]);
  });
});

describe("the header", () => {
  it("names the night, its counts, its sources and the last complete day", () => {
    const root = show(table(ROWS));
    const header = root.querySelector('[data-trends-header="true"]');
    expect(header?.querySelector("h2")?.textContent).toBe(
      `${BASE.batch?.target_date} 的趋势表（已采完）`,
    );
    expect(root.querySelector('[data-trends-counts="true"]')?.textContent).toBe(
      "这晚计划查 5 部：有数据 4 部，Google 未返回数据 0 部，这晚未查到 1 部。",
    );
    expect(header?.textContent).toContain("最后一个完整日是 2026-09-24（UTC）");
    expect(
      root.querySelector('[data-trends-sources="true"]')?.textContent,
    ).toBe(
      "剧的来源：鹊娱 7 日转化率榜 2026-09-24 期 5 部；鹊娱 7 日总收入榜没有可用的一期；KalosTV 日榜没有可用的一期。ReelShort 收入：还没有已发布的镜像版本。",
    );
  });

  it("counts the rows still pending while the night runs", () => {
    const batch = BASE.batch;
    if (!batch) throw new Error("the fixture has a batch");
    const running = {
      ...batch,
      outcome: "running",
      collecting: true,
      finished_at: null,
      counts: { ...batch.counts, pending: 2 },
    };
    const root = show(table(ROWS, { batch: running }));
    expect(
      root.querySelector('[data-trends-counts="true"]')?.textContent,
    ).toContain("，还在查 2 部。");
    expect(root.querySelector("h2")?.textContent).toContain("（还在采集）");
  });

  it("says a night left running past its deadline stopped short", () => {
    const batch = BASE.batch;
    if (!batch) throw new Error("the fixture has a batch");
    const stopped = {
      ...batch,
      outcome: "running",
      collecting: false,
      finished_at: null,
    };
    const root = show(table(ROWS, { batch: stopped }));
    expect(root.querySelector("h2")?.textContent).toContain(
      "（没有采完：采集中途停了）",
    );
  });

  it("shows the gateway's banners, red first", () => {
    const root = show(table(ROWS));
    expect(
      root.querySelector('[data-trends-banners="true"]')?.textContent,
    ).toMatch(/采集没有按时运行/);
  });
});

describe("empty states", () => {
  it("before the first night: one sentence, no table, no zeros", () => {
    const root = show({ ...BASE, batch: null, rows: [], banners: [] });
    expect(
      root.querySelector('[data-trends-empty="batch"]')?.textContent,
    ).toMatch(/还没有趋势表/);
    expect(root.querySelector("table")).toBeNull();
    expect(root.querySelector('[data-trends-banners="true"]')).toBeNull();
  });

  it("an empty task list: one sentence, no table", () => {
    const root = show(table([]));
    expect(root.querySelector('[data-trends-empty="rows"]')).toBeTruthy();
    expect(root.querySelector("table")).toBeNull();
  });

  it("says when the list was cut at the row limit", () => {
    const root = show(table(ROWS, { truncated: true, row_limit: 200 }));
    expect(root.textContent).toContain("只列出清单的前 200 部");
  });

  it("an unreadable answer is one alert", () => {
    render(<TrendsTableUnavailable />);
    expect(screen.getByRole("alert").textContent).toBe(
      "趋势表暂时读不了，稍后刷新再试。",
    );
  });
});
