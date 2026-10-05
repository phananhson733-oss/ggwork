"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/core/auth/AuthProvider";
import {
  exportSavedPicks,
  getPickResult,
  listSavedPicks,
  type SavedPick,
  updateSavedPick,
} from "@/core/pick/api";

import { CandidateView } from "./candidate-view";
import { useResultNotes } from "./use-result-notes";

function SourceResult({ id }: { id: string }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ["pick-result", user?.id, id],
    queryFn: ({ signal }) => getPickResult(id, signal),
    enabled: !!user && open,
  });
  const notes = useResultNotes(open ? id : undefined);
  return (
    <div className="mt-3">
      <Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>
        {open ? "收起来源" : "查看来源候选"}
      </Button>
      {open && query.isPending && <p role="status">正在读取历史候选…</p>}
      {open && query.error && <p role="alert">{query.error.message}</p>}
      {open && query.data && (
        <div className="mt-3 border-t pt-4">
          <Link
            href={`/workspace/chats/${encodeURIComponent(query.data.thread_id)}`}
            className="text-link mb-3 inline-block text-sm hover:underline"
          >
            打开来源对话
          </Link>
          <CandidateView
            result={query.data}
            selected={[]}
            onToggle={() => undefined}
            onSave={() => undefined}
            busy={false}
            readOnly
            notes={notes}
          />
        </div>
      )}
    </div>
  );
}

function SavedRow({
  row,
  onUpdated,
}: {
  row: SavedPick;
  onUpdated: () => void;
}) {
  const [note, setNote] = useState(row.note);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const command = useRef<{ key: string; requestId: string } | null>(null);
  const update = async (remove = false) => {
    const key = JSON.stringify([row.id, row.version, note, remove]);
    if (command.current?.key !== key)
      command.current = { key, requestId: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      await updateSavedPick(row.id, {
        request_id: command.current.requestId,
        expected_version: row.version,
        ...(remove ? { state: "removed" as const } : { note }),
      });
      command.current = null;
      onUpdated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="bg-card rounded-lg border p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[15px] font-semibold">
            {row.snapshot_json.title}
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            {row.snapshot_json.theater} · {row.snapshot_json.language}
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => void update(true)}
        >
          移出清单
        </Button>
      </div>
      <p className="my-3 text-sm">{row.snapshot_json.reason}</p>
      <label className="text-sm">
        个人备注
        <Textarea
          value={note}
          maxLength={2000}
          onChange={(event) => setNote(event.target.value)}
          className="mt-2"
        />
      </label>
      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="text-muted-foreground text-xs">
          保存于 {new Date(row.created_at).toLocaleString()}
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={busy || note === row.note}
          onClick={() => void update()}
        >
          保存备注
        </Button>
      </div>
      <SourceResult id={row.source_result_id} />
      {error && (
        <p role="alert" className="text-danger-ink mt-2 text-sm">
          {error}
        </p>
      )}
    </article>
  );
}

export function MySelections() {
  const { user } = useAuth();
  const client = useQueryClient();
  const key = ["pick-selections", user?.id];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => listSavedPicks(signal),
    enabled: !!user,
  });
  const [exportError, setExportError] = useState("");
  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 p-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold tracking-[-0.01em]">我的选剧</h1>
          <p className="text-muted-foreground mt-2 text-sm">
            保留你的选择、依据和下一步备注。
          </p>
        </div>
        <Button
          variant="outline"
          disabled={!query.data?.length}
          onClick={() => {
            setExportError("");
            void exportSavedPicks().catch((e: Error) =>
              setExportError(e.message),
            );
          }}
        >
          导出 CSV
        </Button>
      </header>
      {query.isPending && <p role="status">正在读取清单…</p>}
      {(query.error !== null || exportError.length > 0) && (
        <p role="alert" className="text-danger-ink">
          {query.error?.message ?? exportError}
        </p>
      )}
      {query.data?.length === 0 && (
        <div className="rounded-lg border border-dashed p-10 text-center">
          <p>还没有保存的剧目。</p>
          <Link
            href="/workspace/chats/new"
            className="text-link mt-3 inline-block hover:underline"
          >
            开始对话选剧
          </Link>
        </div>
      )}
      {query.data?.map((row) => (
        <SavedRow
          key={`${row.id}:${row.version}`}
          row={row}
          onUpdated={() => void client.invalidateQueries({ queryKey: key })}
        />
      ))}
    </div>
  );
}
