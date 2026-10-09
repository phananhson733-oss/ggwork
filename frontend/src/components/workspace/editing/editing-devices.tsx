"use client";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EditingError, registerEditingDevice } from "@/core/editing/api";
import { useEditingOwner, useEditingSetup } from "@/core/editing/hooks";
import { editingLabel } from "@/core/editing/presentation";

import { EditingNotice } from "./editing-shell";

export const editingInputClass =
  "border-line bg-panel text-ink-1 min-h-11 w-full rounded-md border px-3 py-2 text-base";
export function EditingDevices({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const { devices } = useEditingSetup();
  const { owner, current, expire } = useEditingOwner();
  const [gateway, setGateway] = useState("https://你的站点");
  useEffect(() => setGateway(window.location.origin), []);
  const [name, setName] = useState("我的 Mac");
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    setToken("");
    setCopied(false);
    setError("");
    setBusy(false);
    return () => active.current?.abort();
  }, [owner]);
  const chosen = devices.data?.items.find((device) => device.id === value);
  async function connect() {
    if (busy || !owner) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    try {
      const result = await registerEditingDevice(name, controller.signal);
      if (current.current !== owner || controller.signal.aborted) return;
      setCopied(false);
      setToken(result.token);
      onChange(result.device.id);
      await devices.refetch();
    } catch (error) {
      expire(error);
      if (!controller.signal.aborted && current.current === owner)
        setError(
          error instanceof EditingError
            ? error.message
            : "设备连接失败，请检查网络后重试",
        );
    } finally {
      if (!controller.signal.aborted && current.current === owner)
        setBusy(false);
    }
  }
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">执行 Mac</h2>
      <label className="block">
        选择执行设备
        <select
          className={editingInputClass}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">稍后连接 Mac</option>
          {devices.data?.items
            .filter((device) => !device.revoked)
            .map((device) => (
              <option key={device.id} value={device.id}>
                {device.name} ·{" "}
                {device.online
                  ? device.ready
                    ? "已就绪"
                    : "在线，环境待检查"
                  : "离线"}
              </option>
            ))}
        </select>
      </label>
      {devices.isError && (
        <EditingNotice>
          设备列表读取失败。
          <Button
            type="button"
            variant="outline"
            onClick={() => void devices.refetch()}
          >
            重新检查设备
          </Button>
        </EditingNotice>
      )}
      {chosen && (
        <div role="status">
          <p>
            {chosen.online
              ? chosen.ready
                ? "Mac 已连接，剪辑环境已就绪"
                : "Mac 已连接，剪辑环境待检查"
              : "设备离线，请在 Mac 启动执行器"}
          </p>
          {chosen.reasons.map((reason) => (
            <p key={reason}>{editingLabel(reason)}</p>
          ))}
        </div>
      )}
      <details open={!chosen?.ready}>
        <summary className="text-link min-h-11 cursor-pointer py-2">
          连接你的 Mac / 查看准备指引
        </summary>
        <div className="space-y-3 rounded-lg border p-4">
          <p>
            当前执行器支持 Apple Silicon Mac。视频、转录与成片保存在这台
            Mac；请在 Mac 完成安装、目录授权并保持执行器运行。
          </p>
          <label className="block">
            设备名称
            <Input
              className="min-h-11 text-base"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <Button
            type="button"
            variant="outline"
            disabled={busy || !name.trim()}
            onClick={() => void connect()}
          >
            生成连接凭证
          </Button>
          {token && (
            <div className="space-y-3">
              <p>
                连接凭证仅显示一次。请复制后，在 Mac
                的连接命令提示中粘贴；不要放入命令参数或聊天内容。
              </p>
              <Input
                aria-label="一次性设备连接凭证"
                type="password"
                readOnly
                value={token}
                autoComplete="off"
              />
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  void navigator.clipboard
                    .writeText(token)
                    .then(() => setCopied(true))
                    .catch(() => setError("复制失败，请选择凭证手动复制"));
                }}
              >
                {copied ? "已复制" : "复制连接凭证"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setToken("")}
              >
                隐藏并清除凭证
              </Button>
            </div>
          )}
          <p>
            当前使用开发版 wheel；签名安装包尚未发布。请先在 Mac
            安装项目原生执行器及
            ffmpeg、ffprobe、whisper-cli，并准备已验证的本地 Whisper
            模型。下列命令中的目录和模型需替换为自己的位置；连接凭证只在安全提示中输入。
          </p>
          <pre className="overflow-x-auto rounded border p-3 text-base break-all whitespace-pre-wrap">{`ggwork-edit-worker setup --gateway ${gateway} --device-id ${value || "设备标识"} --output-root /新的空成片目录 --model /本地Whisper模型 --model-sha256 模型的SHA256 --model-language multilingual
 ggwork-edit-worker grant drama /素材目录
 ggwork-edit-worker grant incoming /接收目录 --receive
 ggwork-edit-worker doctor
 ggwork-edit-worker run`}</pre>
          <p>
            目录别名 drama / incoming
            会出现在素材选择中；执行器在线和环境就绪分别显示。命令会在本地保存设备凭证，请保持终端运行。
          </p>
          {value && (
            <p className="break-all">
              设备标识：<code>{value}</code>
            </p>
          )}
        </div>
      </details>
      {error && <EditingNotice>{error}</EditingNotice>}
    </section>
  );
}
