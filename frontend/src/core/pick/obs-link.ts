/**
 * Whether a frozen link fact may be acted on now (plan TR-24; D13; contract
 * section 8): the TS twin of ggwork_pick/observe/link_rules.link_actionable.
 *
 * Facts are materialised into pick_obs.links when a set is published and are
 * never recomputed here. Actionability is judged at request time with an
 * explicit now: a fact stays the same while its action is withdrawn once the
 * Trends set is over 26 hours old or the GSC set over 6 (exactly 26 or 6
 * still holds). Each version is an entry of LINK_RULE_LIMITS; an unknown one
 * is refused, never run as the newest (D29). obs-link.test runs
 * obs_link_cases.json's actionable_cases, as Python's test does.
 */
import { HOUR_US, instantMicros, stampMicros } from "./obs-instants";

export const ACTIONABILITY_REASONS = [
  "label_not_actionable",
  "untimely_pair",
  "stale_row",
  "trends_set_too_old",
  "gsc_set_too_old",
] as const;
export type ActionabilityReason = (typeof ACTIONABILITY_REASONS)[number];

/** The five link_state labels carry an action; global_parallel and different_markets never do. */
const ACTIONABLE_LABELS: ReadonlySet<string> = new Set([
  "both_rising",
  "trends_lead_page",
  "trends_lead_distribution",
  "site_only",
  "cooling",
]);

export const LINK_RULE_LIMITS: Readonly<
  Record<
    string,
    Readonly<{ trendsMaxAgeHours: number; gscMaxAgeHours: number }>
  >
> = {
  "link-rules-v1": { trendsMaxAgeHours: 26, gscMaxAgeHours: 6 },
};

export const ACTIONABILITY_TEXT: Readonly<Record<ActionabilityReason, string>> =
  {
    label_not_actionable: "这个联动标签只作展示，不带动作",
    untimely_pair: "两个锚点相隔太久（时效不符）",
    stale_row: "用到的 Trends 判定行是沿用或陈旧的",
    trends_set_too_old: "Trends 集合发布已超过 26 小时",
    gsc_set_too_old: "GSC 集合发布已超过 6 小时",
  };

export type LinkFactInput = Readonly<{
  label: string;
  timely: boolean;
  stale: boolean;
}>;

export type Actionability = Readonly<{
  actionable: boolean;
  reasons: ActionabilityReason[];
}>;

export function linkActionable(
  fact: LinkFactInput,
  trendsPublishedAt: string,
  gscPublishedAt: string,
  now: string | Date,
  version: string,
): Actionability {
  const limits = LINK_RULE_LIMITS[version];
  if (limits === undefined)
    throw new Error(`未登记的 link-rules 版本：${version}`);
  const moment = instantMicros(now);
  const holds: Readonly<Record<ActionabilityReason, boolean>> = {
    label_not_actionable: !ACTIONABLE_LABELS.has(fact.label),
    untimely_pair: !fact.timely,
    stale_row: fact.stale,
    trends_set_too_old:
      moment - stampMicros(trendsPublishedAt) >
      limits.trendsMaxAgeHours * HOUR_US,
    gsc_set_too_old:
      moment - stampMicros(gscPublishedAt) > limits.gscMaxAgeHours * HOUR_US,
  };
  const reasons = ACTIONABILITY_REASONS.filter((reason) => holds[reason]);
  return { actionable: reasons.length === 0, reasons };
}
