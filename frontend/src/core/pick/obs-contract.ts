/**
 * The contract's flattening rules for observation evidence, checked strictly (plan TR-16, TR-33; D8, premise 1).
 *
 * The frontend parses evidence with the generic pickEvidenceSchema, so an old frontend reads new entries and one bad
 * entry never takes a panel down; obs-format shows such an entry without its value. This module is the TS twin of
 * ggwork_pick/observe/contract.ObsEvidence, with the same messages: contract-fixtures.test runs it on the shared
 * fixtures that Python's test_contract runs, and TR-36's validator runs it on a real result. The chat cards never
 * import it (client-bundle.test).
 */
import { z } from "zod";

import {
  EVIDENCE_KINDS,
  LINK_LABEL_TEXT,
  LINK_LABELS,
  UNOBSERVED_GSC,
  UNOBSERVED_TRENDS,
  type LinkLabel,
  type ObsKind,
} from "./obs-format";
import { LINK_STATES, STAMP_PATTERN, pickResultSchema } from "./types";

export const CONFIRMATIONS = ["confirmed", "first"] as const;
export const ADMISSIONS = ["formal", "descriptive"] as const;
export const ID_EVIDENCE_LEVELS = ["strong", "medium", "weak"] as const;
export const LINK_ACTIONABILITY_REASONS = [
  "label_not_actionable",
  "untimely_pair",
  "stale_row",
  "trends_set_too_old",
  "gsc_set_too_old",
] as const;

const obsEvidenceShape = z
  .object({
    citation_id: z.string().min(1).max(512),
    kind: z.enum(EVIDENCE_KINDS),
    source_ref: z.string().regex(/^obs:[0-9a-f]{32}:[1-9][0-9]{0,18}$/),
    observed_at: z.string().regex(new RegExp(`^${STAMP_PATTERN}$`)),
    value: z.union([z.string().max(4000), z.number().int().min(1)]),
    label: z.string().min(1).max(200),
    rank: z.null(),
    grade: z.string().max(100),
    note: z.string().min(1).max(1000),
  })
  .strict();
type ObsEvidenceEntry = z.infer<typeof obsEvidenceShape>;

/** contract.EVIDENCE_RULES: the grades each value allows (byValue null: any value), the label's scope, the rules family */
type EvidenceRule = Readonly<{
  byValue: ReadonlyMap<string, readonly string[]> | null;
  anyValue: readonly string[];
  scope: string;
  rules: "trend" | "gsc";
  unobserved: string | null;
}>;

const TREND_SCOPE = "WW|[A-Z]{2}";
const RULES: Readonly<Record<ObsKind, EvidenceRule>> = {
  obs_trends: {
    byValue: new Map<string, readonly string[]>([
      ["rising", CONFIRMATIONS],
      ["emerging", CONFIRMATIONS],
      ["cooling", [""]],
      ["", [""]],
    ]),
    anyValue: [],
    scope: TREND_SCOPE,
    rules: "trend",
    unobserved: UNOBSERVED_TRENDS,
  },
  obs_gsc: {
    byValue: null,
    anyValue: ADMISSIONS,
    scope: "ALL|[A-Z]{3}",
    rules: "gsc",
    unobserved: UNOBSERVED_GSC,
  },
  obs_discovery: {
    byValue: null,
    anyValue: ID_EVIDENCE_LEVELS,
    scope: TREND_SCOPE,
    rules: "trend",
    unobserved: null,
  },
};

// ---- the link segment of a note (contract section 4) ----------------------------------------------------------------

const LINK_NOTE_PREFIX = "联动：";
const LINKED_KINDS: readonly string[] = ["obs_trends", "obs_gsc"];
const LABEL_NOT_ACTIONABLE = "label_not_actionable";
const TIME_REASONS = LINK_ACTIONABILITY_REASONS.filter(
  (reason) => reason !== LABEL_NOT_ACTIONABLE,
);

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** A non-empty comma list of these reasons, each at most once, in this order */
function orderedReasons(reasons: readonly string[]): string {
  return reasons
    .map(
      (first, i) =>
        first +
        reasons
          .slice(i + 1)
          .map((later) => `(?:,${later})?`)
          .join(""),
    )
    .join("|");
}

/** A link_state label is actionable or not for time reasons; the other two always and only carry label_not_actionable */
function linkVerdict(label: LinkLabel): string {
  if ((LINK_STATES as readonly string[]).includes(label))
    return `可行动|不可行动：(?:${orderedReasons(TIME_REASONS)})`;
  const optional = TIME_REASONS.map((reason) => `(?:,${reason})?`).join("");
  return `不可行动：${LABEL_NOT_ACTIONABLE}${optional}`;
}

/** 联动：<LINK_LABEL_TEXT[code]>（<code>；<verdict>；判定于 <judged_at>）, anchored at the segment's start */
const LINK_SEGMENT = new RegExp(
  `^${LINK_NOTE_PREFIX}(?:${LINK_LABELS.map(
    (label) =>
      `${escapeRegExp(LINK_LABEL_TEXT[label])}（${label}；(?:${linkVerdict(label)})`,
  ).join("|")})；判定于 ${STAMP_PATTERN}）`,
);

function linkSegmentsOk(note: string): boolean {
  const starts = [...note.matchAll(new RegExp(LINK_NOTE_PREFIX, "g"))];
  return starts.every((found) =>
    LINK_SEGMENT.test(note.slice(found.index ?? 0)),
  );
}

// ---- the checks, in contract.ObsEvidence's order: the first that fails is the message ------------------------------

const join = (values: readonly string[]) =>
  values.map((v) => v || "空串").join("、");

function valueProblem(e: ObsEvidenceEntry, rule: EvidenceRule) {
  if (rule.byValue !== null) {
    const values = [...rule.byValue.keys()];
    return values.includes(e.value as string)
      ? null
      : `value 只能是 ${join(values)}`;
  }
  if (e.kind === "obs_gsc")
    return typeof e.value === "number" || e.value === ""
      ? null
      : "value 是正整数的曝光数，未观测到时为空串";
  return typeof e.value === "string" && e.value ? null : "value 是发现词";
}

function gradeProblem(e: ObsEvidenceEntry, rule: EvidenceRule) {
  const allowed =
    rule.byValue === null
      ? rule.anyValue
      : (rule.byValue.get(e.value as string) ?? []);
  return allowed.includes(e.grade)
    ? null
    : `value 为「${e.value}」时 grade 只能是 ${join(allowed)}`;
}

function labelProblem(e: ObsEvidenceEntry, rule: EvidenceRule) {
  return new RegExp(`^(${rule.scope}) · \\S`).test(e.label)
    ? null
    : "label 以 geo 或国家码加「 · 」开头";
}

function noteProblem(e: ObsEvidenceEntry, rule: EvidenceRule) {
  const head = `^${rule.rules}-rules-v[0-9A-Za-z]+；截至 ${STAMP_PATTERN}(；|$)`;
  return new RegExp(head).test(e.note)
    ? null
    : "note 以规则版本与「截至 T」开头";
}

function emptyProblem(e: ObsEvidenceEntry, rule: EvidenceRule) {
  const said = rule.unobserved === null || e.note.includes(rule.unobserved);
  return e.value !== "" || said
    ? null
    : `空值要在 note 里写明「${rule.unobserved}」`;
}

function linkProblem(e: ObsEvidenceEntry) {
  if (!LINKED_KINDS.includes(e.kind) && e.note.includes(LINK_NOTE_PREFIX))
    return "联动段只挂在 obs_trends 或 obs_gsc 上";
  return linkSegmentsOk(e.note) ? null : "联动段的写法不对";
}

type Check = (e: ObsEvidenceEntry, rule: EvidenceRule) => string | null;
const CHECKS: readonly Check[] = [
  valueProblem,
  gradeProblem,
  labelProblem,
  noteProblem,
  emptyProblem,
  linkProblem,
];

/** One obs_* evidence entry, strictly: the nine keys, then the flattening rules (contract.ObsEvidence) */
export const obsEvidenceSchema = obsEvidenceShape.superRefine((entry, ctx) => {
  const rule = RULES[entry.kind];
  const problem = CHECKS.map((check) => check(entry, rule)).find(Boolean);
  if (problem) ctx.addIssue({ code: z.ZodIssueCode.custom, message: problem });
});

function arrayAt(value: unknown, key: string): unknown[] {
  const found =
    typeof value === "object" && value !== null
      ? (value as Record<string, unknown>)[key]
      : undefined;
  return Array.isArray(found) ? found : [];
}

function isObsKindEntry(entry: unknown): boolean {
  const kind = (entry as { kind?: unknown } | null)?.kind;
  return typeof kind === "string" && kind.startsWith("obs_");
}

function evidenceIssues(payload: unknown): z.ZodIssue[] {
  return arrayAt(payload, "items").flatMap((item, i) =>
    arrayAt(item, "evidence").flatMap((entry, j) => {
      if (!isObsKindEntry(entry)) return [];
      const parsed = obsEvidenceSchema.safeParse(entry);
      if (parsed.success) return [];
      return parsed.error.issues.map((issue) => ({
        ...issue,
        path: ["items", i, "evidence", j, ...issue.path],
      }));
    }),
  );
}

/**
 * Everything wrong with a /api/pick/results payload: the strict result schema's issues, then every obs_* entry
 * (any kind starting with obs_, as Python's contract test reads it) checked against the flattening rules. Empty
 * when the payload keeps the contract.
 */
export function obsResultIssues(payload: unknown): z.ZodIssue[] {
  const parsed = pickResultSchema.safeParse(payload);
  return [
    ...(parsed.success ? [] : parsed.error.issues),
    ...evidenceIssues(payload),
  ];
}
