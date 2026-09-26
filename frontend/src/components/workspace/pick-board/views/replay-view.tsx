// 工作台新建（RealShort 没有回放）：选剧 tab 带 result= 时的回放视图（P4-2，方案 2.5 第 4 条，批判 B10、B11）。
// 名单与顺序是 gateway 按智能体语义重跑出来的（/api/pick/replay），行由页面按 row_key 从镜像取好传进来：本视图不筛、
// 不排，只照名单顺序渲染，卡片展示过的前 limit 部单独框出来；取不到的行、镜像里没有的剧逐条列出。视图是同步的。
// GGWork 配色：展示过的那一组用 link 色框出（品牌色是墨色，框出来像黑边），链接用 link 色，提示框圆角 12。
import Link from "next/link";

import {
  Empty,
  OutOfRange,
  Pager,
  pickHref,
} from "@/components/workspace/pick-board/toolbar";
import type { PickConditions } from "@/core/pick/types";
import { formatObservedAt } from "@/core/pick-board/metrics";
import type { PickRequest } from "@/core/pick-board/request";
import type { PickRow, ReplayAnswer } from "@/server/pick-board";

import { RowsTable } from "../rows-table";

import type { BoardContext } from "./board-data";
import {
  agentOnlyConditions,
  nearFilter,
  type NearFilter,
  type ReplayEntry,
  type ReplayPage,
} from "./replay-rules";

export type ReplayNoticeReason =
  | "notFound"
  | "gone"
  | "conflict"
  | "unavailable";

export type ReplayData =
  | Readonly<{
      kind: "rows";
      answer: ReplayAnswer;
      /** 结果的条件（近似筛选用）；取不到时 null */
      conditions: PickConditions | null;
      /** 这一页怎么切（页面先按它取行） */
      plan: ReplayPage;
      /** 这一页取到的行，顺序不要紧：按 plan 的名单序渲染 */
      rows: readonly PickRow[];
      /** 整份名单里当前版本已无的行键，名单序 */
      missing: readonly string[];
    }>
  | Readonly<{
      kind: "notice";
      reason: ReplayNoticeReason;
      conditions: PickConditions | null;
    }>;

const NOTICE: Record<ReplayNoticeReason, string> = {
  notFound: "找不到这份候选：链接不完整，或它不是你的结果。",
  gone: "这份候选用的剧库批次已过保留期被清理，无法回放。",
  conflict: "这份候选的条件已不能按当前规则重跑，无法回放。",
  unavailable: "暂时拿不到这份候选的回放（工作台后端没有回应），稍后刷新再试。",
};

const BOX =
  "border-warning-line bg-warning-surface text-warning-ink mb-4 rounded-lg border px-4 py-3 text-[13px] leading-[1.65]";
const PANEL =
  "border-line bg-panel mb-4 rounded-lg border px-4 py-3 text-[13px] leading-[1.65]";
const LINK = "text-link hover:underline";
/** 「当前版本已无此行」最多逐条列这么多，其余只给条数 */
const MISSING_LISTED = 100;

function RankNote() {
  return (
    <p className="text-helper mt-1">
      按名次排的候选：榜单 tab
      含已下架的行，智能体候选池不含；同名次时智能体按编号排、榜单按剧名排。
    </p>
  );
}

function NearLinks({ near }: { near: NearFilter }) {
  if (near.href === null)
    return (
      <p className="text-helper mt-1">
        暂时拿不到这份候选的条件，没有近似筛选链接。
      </p>
    );
  return (
    <p className="mt-1 flex flex-wrap gap-3">
      <Link prefetch={false} href={near.href} className={LINK}>
        在选剧 tab 打开近似筛选
      </Link>
      {near.rankHref ? (
        <Link prefetch={false} href={near.rankHref} className={LINK}>
          看这张榜最新一期
        </Link>
      ) : null}
    </p>
  );
}

function NearFilterBox({
  near,
  rankSort,
}: {
  near: NearFilter;
  rankSort: boolean;
}) {
  return (
    <div data-replay-near="true" className={PANEL}>
      <p className="text-ink-2 font-semibold">近似筛选</p>
      <p className="text-helper">
        资料页的筛选语义与智能体不同，只能近似：剧场、语种、依据、未发过对得上，排序与下面列出的条件对不上。
      </p>
      <NearLinks near={near} />
      {rankSort ? <RankNote /> : null}
      {near.unmapped.length > 0 ? (
        <ul className="text-helper mt-1 list-disc pl-5">
          {near.unmapped.map((u) => (
            <li key={u.key}>
              {u.label}
              {u.value ? `：${u.value}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ordinalOf(entries: readonly ReplayEntry[], rowKey: string): number {
  return entries.find((e) => e.rowKey === rowKey)?.ordinal ?? 0;
}

function MissingList({
  missing,
  entries,
}: {
  missing: readonly string[];
  entries: readonly ReplayEntry[];
}) {
  if (missing.length === 0) return null;
  const rest = missing.length - MISSING_LISTED;
  return (
    <div role="alert" data-replay-missing="true" className={BOX}>
      <p>名单里有 {missing.length} 部在这个镜像版本里已经没有：</p>
      <ul className="mt-1 list-disc pl-5">
        {missing.slice(0, MISSING_LISTED).map((key) => (
          <li key={key}>
            第 {ordinalOf(entries, key)} 位 · {key}：当前版本已无此行
          </li>
        ))}
      </ul>
      {rest > 0 ? <p className="mt-1">另有 {rest} 部未列出。</p> : null}
    </div>
  );
}

/** identity 是 [source, source_id, language] 的 JSON；解不开就原样（截短） */
function describeIdentity(identity: string): string {
  try {
    const parsed: unknown = JSON.parse(identity);
    if (Array.isArray(parsed) && parsed.every((p) => typeof p === "string"))
      return parsed.join(" · ");
  } catch {
    // Not JSON: shown as it is.
  }
  return identity.slice(0, 120);
}

function OffsiteList({ page }: { page: readonly ReplayEntry[] }) {
  const offsite = page.filter((e) => e.rowKey === null);
  if (offsite.length === 0) return null;
  return (
    <div data-replay-offsite="true" className={PANEL}>
      <p>
        本页有 {offsite.length}{" "}
        部不在镜像里（个人导入的剧，或编号解不出行键），本页没有它们的行：
      </p>
      <ul className="text-helper mt-1 list-disc pl-5">
        {offsite.map((e) => (
          <li key={e.ordinal}>
            第 {e.ordinal} 位 · {describeIdentity(e.identity)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function rowsOf(
  entries: readonly ReplayEntry[],
  byKey: ReadonlyMap<string, PickRow>,
): PickRow[] {
  const keys = [
    ...new Set(entries.flatMap((e) => (e.rowKey ? [e.rowKey] : []))),
  ];
  return keys.flatMap((key) => {
    const row = byKey.get(key);
    return row ? [row] : [];
  });
}

function RowsGroup({
  kind,
  title,
  rows,
  req,
  ctx,
}: {
  kind: "shown" | "rest";
  title: string;
  rows: PickRow[];
  req: PickRequest;
  ctx: BoardContext;
}) {
  if (rows.length === 0) return null;
  const frame = kind === "shown" ? "border-link rounded-lg border-2 p-2" : "";
  return (
    <div data-replay-group={kind} className={`mb-3 ${frame}`}>
      <p className="text-ink-2 mb-2 text-[13px] font-semibold">{title}</p>
      <RowsTable rows={rows} req={req} rules={ctx.rules} />
    </div>
  );
}

function ReplayRows({
  data,
  req,
  ctx,
}: {
  data: Extract<ReplayData, { kind: "rows" }>;
  req: PickRequest;
  ctx: BoardContext;
}) {
  const { plan, answer } = data;
  if (plan.entries.length === 0)
    return <Empty>这份候选当时没有符合条件的剧。</Empty>;
  if (plan.page.length === 0)
    return <OutOfRange req={req} total={plan.entries.length} />;
  const byKey = new Map(data.rows.map((r) => [r.rowKey, r]));
  const shown = rowsOf(
    plan.page.filter((e) => e.shown),
    byKey,
  );
  const rest = rowsOf(
    plan.page.filter((e) => !e.shown),
    byKey,
  );
  return (
    <>
      <p className="text-ink-dim mb-2 text-[12px]">
        本页是名单第 {plan.first}–{plan.last} 位
      </p>
      <RowsGroup
        kind="shown"
        title={`卡片展示的前 ${answer.limit} 部`}
        rows={shown}
        req={req}
        ctx={ctx}
      />
      <RowsGroup
        kind="rest"
        title="其余符合条件的剧"
        rows={rest}
        req={req}
        ctx={ctx}
      />
      <Pager
        req={req}
        hasMore={plan.hasMore}
        count={plan.page.length}
        total={plan.entries.length}
      />
    </>
  );
}

function ReplayIntro({
  answer,
  ctx,
}: {
  answer: ReplayAnswer;
  ctx: BoardContext;
}) {
  const batch = answer.sourceAsOf
    ? `（采集于 ${formatObservedAt(answer.sourceAsOf)}）`
    : "";
  return (
    <>
      <h2
        id="replay-title"
        className="text-ink-1 mb-1 text-[15px] font-semibold"
      >
        回放智能体候选
      </h2>
      <p className="text-helper mb-3 text-sm leading-[1.65]">
        名单 {answer.total.toLocaleString("en-US")} 部：在智能体当时的批次
        {
          batch
        }上按它自己的筛选语义重跑，顺序同智能体；框出来的是卡片上展示的前{" "}
        {answer.limit} 部。行数据取自镜像 v{ctx.versionId}。
      </p>
    </>
  );
}

function ReplayList({
  data,
  req,
  ctx,
}: {
  data: Extract<ReplayData, { kind: "rows" }>;
  req: PickRequest;
  ctx: BoardContext;
}) {
  const near = nearFilter(
    data.conditions,
    ctx.rules,
    ctx.versionId,
    data.answer.unmappable,
  );
  return (
    <section aria-labelledby="replay-title" data-replay="list">
      <ReplayIntro answer={data.answer} ctx={ctx} />
      <NearFilterBox near={near} rankSort={data.conditions?.sort === "rank"} />
      <MissingList missing={data.missing} entries={data.plan.entries} />
      <OffsiteList page={data.plan.page} />
      <ReplayRows data={data} req={req} ctx={ctx} />
    </section>
  );
}

function ReplayNotice({
  data,
  req,
  ctx,
}: {
  data: Extract<ReplayData, { kind: "notice" }>;
  req: PickRequest;
  ctx: BoardContext;
}) {
  const { reason, conditions } = data;
  const withNear =
    (reason === "gone" || reason === "conflict") && conditions !== null;
  return (
    <section data-replay="notice">
      <p role="alert" className={BOX}>
        {NOTICE[reason]}
      </p>
      {withNear ? (
        <NearFilterBox
          near={nearFilter(
            conditions,
            ctx.rules,
            ctx.versionId,
            agentOnlyConditions(conditions),
          )}
          rankSort={conditions.sort === "rank"}
        />
      ) : null}
      <p className="text-[13px]">
        <Link
          prefetch={false}
          href={pickHref(req, { result: "" })}
          className={LINK}
        >
          回到选剧列表
        </Link>
      </p>
    </section>
  );
}

export function ReplayBody({
  data,
  req,
  ctx,
}: {
  data: ReplayData;
  req: PickRequest;
  ctx: BoardContext;
}) {
  return data.kind === "rows" ? (
    <ReplayList data={data} req={req} ctx={ctx} />
  ) : (
    <ReplayNotice data={data} req={req} ctx={ctx} />
  );
}
