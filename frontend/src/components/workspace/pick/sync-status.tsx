"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/core/auth/AuthProvider";
import { getPickSyncStatus, startPickSync } from "@/core/pick/api";
import { dataAsOfLine } from "@/core/pick/format";
import {
  isMirrorReadError,
  parseSyncTime,
  type PickMirrorField,
  type PickMirrorStatus,
} from "@/core/pick/sync-schema";

const STATUS_LABEL: Record<string, string> = {
  running: "同步中",
  success: "成功",
  failed: "失败",
};
/** Run status as a semantic pill: running is info, success and failed their own. */
const STATUS_TONE: Record<string, string> = {
  running: "bg-info-surface text-info-ink",
  success: "bg-success-surface text-success-ink",
  failed: "bg-danger-surface text-danger-ink",
};
const TRIGGER_LABEL: Record<string, string> = {
  cron: "定时",
  manual: "手动",
};

/** A /sync moment as the pick data board prints it: UTC, to the minute. */
function utc(value: string | null | undefined): string | null {
  const moment = parseSyncTime(value);
  return moment
    ? `${moment.toISOString().slice(0, 16).replace("T", " ")} UTC`
    : null;
}

function earlier(a: string | null, b: string | null): string | null {
  if (a === null || b === null) return a ?? b;
  return a < b ? a : b;
}

function versionPart(mirror: PickMirrorStatus): string {
  const current = mirror.current;
  if (!current) return "镜像还没有发布版本";
  const curve = earlier(current.latest_snapshot, mirror.series_through);
  return `镜像 v${current.id} 采集于 ${utc(current.as_of) ?? "时间未知"} · 曲线截至 ${curve ?? "日期未知"}`;
}

function failureParts(mirror: PickMirrorStatus): string[] {
  if (mirror.consecutive_failures === 0) return [];
  const last = [mirror.last_failure, utc(mirror.last_failure_at)]
    .filter((part): part is string => Boolean(part))
    .join("，");
  return [
    `镜像连续失败 ${mirror.consecutive_failures} 次${last ? `（最近：${last}）` : ""}`,
  ];
}

function lockParts(lock: PickMirrorStatus["lock_stuck"]): string[] {
  if (!lock) return [];
  return [
    `镜像同步锁被 ${lock.holder ?? "未知进程"} 占着，自 ${utc(lock.since) ?? "时间未知"} 起超过 60 分钟没释放（进程 ${lock.pid ?? "未知"}）`,
  ];
}

/** The mirror's line in the sync panel (P2-8b's /sync mirror key; ★U20). */
function mirrorParts(mirror: PickMirrorStatus): string[] {
  return [
    versionPart(mirror),
    ...(mirror.enabled ? [] : ["镜像同步已关闭"]),
    ...failureParts(mirror),
    ...lockParts(mirror.lock_stuck),
  ];
}

function MirrorLine({
  mirror,
}: {
  mirror: PickMirrorField | null | undefined;
}) {
  if (!mirror) return null;
  const text = isMirrorReadError(mirror)
    ? `暂时读不到镜像状态（${mirror.error}）`
    : mirrorParts(mirror).join(" · ");
  return (
    <p className="text-sm" data-testid="pick-sync-mirror">
      {text}
    </p>
  );
}

export function SyncStatus() {
  const { user } = useAuth();
  const client = useQueryClient();
  const key = ["pick-sync", user?.id];
  const [watchUntil, setWatchUntil] = useState(0);
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => getPickSyncStatus(signal),
    enabled: !!user,
    // A manual run is recorded a moment after the click; keep polling until it shows up and ends.
    refetchInterval: (q) =>
      q.state.data?.runs[0]?.status === "running" || Date.now() < watchUntil
        ? 3000
        : false,
  });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const latestId = query.data?.runs[0]?.id;
  const latestStatus = query.data?.runs[0]?.status;
  useEffect(() => {
    if (latestId && latestStatus !== "running")
      void client.invalidateQueries({ queryKey: ["pick-imports", user?.id] });
  }, [client, latestId, latestStatus, user?.id]);
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
      setWatchUntil(Date.now() + 120_000);
      await client.invalidateQueries({ queryKey: key });
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
      className="bg-card space-y-3 rounded-lg border p-5"
      aria-label={
        data?.native_source ? "GGWork 数据同步" : "RealShort 数据同步"
      }
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold">
          {data?.native_source ? "GGWork 数据同步" : "RealShort 数据同步"}
        </h2>
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
      <MirrorLine mirror={data?.mirror} />
      {data?.native_source && (
        <div className="space-y-1 text-xs" data-testid="pick-native-source">
          <p>直接采集数据；「立即同步」发布已采集的资料版本。</p>
          {data.native_source.error && (
            <p role="alert">暂时无法读取采集状态。</p>
          )}
          {data.native_source.jobs.map((job) => (
            <p
              key={job.name}
              role={
                job.status === "failed" || job.error_code ? "alert" : "status"
              }
            >
              {
                {
                  cps: "ReelShort 片库与账单",
                  catalog: "飞书剧单与发布记录",
                  queyu: "鹊娱榜单与剧库",
                }[job.name]
              }
              ：{STATUS_LABEL[job.status]} · 最近成功{" "}
              {utc(job.last_success_at) ?? "尚未成功采集"}
              {job.error_code === "moboreels_retained"
                ? " · MoboReels 保留原资料和原日期"
                : job.error_code === "source_access_denied"
                  ? " · 飞书源表无查看权限，保留旧资料"
                  : job.error_code === "queyu_auth_required"
                    ? " · 需要接通鹊娱登录态，保留原有榜单日期"
                    : job.error_code === "queyu_library_incomplete"
                      ? " · 榜单已采集，剧库未完整更新"
                      : job.error_code
                        ? " · 本轮未完成，保留最近成功数据"
                        : ""}
            </p>
          ))}
        </div>
      )}
      {data?.runs.length ? (
        <ul className="text-muted-foreground space-y-1 text-xs">
          {data.runs.slice(0, 5).map((run) => (
            <li key={run.id}>
              {new Date(run.started_at).toLocaleString("zh-CN", {
                hour12: false,
              })}{" "}
              · {TRIGGER_LABEL[run.trigger] ?? run.trigger} ·{" "}
              <span
                className={`rounded-sm px-2 py-0.5 text-[11.5px] font-medium ${STATUS_TONE[run.status] ?? "bg-muted text-muted-foreground"}`}
              >
                {STATUS_LABEL[run.status] ?? run.status}
              </span>
              {run.rows !== null && ` · ${run.rows} 部`}
              {run.error && ` · ${run.error}`}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="text-muted-foreground text-xs">
        后端每天
        11:40、23:40（北京时间）同步一次：智能体的候选池与本页的镜像版本同一次采集；镜像失败时智能体照常更新，本页显示落后横幅。分页期间条数变化超过
        max(20, 1%) 时整批放弃、继续用上一版；小幅变化照常发布并记录。
      </p>
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
    </section>
  );
}
