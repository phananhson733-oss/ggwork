"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  EditingError,
  createEditingTask,
  uploadSource,
} from "@/core/editing/api";
import { useEditingOwner, useEditingSetup } from "@/core/editing/hooks";
import { editingLabel, safeEditingReturn } from "@/core/editing/presentation";
import {
  type CreateTask,
  type EditingTask,
  type Requirements,
  type Source,
} from "@/core/editing/types";

import { EditingDevices, editingInputClass } from "./editing-devices";
import { EditingNotice } from "./editing-shell";

export function EditingForm({
  initialTitle = "",
  returnTo = null,
  parent,
}: {
  initialTitle?: string;
  returnTo?: string | null;
  parent?: EditingTask;
}) {
  const router = useRouter();
  const { owner, current, expire } = useEditingOwner();
  const { devices, capabilities } = useEditingSetup();
  const [title, setTitle] = useState(parent?.title ?? initialTitle);
  const [deviceId, setDeviceId] = useState(parent?.device_id ?? "");
  const [grant, setGrant] = useState(
    parent?.source_directory?.grant_id ??
      parent?.source_manifest?.grant_id ??
      "",
  );
  const [directory, setDirectory] = useState(
    parent?.source_directory?.relative_path ?? ".",
  );
  const [sourceMode, setSourceMode] = useState<
    "directory" | "files" | "existing"
  >(parent?.source_manifest ? "existing" : "directory");
  const [files, setFiles] = useState<{ file: File; source: Source }[]>([]);
  const [requirements, setRequirements] = useState<Requirements>(
    parent?.requirements ?? {
      profile: "",
      instructions: "",
      output_count: 1,
      duration_seconds: 30,
      aspect_ratio: "9:16",
      language: "auto",
      review_plan: false,
    },
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [submitted, setSubmitted] = useState<EditingTask | null>(null);
  const [locked, setLocked] = useState(false);
  const request = useRef<CreateTask | null>(null);
  const active = useRef<AbortController | null>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const accepted = useRef(false);
  const pendingKey = `editing:pending:${owner}:${parent?.id ?? "new"}`;
  const restoredFor = useRef("");
  useEffect(() => {
    if (!owner || restoredFor.current === pendingKey) return;
    restoredFor.current = pendingKey;
    try {
      const saved = JSON.parse(
        sessionStorage.getItem(pendingKey) ?? "null",
      ) as { payload: CreateTask; task_id?: string } | null;
      if (
        !saved?.payload?.request_id ||
        !saved.payload.title ||
        !saved.payload.requirements
      )
        return;
      if (saved.task_id) {
        sessionStorage.removeItem(pendingKey);
        router.push(`/workspace/editing/${encodeURIComponent(saved.task_id)}`);
        return;
      }
      request.current = saved.payload;
      setTitle(saved.payload.title);
      setRequirements(saved.payload.requirements);
      setDeviceId(saved.payload.device_id ?? "");
      setLocked(true);
      setError("提交结果尚未确认，请确认同一请求，避免重复创建任务。");
    } catch {
      /* Browser storage is optional; the mounted request remains stable. */
    }
  }, [owner, pendingKey, router]);
  function savePending(payload: CreateTask, taskId?: string) {
    try {
      sessionStorage.setItem(
        pendingKey,
        JSON.stringify({ payload, task_id: taskId }),
      );
    } catch {
      /* Storage-disabled browsers retain the mounted request. */
    }
  }

  useEffect(() => () => active.current?.abort(), [owner]);
  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);
  const capability = capabilities.data;
  const device = devices.data?.items.find((value) => value.id === deviceId);
  const profile =
    requirements.profile ||
    (capability?.profiles.find((value) => value.available)?.id ?? "");
  const admitted =
    capability?.profiles.some(
      (value) => value.id === profile && value.available,
    ) === true;
  const back = safeEditingReturn(returnTo);
  function update<K extends keyof Requirements>(
    key: K,
    value: Requirements[K],
  ) {
    setRequirements((previous) => ({ ...previous, [key]: value }));
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !owner) return;
    if (!request.current && !admitted) {
      setError("当前没有已就绪的剪辑模式，请先完成能力配置");
      return;
    }
    if (!request.current) {
      if (
        sourceMode === "files" &&
        capability &&
        files.length > capability.limits.max_sources
      ) {
        setError(`本次最多选择 ${capability.limits.max_sources} 个素材`);
        return;
      }
      if (
        sourceMode === "files" &&
        (!device?.online || !device.ready || !files.length)
      ) {
        setError("提交文件需要已就绪的在线 Mac，并选择本次全部素材");
        return;
      }
      if (
        sourceMode === "files" &&
        new Set(files.map((item) => item.source.episode)).size !== files.length
      ) {
        setError("每个文件需要明确且不重复的集号");
        return;
      }
      request.current = {
        request_id: crypto.randomUUID(),
        title: title.trim(),
        requirements: { ...requirements, profile },
        ...(deviceId ? { device_id: deviceId } : {}),
        ...(parent ? { parent_task_id: parent.id } : {}),
        ...(sourceMode === "existing" && parent?.source_manifest
          ? {
              source_manifest: {
                ...parent.source_manifest,
                files: parent.source_manifest.files.map((source) => ({
                  ...source,
                  state: "selected" as const,
                  sha256: null,
                  duration_seconds: null,
                })),
              },
            }
          : grant
            ? sourceMode === "directory"
              ? {
                  source_directory: {
                    grant_id: grant,
                    relative_path: directory,
                  },
                }
              : {
                  source_manifest: {
                    version: 1,
                    grant_id: grant,
                    files: files.map((item) => item.source),
                  },
                }
            : {}),
      };
      savePending(request.current);
      setLocked(true);
    }
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    try {
      const task =
        submitted ??
        (await createEditingTask(request.current, controller.signal));
      if (current.current !== owner || controller.signal.aborted) return;
      accepted.current = true;
      savePending(request.current, task.id);
      setSubmitted(task);
      if (sourceMode === "files") {
        for (const item of files) {
          setProgress(`${item.file.name}：正在提交到 ${device?.name ?? "Mac"}`);
          await uploadSource(
            task.id,
            item.source.media_id,
            item.file,
            controller.signal,
            (bytes) => {
              if (current.current === owner && !controller.signal.aborted)
                setProgress(
                  `${item.file.name}：Mac 已收到 ${bytes.toLocaleString()} / ${item.file.size.toLocaleString()} 字节`,
                );
            },
          );
        }
      }
      if (current.current === owner && !controller.signal.aborted) {
        try {
          sessionStorage.removeItem(pendingKey);
        } catch {}
        router.push(
          `/workspace/editing/${encodeURIComponent(task.id)}${back ? `?${new URLSearchParams({ returnTo: back })}` : ""}`,
        );
      }
    } catch (error) {
      expire(error);
      if (
        !accepted.current &&
        error instanceof EditingError &&
        [400, 403, 422].includes(error.status)
      ) {
        request.current = null;
        setLocked(false);
        try {
          sessionStorage.removeItem(pendingKey);
        } catch {}
        setError(error.message);
        return;
      }
      if (!controller.signal.aborted && current.current === owner)
        setError(
          accepted.current
            ? "文件提交未完成，已创建任务保留。请打开任务，重新提交未完成文件。"
            : "提交结果尚未确认。可再次确认同一请求，系统不会重复创建任务。",
        );
    } finally {
      if (!controller.signal.aborted && current.current === owner)
        setBusy(false);
    }
  }
  return (
    <form
      onSubmit={(event) => void submit(event)}
      noValidate={locked}
      className="space-y-6"
    >
      {back && (
        <Link
          href={back}
          className="text-link inline-block min-h-11 py-2 underline"
        >
          返回原选剧列表
        </Link>
      )}
      {parent && (
        <p>
          将创建关联的新版本，原版成片继续保留。
          <Link
            href={`/workspace/editing/${parent.id}`}
            className="text-link underline"
          >
            查看原版
          </Link>
        </p>
      )}
      <fieldset
        disabled={locked || busy}
        className="grid min-w-0 gap-6 lg:grid-cols-2 [&>label]:lg:col-span-2 [&>section:first-of-type]:lg:col-span-2"
      >
        <label className="block font-medium">
          当前剧目
          <Input
            required
            className="min-h-11 text-base"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <EditingDevices
          value={deviceId}
          onChange={(value) => {
            setDeviceId(value);
            setGrant("");
          }}
        />
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">素材来源</h2>
          <p>
            所指目录位于选定 Mac 的授权范围内，浏览器不会直接读取 Mac 文件。
          </p>
          <label className="block">
            提供素材方式
            <select
              className={editingInputClass}
              value={sourceMode}
              onChange={(event) =>
                setSourceMode(
                  event.target.value as "directory" | "files" | "existing",
                )
              }
            >
              {parent?.source_manifest && (
                <option value="existing">沿用原版选定素材（重新校验）</option>
              )}
              <option value="directory">Mac 上的文件夹</option>
              <option value="files">提交文件到这台 Mac</option>
            </select>
          </label>
          <label className="block">
            已授权目录
            <select
              className={editingInputClass}
              value={grant}
              onChange={(event) => setGrant(event.target.value)}
              required={sourceMode === "files"}
              disabled={sourceMode === "existing"}
            >
              <option value="">稍后授权目录</option>
              {device?.grants.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          {sourceMode === "existing" ? (
            <p>
              将沿用原版选定的 {parent?.source_manifest?.files.length}{" "}
              个素材；重新校验后生成新版本，不扩大素材范围。
            </p>
          ) : sourceMode === "directory" ? (
            <label className="block">
              授权目录内的相对文件夹
              <Input
                required
                className="min-h-11 text-base"
                value={directory}
                onChange={(event) => setDirectory(event.target.value)}
              />
              <span className="font-normal">
                填写 . 表示授权目录本身；无需填写绝对路径。
              </span>
            </label>
          ) : (
            <>
              <p>
                目标：{device?.name ?? "请先选择 Mac"}
                。选择文件只更新草稿。传输期间浏览器和 Mac
                都需在线，全部收到后还需本地校验。
              </p>
              <label className="block">
                选择本次素材
                <Input
                  type="file"
                  multiple
                  accept="video/*"
                  className="min-h-11 text-base"
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
                <div
                  key={item.source.media_id}
                  className="flex flex-wrap items-center gap-3"
                >
                  <span className="min-w-0 break-all">{item.file.name}</span>
                  <label>
                    集号
                    <Input
                      aria-label={`${item.file.name} 集号`}
                      type="number"
                      min={1}
                      required
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
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() =>
                      setFiles((previous) =>
                        previous.filter((_, i) => i !== index),
                      )
                    }
                  >
                    移除此文件
                  </Button>
                </div>
              ))}
            </>
          )}
        </section>
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">剪辑要求</h2>
          {capabilities.isPending && <p role="status">正在读取可执行能力…</p>}
          {capabilities.isError && (
            <EditingNotice>
              剪辑能力读取失败。
              <Button
                type="button"
                variant="outline"
                onClick={() => void capabilities.refetch()}
              >
                重试
              </Button>
            </EditingNotice>
          )}
          <label className="block">
            剪辑模式
            <select
              className={editingInputClass}
              value={profile}
              onChange={(event) => update("profile", event.target.value)}
              required
            >
              <option value="">请选择可用模式</option>
              {capability?.profiles.map((value) => (
                <option
                  key={value.id}
                  value={value.id}
                  disabled={!value.available}
                >
                  {editingLabel(value.id)}
                  {value.available
                    ? ""
                    : ` · ${value.reasons.map(editingLabel).join("、")}`}
                </option>
              ))}
            </select>
          </label>
          {!admitted && (
            <p>当前尚无可执行模式。请完成云端规划与原生执行器配置后再开始。</p>
          )}
          <label className="block">
            剪辑要求
            <textarea
              required
              rows={4}
              className={editingInputClass}
              value={requirements.instructions}
              onChange={(event) => update("instructions", event.target.value)}
            />
          </label>
          <div className="grid gap-4 md:grid-cols-2">
            <label>
              成片数量
              <Input
                required
                type="number"
                min={1}
                max={capability?.limits.max_outputs}
                value={requirements.output_count}
                onChange={(event) =>
                  update("output_count", Number(event.target.value))
                }
              />
            </label>
            <label>
              每条时长（秒）
              <Input
                required
                type="number"
                min={capability?.limits.min_duration_seconds}
                max={capability?.limits.max_duration_seconds}
                value={requirements.duration_seconds}
                onChange={(event) =>
                  update("duration_seconds", Number(event.target.value))
                }
              />
            </label>
            <label>
              画幅
              <select
                className={editingInputClass}
                value={requirements.aspect_ratio}
                onChange={(event) => update("aspect_ratio", event.target.value)}
              >
                <option>9:16</option>
                <option>16:9</option>
                <option>1:1</option>
              </select>
            </label>
            <label>
              素材语言
              <Input
                value={requirements.language}
                onChange={(event) => update("language", event.target.value)}
                required
              />
            </label>
          </div>
          <label className="flex min-h-11 items-center gap-3">
            <input
              type="checkbox"
              checked={requirements.review_plan}
              onChange={(event) => update("review_plan", event.target.checked)}
            />
            先看方案，确认后再渲染
          </label>
        </section>
      </fieldset>
      <p>
        视频和成片保存在你的
        Mac；必要的转录文本用于云端生成剪辑方案。云端使用管理员配置的规划模型。
      </p>
      {error && (
        <div tabIndex={-1} ref={errorRef}>
          <EditingNotice>{error}</EditingNotice>
        </div>
      )}
      {progress && (
        <div>
          <p role="status">
            {busy
              ? "正在提交素材，请保持浏览器和 Mac 在线。"
              : "请打开任务查看未完成的素材与本地校验状态。"}
          </p>
          <p aria-live="off">{progress}</p>
        </div>
      )}
      {submitted && (
        <Link
          href={`/workspace/editing/${submitted.id}`}
          className="text-link block underline"
        >
          打开已创建任务，检查素材与进度
        </Link>
      )}
      <Button
        type="submit"
        disabled={busy || (!locked && !admitted) || !!submitted}
        className="min-h-11 w-full text-base md:w-auto"
      >
        {busy ? "正在提交…" : locked ? "确认提交结果" : "开始剪辑"}
      </Button>
    </form>
  );
}
