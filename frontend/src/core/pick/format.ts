import {
  isObsEvidence,
  obsConditionParts,
  obsEvidenceLine,
} from "./obs-format";
import type {
  PickConditions,
  PickDataAsOf,
  PickEvidence,
  PickPosted,
} from "./types";

export function day(value: unknown): string | null {
  if (typeof value !== "string" || value.length < 10) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? null
    : `${date.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" })} 北京时间`;
}

/** "数据截至" line: when the shared sync read the source, plus the source's own import times. */
export function dataAsOfLine(asOf: PickDataAsOf | null | undefined): string {
  if (!asOf) return "数据时点未知（个人导入的旧批次）";
  const parts = [
    `${asOf.shared ? "RealShort 同步于" : "导入于"} ${day(asOf.source_as_of) ?? day(asOf.published_at) ?? "时间未知"}`,
  ];
  const catalog = day(asOf.freshness?.catalogImportedAt);
  const reelshort = day(asOf.freshness?.reelshortSyncedAt);
  if (catalog) parts.push(`剧单导入 ${catalog}`);
  if (reelshort) parts.push(`ReelShort 指标采集 ${reelshort}`);
  return parts.join(" · ");
}

/** Source calendar dates stay dates; timestamps use the operator timezone. */
export function evidenceDate(value: string | null): string {
  return value && /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? value
    : (day(value) ?? "日期未知");
}

export function evidenceLine(evidence: PickEvidence): string {
  // obs_* entries: an unobserved value says so, never zero (premise 1, plan TR-16).
  if (isObsEvidence(evidence)) return obsEvidenceLine(evidence);
  const name = evidence.label?.trim() ? evidence.label : evidence.kind;
  const facts = [
    typeof evidence.rank === "number" ? `第${evidence.rank}名` : null,
    evidence.grade?.trim() ? `评级 ${evidence.grade}` : null,
    evidence.note?.trim() ? evidence.note : null,
    evidence.value !== null && evidence.value !== undefined
      ? String(evidence.value)
      : null,
  ].filter(Boolean);
  return `${name} · ${facts.length ? facts.join(" · ") : "数值未知"}`;
}

export function postedLine(
  posted: PickPosted | undefined,
  conditions?: Pick<PickConditions, "exclude_posted" | "posted_account">,
): string | null {
  if (!posted) return null;
  if (!posted.matched)
    // Under a posted filter the backend puts the same caution in the card warnings.
    return conditions?.exclude_posted || conditions?.posted_account?.trim()
      ? null
      : "发布记录：未对上（不代表从未发布）";
  const parts = [`已发 ${posted.post_count} 条`];
  if (posted.sched_count) parts.push(`待公开 ${posted.sched_count} 条`);
  if (posted.last_post_on) parts.push(`最近 ${posted.last_post_on}`);
  if (posted.accounts.length)
    parts.push(
      `账号 ${posted.accounts.slice(0, 3).join("、")}${posted.accounts.length > 3 ? " 等" : ""}`,
    );
  return `发布记录：${parts.join(" · ")}`;
}

export function conditionsLine(conditions: PickConditions): string {
  const parts = [
    conditions.theater?.trim() ? conditions.theater : "全部剧场",
    conditions.language?.trim() ? conditions.language : "全部语种",
  ];
  // confirmed_eligible_only filters only with a channel; the gateway's default is true.
  if (conditions.channel)
    parts.push(
      `${conditions.channel} ${conditions.confirmed_eligible_only === false ? "只排除明确禁用" : "只要确认可发"}`,
    );
  if (conditions.query?.trim()) parts.push(`关键词「${conditions.query}」`);
  if (conditions.tags?.length) parts.push(`标签 ${conditions.tags.join("、")}`);
  parts.push(conditions.exclude_selected ? "排除我的已选" : "包含我的已选");
  if (conditions.exclude_previous)
    parts.push("换一批（排除这条候选链看过的剧）");
  if (conditions.signal_kind)
    parts.push(
      `${conditions.sort === "rank" ? "按榜单名次" : "只看榜单"} ${conditions.signal_kind}`,
    );
  if (conditions.hot_only) parts.push("只要热门依据");
  if (conditions.exclude_posted) parts.push("排除团队已发");
  if (conditions.posted_account?.trim())
    parts.push(`排除账号 ${conditions.posted_account} 已发`);
  return [...parts, ...obsConditionParts(conditions)].join(" · ");
}

/**
 * What one save did, by receipt status: a removed pick comes back with this save's note, while one already in the
 * list keeps its old note (the gateway leaves it untouched).
 */
export function saveReceiptLine(
  saved: ReadonlyArray<{ status: "created" | "existing" | "restored" }>,
): string {
  const count = (status: string) =>
    saved.filter((row) => row.status === status).length;
  const parts = [
    count("created") ? `新存入 ${count("created")} 部` : null,
    count("restored")
      ? `恢复 ${count("restored")} 部（之前移出过，备注用这次的）`
      : null,
    count("existing") ? `${count("existing")} 部已在清单（备注未改）` : null,
  ].filter(Boolean);
  return `保存完成：${parts.length ? parts.join("，") : "没有新的变化"}`;
}
