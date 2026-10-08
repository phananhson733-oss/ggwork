import { expect, test } from "@rstest/core";

import { trendsCsv } from "@/core/pick/trends-table-export";
import { trendsTableSchema } from "@/core/pick/trends-table-schema";

import fixture from "./fixtures/backend-trends-table.json";

test("exports the displayed batch without filling unknown observations", () => {
  const row = trendsTableSchema.parse(fixture).rows[0]!;
  const text = trendsCsv(
    [
      {
        row: {
          ...row,
          title: "=SUM(1)",
          result: "not_fetched",
          status: "rate_limited",
          series: null,
        },
        stats: null,
      },
    ],
    "2026-10-08",
    "2026-10-06",
  );
  expect(text.startsWith("\uFEFF")).toBe(true);
  expect(text).toContain("'=SUM(1)");
  expect(text).toContain('"","","","这晚未查到"');
  expect(text).toContain("2026-10-06");
  expect(text).toContain("被限流（429）");
  expect(text.split("\r\n")).toHaveLength(2);
});
