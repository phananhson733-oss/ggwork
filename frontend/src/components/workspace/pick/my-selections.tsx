"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/core/auth/AuthProvider";
import {
  exportSavedPicks,
  getPickResult,
  listSavedPicks,
  PickApiError,
  type SavedPick,
  updateSavedPick,
} from "@/core/pick/api";

import { CandidateView } from "./candidate-view";
import { useSelectionDrafts } from "./selection-drafts";
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
      <Button
        className="min-h-11"
        size="sm"
        variant="ghost"
        onClick={() => setOpen(!open)}
      >
        {open ? "收起来源" : "查看来源候选"}
      </Button>
      {open && query.isPending && <p role="status">正在读取历史候选…</p>}
      {open && query.error && (
        <p role="alert">
          {query.error instanceof PickApiError
            ? query.error.message
            : "历史来源暂不可用。已保存的资料快照仍保留，可稍后重试。"}
        </p>
      )}
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
  missing,
  onUpdated,
}: {
  row: SavedPick;
  missing: boolean;
  onUpdated: (row: SavedPick) => void;
}) {
  const recovery = useSelectionDrafts();
  const [base, setBase] = useState(recovery.drafts[row.id]?.base ?? row);
  const [note, setNote] = useState(recovery.drafts[row.id]?.note ?? row.note);
  const [latest, setLatest] = useState<SavedPick | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [removedElsewhere, setRemovedElsewhere] = useState(false);
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const command = useRef<{ key: string; requestId: string } | null>(null);
  const dirty = note !== base.note;
  const server = latest && latest.version > row.version ? latest : row;
  const absent = missing || removedElsewhere;
  const conflict = server.version > base.version;
  // A clean row can accept a refetch. Dirty rows keep their original version and input.
  if (conflict && !dirty && !busy && !error) {
    setBase(server);
    setNote(server.note);
  }
  const reconcile = (keep: boolean) => {
    setBase(server);
    if (!keep) setNote(server.note);
    command.current = null;
    setError("");
    setStatus("");
    recovery.setDraft(
      row.id,
      keep && note !== server.note ? { base: server, note } : null,
    );
  };
  const update = async (remove = false) => {
    if (busy || conflict || absent) return;
    const key = JSON.stringify([
      row.id,
      base.version,
      remove ? null : note,
      remove,
    ]);
    if (command.current?.key !== key)
      command.current = { key, requestId: crypto.randomUUID() };
    setBusy(true);
    setError("");
    setStatus("");
    try {
      const receipt = await updateSavedPick(row.id, {
        request_id: command.current.requestId,
        expected_version: base.version,
        ...(remove ? { state: "removed" as const } : { note }),
      });
      if (!active.current) return;
      command.current = null;
      const updated = {
        ...base,
        version: receipt.version,
        state: receipt.state,
        note: remove ? base.note : note,
      };
      setBase(updated);
      setStatus(remove ? "已移出清单" : "备注已保存");
      recovery.setDraft(row.id, null);
      onUpdated(updated);
    } catch (error) {
      if (!active.current) return;
      if (error instanceof PickApiError && error.status === 409) {
        setError("记录已在其他位置修改。本地备注已保留，请比较最新版本。");
        try {
          const current = (await listSavedPicks()).find(
            (item) => item.id === row.id,
          );
          if (!active.current) return;
          if (current) setLatest(current);
          else setRemovedElsewhere(true);
        } catch {
          if (active.current)
            setError("无法读取最新版本。本地备注已保留，请刷新清单后比较。");
        }
      } else {
        setError(
          error instanceof PickApiError
            ? error.message
            : "操作结果尚未确认。本地备注已保留，可使用同一操作重试。",
        );
      }
    } finally {
      if (active.current) setBusy(false);
    }
  };
  return (
    <article
      className="min-w-0 border-b py-5 [overflow-wrap:anywhere]"
      role="listitem"
      aria-label={row.snapshot_json.title}
    >
      <div className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_6rem] sm:items-start">
        <div>
          <h2 className="text-lg leading-[26px] font-semibold">
            {row.snapshot_json.title}
          </h2>
          <p className="text-helper mt-1">
            {row.snapshot_json.theater || "剧场未注明"} ·{" "}
            {row.snapshot_json.language || "语言未注明"}
          </p>
        </div>
        <p className="text-helper">
          保存于 {new Date(row.created_at).toLocaleString()}
        </p>
        <Button
          className="min-h-11"
          variant="ghost"
          disabled={busy || conflict || absent}
          onClick={() => void update(true)}
        >
          移出清单
        </Button>
      </div>
      <p className="my-3">{row.snapshot_json.reason}</p>
      <label className="block">
        个人备注
        <Textarea
          value={note}
          maxLength={2000}
          disabled={busy}
          onChange={(event) => {
            setNote(event.target.value);
            setStatus("");
            recovery.setDraft(
              row.id,
              event.target.value !== base.note
                ? { base, note: event.target.value }
                : null,
            );
          }}
          className="mt-2 text-base md:text-base"
        />
      </label>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <span className="text-helper">
          {busy ? "保存中…" : dirty ? "有未保存修改" : "与已保存备注一致"}
        </span>
        <Button
          className="min-h-11"
          variant="outline"
          disabled={busy || !dirty || conflict || absent}
          onClick={() => void update()}
        >
          保存备注
        </Button>
      </div>
      {absent && (
        <p role="alert" className="text-warning-ink mt-3">
          已在其他位置移出清单。本地备注和保存时的资料仍保留在此页，可复制后重新选择来源。
        </p>
      )}
      {conflict && !absent && (
        <section
          className="bg-warning-surface mt-3 space-y-3 rounded-md p-4"
          aria-label="备注版本冲突"
        >
          <p>
            服务器版本 {server.version}；本地编辑基于版本 {base.version}。
          </p>
          <p className="whitespace-pre-wrap">
            服务器备注：{server.note || "（空）"}
          </p>
          <p>你的备注保留在上方输入框；采用新版本后再明确保存。</p>
          <div className="flex flex-wrap gap-2">
            <Button
              className="min-h-11"
              variant="outline"
              onClick={() => reconcile(true)}
            >
              保留我的备注并采用新版本
            </Button>
            <Button
              className="min-h-11"
              variant="ghost"
              onClick={() => reconcile(false)}
            >
              放弃本地修改，采用服务器备注
            </Button>
          </div>
        </section>
      )}
      <SourceResult id={row.source_result_id} />
      {status && (
        <p role="status" className="mt-2">
          {status}
        </p>
      )}
      {error && (
        <p role="alert" className="text-danger-ink mt-2">
          {error}
        </p>
      )}
    </article>
  );
}

export function MySelections() {
  const { user } = useAuth();
  return user ? (
    <OwnedSelections key={user.id} ownerId={user.id} />
  ) : (
    <p>请先登录以读取个人清单。</p>
  );
}

function OwnedSelections({ ownerId }: { ownerId: string }) {
  const client = useQueryClient();
  const key = ["pick-selections", ownerId];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => listSavedPicks(signal),
  });
  const recovery = useSelectionDrafts();
  const [recovered] = useState(() => Object.keys(recovery.drafts).length > 0);
  const draftRows = Object.fromEntries(
    Object.entries(recovery.drafts).map(([id, draft]) => [id, draft.base]),
  );
  const [exportError, setExportError] = useState("");
  const [exportStatus, setExportStatus] = useState("");
  const [exportBusy, setExportBusy] = useState(false);
  const exporting = useRef<AbortController | null>(null);
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => () => exporting.current?.abort(), []);
  const hasDrafts = Object.keys(draftRows).length > 0;
  useEffect(() => {
    if (!hasDrafts) return;
    const leave = (event: MouseEvent) => {
      const link =
        event.target instanceof Element
          ? event.target.closest("a[href]")
          : null;
      if (
        !link ||
        link.hasAttribute("download") ||
        link.getAttribute("target") === "_blank" ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.altKey ||
        event.button !== 0
      )
        return;
      const destination = new URL(
        link.getAttribute("href")!,
        window.location.href,
      );
      if (
        destination.origin === window.location.origin &&
        destination.pathname === window.location.pathname &&
        destination.search === window.location.search
      )
        return;
      if (!window.confirm("有未保存的备注。离开会丢弃本地修改，是否离开？")) {
        event.preventDefault();
        event.stopPropagation();
      } else {
        recovery.discard();
      }
    };
    document.addEventListener("click", leave, true);
    return () => {
      document.removeEventListener("click", leave, true);
    };
  }, [hasDrafts, recovery]);
  const rows = [
    ...(query.data ?? []),
    ...Object.values(draftRows).filter(
      (row) => !query.data?.some((item) => item.id === row.id),
    ),
  ];
  return (
    <div className="mx-auto w-full max-w-5xl min-w-0 space-y-5 p-4 text-base leading-6 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl leading-8 font-bold">我的选剧</h1>
          <p className="text-helper mt-2">保留你的选择、依据和下一步备注。</p>
          <p className="text-helper mt-2">仅作选剧参考，未完成排期执行核对。</p>
        </div>
        <Button
          className="min-h-11"
          variant="outline"
          disabled={!query.data?.length || exportBusy}
          onClick={() => {
            setExportError("");
            setExportStatus("");
            setExportBusy(true);
            exporting.current = new AbortController();
            void exportSavedPicks(exporting.current.signal)
              .then(() => {
                if (active.current) setExportStatus("参考清单已发起下载");
              })
              .catch(() => {
                if (active.current)
                  setExportError("参考清单下载未完成，请重试。");
              })
              .finally(() => {
                if (active.current) setExportBusy(false);
              });
          }}
        >
          {exportBusy ? "准备参考清单…" : "导出参考清单"}
        </Button>
      </header>
      {recovered && hasDrafts && (
        <p role="status">
          已恢复本次工作区内未保存的备注及原版本。刷新或关闭浏览器不会保留这些修改。
        </p>
      )}
      {query.isPending && <p role="status">正在读取清单…</p>}
      {query.error && (
        <div role="alert" className="text-danger-ink">
          <p>
            {query.error instanceof PickApiError
              ? query.error.message
              : "清单读取失败，请重试。"}
          </p>
          {query.data && (
            <p>
              仍显示上次读取的清单（
              {new Date(query.dataUpdatedAt).toLocaleString()}）。
            </p>
          )}
          <Button
            className="mt-2 min-h-11"
            variant="outline"
            onClick={() => void query.refetch()}
          >
            重新读取清单
          </Button>
        </div>
      )}
      {exportError && (
        <p role="alert" className="text-danger-ink">
          {exportError}
        </p>
      )}
      {exportStatus && <p role="status">{exportStatus}</p>}
      {query.isSuccess && rows.length === 0 && (
        <div className="rounded-lg border border-dashed p-6 text-center">
          <p>还没有保存的剧目。</p>
          <Link
            href="/workspace/chats/new"
            className="text-link mt-3 inline-flex min-h-11 items-center hover:underline"
          >
            开始对话选剧
          </Link>
        </div>
      )}
      {query.data && (
        <p>
          清单共 {query.data.length} 部
          {Object.keys(draftRows).some(
            (id) => !query.data?.some((row) => row.id === id),
          )
            ? "，另保留已移出条目的本地修改"
            : ""}
        </p>
      )}
      {rows.length > 0 && (
        <section role="list" aria-label="个人选剧清单" className="border-t">
          <div
            className="text-helper hidden grid-cols-[minmax(0,2fr)_minmax(0,1fr)_6rem] gap-3 border-b py-3 sm:grid"
            aria-hidden="true"
          >
            <span>剧目与个人备注</span>
            <span>保存时间</span>
            <span>操作</span>
          </div>
          {rows.map((row) => (
            <SavedRow
              key={row.id}
              row={row}
              missing={
                !!query.data && !query.data.some((item) => item.id === row.id)
              }
              onUpdated={(updated) => {
                client.setQueryData<SavedPick[]>(key, (current) =>
                  current?.flatMap((item) =>
                    item.id !== updated.id
                      ? [item]
                      : updated.state === "removed"
                        ? []
                        : [updated],
                  ),
                );
                void client.invalidateQueries({ queryKey: key });
              }}
            />
          ))}
        </section>
      )}
    </div>
  );
}
