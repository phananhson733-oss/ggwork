import { z } from "zod";

import { fetch as fetchWithAuth } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import { pickAnswerCheckSchema } from "./answer-checks";
import {
  completionErrorSchema,
  type CompletionError,
} from "./completion-types";
import { type PickNotesState, pickResultNotesSchema } from "./notes";
import { syncStatusSchema } from "./sync-schema";
import { pickItemSchema, pickResultSchema } from "./types";

export type SaveCommand = {
  request_id: string;
  result_id: string;
  item_ids: string[];
  note: string;
};
const receiptSchema = z.object({
  request_id: z.string(),
  saved: z.array(
    z.object({
      id: z.string(),
      identity: z.string(),
      status: z.enum(["created", "existing", "restored"]),
      version: z.number().int(),
    }),
  ),
});
export type SaveReceipt = z.infer<typeof receiptSchema>;
const selectionSchema = z.object({
  id: z.string(),
  identity: z.string(),
  source_result_id: z.string(),
  source_item_id: z.string(),
  snapshot_json: pickItemSchema,
  note: z.string(),
  state: z.enum(["selected", "removed"]),
  version: z.number().int(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type SavedPick = z.infer<typeof selectionSchema>;
const batchSchema = z.object({
  id: z.string(),
  kind: z.enum(["catalog", "knowledge"]),
  status: z.enum(["importing", "published", "failed", "pruned"]),
  shared: z.boolean().optional(),
  content_hash: z.string(),
  source_as_of: z.string().nullable(),
  created_at: z.string(),
  published_at: z.string().nullable(),
  validation_json: z
    .object({ rows: z.number().int().nonnegative() })
    .passthrough(),
});
export type PickBatch = z.infer<typeof batchSchema>;
// /sync's shape lives in the pure sync-schema module, shared with the
// server-side pick data board (critique B14).
export type { PickSyncStatus } from "./sync-schema";

export class PickApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: CompletionError["code"],
    readonly currentVersion?: number | null,
    readonly retryable?: boolean,
  ) {
    super(message);
    this.name = "PickApiError";
  }
}

export async function responseFor(path: string, init?: RequestInit) {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/pick${path}`,
    init,
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    const detail = completionErrorSchema.safeParse(body?.detail);
    throw new PickApiError(
      detail.success
        ? detail.data.message
        : typeof body?.detail === "string" && body.detail.length <= 1000
          ? body.detail
          : `操作未完成（${response.status}），请检查资料或重试`,
      response.status,
      detail.success ? detail.data.code : undefined,
      detail.success ? detail.data.current_version : undefined,
      detail.success ? detail.data.retryable : undefined,
    );
  }
  return response;
}

function saveReceipt(body: unknown) {
  const receipt = receiptSchema.safeParse(body);
  if (!receipt.success)
    throw new Error("保存回执无效，请查询回执或使用同一操作重试");
  return receipt.data;
}

/** A missing receipt is an unknown outcome, never proof that the write failed. */
export async function getSaveReceipt(requestId: string, signal?: AbortSignal) {
  try {
    return saveReceipt(
      await (
        await responseFor(`/commands/${encodeURIComponent(requestId)}`, {
          signal,
        })
      ).json(),
    );
  } catch (error) {
    if (error instanceof PickApiError && error.status === 404) return null;
    throw error;
  }
}

export async function listPickResults(threadId: string, signal?: AbortSignal) {
  const response = await responseFor(
    `/results?thread_id=${encodeURIComponent(threadId)}`,
    { signal },
  );
  return z
    .object({ results: z.array(pickResultSchema) })
    .parse(await response.json()).results;
}

export async function getPickResult(id: string, signal?: AbortSignal) {
  return pickResultSchema.parse(
    await (
      await responseFor(`/results/${encodeURIComponent(id)}`, { signal })
    ).json(),
  );
}

/**
 * A result's notes for the card. A 404 (no such result, or a gateway older
 * than the endpoint) is "none" and shows nothing; a 410 means the result's
 * batch was pruned. Any other failure throws.
 */
export async function getPickResultNotes(
  id: string,
  signal?: AbortSignal,
): Promise<PickNotesState> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/pick/results/${encodeURIComponent(id)}/notes`,
    { signal },
  );
  if (response.status === 404) return { kind: "none" };
  if (response.status === 410) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    return {
      kind: "gone",
      message:
        typeof body?.detail === "string"
          ? body.detail
          : "这份候选用的剧库批次已被清理，依据说明不可用",
    };
  }
  if (!response.ok) throw new Error(`依据说明读取失败（${response.status}）`);
  return {
    kind: "notes",
    notes: pickResultNotesSchema.parse(await response.json()),
  };
}

export async function savePickSelection(
  command: SaveCommand,
  signal?: AbortSignal,
) {
  return saveReceipt(
    await (
      await responseFor("/selections", {
        signal,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(command),
      })
    ).json(),
  );
}

export async function listSavedPicks(signal?: AbortSignal) {
  return z
    .object({ selections: z.array(selectionSchema) })
    .parse(await (await responseFor("/selections", { signal })).json())
    .selections;
}

export async function updateSavedPick(
  id: string,
  command: {
    request_id: string;
    expected_version: number;
    note?: string;
    state?: "removed";
  },
) {
  const body: unknown = await (
    await responseFor(`/selections/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
    })
  ).json();
  const receipt = z
    .object({
      request_id: z.string(),
      id: z.string(),
      version: z.number().int().positive(),
      state: z.enum(["selected", "removed"]),
    })
    .safeParse(body);
  if (!receipt.success) throw new Error("保存回执无效，请使用同一操作重试");
  return receipt.data;
}

export async function listPickBatches(signal?: AbortSignal) {
  return z
    .object({ batches: z.array(batchSchema) })
    .parse(await (await responseFor("/imports", { signal })).json()).batches;
}

export async function listPickAnswerChecks(
  threadId: string,
  signal?: AbortSignal,
) {
  const response = await responseFor(
    `/answer-checks?thread_id=${encodeURIComponent(threadId)}`,
    { signal },
  );
  return z
    .object({ checks: z.array(pickAnswerCheckSchema) })
    .parse(await response.json()).checks;
}

export async function getPickSyncStatus(signal?: AbortSignal) {
  return syncStatusSchema.parse(
    await (await responseFor("/sync", { signal })).json(),
  );
}

export async function startPickSync() {
  return z
    .object({ status: z.enum(["started", "already_running"]) })
    .parse(await (await responseFor("/sync", { method: "POST" })).json());
}

export async function importPickData(
  kind: "catalog" | "knowledge",
  files: File[],
  sourceRef: string,
) {
  const form = new FormData();
  form.set("kind", kind);
  form.set("source_ref", sourceRef);
  files.forEach((file) => form.append("files", file));
  return batchSchema.parse(
    await (
      await responseFor("/imports", { method: "POST", body: form })
    ).json(),
  );
}

export async function exportSavedPicks(signal?: AbortSignal) {
  const blob = await (
    await responseFor("/selections/export.csv", { signal })
  ).blob();
  signal?.throwIfAborted();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "我的选剧.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
