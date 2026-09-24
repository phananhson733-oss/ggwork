// 工作台新建（RealShort 没有这个文件）：资料页上两类「不是站内 pickHref」的链接只在这里渲染。
// 1. 版本规则里的链接（剧场文档、选剧池）：buildBoardRules 已把它归成三种——改写好的
//    /workspace/pick-data?…&v=、https:// 外链、空串（含被置空的 javascript: 之类），这里按种类出 Link / a / 不出链接。
// 2. 数据里的 url（账号主页、帖子链接）：只有 https:// 开头的才出 <a>（批判 B9、U10）；网盘清洗把整串换成
//    「[网盘信息已移除]」时它不是链接，原样当文字显示，不能让它变成一个相对链接。
import Link from "next/link";
import type { ReactNode } from "react";

const BOARD_LINK = "/workspace/pick-data?";

/** 数据里的 url 能不能出 <a>：只认 https://（http 与其余一律当文字） */
export function isSafeUrl(url: string | null | undefined): url is string {
  return typeof url === "string" && url.startsWith("https://");
}

/** 站外链接：新标签打开、不带 opener */
export function ExternalLink({
  href,
  className,
  title,
  rel = "noopener",
  children,
}: {
  href: string;
  className?: string;
  title?: string;
  rel?: string;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel={rel}
      className={className}
      title={title}
    >
      {children}
    </a>
  );
}

/**
 * 版本规则里的链接。站内的走 next/link（不预取），外链新标签打开，空串或别的一律不出链接，
 * 这时显示 fallback（默认什么都不显示）。
 */
export function RuleLink({
  href,
  className,
  children,
  fallback = null,
}: {
  href: string;
  className?: string;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  if (href.startsWith(BOARD_LINK))
    return (
      <Link prefetch={false} href={href} className={className}>
        {children}
      </Link>
    );
  if (isSafeUrl(href))
    return (
      <ExternalLink
        href={href}
        rel="nofollow sponsored noopener"
        className={className}
      >
        {children}
      </ExternalLink>
    );
  return <>{fallback}</>;
}
