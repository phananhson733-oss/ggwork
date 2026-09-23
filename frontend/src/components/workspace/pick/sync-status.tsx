"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/core/auth/AuthProvider";
import { getPickSyncStatus, startPickSync } from "@/core/pick/api";
import { dataAsOfLine } from "@/core/pick/format";

const STATUS_LABEL: Record<string, string> = {
  running: "同步中",
  success: "成功",
  failed: "失败",
};
const TRIGGER_LABEL: Record<string, string> = {
  cron: "定时",
  manual: "手动",
};

export function SyncStatus() {
  const { user } = useAuth();
  const client = useQueryClient();
  const key = ["pick-sync", user?.id];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => getPickSyncStatus(signal),
    enabled: !!user,
    refetchInterval: (q) =>
      q.state.data?.runs[0]?.status === "running" ? 3000 : false,
  });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const trigger = async () => {
    setBusy(true);
    setMessage("");
    try {
      const outcome = await startPickSync();
      setMessage(
        outcome.status === "started"
          ? "已开始同步，约需半分钟。"
          : "已有同步在进行。",
      );
      await client.invalidateQueries({ queryKey: key });
      await client.invalidateQueries({ queryKey: ["pick-imports", user?.id] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "同步没有开始");
    } finally {
      setBusy(false);
    }
  };
  const data = query.data;
  const current = data?.current;
  return (
    <section
      className="space-y-3 rounded-xl border p-5"
      aria-label="RealShort 数据同步"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold">RealShort 数据同步</h2>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void trigger()}
          disabled={
            busy || !data?.configured || data.runs[0]?.status === "running"
          }
        >
          {busy ? "正在提交…" : "立即同步"}
        </Button>
      </div>
      {query.isPending && <p role="status">正在读取…</p>}
      {query.error && <p role="alert">{query.error.message}</p>}
      {data && !data.configured && (
        <p className="text-muted-foreground text-sm">
          尚未配置 RealShort 只读接口，只能手动导入资料。
        </p>
      )}
      {data && (
        <p className="text-sm" data-testid="pick-sync-current">
          {current
            ? `当前剧库 ${String(current.rows ?? 0)} 部 · ${dataAsOfLine({
                source_as_of: current.source_as_of,
                published_at: current.published_at,
                freshness: current.freshness ?? null,
                shared: current.shared,
              })}`
            : "当前没有可用剧库。"}
        </p>
      )}
      {data?.runs.length ? (
        <ul className="text-muted-foreground space-y-1 text-xs">
          {data.runs.slice(0, 5).map((run) => (
            <li key={run.id}>
              {new Date(run.started_at).toLocaleString("zh-CN", {
                hour12: false,
              })}{" "}
              · {TRIGGER_LABEL[run.trigger] ?? run.trigger} ·{" "}
              {STATUS_LABEL[run.status] ?? run.status}
              {run.rows !== null && ` · ${run.rows} 部`}
              {run.error && ` · ${run.error}`}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="text-muted-foreground text-xs">
        每天
        11:40、23:40（北京时间）自动拉取有来源信号且未下架的候选池与发布记录；条数对不上时整批放弃，继续用上一版。
      </p>
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
    </section>
  );
}
