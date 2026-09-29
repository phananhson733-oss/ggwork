"use client";

// The imports tab's radar status (plan TR-25; design 3.7; D10): /sync's obs key as the gateway computed it at checked_at.
// It shares the ["pick-sync", user] query with SyncStatus, so one request feeds both panels. Where each channel stands
// (the live set, the latest set, the last run) is spelled out, and before the crons run the panel says so in words:
// "no run and no set" is a state, never a zero. A gateway from before TR-25 (no obs key) or a malformed key shows nothing.
import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/core/auth/AuthProvider";
import { getPickSyncStatus } from "@/core/pick/api";
import { OBS_CHANNEL_LABELS } from "@/core/pick/obs-status";
import {
  isObsReadError,
  type PickObsChannelStatus,
  type PickObsField,
  type PickObsStatus,
  utcMinute,
} from "@/core/pick/sync-schema";

import { ObsBannerList, type ObsBannerItem } from "./obs-banner-list";

const NOTHING_YET =
  "趋势雷达还没有运行记录，也没有发布过观测集合：采集尚未开启，观测数据未就绪。";

function at(value: string | null): string {
  return utcMinute(value) ?? "时间未知";
}

function channelLine(status: PickObsChannelStatus): string {
  const parts = [
    status.live_set_id
      ? `当前生效集合 ${at(status.live_published_at)} 发布`
      : "没有生效的 live 集合",
  ];
  if (status.latest_set_id && status.latest_set_id !== status.live_set_id)
    parts.push(
      status.latest_mode === "shadow"
        ? `最新集合是影子，${at(status.latest_published_at)} 发布`
        : `最新集合 ${at(status.latest_published_at)} 发布`,
    );
  parts.push(
    status.last_run_at
      ? `最近一次运行 ${at(status.last_run_at)} 开始`
      : "还没有运行记录",
  );
  return `${OBS_CHANNEL_LABELS[status.channel]}：${parts.join(" · ")}`;
}

function neverRan(status: PickObsStatus): boolean {
  return status.channels.every(
    (c) => !c.live_set_id && !c.latest_set_id && !c.last_run_at,
  );
}

function bannersOf(status: PickObsStatus): ObsBannerItem[] {
  return status.channels.flatMap((c) =>
    c.banners.map((b) => ({
      channel: c.channel,
      code: b.code,
      level: b.level,
    })),
  );
}

function StatusBody({ status }: { status: PickObsStatus }) {
  return (
    <>
      <ObsBannerList banners={bannersOf(status)} />
      {neverRan(status) ? (
        <p className="text-sm">{NOTHING_YET}</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {status.channels.map((c) => (
            <li key={c.channel}>{channelLine(c)}</li>
          ))}
        </ul>
      )}
      <p className="text-muted-foreground text-xs">
        状态按 {at(status.checked_at)}{" "}
        计算。两个定时服务各自采集；还没运行过的通道不会显示「没有按时运行」。
      </p>
    </>
  );
}

export function ObsStatusView({
  obs,
}: {
  obs: PickObsField | null | undefined;
}) {
  if (!obs) return null;
  return (
    <section
      className="bg-card space-y-3 rounded-lg border p-5"
      aria-label="趋势雷达状态"
      data-testid="pick-obs-status"
    >
      <h2 className="font-semibold">趋势雷达（Google Trends 与 GSC）</h2>
      {isObsReadError(obs) ? (
        <p className="text-sm">暂时读不到趋势雷达状态（{obs.error}）</p>
      ) : (
        <StatusBody status={obs} />
      )}
    </section>
  );
}

export function ObsStatusPanel() {
  const { user } = useAuth();
  const query = useQuery({
    queryKey: ["pick-sync", user?.id],
    queryFn: ({ signal }) => getPickSyncStatus(signal),
    enabled: !!user,
  });
  return <ObsStatusView obs={query.data?.obs} />;
}
