import { z } from "zod";

import { fetch as fetchWithAuth } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

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
  status: z.enum(["importing", "published", "failed"]),
  content_hash: z.string(),
  source_as_of: z.string().nullable(),
  created_at: z.string(),
  published_at: z.string().nullable(),
  validation_json: z
    .object({ rows: z.number().int().nonnegative() })
    .passthrough(),
});
export type PickBatch = z.infer<typeof batchSchema>;

async function responseFor(path: string, init?: RequestInit) {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/pick${path}`,
    init,
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `操作未完成（${response.status}），请检查资料或重试`,
    );
  }
  return response;
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

export async function savePickSelection(command: SaveCommand) {
  return receiptSchema.parse(
    await (
      await responseFor("/selections", {
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
  return (
    await responseFor(`/selections/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
    })
  ).json();
}

export async function listPickBatches(signal?: AbortSignal) {
  return z
    .object({ batches: z.array(batchSchema) })
    .parse(await (await responseFor("/imports", { signal })).json()).batches;
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

export async function exportSavedPicks() {
  const blob = await (await responseFor("/selections/export.csv")).blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "我的选剧.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
