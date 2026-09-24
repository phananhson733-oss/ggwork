// PORTED_FROM: realshort@816ca2e src/components/admin/pick/posted-record.tsx
// 本地改动：Link 加 prefetch={false}；公开页（dramaPath）是 ReelShort 站的绝对地址，新标签打开（★38）；
// 帖子链接只对 https:// 出 <a>，别的（含网盘清洗留下的「[网盘信息已移除]」、http）照原文显示为文字（B9）；
// 帖子一行与帖子汇总拆成小函数（函数 <50 行）。
import Link from "next/link";

import {
  PLATFORM_LABELS,
  reelshortRowKey,
  type PickRequest,
} from "@/core/pick-board/request";
import { dramaPath } from "@/core/pick-board/site";
import type { PostedLinks, PostedRecord } from "@/server/pick-board";

import { ExternalLink, isSafeUrl } from "./links";
import { pickHref, rowHref } from "./toolbar";

/**
 * 一条发布记录的完整形态：池内字段 + 帖子逐条表。证据页的「我们的发布记录」与发布记录 tab 的单条页共用，
 * 两处只有一份「缺数据长什么样」的定义（状态未填 / 上线日期未填 / 还没有帖子）。
 */
function fmt(n: number | null | undefined): string {
  return typeof n === "number" ? n.toLocaleString("en-US") : "—";
}

export function PostedStateChips({ p }: { p: PostedRecord }) {
  return (
    <span className="inline-flex flex-wrap gap-1 align-middle">
      {p.life ? (
        <span className="border-line text-ink-2 rounded-[4px] border px-1 py-px text-[10px]">
          {p.life}
        </span>
      ) : null}
      {p.scheduled ? (
        <span className="border-success-ink/40 bg-success-surface text-success-ink rounded-[4px] border px-1 py-px text-[10px]">
          已排期
        </span>
      ) : (
        <span className="border-warning-line bg-warning-surface text-warning-ink rounded-[4px] border px-1 py-px text-[10px]">
          未排期
        </span>
      )}
      {p.archived ? (
        <span className="border-line text-ink-dim rounded-[4px] border px-1 py-px text-[10px]">
          已归档
        </span>
      ) : null}
    </span>
  );
}

function present<T>(value: T | undefined): value is T {
  return value !== undefined;
}

type LinkedRow = PostedLinks["rows"] extends Map<string, infer T> ? T : never;
type LinkedDrama =
  PostedLinks["dramas"] extends Map<string, infer T> ? T : never;

function LinkedRows({
  rows,
  req,
  limit,
}: {
  rows: readonly LinkedRow[];
  req: PickRequest;
  limit: number;
}) {
  const shown = rows.slice(0, limit);
  return (
    <>
      {shown.map((r) => (
        <div key={r.rowKey}>
          <Link
            prefetch={false}
            href={rowHref(req, r.rowKey)}
            className="text-brand hover:underline"
          >
            {PLATFORM_LABELS[r.platform]} · {r.lang || "语种未标"} · {r.title}
          </Link>
          {r.offOn ? (
            <span className="text-brand ml-1 text-[10px]">下架 {r.offOn}</span>
          ) : null}
        </div>
      ))}
      {rows.length > shown.length ? (
        <div className="text-ink-dim">另 {rows.length - shown.length} 行</div>
      ) : null}
    </>
  );
}

function LinkedDramas({
  dramas,
  req,
  limit,
}: {
  dramas: readonly LinkedDrama[];
  req: PickRequest;
  limit: number;
}) {
  return (
    <>
      {dramas.slice(0, limit).map((d) => (
        <div key={d.id} className="flex flex-wrap gap-x-2">
          <span className="text-ink-2">
            ReelShort · {d.locale} · {d.title}
          </span>
          <ExternalLink
            href={dramaPath(d.locale, d.slug)}
            className="text-brand hover:underline"
          >
            公开页
          </ExternalLink>
          <Link
            prefetch={false}
            href={rowHref(req, reelshortRowKey(d.id))}
            className="text-brand hover:underline"
          >
            证据页
          </Link>
        </div>
      ))}
      {dramas.length > limit ? (
        <div className="text-ink-dim">另 {dramas.length - limit} 部</div>
      ) : null}
    </>
  );
}

/** 对上的剧场行 / ReelShort 剧的链接列表；对不上就说清楚怎么再核 */
export function PostedLinkList({
  p,
  links,
  req,
  limit = 4,
}: {
  p: PostedRecord;
  links: PostedLinks;
  req: PickRequest;
  limit?: number;
}) {
  const rows = p.rowKeys.map((k) => links.rows.get(k)).filter(present);
  const dramas = p.dramaIds.map((id) => links.dramas.get(id)).filter(present);
  if (rows.length === 0 && dramas.length === 0)
    return (
      <div className="text-[12px]">
        <span className="text-ink-dim">剧库未对上</span>
        <div className="text-helper mt-0.5 text-[11px]">
          剧名归一后九个剧场和 ReelShort
          里都没有同名行；用「全部剧库」搜关键字再核
        </div>
      </div>
    );
  return (
    <div className="text-[12px]">
      <LinkedRows rows={rows} req={req} limit={limit} />
      <LinkedDramas dramas={dramas} req={req} limit={limit} />
      <div className="mt-0.5">
        <span className="border-warning-line bg-warning-surface text-warning-ink rounded-[4px] border px-1 py-px text-[10px]">
          同名未核
        </span>
      </div>
    </div>
  );
}

type Post = PostedRecord["posts"][number];

/** 帖子链接：https 才是链接；别的原文当文字（清洗留下的占位、http 链接都不给 <a>） */
function PostUrl({ url }: { url: string | undefined }) {
  if (isSafeUrl(url))
    return (
      <ExternalLink
        href={url}
        rel="nofollow sponsored noopener"
        className="text-brand hover:underline"
      >
        打开 ↗
      </ExternalLink>
    );
  if (!url) return <>—</>;
  return <>{url}</>;
}

function PostRow({ x }: { x: Post }) {
  const num = "py-1 pr-2 text-right tabular-nums";
  return (
    <tr className="border-line border-t">
      <td className="py-1 pr-2 whitespace-nowrap">{x.d ?? "—"}</td>
      <td className="py-1 pr-2">{x.acct ?? "—"}</td>
      <td className="py-1 pr-2 whitespace-nowrap">{x.st ?? "—"}</td>
      <td className={num}>{fmt(x.views)}</td>
      <td className={num}>{fmt(x.likes)}</td>
      <td className={num}>{fmt(x.favs)}</td>
      <td className={num}>{fmt(x.cmts)}</td>
      <td className={num}>{fmt(x.shares)}</td>
      <td className="py-1 pr-2 whitespace-nowrap">{x.md ?? "—"}</td>
      <td className="py-1 pr-2">{x.note ?? ""}</td>
      <td className="py-1 break-all">
        <PostUrl url={x.url} />
      </td>
    </tr>
  );
}

function PostsHead() {
  return (
    <thead>
      <tr className="text-helper text-left">
        <th scope="col" className="py-1 pr-2">
          日期
        </th>
        <th scope="col" className="py-1 pr-2">
          账号
        </th>
        <th scope="col" className="py-1 pr-2">
          状态
        </th>
        <th scope="col" className="py-1 pr-2 text-right">
          播放
        </th>
        <th scope="col" className="py-1 pr-2 text-right">
          点赞
        </th>
        <th scope="col" className="py-1 pr-2 text-right">
          收藏
        </th>
        <th scope="col" className="py-1 pr-2 text-right">
          评论
        </th>
        <th scope="col" className="py-1 pr-2 text-right">
          分享
        </th>
        <th scope="col" className="py-1 pr-2" title="播放等数字是哪天回填的">
          指标日期
        </th>
        <th scope="col" className="py-1 pr-2">
          备注
        </th>
        <th scope="col" className="py-1">
          链接
        </th>
      </tr>
    </thead>
  );
}

export function PostsTable({ posts }: { posts: PostedRecord["posts"] }) {
  if (posts.length === 0)
    return <p className="text-helper mt-1 text-xs">还没有帖子。</p>;
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full min-w-[640px] text-xs">
        <PostsHead />
        <tbody>
          {posts.map((x, i) => (
            <PostRow key={x.pid ?? i} x={x} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 证据页里的那张卡：一条记录的头 + 理由 / 备注 + 帖子表 */
export function PostedRecordCard({
  p,
  req,
  links,
}: {
  p: PostedRecord;
  req: PickRequest;
  links?: PostedLinks;
}) {
  return (
    <div className="bg-raised rounded-lg p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <Link
          prefetch={false}
          href={pickHref(req, { tab: "posted", sd: p.sd, page: 1 })}
          className="text-brand font-mono text-xs hover:underline"
        >
          {p.sd}
        </Link>
        <span className="font-semibold">{p.title}</span>
        {p.platform ? <span className="text-helper">{p.platform}</span> : null}
        {p.lang ? <span className="text-helper">{p.lang}</span> : null}
        <PostedStateChips p={p} />
        <span className="text-helper">
          {p.onlineOn ? `上线 ${p.onlineOn}` : "上线日期未填"} · 入池{" "}
          {p.createdOn ?? "?"}
        </span>
        {p.feishuRecord ? (
          <span className="text-ink-dim text-xs">
            飞书记录 {p.feishuRecord}
          </span>
        ) : null}
      </div>
      <PostedMeta p={p} />
      {links ? (
        <div className="mt-2">
          <PostedLinkList p={p} links={links} req={req} limit={8} />
        </div>
      ) : null}
      <PostsTable posts={p.posts} />
    </div>
  );
}

/** 帖子汇总一行：已发几条（待公开另计）· 播放 · 首发 / 最近 · 账号 · 指标日期 */
function postsSummary(p: PostedRecord): string {
  if (p.postCount === 0)
    return p.schedCount ? `未发，${p.schedCount} 条待公开` : "未发";
  const sched = p.schedCount ? `（另 ${p.schedCount} 条待公开）` : "";
  const counted =
    p.viewsCount && p.viewsCount < p.postCount
      ? `（${p.viewsCount} 条有数）`
      : "";
  const metric = p.metricAt ? ` · 指标截至 ${p.metricAt}` : "";
  return `已发 ${p.postCount} 条${sched} · 播放 ${fmt(p.viewsTotal)}${counted} · 首发 ${p.firstPostOn ?? "?"} · 最近 ${p.lastPostOn ?? "?"} · ${p.accounts.join("、") || "账号未填"}${metric}`;
}

/** 来源 / 分类 / 推荐人 / 理由 / 备注 / 帖子汇总，一行一行给 */
export function PostedMeta({ p }: { p: PostedRecord }) {
  const lines: { k: string; v: string }[] = [
    ...(p.sources.length ? [{ k: "来源", v: p.sources.join(" / ") }] : []),
    ...(p.cats.length ? [{ k: "分类", v: p.cats.join(" · ") }] : []),
    ...(p.who.length ? [{ k: "推荐人", v: p.who.join("、") }] : []),
    ...(p.why ? [{ k: "推荐理由", v: p.why }] : []),
    ...(p.note ? [{ k: "备注", v: p.note }] : []),
    { k: "帖子", v: postsSummary(p) },
    ...(p.updatedOn ? [{ k: "最后修改", v: p.updatedOn }] : []),
  ];
  return (
    <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
      {lines.map((l) => (
        <div key={l.k} className="contents">
          <dt className="text-helper">{l.k}</dt>
          <dd className="text-ink-2">{l.v}</dd>
        </div>
      ))}
    </dl>
  );
}
