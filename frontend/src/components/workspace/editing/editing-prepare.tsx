"use client";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { prepareEditingTask, uploadSource } from "@/core/editing/api";
import { useEditingOwner, useEditingSetup } from "@/core/editing/hooks";
import { editingLabel } from "@/core/editing/presentation";
import type { EditingTask, Source } from "@/core/editing/types";

import { EditingDevices, editingInputClass } from "./editing-devices";
import { EditingNotice } from "./editing-shell";

export function EditingPrepare({
  task,
  onChange,
}: {
  task: EditingTask;
  onChange: () => void;
}) {
  const { devices } = useEditingSetup();
  const { owner, current, expire } = useEditingOwner();
  const [deviceId, setDeviceId] = useState(task.device_id ?? "");
  const [grant, setGrant] = useState(task.source_directory?.grant_id ?? "");
  const [path, setPath] = useState(task.source_directory?.relative_path ?? ".");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [files, setFiles] = useState<{ file: File; source: Source }[]>([]);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  const device = devices.data?.items.find((value) => value.id === deviceId);
  async function perform(operation: (signal: AbortSignal) => Promise<unknown>) {
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    try {
      await operation(controller.signal);
      if (current.current === owner && !controller.signal.aborted) onChange();
    } catch (error) {
      expire(error);
      if (current.current === owner && !controller.signal.aborted)
        setError(error instanceof Error ? error.message : "操作未完成");
    } finally {
      if (current.current === owner && !controller.signal.aborted)
        setBusy(false);
    }
  }
  function sendFile(source: Source, file: File) {
    if (file.name !== source.name || file.size !== source.size_bytes) {
      setError(
        "所选文件与清单不匹配，请选择相同名称和大小的原文件；内容还将由 Mac 校验。",
      );
      return;
    }
    void perform((signal) =>
      uploadSource(task.id, source.media_id, file, signal, (bytes) =>
        setProgress(`${source.name}：Mac 已收到 ${bytes}/${file.size} 字节`),
      ),
    );
  }
  return (
    <section className="space-y-4 rounded-lg border p-4">
      <h2 className="text-lg font-semibold">补齐准备条件</h2>
      <p>这是已提交的执行意图。全部条件就绪后自动执行同一任务。</p>
      <EditingDevices value={deviceId} onChange={setDeviceId} />
      <Button
        type="button"
        disabled={busy || !deviceId}
        variant="outline"
        onClick={() =>
          void perform((signal) =>
            prepareEditingTask(task.id, { device_id: deviceId }, signal),
          )
        }
      >
        绑定这台 Mac，保留原素材选择
      </Button>
      {!task.manifest_frozen && (
        <>
          <label className="block">
            已授权目录
            <select
              className={editingInputClass}
              value={grant}
              onChange={(event) => setGrant(event.target.value)}
            >
              <option value="">请选择授权目录</option>
              {device?.grants.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label className="block">
            授权目录内的相对文件夹
            <Input
              value={path}
              onChange={(event) => setPath(event.target.value)}
            />
          </label>
          <Button
            type="button"
            variant="outline"
            disabled={busy || !deviceId || !grant || !path}
            onClick={() =>
              void perform((signal) =>
                prepareEditingTask(
                  task.id,
                  {
                    device_id: deviceId,
                    source_directory: { grant_id: grant, relative_path: path },
                  },
                  signal,
                ),
              )
            }
          >
            使用此文件夹，重新检查素材
          </Button>
        </>
      )}
      {!task.manifest_frozen && (
        <details>
          <summary className="text-link cursor-pointer py-3">
            提交文件到这台 Mac
          </summary>
          <div className="space-y-3">
            <p>
              目标：{device?.name ?? "请先选择 Mac"}
              。使用上方已授权目录作为接收目录；该目录必须已在 Mac 使用
              --receive 授权。选择文件仍是草稿，提交后会补齐当前执行意图。
            </p>
            <label className="block">
              选择待提交文件
              <Input
                type="file"
                multiple
                accept="video/*"
                disabled={busy}
                onChange={(event) =>
                  setFiles(
                    Array.from(event.target.files ?? []).map((file) => ({
                      file,
                      source: {
                        media_id: crypto.randomUUID(),
                        name: file.name,
                        relative_path: file.name,
                        episode: 0,
                        size_bytes: file.size,
                        state: "selected",
                      },
                    })),
                  )
                }
              />
            </label>
            {files.map((item, index) => (
              <label key={item.source.media_id} className="block break-all">
                {item.file.name} 集号
                <Input
                  type="number"
                  min={1}
                  value={item.source.episode || ""}
                  onChange={(event) =>
                    setFiles((previous) =>
                      previous.map((value, i) =>
                        i === index
                          ? {
                              ...value,
                              source: {
                                ...value.source,
                                episode: Number(event.target.value),
                              },
                            }
                          : value,
                      ),
                    )
                  }
                />
              </label>
            ))}
            <Button
              type="button"
              variant="outline"
              disabled={
                busy ||
                !device?.online ||
                !device.ready ||
                !grant ||
                !files.length ||
                files.some((value) => value.source.episode < 1) ||
                new Set(files.map((value) => value.source.episode)).size !==
                  files.length
              }
              onClick={() =>
                void perform(async (signal) => {
                  await prepareEditingTask(
                    task.id,
                    {
                      device_id: deviceId,
                      source_manifest: {
                        version: (task.source_manifest?.version ?? 0) + 1,
                        grant_id: grant,
                        files: files.map((value) => value.source),
                      },
                    },
                    signal,
                  );
                  for (const item of files)
                    await uploadSource(
                      task.id,
                      item.source.media_id,
                      item.file,
                      signal,
                      (bytes) =>
                        setProgress(
                          `${item.file.name}：Mac 已收到 ${bytes}/${item.file.size} 字节`,
                        ),
                    );
                  setFiles([]);
                })
              }
            >
              提交这些文件，替换本次素材清单
            </Button>
          </div>
        </details>
      )}
      {task.source_manifest && (
        <ul className="space-y-3">
          {task.source_manifest.files.map((source) => (
            <li key={source.media_id}>
              <p className="break-all">
                第 {source.episode} 集 · {source.name} ·{" "}
                {editingLabel(source.state)}
              </p>
              {!task.source_directory && source.state !== "verified" && (
                <label className="block">
                  重新提交此文件
                  <Input
                    type="file"
                    disabled={busy || !device?.online}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) sendFile(source, file);
                    }}
                  />
                </label>
              )}
              {!task.manifest_frozen && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy || task.source_manifest!.files.length < 2}
                  onClick={() =>
                    void perform((signal) =>
                      prepareEditingTask(
                        task.id,
                        {
                          device_id: deviceId,
                          source_manifest: {
                            ...task.source_manifest!,
                            version: task.source_manifest!.version + 1,
                            files: task
                              .source_manifest!.files.filter(
                                (value) => value.media_id !== source.media_id,
                              )
                              .map((value) => ({
                                ...value,
                                state: "selected",
                                sha256: null,
                                duration_seconds: null,
                              })),
                          },
                        },
                        signal,
                      ),
                    )
                  }
                >
                  从本次素材中移除
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {progress && (
        <p role="status">
          {progress}。收到后还需本地校验，传输期间请保持浏览器和 Mac 在线。
        </p>
      )}
      {error && <EditingNotice>{error}</EditingNotice>}
    </section>
  );
}
