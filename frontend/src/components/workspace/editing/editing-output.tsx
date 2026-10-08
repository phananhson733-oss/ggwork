"use client";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { editingRequest, outputURL } from "@/core/editing/api";
import { editingKeys, useEditingOwner } from "@/core/editing/hooks";
import { editingLabel } from "@/core/editing/presentation";
import { type EditingTask, type Output } from "@/core/editing/types";

import { EditingNotice } from "./editing-shell";

export function EditingOutput({
  task,
  output,
  onRetry,
  busy,
}: {
  task: EditingTask;
  output: Output;
  onRetry: () => void;
  busy: boolean;
}) {
  const { owner, expire } = useEditingOwner();
  const [requested, setRequested] = useState(false);
  const [check, setCheck] = useState(0);
  const [play, setPlay] = useState(false);
  const [interrupted, setInterrupted] = useState(false);
  const online = task.device_status === "online";
  const complete = output.status === "completed" && output.result?.verified;
  const access = useQuery({
    queryKey: [...editingKeys.task(owner, task.id), "access", output.id, check],
    queryFn: ({ signal }) =>
      editingRequest<{ access_status: string }>(
        `/tasks/${encodeURIComponent(task.id)}/outputs/${encodeURIComponent(output.id)}/access`,
        { signal },
      ),
    enabled: !!owner && online && !!complete && requested,
    gcTime: 0,
    retry: false,
    refetchInterval: requested && online ? 15000 : false,
  });
  useEffect(() => {
    expire(access.error);
  }, [access.error]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setRequested(false);
    setPlay(false);
    setInterrupted(false);
  }, [online, owner, task.id, output.id]);
  const authorized =
    requested &&
    online &&
    access.data?.access_status === "available" &&
    !access.isError;
  const available = authorized && !access.isFetching;
  return (
    <li className="space-y-3 border-b py-4">
      <h3 className="text-lg font-medium">
        成片 {output.id.replace("out-", "")} · {editingLabel(output.status)}
      </h3>
      {complete && (
        <>
          <p>
            {online
              ? !requested
                ? "检查成片访问后可预览或下载，完成记录不受影响"
                : access.isPending
                  ? "正在检查生成设备上的文件…"
                  : access.isError
                    ? "成片访问检查失败，原成片记录仍保留"
                    : editingLabel(access.data?.access_status ?? "unchecked")
              : editingLabel(task.access_status)}
          </p>
          {available && (
            <div className="flex flex-wrap gap-3">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setPlay(true);
                  setInterrupted(false);
                }}
              >
                预览成片
              </Button>
              <Button asChild variant="outline">
                <a href={outputURL(task.id, output.id, true)} download>
                  下载成片
                </a>
              </Button>
            </div>
          )}
          {online && (!available || interrupted) && (
            <Button
              type="button"
              variant="outline"
              disabled={access.isFetching}
              onClick={() => {
                setRequested(true);
                setCheck((value) => value + 1);
                setPlay(false);
                setInterrupted(false);
              }}
            >
              {requested ? "重新检查文件" : "检查成片访问"}
            </Button>
          )}
          {play && authorized && !interrupted && (
            <video
              controls
              preload="metadata"
              aria-label={`成片 ${output.id} 预览`}
              className="max-h-[60vh] w-full rounded-lg bg-black"
              src={outputURL(task.id, output.id)}
              onError={() => setInterrupted(true)}
            />
          )}
          {interrupted && (
            <EditingNotice>
              视频传输中断。原成片仍保留，请重新检查文件后重试播放或下载；无需重新剪辑。
            </EditingNotice>
          )}
        </>
      )}
      {output.error && (
        <p className="text-danger-ink">{editingLabel(output.error)}</p>
      )}
      {(output.status === "failed" ||
        (output.status === "stopped" && task.plan && task.plan_confirmed)) &&
        task.available_actions.includes("retry") && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onRetry}
          >
            重试此条
          </Button>
        )}
    </li>
  );
}
