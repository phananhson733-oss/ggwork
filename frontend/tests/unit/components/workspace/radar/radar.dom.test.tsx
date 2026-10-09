import { afterEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { HistoricalRadar } from "@/components/workspace/radar/historical-radar";

const calls: string[] = [];
afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
  calls.length = 0;
});
it("shows coverage and composes filters, signal view and all-matches export", async () => {
  rs.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    calls.push(url);
    return new Response(
      JSON.stringify(
        url.endsWith("/stats")
          ? {
              catalog_total: 54,
              with_series: 53,
              without_series: 1,
              latest_series_end: "2026-09-14",
              series_end_dates: ["2026-09-14"],
              snapshot: {
                snapshot_id: "synthetic",
                latest_cache_written_at: null,
                rules_version: "pilot-rules-v1",
              },
            }
          : {
              total: 0,
              limit: 50,
              offset: 0,
              items: [],
              snapshot_id: "synthetic",
            },
      ),
      { status: 200 },
    );
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <HistoricalRadar dailyHref="?tab=trends&rv=daily" />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("54")).toBeTruthy();
  expect(screen.getByText("目录条目")).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", { name: "平台" }), {
    target: { value: "ReelShort" },
  });
  fireEvent.change(screen.getByRole("combobox", { name: "实验等级" }), {
    target: { value: "unrated" },
  });
  fireEvent.click(screen.getByRole("tab", { name: "历史趋势信号" }));
  await waitFor(() =>
    expect(
      calls.some(
        (u) =>
          u.includes("tab=signals") &&
          u.includes("platform=ReelShort") &&
          u.includes("tier=unrated"),
      ),
    ).toBe(true),
  );
  const search = screen.getByRole("searchbox", { name: "搜索剧名" });
  fireEvent.change(search, { target: { value: "Fir" } });
  fireEvent.change(search, { target: { value: "First" } });
  expect(calls.some((u) => u.includes("search="))).toBe(false);
  await waitFor(() =>
    expect(calls.some((u) => u.includes("search=First"))).toBe(true),
  );
  expect(calls.some((u) => u.includes("search=Fir&"))).toBe(false);
  const href = screen
    .getByRole("link", { name: "导出当前筛选 CSV" })
    .getAttribute("href")!;
  expect(href).toContain("tab=signals");
  expect(href).toContain("platform=ReelShort");
  expect(href).not.toContain("limit=");
  expect(
    await screen.findByText("没有匹配的历史记录，请调整筛选条件。"),
  ).toBeTruthy();
});

it("detail navigation rejects an earlier response after another drama was selected", async () => {
  const { RadarDetailDialog } =
    await import("@/components/workspace/radar/detail");
  const { useState } = await import("react");
  const { detailSchema } = await import("@/core/radar/schema");
  const fixture = (await import("./synthetic-detail.json")).default;
  const first = detailSchema.parse(fixture);
  const second = { ...first, id: 2, original_title: "Second synthetic" };
  let resolveFirst!: (response: Response) => void;
  rs.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.endsWith("/1"))
      return new Promise<Response>((resolve) => {
        resolveFirst = resolve;
      });
    return new Response(JSON.stringify(second), { status: 200 });
  });
  function Wrapper() {
    const [row, setRow] = useState<typeof first | null>(first);
    return (
      <RadarDetailDialog
        row={row}
        items={[first, second]}
        onSelect={(r) => setRow(r ? (r.id === 1 ? first : second) : null)}
      />
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Wrapper />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(resolveFirst).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "下一部" }));
  expect(
    await screen.findByRole("heading", { name: "Second synthetic" }),
  ).toBeTruthy();
  await waitFor(() =>
    expect(screen.getByText(/末 7 日均值较前 7 日：\+100%/)).toBeTruthy(),
  );
  resolveFirst(new Response(JSON.stringify(first), { status: 200 }));
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Sample" })).toBeNull(),
  );
  expect(
    screen.getByRole("heading", { name: "Second synthetic" }),
  ).toBeTruthy();
  expect(screen.getByText(/总分由热度 35%/)).toBeTruthy();
});
