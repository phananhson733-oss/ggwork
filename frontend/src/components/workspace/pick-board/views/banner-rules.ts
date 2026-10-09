// 工作台新建（RealShort 没有横幅）：资料页数据 tab 上方的提示条，按合成稿 P3-5 的横幅表与批判 B18、B19、B20、C26。
// 纯函数：输入是解析出的版本、gateway 的 /sync 回答、请求和「现在」（页面给，组件里不读墙上时钟），输出是有序的横幅表。
import { pickHref } from "@/components/workspace/pick-board/toolbar";
import {
  isMirrorReadError,
  parseSyncTime,
  type PickMirrorStatus,
  type PickSyncStatus,
} from "@/core/pick/sync-schema";
import { formatObservedAt } from "@/core/pick-board/metrics";
import type { PickRequest } from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import {
  OBSERVE_SOURCES,
  SOURCE_LABELS,
  type ObserveSource,
} from "@/core/pick-board/source-types";
import type { GatewayResult, ReadyBoard } from "@/server/pick-board";

/** 同步每 12 小时一次（北京时间 11:40、23:40）：当前版本采集超过 14 小时说明至少漏了一次 */
export const STALE_AFTER_MS = 14 * 60 * 60 * 1000;
export const IMPORTS_HREF = "/workspace/pick-data?tab=imports";

export type BannerLink = Readonly<{ href: string; text: string }>;
export type Banner = Readonly<{
  key: string;
  /** status = 状态说明；alert = 失败或数据可能不全 */
  role: "status" | "alert";
  text: string;
  link?: BannerLink;
}>;

export type BannerBoard = Pick<
  ReadyBoard<BoardRules>,
  | "scope"
  | "current"
  | "warnings"
  | "requestedV"
  | "pinned"
  | "pruned"
  | "ignoredV"
  | "unreadable"
  | "freshness"
  | "postedImportedAt"
>;

export type BannerInput = Readonly<{
  board: BannerBoard;
  sync: GatewayResult<PickSyncStatus>;
  req: PickRequest;
  now: Date;
}>;

const TO_IMPORTS: BannerLink = { href: IMPORTS_HREF, text: "看「同步与导入」" };

/** 条件成立时是这一条，否则没有：横幅表用展开拼出来，不往数组里 push */
function when(on: boolean, banner: Banner): Banner[] {
  return on ? [banner] : [];
}

/** 1–3：链接里的 v 怎么落到了哪个版本（全靠 reader） */
function versionBanners({ board, req }: BannerInput): Banner[] {
  const shown = board.scope.versionId;
  const latest = board.current.id;
  const asked = `v${String(board.requestedV)}`;
  return [
    ...when(board.pinned, {
      key: "pinned",
      role: "status",
      text: `正在看智能体当时用的版本 v${shown}（采集于 ${formatObservedAt(board.scope.asOf)}），当前最新 v${latest}`,
      link: { href: pickHref(req, { v: null }), text: "切换到最新" },
    }),
    ...when(board.pruned, {
      key: "pruned",
      role: "status",
      text: `链接里的版本 ${asked} 已清理，已显示当前版本 v${latest}`,
    }),
    ...when(board.unreadable, {
      key: "unreadable",
      role: "alert",
      text: `链接里的版本 ${asked} 本页读不到（授权缺失），已显示当前版本 v${latest}；请联系管理员`,
    }),
    ...when(board.ignoredV, {
      key: "ignored",
      role: "status",
      text: `链接里的版本 ${asked} 不存在或未发布，已显示当前版本 v${latest}`,
    }),
  ];
}

function syncMoment(value: string | null | undefined): string | null {
  const moment = parseSyncTime(value);
  return moment ? formatObservedAt(moment) : null;
}

/** 5：镜像落后于智能体。时间只引用共享批次的（B20），登录用户自己的导入不算 */
function behindBanner(mirror: PickMirrorStatus, board: BannerBoard): Banner {
  const id = mirror.current?.id ?? board.current.id;
  const asOf =
    syncMoment(mirror.current?.as_of) ?? formatObservedAt(board.current.asOf);
  const agent = syncMoment(mirror.shared_source_as_of);
  return {
    key: "behind",
    role: "status",
    text: `资料页落后于智能体：镜像最新版本 v${id} 采集于 ${asOf}，${agent ? `智能体数据采集于 ${agent}` : "智能体已换用更新的数据"}`,
  };
}

function alertBanner(mirror: PickMirrorStatus): Banner {
  const last = [mirror.last_failure, syncMoment(mirror.last_failure_at)]
    .filter((part): part is string => Boolean(part))
    .join("，");
  return {
    key: "alert",
    role: "alert",
    text: `镜像同步已连续失败 ${mirror.consecutive_failures} 次${last ? `（最近：${last}）` : ""}`,
  };
}

/** 4–6 与「已关闭」：只在 /sync 拿得到时出；拿不到时只说一句（U31） */
function gatewayBanners({ board, sync }: BannerInput): Banner[] {
  if (!sync.ok)
    return [
      { key: "sync-unavailable", role: "status", text: "暂时拿不到同步状态" },
    ];
  const personal = when(sync.data.current?.shared === false, {
    key: "personal",
    role: "status",
    text: "智能体当前用的是你手动导入的剧库，不在本页",
    link: TO_IMPORTS,
  });
  const mirror = sync.data.mirror;
  if (isMirrorReadError(mirror))
    return [
      ...personal,
      {
        key: "mirror-unreadable",
        role: "status",
        text: "暂时拿不到镜像同步状态",
      },
    ];
  if (!mirror) return personal;
  const nativeWarnings = (sync.data.native_source?.jobs ?? []).flatMap(
    (job): Banner[] => {
      const text =
        job.error_code === "moboreels_retained"
          ? "MoboReels 剧单保留上次成功资料；本轮未刷新该剧场，原导入时间与榜单日期保持不变。"
          : job.error_code === "queyu_auth_required"
            ? "鹊娱自动采集尚未接通，当前沿用已有榜单与原始日期。"
            : job.status === "failed"
              ? `数据采集未完成：${{ cps: "ReelShort 片库与账单", catalog: "飞书剧单与发布记录", queyu: "鹊娱榜单与剧库" }[job.name]}；保留最近成功资料。`
              : null;
      return text
        ? [{ key: `native-${job.name}`, role: "alert", text, link: TO_IMPORTS }]
        : [];
    },
  );
  return [
    ...personal,
    ...nativeWarnings,
    ...when(mirror.behind, behindBanner(mirror, board)),
    ...when(mirror.alert, alertBanner(mirror)),
    ...when(!mirror.enabled, {
      key: "disabled",
      role: "status",
      text: `镜像同步已关闭：本页停在 v${board.current.id}（采集于 ${formatObservedAt(board.current.asOf)}），不再随 RealShort 更新`,
    }),
  ];
}

/** 当前版本采集超过 14 小时（只靠 reader 判断，B19）；已有「已关闭」或连续失败的横幅时不重复说 */
function staleBanner(input: BannerInput, shown: readonly Banner[]): Banner[] {
  if (shown.some((b) => b.key === "disabled" || b.key === "alert")) return [];
  const asOf = Date.parse(input.board.current.asOf);
  if (Number.isNaN(asOf) || input.now.getTime() - asOf <= STALE_AFTER_MS)
    return [];
  return [
    {
      key: "stale",
      role: "status",
      text: `镜像最新版本 v${input.board.current.id} 采集于 ${formatObservedAt(input.board.current.asOf)}，已超过 14 小时没有新版本；同步可能停了`,
      link: TO_IMPORTS,
    },
  ];
}

function sourceLabel(source: unknown): string {
  const known = OBSERVE_SOURCES.find((s): s is ObserveSource => s === source);
  if (known) return SOURCE_LABELS[known];
  return typeof source === "string" && source ? source : "未知来源";
}

/** 7–8：版本自己记下的告警（versions.warnings），不依赖 /sync */
function warningBanners({ board }: BannerInput): Banner[] {
  return board.warnings.flatMap((w, n): Banner[] => {
    if (w.code === "catalog_import_incomplete")
      return [
        {
          key: "catalog_import_incomplete",
          role: "alert",
          text: "本版本采集时剧单导入不完整，请在 RealShort 重跑剧单导入",
        },
      ];
    if (w.code !== "source_stale_running") return [];
    const since =
      typeof w.attemptedAt === "string" && w.attemptedAt
        ? ` ${formatObservedAt(w.attemptedAt)} `
        : "时间未知";
    return [
      {
        key: `source_stale_running:${n}`,
        role: "alert",
        text: `来源「${sourceLabel(w.source)}」自${since}起一直未结束（可能已中断），本版本里它的数据停在更早一次成功的采集`,
      },
    ];
  });
}

function driftBanner({ board }: BannerInput): Banner[] {
  const unknown = board.scope.rules.unknownPlatforms;
  if (unknown.length === 0) return [];
  return [
    {
      key: "drift",
      role: "status",
      text: `RealShort 新增了剧场（${unknown.join("、")}），部分标签可能过时`,
    },
  ];
}

/** 每日采集允许 36 小时；历史版本只判断它采集时的来源年龄，避免把正常回放误报成停更。 */
function sourceFreshnessBanners({ board, now }: BannerInput): Banner[] {
  const reference =
    board.scope.versionId === board.current.id
      ? now.getTime()
      : Date.parse(board.scope.asOf);
  const sources = [
    {
      key: "catalog-stale",
      label: "剧场剧单",
      at: board.freshness?.importedAt,
      consequence: "新剧、下架状态与榜单信号可能滞后",
    },
    {
      key: "posted-stale",
      label: "运营发布记录",
      at: board.postedImportedAt,
      consequence: "没有匹配记录不能据此认定未发布",
    },
  ];
  return sources.flatMap(({ key, label, at, consequence }): Banner[] => {
    if (typeof at !== "string") return [];
    const captured = Date.parse(at);
    if (!Number.isFinite(captured) || reference - captured <= 36 * 3600_000)
      return [];
    return [
      {
        key,
        role: "alert",
        text: `本版本的${label}导入于 ${formatObservedAt(at)}，已超过 36 小时：${consequence}。请先刷新上游资料；工作台「立即同步」只复制上游已有数据。`,
      },
    ];
  });
}

/** 横幅表：版本三条 → gateway 几条 → 14 小时 → 版本告警 → 规则漂移 */
export function bannersFor(input: BannerInput): Banner[] {
  const head = [...versionBanners(input), ...gatewayBanners(input)];
  return [
    ...head,
    ...staleBanner(input, head),
    ...sourceFreshnessBanners(input),
    ...warningBanners(input),
    ...driftBanner(input),
  ];
}
