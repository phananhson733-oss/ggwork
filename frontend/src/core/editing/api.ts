import { fetch as authenticatedFetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  Capabilities,
  CreateTask,
  Device,
  EditingTask,
  Manifest,
  SourceDirectory,
  TaskPage,
} from "./types";

export class EditingError extends Error {
  constructor(
    public status: number,
    public code: string,
  ) {
    super(
      status === 403
        ? "当前操作未获授权，请检查登录和设备授权"
        : status === 404
          ? "任务或文件不可用，请刷新后检查"
          : status === 409
            ? "状态已变化或设备暂不可用，请刷新后重试"
            : "操作未完成，请检查连接后重试",
    );
  }
}
export function editingURL(path: string) {
  return `${getBackendBaseURL()}/api/editing${path}`;
}
export async function editingRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await authenticatedFetch(editingURL(path), init);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    const code =
      typeof body?.detail === "string" && /^[a-z_]+$/.test(body.detail)
        ? body.detail
        : "request_failed";
    throw new EditingError(response.status, code);
  }
  return (await response.json()) as T;
}
export const listEditingTasks = (offset = 0, signal?: AbortSignal) =>
  editingRequest<TaskPage>(`/tasks?limit=25&offset=${offset}`, { signal });
export const getEditingTask = (id: string, signal?: AbortSignal) =>
  editingRequest<EditingTask>(`/tasks/${encodeURIComponent(id)}`, { signal });
export const getEditingDevices = (signal?: AbortSignal) =>
  editingRequest<{ items: Device[] }>("/devices", { signal });
export const getEditingCapabilities = (signal?: AbortSignal) =>
  editingRequest<Capabilities>("/capabilities", { signal });
function post<T>(path: string, payload: unknown, signal?: AbortSignal) {
  return editingRequest<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}
export const createEditingTask = (payload: CreateTask, signal?: AbortSignal) =>
  post<EditingTask>("/tasks", payload, signal);
export const registerEditingDevice = (name: string, signal?: AbortSignal) =>
  post<{ device: Device; token: string }>("/devices", { name }, signal);
export const prepareEditingTask = (
  id: string,
  payload: {
    device_id: string;
    source_manifest?: Manifest;
    source_directory?: SourceDirectory;
  },
  signal?: AbortSignal,
) =>
  post<EditingTask>(
    `/tasks/${encodeURIComponent(id)}/prepare`,
    payload,
    signal,
  );
export const editingAction = (
  id: string,
  action: "stop" | "retry" | "confirm-plan",
  payload: unknown = {},
  signal?: AbortSignal,
) =>
  post<EditingTask>(
    `/tasks/${encodeURIComponent(id)}/${action}`,
    payload,
    signal,
  );
export function outputURL(taskId: string, outputId: string, download = false) {
  return editingURL(
    `/tasks/${encodeURIComponent(taskId)}/outputs/${encodeURIComponent(outputId)}/content${download ? "?download=true" : ""}`,
  );
}

type Transfer = {
  transfer_id: string;
  offset: number;
  size_bytes: number;
  state: string;
  chunk_bytes: number;
};
export async function uploadSource(
  taskId: string,
  mediaId: string,
  file: Blob,
  signal: AbortSignal,
  onProgress: (bytes: number) => void,
) {
  let transfer = await post<Transfer>(
    `/tasks/${encodeURIComponent(taskId)}/uploads`,
    { media_id: mediaId },
    signal,
  );
  const chunkBytes = transfer.chunk_bytes;
  try {
    while (transfer.offset < file.size) {
      const expected = Math.min(transfer.offset + chunkBytes, file.size);
      const next = await editingRequest<Transfer>(
        `/uploads/${encodeURIComponent(transfer.transfer_id)}?offset=${transfer.offset}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/octet-stream" },
          body: file.slice(transfer.offset, expected),
          signal,
        },
      );
      if (next.offset !== expected)
        throw new EditingError(409, "transfer_failed");
      transfer = next;
      onProgress(transfer.offset);
    }
    return transfer;
  } catch (error) {
    if (!signal.aborted)
      await editingRequest(
        `/uploads/${encodeURIComponent(transfer.transfer_id)}`,
        { method: "DELETE", signal },
      ).catch(() => undefined);
    throw error;
  }
}
