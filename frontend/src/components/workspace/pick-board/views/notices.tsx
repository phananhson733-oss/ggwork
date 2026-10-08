// 工作台新建：资料页拿不到数据时的提示。访客不能看（AuthNotice）、镜像读不了（MirrorNotice）两类，都是 role="alert"，
// 不进 error.tsx：它们是部署或数据状态，不是页面坏了。文案只说原因与去处，不带 SQL、参数或连接串。
import Link from "next/link";

import { pickHref } from "@/components/workspace/pick-board/toolbar";
import { buildLoginUrl } from "@/core/auth/types";
import {
  RANKS,
  RANK_LABELS,
  type PickRequest,
} from "@/core/pick-board/request";
import { cn } from "@/lib/utils";
import type {
  BoardNoticeReason,
  MisconfiguredReason,
} from "@/server/pick-board";

import { IMPORTS_HREF } from "./banner-rules";

const BOX =
  "border-warning-line bg-warning-surface text-warning-ink mb-4 rounded-lg border px-4 py-3 text-[13px] leading-[1.65]";
const LINK = "text-link ml-2 hover:underline";

const AUTH_TEXT: Record<BoardNoticeReason, string> = {
  forbidden:
    "选剧资料只给登录的正式账号看：演示账号和关闭登录的本机模式看不到这里的数据。",
  gateway_unavailable: "暂时连不上工作台后端，没法确认你的身份；稍后刷新再试。",
  config_error: "工作台的后端地址没有配置，选剧资料打不开；请联系管理员。",
};

export function AuthNotice({ reason }: { reason: BoardNoticeReason }) {
  return (
    <p role="alert" className={BOX}>
      {AUTH_TEXT[reason]}
    </p>
  );
}

export type MirrorNoticeKind =
  | Readonly<{ kind: "unavailable" }>
  | Readonly<{ kind: "empty" }>
  | Readonly<{ kind: "misconfigured"; reason: MisconfiguredReason }>
  | Readonly<{ kind: "busy" }>
  | Readonly<{ kind: "gone" }>
  | Readonly<{ kind: "period-missing" }>
  | Readonly<{ kind: "session-required" }>;

function misconfiguredText(reason: MisconfiguredReason): string {
  switch (reason) {
    case "permission":
    case "current_unreadable":
      return "镜像版本不可读（授权缺失），请联系管理员。";
    case "control_missing":
      return "读连接指向的库里没有镜像表（库名不对，或迁移没跑），请联系管理员。";
    case "rules":
      return "镜像版本的规则形状本页不认识（rules），请联系管理员。";
    case "control_shape":
      return "镜像版本表的形状本页不认识（control_shape），请联系管理员。";
    default:
      return `镜像库的读连接配置有误（${reason}），请联系管理员。`;
  }
}

function noticeText(notice: MirrorNoticeKind): string {
  switch (notice.kind) {
    case "unavailable":
      return "此部署未连接镜像库，这里只有同步与导入可用。";
    case "empty":
      return "镜像还没有发布任何版本。";
    case "misconfigured":
      return misconfiguredText(notice.reason);
    case "busy":
      return "镜像库繁忙，请稍后刷新。";
    case "gone":
      return "该版本刚被清理。";
    case "session-required":
      return "登录验证需要更新，请重新登录后重试。";
    case "period-missing":
      return "当前资料中没有可读取的期次。";
  }
}

/** 镜像读不了：解析版本时（没有版本号可用，去处是「同步与导入」）或读某个 tab 时（「打开当前版本」） */
export function MirrorNotice({
  notice,
  req,
}: {
  notice: MirrorNoticeKind;
  req: PickRequest;
}) {
  if (notice.kind === "session-required")
    return (
      <p role="alert" className={cn(BOX, "text-base")}>
        {noticeText(notice)}
        <Link
          prefetch={false}
          className={`${LINK} inline-flex min-h-11 items-center`}
          href={buildLoginUrl(pickHref(req, { page: req.page }))}
        >
          重新登录
        </Link>
      </p>
    );
  if (notice.kind === "period-missing") {
    const params = new URL(pickHref(req, {}), "http://board.invalid")
      .searchParams;
    return (
      <section role="alert" className={cn(BOX, "text-base")}>
        <p>当前资料中没有可读取的期次。</p>
        <form
          action="/workspace/pick-data"
          method="get"
          className="my-3 flex flex-wrap items-center gap-3"
        >
          {[...params]
            .filter(([key]) => key !== "rk")
            .map(([key, value]) => (
              <input key={key} type="hidden" name={key} value={value} />
            ))}
          <label>
            更换榜单
            <select
              name="rk"
              defaultValue={req.rank}
              className="bg-surface text-ink-1 ml-2 min-h-11 rounded border p-2"
            >
              {RANKS.map((rank) => (
                <option key={rank} value={rank}>
                  {RANK_LABELS[rank]}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            className="text-link min-h-11 rounded border px-3"
          >
            查看所选榜单
          </button>
        </form>
        <Link
          prefetch={false}
          href={pickHref(req, { tab: "imports" })}
          className={`${LINK} inline-flex min-h-11 items-center`}
        >
          查看同步状态
        </Link>
      </section>
    );
  }
  const link =
    notice.kind === "gone" ? (
      <Link prefetch={false} href={pickHref(req, { v: null })} className={LINK}>
        打开当前版本
      </Link>
    ) : notice.kind === "busy" ? null : (
      <Link prefetch={false} href={IMPORTS_HREF} className={LINK}>
        看「同步与导入」
      </Link>
    );
  return (
    <p role="alert" className={BOX}>
      {noticeText(notice)}
      {link}
    </p>
  );
}
