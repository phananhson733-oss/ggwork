import type { PickItem, PickResult } from "./types";

export type ComparisonSource = { result_id: string; item: PickItem };
export type ComparisonRow = {
  identity: string;
  first?: ComparisonSource;
  second?: ComparisonSource;
  ambiguousTitle: boolean;
};
export type ComparisonChoices = Record<string, "first" | "second" | undefined>;

/** Only owner-authorized HTTP results enter here; visual matching grants no authorization. */
export function alignCandidates(
  threadId: string,
  first: PickResult,
  second: PickResult,
): ComparisonRow[] {
  if (
    first.id === second.id ||
    [first, second].some(
      (result) =>
        result.thread_id !== threadId || result.run_status !== "success",
    )
  )
    throw new Error("请选择当前对话中两份不同的已完成候选");
  const rows = new Map<string, ComparisonRow>();
  for (const [side, result] of [
    ["first", first],
    ["second", second],
  ] as const) {
    for (const item of result.items) {
      const row = rows.get(item.identity) ?? {
        identity: item.identity,
        ambiguousTitle: false,
      };
      if (row[side]) throw new Error("候选身份重复，请重新核对来源");
      row[side] = { result_id: result.id, item };
      rows.set(item.identity, row);
    }
  }
  const titles = new Map<string, Set<string>>();
  for (const row of rows.values())
    for (const source of [row.first, row.second]) {
      if (!source) continue;
      const key = source.item.title.trim().toLocaleLowerCase();
      const identities = titles.get(key) ?? new Set<string>();
      identities.add(row.identity);
      titles.set(key, identities);
    }
  return [...rows.values()].map((row) => ({
    ...row,
    ambiguousTitle: [row.first, row.second].some(
      (source) =>
        source &&
        (titles.get(source.item.title.trim().toLocaleLowerCase())?.size ?? 0) >
          1,
    ),
  }));
}

/** One explicit evidence choice per stable identity; every command remains single-batch. */
export function selectionGroups(
  rows: ComparisonRow[],
  choices: ComparisonChoices,
) {
  const groups = new Map<string, string[]>();
  for (const row of rows) {
    const side = choices[row.identity];
    const source = side ? row[side] : undefined;
    if (!source) continue;
    const ids = groups.get(source.result_id) ?? [];
    ids.push(source.item.item_id);
    groups.set(source.result_id, ids);
  }
  return [...groups].map(([result_id, item_ids]) => ({ result_id, item_ids }));
}
