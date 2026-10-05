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

/** Where a sent turn keeps its pick reference: its human message's additional_kwargs (evaluation batch 2). */
export const PICK_REFERENCE_KEY = "pick_reference";

/** The stored form: the reference plus the thread it was sent in, since a branch copies messages but not results. */
export function storablePickReference(
  threadId: string,
  reference: PickReference,
): PickReference & { thread_id: string } {
  return {
    ...reference,
    item_ids: [...reference.item_ids],
    thread_id: threadId,
  };
}

type TurnMessage = {
  id?: string;
  type: string;
  name?: string;
  additional_kwargs?: Record<string, unknown>;
};

function storedReference(
  value: unknown,
  threadId: string,
): PickReference | undefined {
  if (!value || typeof value !== "object") return undefined;
  const {
    result_id: resultId,
    item_ids: itemIds,
    thread_id: sentIn,
  } = value as Record<string, unknown>;
  if (
    sentIn !== threadId ||
    typeof resultId !== "string" ||
    !Array.isArray(itemIds) ||
    itemIds.length > 20 ||
    !itemIds.every((id) => typeof id === "string")
  )
    return undefined;
  return { result_id: resultId, item_ids: [...itemIds] };
}

/**
 * Whether the gateway replays this human message as a turn's input (thread_runs._is_regenerate_human_message): not
 * a summary, and a hidden one only when it answers a human-input card. Goal continuations and other hidden control
 * messages are skipped, as the gateway skips them.
 */
function isReplayedHuman(message: TurnMessage): boolean {
  if (message.type !== "human" || message.name === "summary") return false;
  const kwargs = message.additional_kwargs ?? {};
  return kwargs.hide_from_ui === true
    ? Boolean(kwargs.human_input_response)
    : true;
}

/**
 * The pick reference the turn holding `messageId` was sent with, for a regenerate or edit replay: read from that
 * message when it is the human input, else from the nearest one before it. Undefined for a turn sent without one,
 * sent before references were stored, sent in another thread (a branch copies the messages, not the results), or
 * holding a malformed value; the replay then runs unbound as before.
 */
export function turnPickReference(
  messages: readonly TurnMessage[],
  messageId: string,
  threadId: string,
): PickReference | undefined {
  const index = messages.findIndex((message) => message.id === messageId);
  for (let at = index; at >= 0; at -= 1) {
    const message = messages[at]!;
    if (isReplayedHuman(message))
      return storedReference(
        message.additional_kwargs?.[PICK_REFERENCE_KEY],
        threadId,
      );
  }
  return undefined;
}
