/**
 * The market map's display groups on the data page (plan TR-24; design 1.3,
 * 5.7). A group only arranges the page: its total is a display sum, never a
 * unit of judgment, and a country in no group (the Philippines, India) shows
 * only as site-total-only. The table is obs-market-map.json, a verbatim copy
 * of the backend's fixture (obs-market-map.test keeps them equal); a set
 * names its version in frozen_inputs.market_map_version and an unknown
 * version is refused, never read as the newest.
 */
import data from "./obs-market-map.json";

export type MarketGroup = Readonly<{
  key: string;
  label: string;
  countries: readonly string[];
  geos: readonly string[];
  core: boolean;
  note: string | null;
}>;

export type MarketMap = Readonly<{
  version: string;
  groups: readonly MarketGroup[];
  geo_country: Readonly<Record<string, string>>;
}>;

const MAPS: readonly MarketMap[] = data.maps;

export const OTHER_GROUP_KEY = "other";
const OTHER_LABEL = "其他国家（只计入全站合计）";

/** The registered map of this version; null for an unknown one, which a caller shows ungrouped, never as the newest. */
export function findMarketMap(version: string): MarketMap | null {
  return MAPS.find((map) => map.version === version) ?? null;
}

export function marketMap(version: string): MarketMap {
  const found = findMarketMap(version);
  if (!found) throw new Error(`未登记的市场表版本：${version}`);
  return found;
}

export function groupOfCountry(
  map: MarketMap,
  country: string,
): MarketGroup | null {
  return map.groups.find((group) => group.countries.includes(country)) ?? null;
}

export type GroupedRows<R> = Readonly<{
  key: string;
  label: string;
  note: string | null;
  rows: R[];
}>;

/** Rows by display group, in the map's order; rows whose country is in no group come last. Empty groups are left out. */
export function groupRows<R extends Readonly<{ scope: string }>>(
  map: MarketMap,
  rows: readonly R[],
): GroupedRows<R>[] {
  const grouped = map.groups.map((group) => ({
    key: group.key,
    label: group.label,
    note: group.note,
    rows: rows.filter((row) => group.countries.includes(row.scope)),
  }));
  const other = rows.filter((row) => groupOfCountry(map, row.scope) === null);
  return [
    ...grouped,
    { key: OTHER_GROUP_KEY, label: OTHER_LABEL, note: null, rows: other },
  ].filter((group) => group.rows.length > 0);
}
