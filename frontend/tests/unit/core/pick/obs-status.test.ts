/**
 * The radar's status wording on the frontend (plan TR-25, TR-24; D10): the
 * code -> level and code -> text tables are the backend's, read here from the
 * shared fixtures so the two copies cannot drift apart. The imports tab shows
 * the gateway's banners with them; the data page computes its own banners
 * (obs-banner-rules.ts) and words them the same way.
 */
import { describe, expect, it } from "@rstest/core";

import { forbiddenIn } from "@/core/pick/obs-format";
import {
  BANNER_LEVELS,
  OBS_CHANNEL_LABELS,
  OBS_STATUS_CODES,
  OBS_STATUS_LEVELS,
  OBS_STATUS_TEXT,
  obsBannerText,
} from "@/core/pick/obs-status";
import { SYNC_BANNER_LEVELS, SYNC_OBS_CHANNELS } from "@/core/pick/sync-schema";

import { repoText } from "./obs-contract-fixtures";

const CASES = JSON.parse(
  repoText(
    "customizations/pick-workbench/tests/fixtures/obs_status_cases.json",
  ),
) as { levels: Record<string, string>; texts: Record<string, string> };
const CODES = JSON.parse(
  repoText(
    "customizations/pick-workbench/tests/fixtures/obs_contract/status_codes.json",
  ),
) as { codes: string[]; banner_levels: string[] };

describe("the status tables", () => {
  it("list the contract's codes and levels, in its order", () => {
    expect([...OBS_STATUS_CODES]).toEqual(CODES.codes);
    expect([...BANNER_LEVELS]).toEqual(CODES.banner_levels);
    // sync-schema.ts imports zod only, so it keeps its own copies.
    expect([...SYNC_BANNER_LEVELS]).toEqual([...BANNER_LEVELS]);
    expect([...SYNC_OBS_CHANNELS]).toEqual(Object.keys(OBS_CHANNEL_LABELS));
  });

  it("classify and word every code as status_rules does", () => {
    expect({ ...OBS_STATUS_LEVELS }).toEqual(CASES.levels);
    expect({ ...OBS_STATUS_TEXT }).toEqual(CASES.texts);
  });

  it("never call something unobserved zero", () => {
    for (const text of Object.values(OBS_STATUS_TEXT))
      expect(forbiddenIn(text)).toEqual([]);
  });

  it("name the two channels", () => {
    expect(OBS_CHANNEL_LABELS).toEqual({
      trends: "Google Trends",
      gsc: "GSC（站内搜索表现）",
    });
  });
});

describe("obsBannerText", () => {
  it("words a known code with the contract's text", () => {
    expect(obsBannerText("run_missed")).toBe(OBS_STATUS_TEXT.run_missed);
  });

  it("words a code from a newer gateway without guessing its meaning", () => {
    expect(obsBannerText("future_code")).toBe(
      "状态码 future_code：这个页面版本还没有它的说明",
    );
  });
});
