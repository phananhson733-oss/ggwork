/**
 * market-map-v1 on the data page (plan TR-24; design 1.3, 5.7): the display
 * groups come from the backend's tests/fixtures/obs_market_map.json, which
 * test_obs_market_map pins to MARKET_MAPS. The frontend keeps a verbatim copy
 * inside src (the frontend deploys on its own); this test keeps it equal.
 */
import { describe, expect, it } from "@rstest/core";

import {
  groupOfCountry,
  groupRows,
  marketMap,
} from "@/core/pick/obs-market-map";
import copy from "@/core/pick/obs-market-map.json";

import { repoText } from "./obs-contract-fixtures";

const BACKEND = JSON.parse(
  repoText("customizations/pick-workbench/tests/fixtures/obs_market_map.json"),
) as { maps: unknown };

describe("the market map copy", () => {
  it("equals the backend's fixture", () => {
    expect(copy.maps).toEqual(BACKEND.maps);
  });

  it("refuses a version it does not know", () => {
    expect(() => marketMap("market-map-v9")).toThrow("market-map-v9");
    expect(marketMap("market-map-v1").version).toBe("market-map-v1");
  });
});

describe("groups on the page", () => {
  const map = marketMap("market-map-v1");

  it("puts a country in its display group; Bulgaria alone; the Philippines nowhere", () => {
    expect(groupOfCountry(map, "ALL")?.key).toBe("global");
    expect(groupOfCountry(map, "USA")?.key).toBe("north_america");
    expect(groupOfCountry(map, "IRL")?.key).toBe("uk_ie_au_nz");
    expect(groupOfCountry(map, "BGR")?.key).toBe("bulgaria");
    expect(groupOfCountry(map, "PHL")).toBeNull();
  });

  it("groups rows in the map's order, the rest last as site-total-only", () => {
    const rows = [
      { scope: "PHL" },
      { scope: "BGR" },
      { scope: "USA" },
      { scope: "ALL" },
      { scope: "MEX" },
      { scope: "CAN" },
    ];
    const grouped = groupRows(map, rows);
    expect(grouped.map((g) => g.key)).toEqual([
      "global",
      "north_america",
      "latin_america",
      "bulgaria",
      "other",
    ]);
    expect(grouped[1]?.rows.map((r) => r.scope)).toEqual(["USA", "CAN"]);
    expect(grouped.at(-1)?.label).toBe("其他国家（只计入全站合计）");
    expect(grouped[3]?.note).toContain("保语页面口径");
  });
});
