export type PickReference = {
  result_id: string;
  item_ids: string[];
};

export type ReferenceablePickResult = {
  id: string;
  thread_id: string;
  items: ReadonlyArray<{ item_id: string }>;
};

/** Freeze the visible selection before dispatch; ownership is rechecked by the API. */
export function bindPickReference(
  threadId: string,
  result: ReferenceablePickResult,
  itemIds: readonly string[],
): PickReference {
  if (result.thread_id !== threadId) {
    throw new Error("候选结果不属于当前对话");
  }
  const requested = new Set(itemIds);
  const available = new Set(result.items.map((item) => item.item_id));
  if (itemIds.some((id) => !available.has(id))) {
    throw new Error("所选条目不在这份候选结果中");
  }
  return {
    result_id: result.id,
    item_ids: result.items
      .filter((item) => requested.has(item.item_id))
      .map((item) => item.item_id),
  };
}

export function resolvePickOrdinals(
  result: ReferenceablePickResult | null,
  ordinals: readonly number[],
): PickReference {
  if (!result) throw new Error("请先选择要引用的候选结果");
  if (
    ordinals.some(
      (n) => !Number.isInteger(n) || n < 1 || n > result.items.length,
    )
  ) {
    throw new Error("序号超出这份候选结果的范围");
  }
  return bindPickReference(
    result.thread_id,
    result,
    ordinals.map((n) => result.items[n - 1]!.item_id),
  );
}

type DatedPickResult = ReferenceablePickResult & { created_at: string };

/**
 * Which result a follow-up message is about. An open panel is an explicit choice;
 * otherwise the newest finished candidate card in this thread is what the user is
 * looking at. A closed panel only wins when it is at least as new as that card.
 */
export function chooseReference(
  threadId: string,
  panel: {
    result: DatedPickResult | null;
    open: boolean;
    selected: readonly string[];
  },
  latest: DatedPickResult | null,
): PickReference | undefined {
  const own = (r: DatedPickResult | null) =>
    r?.thread_id === threadId ? r : null;
  const shown = own(panel.result);
  const newest = own(latest);
  if (shown && (panel.open || !newest || shown.created_at >= newest.created_at))
    return bindPickReference(threadId, shown, panel.selected);
  if (newest) return bindPickReference(threadId, newest, []);
  return undefined;
}
