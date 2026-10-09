import { afterEach, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import {
  HistoricalWindowBadge,
  RadarGrowth,
  TrendBadge,
} from "@/components/workspace/radar/semantic";
import { explain } from "@/core/radar/presentation";
import { detailSchema } from "@/core/radar/schema";
import { historicalSignalTone } from "@/core/radar/semantic-tones";

import fixture from "./synthetic-detail.json";

afterEach(cleanup);
const row = detailSchema.parse(fixture);

it("keeps a neutral signal, positive growth and historical warning visible together", () => {
  const pilot = {
    ...row.pilot,
    prediction_label: "未命中增强信号",
    growth_state: "percent",
    growth_pct: 9.2,
  };
  const reading = explain({ ...row, pilot });
  render(
    <>
      <TrendBadge tone={historicalSignalTone(reading.label)}>
        {reading.label}
      </TrendBadge>
      <RadarGrowth pilot={pilot} />
      <HistoricalWindowBadge />
    </>,
  );
  expect(
    screen.getByText("未命中增强信号").getAttribute("data-trend-tone"),
  ).toBe("neutral");
  expect(screen.getByText("+9.2%").className).toContain("text-success-ink");
  expect(screen.getByText("较早历史窗口").getAttribute("data-trend-tone")).toBe(
    "warning",
  );
});

it("does not color from-zero or unknown comparison values as percentage growth", () => {
  const { rerender } = render(
    <RadarGrowth
      pilot={{ ...row.pilot, growth_state: "from_zero", growth_pct: 100 }}
    />,
  );
  expect(screen.getByText("从零基数出现").className).toContain("text-info-ink");
  rerender(
    <RadarGrowth
      pilot={{ ...row.pilot, growth_state: "unavailable", growth_pct: null }}
    />,
  );
  expect(screen.getByText("—").className).toContain("text-ink-2");
});
