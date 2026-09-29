// 工作台新建（TR-24）：趋势雷达两个 tab 与详情页共用的小块。一个通道显示的是哪个集合（钉住的、生效的，还是上线前
// 最新的影子集合）、最近发布的几个集合、最近一次运行、这一刻的横幅（obs-banner-rules，按请求时刻算）。
// 每一块都有明确的空状态：没有集合、没有运行记录、没有横幅都写成一句话，不写成 0。服务端组件，不加 client。
import Link from "next/link";
import type { ReactNode } from "react";

import { ObsBannerList } from "@/components/workspace/pick/obs-banner-list";
import type { ObsRun, ObsSetBrief } from "@/core/pick/obs-rows";
import { OBS_CHANNEL_LABELS } from "@/core/pick/obs-status";
import { RUN_OUTCOME_TEXT, SHADOW_MARK, textOf } from "@/core/pick/obs-wording";
import { utcMinute } from "@/core/pick/sync-schema";
import type { PickRequest } from "@/core/pick-board/request";
import type { ObsChannelLoad } from "@/server/pick-board";

import { pickHref } from "../toolbar";

import { channelBanners } from "./obs-banner-rules";

export const SECTION =
  "border-line bg-panel mb-4 rounded-lg border px-4 py-3 text-[13px] leading-[1.65]";
export const NOTE =
  "border-warning-line bg-warning-surface text-warning-ink mb-2 rounded-lg border px-3 py-2 text-[13px]";
export const HEADING = "text-ink-1 mb-2 text-[14px] font-semibold";
export const SUBHEADING = "text-ink-1 mt-3 mb-1.5 text-[13px] font-semibold";
export const MUTED = "text-helper";
export const LINK = "text-link hover:underline";
export const TABLE = "w-full border-collapse text-[13px]";
export const TH =
  "border-line text-helper border-b px-2 py-1.5 text-left font-normal";
export const TD = "border-line border-b px-2 py-1.5 align-top";
const BADGE =
  "border-line-strong text-ink-dim ml-1.5 rounded border px-1.5 text-[12px]";

export function shortId(setId: string): string {
  return setId.slice(0, 8);
}

export function at(value: string | null | undefined): string {
  return utcMinute(value) ?? "时间不详";
}

const IDENTITY_SHAPE = /^\["([^"\\]+)","(?:[^"\\]|\\.)+","([^"\\]+)"\]$/;

/** An identity as people read it: "realshort · en"; one the page does not recognise is shown as stored. */
export function identityLabel(identity: string): string {
  const found = IDENTITY_SHAPE.exec(identity);
  return found ? `${found[1]} · ${found[2]}` : identity;
}

export function ShadowMark({ mode }: { mode: string }) {
  return mode === "shadow" ? (
    <span className={BADGE} data-obs-shadow="true">
      {SHADOW_MARK}
    </span>
  ) : null;
}

/** The link to an identity's detail, from either tab; the pinned set stays pinned. */
export function IdentityLink({
  req,
  identity,
  children,
}: {
  req: PickRequest;
  identity: string;
  children: ReactNode;
}) {
  return (
    <Link
      prefetch={false}
      href={pickHref(req, { oid: identity })}
      className={LINK}
    >
      {children}
    </Link>
  );
}

function ChannelBanners({ load, now }: { load: ObsChannelLoad; now: Date }) {
  const banners = channelBanners(load.channel, {
    latestRun: load.latestRun,
    livePublishedAt: load.live?.published_at ?? null,
    now,
  });
  if (banners.length === 0)
    return <p className={MUTED}>这个通道现在没有需要提醒的状态。</p>;
  return (
    <div className="mb-2">
      <ObsBannerList
        banners={banners.map((b) => ({ ...b, channel: load.channel }))}
      />
    </div>
  );
}

function PinNote({ load, req }: { load: ObsChannelLoad; req: PickRequest }) {
  if (load.pin === "missing")
    return (
      <p className={NOTE} role="status">
        链接钉住的集合不是这个通道已发布的集合（可能已被清理），下面显示的是当前集合。
      </p>
    );
  if (load.pin !== "shown" || load.shown?.set_id === load.live?.set_id)
    return null;
  return (
    <p className={NOTE} role="status">
      这是钉住的集合，不是当前生效的集合。
      <Link
        prefetch={false}
        href={pickHref(req, { obs: "" })}
        className={`${LINK} ml-2`}
      >
        回到当前集合
      </Link>
    </p>
  );
}

function ShownLine({ load }: { load: ObsChannelLoad }) {
  const set = load.shown;
  if (!set)
    return (
      <p data-obs-empty="set">
        这个通道还没有发布过观测集合，没有可显示的观测数据。
      </p>
    );
  const liveNote =
    load.live === null ? "还没有生效的集合，下面是最新发布的影子集合。" : null;
  return (
    <p>
      显示集合 <code>{shortId(set.set_id)}</code>
      <ShadowMark mode={set.mode} />
      ，发布于 {at(set.published_at)}，数据截至 {at(set.as_of)}。
      {set.mode === "shadow" ? " 影子集合只在资料页展示，不进智能体。" : null}
      {liveNote ? <span className={MUTED}> {liveNote}</span> : null}
    </p>
  );
}

export function RunLine({ run }: { run: ObsRun | null }) {
  if (!run) return <p className={MUTED}>还没有运行记录。</p>;
  return (
    <p className={MUTED}>
      最近一次运行：{at(run.started_at)} 开始，
      {textOf(RUN_OUTCOME_TEXT, run.outcome)}
      {run.mode === "shadow" ? `（${SHADOW_MARK}）` : ""}。
    </p>
  );
}

function RecentSets({ load, req }: { load: ObsChannelLoad; req: PickRequest }) {
  if (load.recent.length < 2) return null;
  const shown = load.shown?.set_id;
  return (
    <p className={MUTED}>
      最近发布的集合：
      {load.recent.map((set: ObsSetBrief) => (
        <Link
          key={set.set_id}
          prefetch={false}
          href={pickHref(req, { obs: set.set_id })}
          aria-current={set.set_id === shown ? "true" : undefined}
          className={`${LINK} mr-3`}
        >
          {shortId(set.set_id)}（{at(set.published_at)}
          {set.mode === "shadow" ? `，${SHADOW_MARK}` : ""}）
        </Link>
      ))}
    </p>
  );
}

/**
 * A channel's head: its banners at `now`, which set is shown and why, its latest run and, when this channel is the
 * tab's (?obs= pins the tab's channel only), the sets to pin.
 */
export function ChannelHead({
  load,
  req,
  now,
  pinnable = true,
}: {
  load: ObsChannelLoad;
  req: PickRequest;
  now: Date;
  pinnable?: boolean;
}) {
  return (
    <section className={SECTION} data-obs-channel={load.channel}>
      <h2 className={HEADING}>{OBS_CHANNEL_LABELS[load.channel]}</h2>
      <ChannelBanners load={load} now={now} />
      <PinNote load={load} req={req} />
      <ShownLine load={load} />
      <RunLine run={load.latestRun} />
      {pinnable ? <RecentSets load={load} req={req} /> : null}
    </section>
  );
}
