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
