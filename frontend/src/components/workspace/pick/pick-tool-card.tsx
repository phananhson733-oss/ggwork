"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/core/auth/AuthProvider";
import {
  getPickResult,
  savePickSelection,
  type SaveCommand,
} from "@/core/pick/api";
import { pickRunStatusLabel } from "@/core/pick/types";

import { usePickContext } from "./pick-context";

function parseResult(raw: unknown): {
  status?: string;
  id?: string;
  result_id?: string;
  item_ids?: string[];
  note?: string;
  requires_confirmation?: boolean;
} | null {
  try {
    const value = typeof raw === "string" ? (JSON.parse(raw) as unknown) : raw;
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

export function PickToolCard({
  result,
  threadId,
  isLoading = false,
}: {
  result: unknown;
  threadId?: string;
  isLoading?: boolean;
}) {
  const { user } = useAuth();
  const pick = usePickContext();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const command = useRef<{ key: string; payload: SaveCommand } | null>(null);
  const payload = parseResult(result);
  const id =
    typeof payload?.id === "string"
      ? payload.id
      : typeof payload?.result_id === "string"
        ? payload.result_id
        : null;
  const query = useQuery({
    queryKey: ["pick-result", user?.id, id],
    queryFn: ({ signal }) => getPickResult(id!, signal),
    enabled: !!id && !!user,
    refetchInterval: (query) =>
      ["pending", "running"].includes(query.state.data?.run_status ?? "")
        ? 1500
        : false,
  });
  const show = pick?.show;
  const data = query.data?.thread_id === threadId ? query.data : null;
  if (!id)
    return isLoading ? (
      <p role="status" className="text-muted-foreground text-sm">
        正在查询选剧资料…
      </p>
    ) : (
      <p role="alert" className="text-sm text-red-600">
        {payload?.status === "catalog_unavailable"
          ? "当前工作空间尚未接入剧库。请在「选剧资料」确认数据状态后重新提问。"
          : "选剧查询未完成，请检查资料或重试。"}
      </p>
    );
  const requested = Array.isArray(payload?.item_ids)
    ? payload.item_ids.filter(
        (value): value is string => typeof value === "string",
      )
    : [];
  const validSelection =
    data?.run_status === "success" &&
    requested.length > 0 &&
    requested.every((itemId) =>
      data.items.some((item) => item.item_id === itemId),
    );
  const confirm = async () => {
    if (!data || !validSelection) return;
    const note = typeof payload?.note === "string" ? payload.note : "";
    const key = JSON.stringify([data.id, requested, note]);
    if (command.current?.key !== key)
      command.current = {
        key,
        payload: {
          request_id: crypto.randomUUID(),
          result_id: data.id,
          item_ids: [...requested],
          note,
        },
      };
    setBusy(true);
    setError("");
    try {
      const receipt = await savePickSelection(command.current.payload);
      setFeedback(`已保存 ${receipt.saved.length} 部`);
      void client.invalidateQueries({ queryKey: ["pick-selections"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败，请重试");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="my-3 rounded-xl border p-4">
      <p className="font-medium">
        {payload?.requires_confirmation ? "确认要保存的剧目" : "选剧候选已生成"}
      </p>
      {data && (
        <p className="text-muted-foreground mt-1 text-sm">
          {payload?.requires_confirmation
            ? data.items
                .filter((item) => requested.includes(item.item_id))
                .map((item) => item.title)
                .join("、")
            : `找到 ${data.items.length} 部，点击查看依据和保存。`}
        </p>
      )}
      {data && (
        <p role="status" className="mt-2 text-sm">
          {pickRunStatusLabel[data.run_status]}
        </p>
      )}
      {payload?.requires_confirmation &&
        typeof payload.note === "string" &&
        payload.note && <p className="mt-2 text-sm">备注：{payload.note}</p>}
      {query.error && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          {query.error.message}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          {error}
        </p>
      )}
      {feedback && (
        <p role="status" className="mt-2 text-sm">
          {feedback}
        </p>
      )}
      {payload?.requires_confirmation && (
        <Button
          className="mt-3 mr-2"
          size="sm"
          disabled={!validSelection || busy || !!feedback}
          onClick={() => void confirm()}
        >
          {busy
            ? "保存中…"
            : feedback
              ? "已保存"
              : `确认保存（${requested.length}）`}
        </Button>
      )}
      <Button
        className="mt-3"
        size="sm"
        variant="outline"
        disabled={!data}
        onClick={() => data && show?.(data, requested)}
      >
        {payload?.requires_confirmation ? "查看依据" : "查看候选"}
      </Button>
    </div>
  );
}
