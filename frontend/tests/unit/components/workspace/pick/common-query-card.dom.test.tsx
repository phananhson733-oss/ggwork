import { afterEach, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import { CommonQueryCard } from "@/components/workspace/pick/common-query-card";

import fixture from "../../../core/pick/fixtures/query-model-v1.json";

afterEach(cleanup);
it("shows actual period and immutable scope from the bounded operator projection", () => {
  const ranked = {
    ...fixture.drama,
    request: {
      ...fixture.drama.request,
      domain: "rankings",
      rank: "kd",
      order: "rank",
      period: { kind: "daily", value: "2026-01-01" },
    },
    actual_period: { kind: "daily", value: "2026-01-01" },
  };
  render(<CommonQueryCard result={ranked} />);
  expect(screen.getByText(/实际期次：/).textContent).toContain("2026-01-01");
  expect(screen.getByText(/完整匹配 1 条/)).toBeTruthy();
  expect(
    screen
      .getByRole("link", { name: "打开此版本资料（新标签页）" })
      .getAttribute("href"),
  ).toContain("v=1");
  expect(screen.queryByRole("button", { name: /保存/ })).toBeNull();
});
it("keeps unknown-language source records readonly without fabricating candidate identity or eligibility", () => {
  render(<CommonQueryCard result={fixture.catalog_record} />);
  expect(screen.getByText(/来源未标明语种/)).toBeTruthy();
  expect(screen.getByText(/候选资格待核对/)).toBeTruthy();
  expect(
    screen
      .getByRole("link", { name: "查看这条来源（新标签页）" })
      .getAttribute("href"),
  ).toContain("row=synthetic-row");
  expect(screen.queryByRole("checkbox")).toBeNull();
});
it("separates query page counts from rows and signals omitted by the bounded projection", () => {
  const shortened = {
    ...fixture.drama,
    counts: { ...fixture.drama.counts, matched: 5, returned: 2 },
    query_truncated: true,
    query_next_offset: 2,
    projection: {
      ...fixture.drama.projection,
      shown: 1,
      available_count: 2,
      omitted_rows: 1,
      signals_omitted: 3,
      truncated: true,
      next_offset: 1,
    },
  };
  render(<CommonQueryCard result={shortened} />);
  expect(screen.getByText(/本次查询返回 2 条 · 卡片展示 1 条/)).toBeTruthy();
  expect(screen.getByText(/本卡片省略 1 项/)).toBeTruthy();
  expect(screen.getByText(/3 条依据未在卡片中展开/)).toBeTruthy();
});
it("does not describe rules with source-count zero as missing rules", () => {
  render(<CommonQueryCard result={fixture.rule} />);
  expect(screen.getByText("Synthetic theater")).toBeTruthy();
  expect(screen.queryByText(/没有匹配记录/)).toBeNull();
  expect(screen.queryByText(/查询页上限/)).toBeNull();
  expect(screen.queryByText(/排序：/)).toBeNull();
});
it("does not turn unfinished or malformed output into zero, or expose raw audits", () => {
  const mounted = render(
    <CommonQueryCard
      result={{ status: "rejected", raw: "private diagnostic" }}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain("查询未完成");
  expect(mounted.container.textContent).not.toContain("private diagnostic");
  expect(mounted.container.textContent).not.toContain("0 条");
  mounted.rerender(
    <CommonQueryCard
      result={{ ...fixture.drama, facts: [{ claim: "internal assertion" }] }}
    />,
  );
  expect(mounted.container.textContent).not.toContain("internal assertion");
  mounted.rerender(<CommonQueryCard isLoading result={undefined} />);
  expect(screen.getByRole("status").textContent).toContain("处理中");
});

it("labels selected upstream ranking metrics without claiming our revenue and preserves measured zero", () => {
  const mounted = render(<CommonQueryCard result={fixture.reelshort_metric} />);
  expect(screen.getByText("数值：1000.2")).toBeTruthy();
  expect(
    screen.getByText("上游大盘口径，不表示我们的收入或分成。"),
  ).toBeTruthy();
  const zero = {
    ...fixture.reelshort_metric,
    rows: fixture.reelshort_metric.rows.map((row) => ({
      ...row,
      rank_metric: { ...row.rank_metric, value: "0", verified: true },
    })),
  };
  mounted.rerender(<CommonQueryCard result={zero} />);
  expect(screen.getByText("数值：0")).toBeTruthy();
  mounted.rerender(
    <CommonQueryCard
      result={{
        ...zero,
        rows: zero.rows.map((row) => ({
          ...row,
          rank_metric: { ...row.rank_metric, verified: null },
        })),
      }}
    />,
  );
  expect(screen.getByText("数值：未提供/未核实")).toBeTruthy();
});
it("renders arbitrary source labels as text without inheriting object properties", () => {
  const source = {
    ...fixture.drama,
    effective_sort: "__proto__",
    request: { ...fixture.drama.request, signal_kind: "__proto__" },
    rows: fixture.drama.rows.map((row) => ({
      ...row,
      signals: row.signals.map((signal) => ({ ...signal, kind: "__proto__" })),
    })),
  };
  const { container } = render(<CommonQueryCard result={source} />);
  expect(container.textContent).toContain("依据类型：__proto__");
  expect(container.textContent).toContain("来源排序");
  expect(container.textContent).not.toContain("[object Object]");
});
it("labels posted cumulative source counts separately from the requested account/window", () => {
  const posted = {
    ...fixture.posted,
    request: {
      ...fixture.posted.request,
      account: "ScopedAccount",
      published_from: "2026-01-01",
    },
    rows: fixture.posted.rows.map((row) => ({
      ...row,
      post_count: 7,
      sched_count: 2,
    })),
  };
  render(<CommonQueryCard result={posted} />);
  expect(
    screen.getByText(/来源记录累计已发布 7 条 · 累计已排期 2 条/),
  ).toBeTruthy();
  expect(
    screen.getByText("累计数不等于当前账号或日期窗口内的条数。"),
  ).toBeTruthy();
});
it("keeps bill promotion type visible as part of the source record identity", () => {
  render(<CommonQueryCard result={fixture.bill} />);
  expect(screen.getByText(/推广类型：cps/)).toBeTruthy();
  expect(screen.getByText(/排序：来源榜单顺序/)).toBeTruthy();
  expect(screen.getByText(/本次仅查询资料，未执行保存或发布/)).toBeTruthy();
});
it("does not invent row omissions for query pagination or apply ignored eligibility defaults", () => {
  const page = {
    ...fixture.catalog_record,
    query_truncated: true,
    query_next_offset: 20,
    projection: {
      ...fixture.catalog_record.projection,
      truncated: true,
      omitted_rows: 0,
      next_offset: 20,
    },
  };
  const mounted = render(<CommonQueryCard result={page} />);
  expect(screen.getByText("查询按页返回，当前不是完整清单。")).toBeTruthy();
  expect(screen.queryByText(/本卡片省略/)).toBeNull();
  expect(mounted.container.textContent).not.toContain("仅确认符合资格");
  mounted.rerender(
    <CommonQueryCard
      result={{
        ...fixture.drama,
        request: { ...fixture.drama.request, channel: "youtube" },
      }}
    />,
  );
  expect(mounted.container.textContent).toContain("仅确认符合资格");
});
