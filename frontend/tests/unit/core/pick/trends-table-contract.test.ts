/**
 * The gateway's trends-table answer (simplified scope 2026-09-30, section 6 item 4). The fixture is the backend's own
 * answer for a real stable night, written by customizations/pick-workbench/tests/observe/test_trends_table.py, so a
 * field the backend renames fails here.
 */
import { describe, expect, it } from "@rstest/core";

import {
  lastCompleteDay,
  trendStats,
  type TrendLabel,
} from "@/core/pick/trends-table";
import { trendsTableSchema } from "@/core/pick/trends-table-schema";

import table from "./fixtures/backend-trends-table.json";

describe("the trends-table contract", () => {
  it("parses the backend's answer", () => {
    const parsed = trendsTableSchema.parse(table);
    expect(parsed.batch?.collect_mode).toBe("stable");
    expect(parsed.batch?.counts.planned).toBe(parsed.rows.length);
    expect(parsed.rows.map((row) => row.result)).toContain("data");
    expect(parsed.banners).toEqual([{ code: "run_missed", level: "red" }]);
    expect(parsed.rows[0]?.basis[0]?.kind).toBe("qc");
  });

  it("gives every row with data a label, from complete days only", () => {
    const parsed = trendsTableSchema.parse(table);
    const last = lastCompleteDay(parsed.batch?.window_end ?? "");
    const labels = parsed.rows
      .filter((row) => row.result === "data" && row.series !== null)
      .map((row) => trendStats(row.series ?? [], last).label);
    expect(labels.length).toBeGreaterThan(0);
    for (const label of labels)
      expect(["too_little", "new", "rising", "falling", "flat"]).toContain(
        label satisfies TrendLabel,
      );
  });

  it("refuses a value out of range or a result it does not know, as a whole", () => {
    const rows = table.rows as unknown as Record<string, unknown>[];
    const first = rows[0] ?? {};
    const outOfRange = {
      ...table,
      rows: [
        {
          ...first,
          series: [{ date: "2026-09-24", value: 101, partial: false }],
        },
      ],
    };
    expect(trendsTableSchema.safeParse(outOfRange).success).toBe(false);
    const unknown = { ...table, rows: [{ ...first, result: "maybe" }] };
    expect(trendsTableSchema.safeParse(unknown).success).toBe(false);
  });

  it("reads an empty table before the first stable night", () => {
    const empty = { ...table, batch: null, rows: [], banners: [] };
    expect(trendsTableSchema.parse(empty).batch).toBeNull();
  });
});
