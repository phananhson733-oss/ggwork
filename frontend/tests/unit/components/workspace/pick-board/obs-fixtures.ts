/**
 * The radar's view data for the component and page tests (plan TR-24): the
 * contract's own valid view rows (views.json, set_summaries.json), parsed by
 * the page's schemas and patched per case, so every value is one the
 * collector could have written.
 */
import {
  obsDiscoverySchema,
  obsLinkSchema,
  obsRunSchema,
  obsSetSchema,
  obsStateSchema,
  setSummarySchema,
  type ObsDiscovery,
  type ObsLink,
  type ObsRun,
  type ObsSet,
  type ObsState,
} from "@/core/pick/obs-rows";
import type { ObsChannel } from "@/core/pick/obs-status";
import type { ObsChannelLoad, ObsTabData } from "@/server/pick-board";

import {
  obsFixture,
  validCase,
  type ModelFixture,
} from "../../../core/pick/obs-contract-fixtures";

type Row = Record<string, unknown>;

const VIEWS = obsFixture<{ valid: { name: string; row: Row }[] }>("views.json");
const SUMMARIES = obsFixture<ModelFixture>("set_summaries.json");

function viewRow(name: string): Row {
  const found = VIEWS.valid.find((c) => c.name === name)?.row;
  if (!found) throw new Error(`no view case ${name}`);
  return structuredClone(found);
}

export const T_LIVE = "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c";
export const T_SHADOW = "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6d";
export const G_LIVE = "3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c";
export const G_SHADOW = "3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8d";
export const IDENTITY =
  '["realshort","UkVFTFNIT1JUOjY1MGExYjJjM2Q0ZTVmNmE3YjhjOWQwZQ","en"]';
/** Both sets were published at 2026-09-25T01:52:10Z: within 26 and 6 hours of this. */
export const OBS_NOW = new Date("2026-09-25T04:00:00Z");

export function summary(name: string): unknown {
  return setSummarySchema.parse(validCase(SUMMARIES, name));
}

export function trendsSet(patch: Row = {}): ObsSet {
  return obsSetSchema.parse({ ...viewRow("sets_trends"), ...patch });
}

export function gscSet(patch: Row = {}): ObsSet {
  return obsSetSchema.parse({ ...viewRow("sets_gsc"), mode: "live", ...patch });
}

export function state(name: string, patch: Row = {}): ObsState {
  return obsStateSchema.parse({ ...viewRow(name), ...patch });
}

export function discovery(name: string, patch: Row = {}): ObsDiscovery {
  return obsDiscoverySchema.parse({ ...viewRow(name), ...patch });
}

export function link(patch: Row = {}): ObsLink {
  return obsLinkSchema.parse({ ...viewRow("links"), ...patch });
}

export function run(channel: ObsChannel, patch: Row = {}): ObsRun {
  return obsRunSchema.parse({ ...viewRow(`run_status_${channel}`), ...patch });
}

const brief = (set: ObsSet) => ({
  set_id: set.set_id,
  mode: set.mode,
  published_at: set.published_at,
});

/** A channel showing its live set, with that set alone published and its latest run. */
export function channelLoad(
  channel: ObsChannel,
  patch: Partial<ObsChannelLoad> = {},
): ObsChannelLoad {
  const shown = channel === "trends" ? trendsSet() : gscSet();
  return {
    channel,
    shown,
    live: brief(shown),
    latestRun: run(channel),
    pin: "none",
    recent: [brief(shown)],
    ...patch,
  };
}

export function emptyChannel(channel: ObsChannel): ObsChannelLoad {
  return {
    channel,
    shown: null,
    live: null,
    latestRun: null,
    pin: "none",
    recent: [],
  };
}

type TrendsData = Extract<ObsTabData, { kind: "trends" }>;
type SearchData = Extract<ObsTabData, { kind: "search" }>;
type DetailData = Extract<ObsTabData, { kind: "detail" }>;

export function trendsData(patch: Partial<TrendsData> = {}): TrendsData {
  return {
    kind: "trends",
    trends: channelLoad("trends"),
    discoveries: {
      rows: [
        discovery("discoveries_unique", { route: "queue" }),
        discovery("discoveries_out_of_pool", {
          discovery_id: 13,
          term: "reelshort the hidden heiress",
          normalized_term: "the hidden heiress",
        }),
      ],
      counts: { queue: 1, display_only: 1 },
      truncated: false,
    },
    ...patch,
  };
}

export function searchData(patch: Partial<SearchData> = {}): SearchData {
  return {
    kind: "search",
    gsc: channelLoad("gsc"),
    states: {
      rows: [
        state("states_gsc"),
        state("states_gsc", {
          row_id: 910,
          scope: "BGR",
          state: "present",
          admission: "descriptive",
          labels: [
            {
              label: "surge",
              formal: false,
              condition:
                "W0 对 W−1 ≥ +50%，W−1 < 20（小基数曝光上升，只作描述）",
              counts: { w0: 40, w_minus_1: null },
            },
          ],
          paste_row: null,
        }),
      ],
      total: 5,
      labeled: 2,
      truncated: false,
    },
    ...patch,
  };
}

export function detailData(patch: Partial<DetailData> = {}): DetailData {
  return {
    kind: "detail",
    tab: "search",
    identity: IDENTITY,
    trends: channelLoad("trends"),
    gsc: channelLoad("gsc"),
    states: [state("states_trends"), state("states_gsc")],
    discoveries: [discovery("discoveries_unique", { route: "queue" })],
    links: [link()],
    ...patch,
  };
}
