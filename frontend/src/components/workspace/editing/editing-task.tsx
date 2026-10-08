"use client";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { editingAction } from "@/core/editing/api";
import {
  editingKeys,
  useEditingOwner,
  useEditingTask,
} from "@/core/editing/hooks";
import {
  editingLabel,
  editingTaskId,
  safeEditingReturn,
} from "@/core/editing/presentation";

import { EditingForm } from "./editing-form";
import { EditingOutput } from "./editing-output";
import { EditingPrepare } from "./editing-prepare";
import { EditingNotice } from "./editing-shell";

export function EditingTaskView({
  taskId,
  compact = false,
  returnTo = null,
}: {
  taskId: string;
  compact?: boolean;
  returnTo?: string | null;
}) {
  const query = useEditingTask(taskId);
  const { owner, current, expire } = useEditingOwner();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(false);
  const active = useRef<AbortController | null>(null);
  const retryKeys = useRef(new Map<string, string>());
  useEffect(() => () => active.current?.abort(), [taskId]);
  const task = query.data;
  const back = safeEditingReturn(returnTo);
  async function action(
    kind: "stop" | "retry" | "confirm-plan",
    outputId?: string,
  ) {
    if (busy) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    const retryScope = outputId ?? task?.stage ?? "planning";
    if (!retryKeys.current.has(retryScope))
      retryKeys.current.set(retryScope, crypto.randomUUID());
    const payload =
      kind === "retry"
        ? {
            request_id: retryKeys.current.get(retryScope),
            ...(outputId
              ? { output_ids: [outputId] }
              : {
                  stage:
                    task?.stage === "transcribing"
                      ? "transcribing"
                      : "planning",
                }),
          }
        : {};
    try {
      const result = await editingAction(
        taskId,
        kind,
        payload,
        controller.signal,
      );
      if (current.current === owner && !controller.signal.aborted) {
        client.setQueryData(editingKeys.task(owner, taskId), result);
        retryKeys.current.delete(retryScope);
      }
    } catch (error) {
      expire(error);
      if (current.current === owner && !controller.signal.aborted)
        setError(error instanceof Error ? error.message : "操作未完成");
    } finally {
      if (current.current === owner && !controller.signal.aborted)
        setBusy(false);
    }
  }
  if (query.isPending) return <p role="status">正在读取当前剪辑状态…</p>;
  if (!task)
    return (
      <EditingNotice>
        任务暂时未加载。
        <Button variant="outline" onClick={() => void query.refetch()}>
          重试
        </Button>
      </EditingNotice>
    );
  return (
    <article className="space-y-6">
      {query.isError && (
        <EditingNotice>
          当前状态更新失败，以下是上次读取的记录。
          <Button variant="outline" onClick={() => void query.refetch()}>
            刷新
          </Button>
        </EditingNotice>
      )}
      <header className="space-y-3">
        <h2 className="text-xl font-semibold">{task.title}</h2>
        <p role="status" className="font-medium">
          {editingLabel(task.status)} · {task.completed_count}/
          {task.requested_count} 条
        </p>
        <p>
          {editingLabel(task.requirements.profile)} ·{" "}
          {task.requirements.duration_seconds} 秒 ·{" "}
          {task.requirements.aspect_ratio}
        </p>
        <p className="whitespace-pre-wrap">{task.requirements.instructions}</p>
        <p>当前阶段：{editingLabel(task.stage)}</p>
        {task.device_status === "offline" && (
          <p>
            {task.status === "completed" || task.status === "partial"
              ? "生成设备离线，已交付的成片记录保留，暂不可预览或下载。"
              : "设备离线，执行状态待确认。请在 Mac 启动执行器；最后确认的阶段保留。"}
          </p>
        )}
        {task.device_status === "revoked" && (
          <p>设备授权已撤销，请重新授权生成设备。</p>
        )}
        {task.preparation_reasons.map((reason) => (
          <p key={reason}>{editingLabel(reason)}</p>
        ))}
        <nav className="flex flex-wrap gap-4">
          {back && (
            <Link href={back} className="text-link underline">
              返回原选剧列表
            </Link>
          )}
          {task.source_thread_id && (
            <Link
              className="text-link underline"
              href={`/workspace/chats/${encodeURIComponent(task.source_thread_id)}`}
            >
              来源对话
            </Link>
          )}
          {task.parent_task_id && (
            <Link
              className="text-link underline"
              href={`/workspace/editing/${encodeURIComponent(task.parent_task_id)}`}
            >
              来源版本（原版保留）
            </Link>
          )}
          {compact && (
            <Link
              className="text-link underline"
              href={`/workspace/editing/${encodeURIComponent(task.id)}`}
            >
              打开剪辑详情
            </Link>
          )}
        </nav>
      </header>
      {error && <EditingNotice>{error}</EditingNotice>}
      {!query.isError && (
        <div className="flex flex-wrap gap-3">
          {task.available_actions.includes("stop") && (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => void action("stop")}
            >
              停止剪辑
            </Button>
          )}
          {task.available_actions.includes("retry") &&
            task.completed_count === 0 &&
            ["transcribing", "planning"].includes(task.stage) && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => void action("retry")}
              >
                重试该阶段
              </Button>
            )}
        </div>
      )}
      {!compact && task.status === "waiting" && !query.isError && (
        <EditingPrepare task={task} onChange={() => void query.refetch()} />
      )}
      {!compact && task.plan && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">剪辑方案</h2>
          {task.plan.outputs.map((output) => (
            <div key={output.output_id}>
              <h3>成片 {output.output_id.replace("out-", "")}</h3>
              <ol className="list-inside list-decimal">
                {output.segments.map((segment, index) => (
                  <li key={index}>
                    {task.source_manifest?.files.find(
                      (source) => source.media_id === segment.media_id,
                    )?.name ?? segment.media_id}
                    ：{segment.start.toFixed(1)}–{segment.end.toFixed(1)} 秒
                  </li>
                ))}
              </ol>
            </div>
          ))}
          {task.available_actions.includes("confirm_plan") &&
            !query.isError && (
              <Button
                disabled={busy}
                onClick={() => void action("confirm-plan")}
              >
                确认方案，开始渲染
              </Button>
            )}
        </section>
      )}
      {!compact && (
        <>
          <section>
            <h2 className="text-lg font-semibold">
              成片 · {task.completed_count}/{task.requested_count} 条已交付
            </h2>
            <ul>
              {task.outputs.map((output) => (
                <EditingOutput
                  key={output.id}
                  task={task}
                  output={output}
                  busy={busy || query.isError}
                  onRetry={() => void action("retry", output.id)}
                />
              ))}
            </ul>
          </section>
          <section className="space-y-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => setVersion((value) => !value)}
            >
              {version ? "收起新版本表单" : "调整要求，创建新版本"}
            </Button>
            {version && <EditingForm key={task.id} parent={task} />}
          </section>
          <details>
            <summary className="cursor-pointer py-3">
              任务记录与连接说明
            </summary>
            <p>创建时间：{new Date(task.created_at).toLocaleString()}</p>
            <p>任务标识：{task.id}</p>
            <p>
              转录和渲染在 Mac
              上运行。已受理的处理可在离开页面后继续；在线预览和下载需要生成设备在线、授权有效且原文件仍存在。
            </p>
            <p>
              请在生成设备运行 ggwork-edit-worker doctor 检查环境，再运行
              ggwork-edit-worker run 保持连接。
            </p>
          </details>
        </>
      )}
    </article>
  );
}
export function EditingToolCard({
  result,
  isLoading = false,
}: {
  result: unknown;
  isLoading?: boolean;
}) {
  const id = editingTaskId(result);
  return (
    <section className="my-3 rounded-lg border p-4 text-base">
      <p className="mb-3 font-medium">
        剪辑任务 · 当前状态（历史叙述保留原意）
      </p>
      {id ? (
        <EditingTaskView key={id} taskId={id} compact />
      ) : (
        <p>
          {isLoading
            ? "正在提交剪辑请求…"
            : "未返回单项任务；请查看工具结果或打开剪辑历史。"}
          <Link href="/workspace/editing" className="text-link ml-2 underline">
            剪辑历史
          </Link>
        </p>
      )}
    </section>
  );
}
