// PORTED_FROM: realshort@816ca2e src/components/admin/pick/posted-table.tsx
// 本地改动：搜索表单的 action 改成 /workspace/pick-data 并带隐藏的 v；选剧池链接改读版本规则 rules.postedPoolUrl
// （buildBoardRules 已净化：空串就不给链接，只写「选剧池」）；Link 加 prefetch={false}。
// GGWork 样式：搜索框聚焦用 link 边框加 brand-soft 光圈，按钮是次要按钮，链接用 link 色，表格圆角 12。
import Link from "next/link";

import {
  POSTED_STATES,
  POSTED_STATE_LABELS,
  type PickRequest,
  type PostedState,
} from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import type {
  PostedLinks,
  PostedRecord,
  PostedStats,
} from "@/server/pick-board";

import { Dim } from "./cells";
import { RuleLink } from "./links";
import { PostedLinkList, PostedStateChips } from "./posted-record";
import { BOARD_PATH, Chips, pickHref } from "./toolbar";

/**
 * 发布记录 tab：五列，与 artifact 的 renderPosted 逐列相同——剧 / 池内状态 / 已发帖子 / 推荐理由 / 对应剧库行。
 * 按最近发布日排，其次入池时间。「对应剧库行」是剧名归一后的同名匹配，不是同剧确认。
 */
function fmt(n: number | null | undefined): string {
  return typeof n === "number" ? n.toLocaleString("en-US") : "—";
}

export function PostedFilters({
  req,
  counts,
}: {
  req: PickRequest;
  counts: Record<PostedState, number>;
}) {
  return (
    <div className="mb-3.5 flex flex-col gap-2.5">
      <form
        action={BOARD_PATH}
        method="get"
        className="flex flex-wrap gap-2"
        role="search"
      >
        <input type="hidden" name="tab" value="posted" />
        {req.postedState ? (
          <input type="hidden" name="pst" value={req.postedState} />
        ) : null}
        {req.v !== null ? <input type="hidden" name="v" value={req.v} /> : null}
        <label htmlFor="posted-search" className="sr-only">
          搜索剧名、编号、理由、备注、来源或账号
        </label>
        <input
          id="posted-search"
          name="q"
          defaultValue={req.q}
          placeholder="搜索剧名 / 编号 / 理由 / 备注 / 来源 / 账号"
          className="border-line bg-panel placeholder:text-ink-dim focus-visible:border-link focus-visible:ring-brand-soft min-w-0 flex-1 rounded-md border px-3 py-2 text-sm focus-visible:ring-[3px] focus-visible:outline-none"
        />
        <button
          className="border-line-strong bg-panel text-ink-1 hover:bg-panel-hover rounded-md border px-4 py-2 text-sm font-medium"
          type="submit"
        >
          搜索
        </button>
      </form>
      <Chips
        label="状态"
        current={req.postedState}
        items={POSTED_STATES.map((s) => ({
          value: s,
          text: POSTED_STATE_LABELS[s],
          count: counts[s],
        }))}
        build={(v) => pickHref(req, { postedState: v as PostedState })}
      />
    </div>
  );
}

function snapshotAt(at: Date | null): string {
  return at ? `${at.toISOString().slice(0, 16).replace("T", " ")} UTC` : "—";
}

export function PostedNote({
  stats,
  shown,
  rules,
}: {
  stats: PostedStats;
  shown: number;
  rules: Pick<BoardRules, "postedPoolUrl">;
}) {
  return (
    <p className="text-helper mb-3 text-sm leading-[1.65]">
      {fmt(shown)} 部（选剧池共 {fmt(stats.total)} 部，已发{" "}
      {fmt(stats.pubCount)} 部、帖子 {fmt(stats.postsSum)} 条、累计播放{" "}
      {fmt(stats.viewsSum)}，指标截至 {stats.metricAt ?? "—"}
      ）。来源：运营的飞书表{" "}
      <RuleLink
        href={rules.postedPoolUrl}
        className="text-link hover:underline"
        fallback="选剧池"
      >
        选剧池 ↗
      </RuleLink>
      ，用户身份只读，快照 {snapshotAt(stats.importedAt)}
      。「已发」只数状态是已回填 /
      已公开的帖子，待公开另计；按最近发布日排，其次入池时间。「对应剧库行」是剧名归一后的同名匹配，不是同剧确认。
    </p>
  );
}

const HEADS: { text: string; hint?: string }[] = [
  {
    text: "剧",
    hint: "选剧池里的剧名；下面是分类与「剧ID · 语言 · 平台 · 来源」原文",
  },
  { text: "池内状态", hint: "选剧池的状态列与是否排期；上线 / 入池日期" },
  {
    text: "已发帖子",
    hint: "已回填 / 已公开的帖子数，待公开另计；播放只加有数的帖子",
  },
  { text: "推荐理由", hint: "推荐人与理由 / 备注原文" },
  {
    text: "对应剧库行",
    hint: "剧名归一后对上的剧场行或 ReelShort 剧，一律同名未核",
  },
];

function TitleTd({ p, req }: { p: PostedRecord; req: PickRequest }) {
  return (
    <td className="px-3 py-2 align-top">
      <div className="text-[13px] leading-snug font-semibold">
        <Link
          prefetch={false}
          href={pickHref(req, { tab: "posted", sd: p.sd, page: 1 })}
          className="hover:underline"
        >
          {p.title}
        </Link>
      </div>
      {p.cats.length ? (
        <div className="text-helper mt-0.5 max-w-[280px] text-[11px]">
          {p.cats.join(" · ")}
        </div>
      ) : null}
      <div
        className="text-ink-dim mt-0.5 text-[11px]"
        title="选剧池里的剧ID · 语言 · 平台 · 来源，原文"
      >
        {p.sd || "无剧ID"} · {p.lang || "语言未填"} ·{" "}
        <span className="bg-raised text-ink-2 rounded-sm px-1 py-px font-semibold">
          {p.platform || "平台未填"}
        </span>
        {p.sources.length ? ` · ${p.sources.join(" / ")}` : ""}
      </div>
    </td>
  );
}

function StateTd({ p }: { p: PostedRecord }) {
  return (
    <td className="px-3 py-2 align-top text-[12px]">
      <PostedStateChips p={p} />
      <div className="text-ink-dim mt-0.5 text-[11px]">
        {p.onlineOn ? `上线 ${p.onlineOn}` : "上线日期未填"} · 入池{" "}
        {p.createdOn ?? "?"}
      </div>
    </td>
  );
}

function PostsTd({ p }: { p: PostedRecord }) {
  if (p.postCount === 0)
    return (
      <td className="px-3 py-2 align-top text-[12px]">
        <Dim>未发</Dim>
        {p.schedCount ? (
          <div className="text-ink-dim mt-0.5 text-[11px]">
            {p.schedCount} 条待公开
          </div>
        ) : null}
      </td>
    );
  return (
    <td className="px-3 py-2 align-top text-[12px]">
      <b>{fmt(p.postCount)} 条</b>
      {p.schedCount ? (
        <span className="text-ink-dim"> +{p.schedCount} 待公开</span>
      ) : null}
      <div className="text-ink-dim mt-0.5 text-[11px]">
        播放 {fmt(p.viewsTotal)}
        {p.viewsCount && p.viewsCount < p.postCount
          ? `（${p.viewsCount} 条有数）`
          : ""}{" "}
        · 最近 {p.lastPostOn ?? "?"} · {p.accounts.join("、") || "账号未填"}
      </div>
    </td>
  );
}

function WhyTd({ p }: { p: PostedRecord }) {
  return (
    <td className="max-w-[300px] px-3 py-2 align-top text-[12px]">
      {p.who.length ? (
        <span className="bg-raised text-ink-2 mr-1 rounded-sm px-1.5 py-px text-[11px]">
          {p.who.join("、")}
        </span>
      ) : null}
      {p.why}
      {p.note ? (
        <div className="text-ink-dim mt-0.5 text-[11px]">备注：{p.note}</div>
      ) : null}
      {!p.why && !p.note && !p.who.length ? <Dim /> : null}
    </td>
  );
}

export function PostedTable({
  rows,
  links,
  req,
}: {
  rows: PostedRecord[];
  links: PostedLinks;
  req: PickRequest;
}) {
  return (
    <div className="border-line bg-panel overflow-x-auto rounded-lg border">
      <table className="w-full min-w-[960px] border-collapse text-left">
        <thead>
          <tr>
            {HEADS.map((h) => (
              <th
                scope="col"
                key={h.text}
                title={h.hint}
                className="border-line bg-raised text-helper border-b px-2.5 py-2 text-left text-[11px] font-semibold tracking-[.06em] whitespace-nowrap"
              >
                {h.text}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr
              key={p.sd}
              className={`border-line border-b last:border-b-0 ${p.archived ? "opacity-70" : ""}`}
            >
              <TitleTd p={p} req={req} />
              <StateTd p={p} />
              <PostsTd p={p} />
              <WhyTd p={p} />
              <td className="px-3 py-2 align-top">
                <PostedLinkList p={p} links={links} req={req} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
