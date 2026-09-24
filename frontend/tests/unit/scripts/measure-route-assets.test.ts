import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

import {
  createBuildEnvironment,
  evaluateBudgets,
  extractAssetPaths,
  formatMeasurementSummary,
  ROUTES,
} from "../../../scripts/measure-route-assets.mjs";

describe("route asset measurement", () => {
  it("covers every approved representative route", () => {
    expect(ROUTES).toContain("/login");
    expect(ROUTES).toContain("/workspace/pick-data");
  });

  it("budgets exactly the routes it measures", () => {
    const budgets = JSON.parse(
      readFileSync(
        path.resolve(__dirname, "../../../performance-budgets.json"),
        "utf8",
      ),
    ) as Record<string, { css: number; js: number }>;
    expect(Object.keys(budgets).sort()).toEqual([...ROUTES].sort());
    for (const limits of Object.values(budgets)) {
      expect(limits.css).toBeGreaterThan(0);
      expect(limits.js).toBeGreaterThan(0);
    }
  });

  it("removes an inherited static-mode flag from the normal build", () => {
    expect(
      createBuildEnvironment(false, {
        KEEP_ME: "yes",
        NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "true",
      }),
    ).toEqual({ KEEP_ME: "yes" });
    expect(createBuildEnvironment(true, {})).toEqual({
      NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "true",
    });
  });

  it("extracts unique Next.js scripts and styles", () => {
    const html = `
      <link rel="stylesheet" href="/_next/static/css/app.css?dpl=1">
      <script src="/_next/static/chunks/app.js"></script>
      <script src="/_next/static/chunks/app.js"></script>
      <script src="https://cdn.example.com/external.js"></script>
    `;

    expect(extractAssetPaths(html)).toEqual({
      css: ["css/app.css"],
      js: ["chunks/app.js"],
    });
  });

  it("reports every route budget overage with exact values", () => {
    const result = evaluateBudgets(
      {
        "/": { css: 101, js: 200 },
        "/en/docs": { css: 50, js: 301 },
      },
      {
        "/": { css: 100, js: 200 },
        "/en/docs": { css: 50, js: 300 },
      },
    );

    expect(result).toEqual([
      "/ css: 101 bytes exceeds 100 bytes by 1 byte",
      "/en/docs js: 301 bytes exceeds 300 bytes by 1 byte",
    ]);
  });

  it("formats a stable human-readable route summary", () => {
    expect(
      formatMeasurementSummary({
        "/": { css: 12, js: 345, html: 67, buildMode: "static-demo" },
      }),
    ).toBe(
      "Route asset summary\n/ [static-demo] JS 345 B | CSS 12 B | HTML 67 B",
    );
  });
});
