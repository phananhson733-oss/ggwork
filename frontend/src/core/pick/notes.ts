import { z } from "zod";

import { day, postedLine } from "./format";
import type { PickConditions, PickEvidence, PickItem } from "./types";

// GET /api/pick/results/{id}/notes (evaluation batch 2, 2026-10-05): what the
// historical data timing reconstructed at result creation, and each item's row facts, read
// from the result's own batch. Display-only, so unlike the result schemas these
// are not strict: a newer gateway may add keys without breaking the card.

const facts = z.object({
  tags: z.array(z.string()),
  listed_at: z.string().nullable(),
  channel_rules: z.record(z.string(), z.string()),
});

const relaxation = z.object({
  condition: z.string(),
  value: z.unknown(),
  also_removed: z.array(z.string()).optional(),
  matched_total: z.number().int().nonnegative().nullable(),
  unavailable: z.string().optional(),
});

export const pickResultNotesSchema = z.object({
  item_facts: z.record(z.string(), facts),
  zero_diagnosis: z
    .object({
      catalog_rows: z.number().int().nonnegative(),
      delisted_rows: z.number().int().nonnegative(),
      without_each: z.array(relaxation),
    })
    .optional(),
  hot_scope: z
    .object({ counted: z.array(z.string()), not_counted: z.array(z.string()) })
    .optional(),
  data_notices: z.array(z.string()).optional(),
  notices_reference_at: z
    .string()
    .datetime({ offset: true })
    .nullable()
    .optional(),
});

export type PickItemFacts = z.infer<typeof facts>;
export type PickRelaxation = z.infer<typeof relaxation>;
export type PickResultNotes = z.infer<typeof pickResultNotesSchema>;
/** What the card has of a result's notes: none (404, an older gateway included) shows nothing. */
export type PickNotesState =
  | { kind: "notes"; notes: PickResultNotes }
  | { kind: "gone"; message: string }
  | { kind: "none" }
  | { kind: "error" };

const RULE_LABEL: Record<string, string> = {
  allowed: "规则允许",
  denied: "规则禁用",
  unknown: "待核实",
};

function listed(value: string): string {
  return /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : value;
}

/** One line of an item's row facts; a missing fact says so instead of guessing. */
export function itemFactsLine(
  itemFacts: PickItemFacts | undefined,
  pendingChannel?: string,
) {
  if (!itemFacts) return null;
  const rules = Object.entries(itemFacts.channel_rules).filter(
    ([channel]) => channel !== pendingChannel,
  );
  return [
    itemFacts.tags.length ? `标签 ${itemFacts.tags.join("、")}` : "标签未注明",
    itemFacts.listed_at
      ? `上架 ${listed(itemFacts.listed_at)}`
      : "上架日期未知",
    rules.length
      ? rules
          .map(([channel, rule]) => `${channel} ${RULE_LABEL[rule] ?? rule}`)
          .join("、")
      : pendingChannel
        ? null
        : "渠道规则未注明",
  ]
    .filter(Boolean)
    .join(" · ");
}

function text(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join("、");
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : "";
}

function conditionLabel(condition: string, value?: unknown): string {
  switch (condition) {
    case "theater":
      return `剧场 ${text(value)}`;
    case "language":
      return `语种 ${text(value)}`;
    case "channel":
      return `渠道 ${text(value)}`;
    case "confirmed_eligible_only":
      return "只要确认可发";
    case "query":
      return `关键词「${text(value)}」`;
    case "tags":
      return `标签 ${text(value)}`;
    case "signal_kind":
      return `榜单 ${text(value)}`;
    case "sort":
      return "按名次排序";
    case "hot_only":
      return "只要热门依据";
    case "exclude_posted":
      return "排除团队已发";
    case "posted_account":
      return `排除账号 ${text(value)} 已发`;
    case "excluded":
      return `排除已选与看过的 ${text(value)} 部`;
    default:
      return condition;
  }
}

/** "去掉「语种 en」：3 部" for one zero-diagnosis step; null counts say why instead of 0. */
export function relaxationLine(step: PickRelaxation): string {
  const also = step.also_removed?.length
    ? `（连同${step.also_removed.map((name) => conditionLabel(name)).join("、")}）`
    : "";
  const count =
    step.matched_total === null
      ? (step.unavailable ?? "数不出来")
      : `${step.matched_total} 部`;
  return `去掉「${conditionLabel(step.condition, step.value)}」${also}：${count}`;
}

export function zeroDiagnosisLines(
  diagnosis: NonNullable<PickResultNotes["zero_diagnosis"]>,
): { lead: string; steps: string[]; caution: string | null } {
  const steps = diagnosis.without_each.map(relaxationLine);
  const lead =
    steps.length === 0
      ? `这批剧库共 ${diagnosis.catalog_rows} 部（已下架 ${diagnosis.delisted_rows} 部不计入），没有可以放宽的条件。`
      : `这批剧库共 ${diagnosis.catalog_rows} 部（已下架 ${diagnosis.delisted_rows} 部不计入）。逐项去掉一个条件、其余不变时：`;
  const caution =
    steps.length > 0 &&
    // An uncountable step (null) is not a zero: no conclusion then.
    diagnosis.without_each.every((step) => step.matched_total === 0)
      ? "单放宽一项都没有结果：可能要同时调整几项，也可能这批数据里没有。"
      : null;
  return { lead, steps, caution };
}

export function hotScopeLine(
  scope: NonNullable<PickResultNotes["hot_scope"]>,
): string {
  const counted = scope.counted.length
    ? `热门依据算了 ${scope.counted.join("、")}`
    : "这批剧库里没有能算作热门依据的榜单";
  return scope.not_counted.length
    ? `${counted}；不算 ${scope.not_counted.join("、")}`
    : counted;
}

/** The same day ordering as selection.py; select evidence, never reorder candidates. */
export function primaryEvidence(
  item: Pick<PickItem, "evidence">,
  conditions: PickConditions,
): PickEvidence | undefined {
  const hot = new Set([
    "kd",
    "kw",
    "qc",
    "qr",
    "sm",
    "smd",
    "mg",
    "fh",
    "sh",
    "gh",
    "gn",
    "ghh",
    "dbn",
  ]);
  const candidates = item.evidence.filter((entry) =>
    conditions.signal_kind
      ? entry.kind === conditions.signal_kind
      : conditions.sort === "obs"
        ? entry.kind.startsWith("obs_")
        : !conditions.hot_only || hot.has(entry.kind),
  );
  return [...candidates].sort((a, b) => {
    const aDate = a.observed_at ?? "";
    const bDate = b.observed_at ?? "";
    const date = conditions.signal_kind
      ? bDate.localeCompare(aDate)
      : bDate.slice(0, 10).localeCompare(aDate.slice(0, 10));
    if (date || !conditions.signal_kind) return date;
    return (a.rank ?? Infinity) - (b.rank ?? Infinity) || 0;
  })[0];
}

export function notesReferenceLine(notes: PickResultNotes): string {
  if (notes.notices_reference_at === undefined) return "数据时效说明";
  const reference = day(notes.notices_reference_at);
  return reference
    ? `按候选生成时点核对的数据时效 · ${reference}`
    : "查询时点未知，无法核对查询时资料是否过期";
}

/** Only exact, server-owned duplicate warnings are folded; imported prose stays intact. */
export function itemChecks(
  item: PickItem,
  conditions: PickConditions,
  facts?: PickItemFacts,
) {
  const pending = new Set(item.warnings);
  if (item.availability === "unknown") pending.add("上下架状态待核实");
  const channel = conditions.channel;
  const rule = channel ? facts?.channel_rules[channel] : undefined;
  if (channel && rule !== "allowed") {
    pending.delete("目标渠道规则待核实");
    pending.add(
      rule === "denied"
        ? `${channel} 规则明确禁用`
        : `${channel} 渠道规则待核实`,
    );
  }
  const posted = postedLine(item.posted);
  if (
    item.posted?.matched &&
    item.posted.sched_count > 0 &&
    item.posted.post_count === 0
  ) {
    pending.delete(
      `发布记录显示已排期未发（${item.posted.sched_count}条待公开）`,
    );
  }
  if (item.posted && !item.posted.matched) {
    pending.delete("发布记录未对上这部剧；对不上不代表从未发布");
    if (posted) pending.add(posted);
  }
  if (item.evidence.some((entry) => !entry.observed_at))
    pending.add("部分依据日期未知");
  return {
    pending: [...pending],
    posted: item.posted?.matched ? posted : null,
    availability:
      item.availability === "active"
        ? "资料中已确认在架"
        : item.availability === "delisted"
          ? "资料中已下架"
          : null,
  };
}
