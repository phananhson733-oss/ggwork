import type { MirrorNoticeKind } from "@/components/workspace/pick-board/views/notices";
import {
  MirrorBusy,
  MirrorMisconfigured,
  MirrorUnavailable,
  MirrorVersionGone,
  ObsNotReady,
  ObsRowInvalid,
  ObsUnreadable,
} from "@/server/pick-board";

/**
 * 读不了数据的几种：返回提示；别的错误不认，交回调用方抛出（进 error.tsx）。镜像的几种来自 page.tsx 的镜像 tab，
 * 观测的三种来自 obs-route.tsx 的趋势雷达两个 tab（TR-24）；两边走同一个读连接，连接没配、忙这些照用镜像的提示。
 */
export function mirrorNoticeOf(error: unknown): MirrorNoticeKind | null {
  if (error instanceof MirrorUnavailable) return { kind: "unavailable" };
  if (error instanceof MirrorMisconfigured)
    return { kind: "misconfigured", reason: error.reason };
  if (error instanceof MirrorBusy) return { kind: "busy" };
  if (error instanceof MirrorVersionGone) return { kind: "gone" };
  if (error instanceof ObsNotReady) return { kind: "obs_not_ready" };
  if (error instanceof ObsUnreadable) return { kind: "obs_unreadable" };
  if (error instanceof ObsRowInvalid)
    return { kind: "obs_row_invalid", view: error.view };
  return null;
}

export async function guarded<T>(
  read: () => Promise<T>,
): Promise<{ ok: true; value: T } | { ok: false; notice: MirrorNoticeKind }> {
  try {
    return { ok: true, value: await read() };
  } catch (error) {
    const notice = mirrorNoticeOf(error);
    if (notice) return { ok: false, notice };
    throw error;
  }
}
