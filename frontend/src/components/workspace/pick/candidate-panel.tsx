"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  getPickResult,
  savePickSelection,
  type SaveCommand,
} from "@/core/pick/api";

import { CandidateView } from "./candidate-view";
import { usePickContext } from "./pick-context";
import { useResultNotes } from "./use-result-notes";

export function CandidatePanel() {
  const pick = usePickContext();
  if (!pick?.result) return null;
  return <OwnedCandidatePanel key={pick.result.id} />;
}

function OwnedCandidatePanel() {
  const pick = usePickContext()!;
  const client = useQueryClient();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const pending = useRef<{ key: string; command: SaveCommand } | null>(null);
  const snapshot = pick.result!;
  const statusQuery = useQuery({
    queryKey: ["pick-panel-result", snapshot.id],
    queryFn: ({ signal }) => getPickResult(snapshot.id, signal),
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
    if (result.run_status !== "success") return;
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
    try {
      const receipt = await savePickSelection(pending.current.command);
      setFeedback(
        `已保存 ${receipt.saved.length} 部（其中 ${receipt.saved.filter((row) => row.status === "existing").length} 部已在清单）`,
      );
      pending.current = null;
      void client.invalidateQueries({ queryKey: ["pick-selections"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败，请重试");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mb-4 flex items-center justify-between">
        <span className="text-muted-foreground text-xs">
          查询于 {new Date(result.created_at).toLocaleString()}
        </span>
        <Button size="sm" variant="ghost" onClick={pick.close}>
          收起
        </Button>
      </div>
      <CandidateView
        result={result}
        selected={pick.selected}
        onToggle={pick.toggle}
        onSave={() => void save()}
        busy={busy}
        notes={notes}
      />
      <label className="mt-5 block text-sm">
        保存备注
        <Textarea
          className="mt-2"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={2000}
          disabled={busy}
        />
      </label>
      {feedback && (
        <p role="status" className="mt-3 text-sm">
          {feedback}
        </p>
      )}
      {error && (
        <p role="alert" className="text-danger-ink mt-3 text-sm">
          {error}
        </p>
      )}
    </div>
  );
}
