"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  getPickResult,
  getSaveReceipt,
  PickApiError,
  savePickSelection,
  type SaveCommand,
} from "@/core/pick/api";
import { saveReceiptLine } from "@/core/pick/format";

import { CandidateComparisonLauncher } from "./candidate-comparison";
import { CandidateView } from "./candidate-view";
import { usePickContext } from "./pick-context";
import { useResultNotes } from "./use-result-notes";

export function CandidatePanel() {
  const pick = usePickContext();
  if (!pick?.result) return null;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 px-5 pt-4">
        <CandidateComparisonLauncher
          key={`${pick.ownerId}:${pick.result.thread_id}`}
          threadId={pick.result.thread_id}
        />
      </div>
      <OwnedCandidatePanel
        key={`${pick.ownerId}:${pick.result.thread_id}:${pick.result.id}`}
      />
    </div>
  );
}

function OwnedCandidatePanel() {
  const pick = usePickContext()!;
  const client = useQueryClient();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const pending = useRef<{ key: string; command: SaveCommand } | null>(null);
  const active = useRef(true);
  const saving = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      saving.current?.abort();
    };
  }, []);
  const snapshot = pick.result!;
  const statusQuery = useQuery({
    queryKey: [
      "pick-panel-result",
      pick.ownerId,
      snapshot.thread_id,
      snapshot.id,
    ],
    queryFn: async ({ signal }) => {
      const current = await getPickResult(snapshot.id, signal);
      if (current.thread_id !== snapshot.thread_id)
        throw new Error("候选与当前对话不一致，请重新打开来源候选");
      return current;
    },
    refetchInterval: (query) =>
      ["pending", "running"].includes(
        query.state.data?.run_status ?? snapshot.run_status,
      )
        ? 1500
        : false,
  });
  const result = statusQuery.data ?? snapshot;
  const notes = useResultNotes(snapshot.id);
  const save = async () => {
    if (
      busy ||
      statusQuery.isError ||
      result.run_status !== "success" ||
      pick.selected.length === 0
    )
      return;
    const key = JSON.stringify([result.id, [...pick.selected].sort(), note]);
    if (pending.current?.key !== key)
      pending.current = {
        key,
        command: {
          request_id: crypto.randomUUID(),
          result_id: result.id,
          item_ids: [...pick.selected],
          note,
        },
      };
    setBusy(true);
    setError("");
    setFeedback("");
    const controller = new AbortController();
    saving.current = controller;
    const command = pending.current.command;
    try {
      let receipt;
      try {
        receipt = await savePickSelection(command, controller.signal);
      } catch (error) {
        if (controller.signal.aborted || !active.current) return;
        if (error instanceof PickApiError && error.status < 500) throw error;
        // A dropped POST response may still have committed. Reconcile before retrying.
        receipt = await getSaveReceipt(command.request_id, controller.signal);
        if (!receipt)
          throw new Error(
            "保存结果尚未确认。请保留当前选择，使用同一操作重试。",
          );
      }
      if (!active.current || controller.signal.aborted) return;
      setFeedback(saveReceiptLine(receipt.saved));
      pending.current = null;
      void client.invalidateQueries({
        queryKey: ["pick-selections", pick.ownerId],
      });
    } catch (error) {
      if (!active.current || controller.signal.aborted) return;
      setError(
        error instanceof PickApiError
          ? error.message
          : "保存结果尚未确认。请保留当前选择，使用同一操作重试。",
      );
    } finally {
      if (active.current) setBusy(false);
    }
  };
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-5">
      <div className="mb-4 flex items-center justify-between">
        <span className="text-muted-foreground text-xs">
          查询于 {new Date(result.created_at).toLocaleString()}
        </span>
        <Button
          className="min-h-11"
          size="sm"
          variant="ghost"
          onClick={pick.close}
        >
          收起
        </Button>
      </div>
      <CandidateView
        result={result}
        selected={pick.selected}
        onToggle={pick.toggle}
        onSave={() => void save()}
        busy={busy || statusQuery.isError}
        notes={notes}
      />
      {statusQuery.isError && (
        <div role="alert" className="text-danger-ink mt-3">
          <p>
            无法确认候选的最新运行状态，已暂停保存。现有内容仍是查询时快照。
          </p>
          <Button
            className="mt-2 min-h-11"
            variant="outline"
            onClick={() => void statusQuery.refetch()}
          >
            重新核对状态
          </Button>
        </div>
      )}
      <label className="mt-5 block text-base">
        保存备注
        <Textarea
          className="mt-2 text-base md:text-base"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={2000}
          disabled={busy}
        />
      </label>
      {feedback && (
        <div className="mt-3 space-y-2">
          <p role="status">{feedback}</p>
          <Link
            href="/workspace/picks"
            className="text-link inline-flex min-h-11 items-center hover:underline"
          >
            查看我的选剧
          </Link>
        </div>
      )}
      {error && (
        <p role="alert" className="text-danger-ink mt-3 text-sm">
          {error}
        </p>
      )}
    </div>
  );
}
