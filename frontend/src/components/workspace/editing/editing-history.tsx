"use client";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useEditingHistory, useEditingSetup } from "@/core/editing/hooks";
import { editingLabel } from "@/core/editing/presentation";

import { EditingNotice, EditingShell, EditingStatus } from "./editing-shell";

export function EditingHistory() {
  const [offset, setOffset] = useState(0);
  const history = useEditingHistory(offset);
  const { devices } = useEditingSetup();
  return (
    <EditingShell title="剪辑">
      <div className="mb-6">
        <Button asChild className="min-h-11 text-base">
          <Link href="/workspace/editing/new">新建剪辑</Link>
        </Button>
      </div>
      {history.isPending && <p role="status">正在加载任务…</p>}
      {history.isError && (
        <EditingNotice>
          任务暂时未加载；已有记录可能不是最新状态。
          <Button variant="outline" onClick={() => void history.refetch()}>
            重试
          </Button>
        </EditingNotice>
      )}
      {history.data && (
        <>
          <p className="mb-3">共 {history.data.total} 项任务</p>
          <ul className="divide-line divide-y">
            {history.data.items.map((task) => (
              <li key={task.id} className="py-4">
                <Link
                  href={`/workspace/editing/${encodeURIComponent(task.id)}`}
                  className="text-link text-lg font-semibold underline"
                >
                  {task.title}
                </Link>
                <div className="mt-2 flex flex-wrap gap-x-6 gap-y-2">
                  <span>
                    <EditingStatus status={task.status} /> ·{" "}
                    {task.completed_count}/{task.requested_count} 条
                  </span>
                  <span>{editingLabel(task.requirements.profile)}</span>
                  <span>
                    {devices.data?.items.find(
                      (device) => device.id === task.device_id,
                    )?.name ?? "生成设备信息待读取"}
                  </span>
                  <span>{new Date(task.created_at).toLocaleString()}</span>
                </div>
                <p className="mt-2">{editingLabel(task.access_status)}</p>
              </li>
            ))}
          </ul>
          {history.data.items.length === 0 && !history.isError && (
            <p>还没有剪辑任务。选择一部剧并提供素材，就可以开始。</p>
          )}
          <nav aria-label="任务分页" className="mt-6 flex gap-3">
            <Button
              variant="outline"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 25))}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              disabled={history.data.next_offset === null}
              onClick={() => setOffset(history.data!.next_offset!)}
            >
              下一页
            </Button>
          </nav>
        </>
      )}
    </EditingShell>
  );
}
