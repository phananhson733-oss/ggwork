import type { Metadata } from "next";
import { redirect } from "next/navigation";
import type { ReactElement, ReactNode } from "react";

import { Sources } from "@/components/workspace/pick-board/sources";
import { TAB_LABELS } from "@/components/workspace/pick-board/toolbar";
import {
  bannersFor,
  type Banner,
} from "@/components/workspace/pick-board/views/banner-rules";
import { Banners } from "@/components/workspace/pick-board/views/banners";
import type {
  BoardContext,
  DetailData,
  RankData,
  TabData,
} from "@/components/workspace/pick-board/views/board-data";
import {
  BoardHeader,
  BoardTitle,
} from "@/components/workspace/pick-board/views/board-header";
import { BoardShell } from "@/components/workspace/pick-board/views/board-shell";
import {
  BoardTabs,
  ImportsOnlyTabs,
} from "@/components/workspace/pick-board/views/board-tabs";
import { DifferencesNote } from "@/components/workspace/pick-board/views/differences-note";
import { ImportsView } from "@/components/workspace/pick-board/views/imports-view";
import {
  AuthNotice,
  MirrorNotice,
  type MirrorNoticeKind,
} from "@/components/workspace/pick-board/views/notices";
import {
  replayBannerBoard,
  replayBanners,
  replayPage,
  replayVersion,
} from "@/components/workspace/pick-board/views/replay-rules";
import {
  ReplayBody,
  type ReplayData,
} from "@/components/workspace/pick-board/views/replay-view";
import { TabView } from "@/components/workspace/pick-board/views/tab-view";
import { validateAuthNextPath } from "@/core/auth/next-path";
import { buildLoginUrl } from "@/core/auth/types";
import type { PickSyncStatus } from "@/core/pick/sync-schema";
import {
  isObsTab,
  isRsRank,
  parsePickRequest,
  reelshortId,
  type PickRequest,
} from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import {
  freshnessOf,
  type GatewayResult,
  getPickSync,
  loadAccounts,
  loadCandidatePool,
  loadFacets,
  loadGrowthDiagnosis,
  loadMissingKeys,
  loadPickRows,
  loadPostedList,
  loadPostedRecord,
  loadPostedStats,
  loadRankMeta,
  loadRankRows,
  loadReelshortDetail,
  loadReplay,
  loadRowDetail,
  loadRowsByKeys,
  loadRsRank,
  PICK_DATA_PATH,
  pickDataNextPath,
  type ReadyBoard,
  type ReplayLoad,
  requireBoardUser,
  resolveBoard,
  setBoardScope,
  sourcesOf,
} from "@/server/pick-board";

import { guarded } from "./notice-of";
import { obsRoute } from "./obs-route";
import { trendsRoute } from "./trends-route";

/**
 * 选剧资料：RealShort 选剧台的七个 tab，读工作台库里 P2 写的镜像版本（pickm_vN），一个请求钉一个版本。
 *
 * 顺序（合成稿 P3-5）：先鉴权，再决定读哪个版本、同时问 gateway 的 /sync，钉住版本（setBoardScope）之后才取数。
 * 取数都在这里：组件对 @/server/pick-board 只能 import type（契约测试），views/ 下的视图都是同步的。
 * 镜像读不了（没配、没版本、授权缺失、忙、版本刚被清理）是提示，不进 error.tsx；别的错误照常抛给 error.tsx。
 * 回放（tab=pick&result=…，P4-2）：先问 gateway 的 /replay（名单）与 /results/{id}（条件），再按结果配对的镜像版本
 * 钉住（批判 B11：它优先于链接的 v），只取这一页的行、一次查全名单的缺行（B10）。别的 tab 带着 result 一律忽略。
 * 趋势雷达的两个 tab 在鉴权之后早分支（D9）：不解析镜像版本，镜像读不了也能看。trends 是简化版趋势表（2026-09-30），
 * 进 trends-route.tsx 问 gateway；search（GSC，先隐藏）照旧进 obs-route.tsx 读 pick_obs 视图。
 */

export const dynamic = "force-dynamic";
export const maxDuration = 60;

type SearchParams = Promise<Record<string, string | string[] | undefined>>;
type Board = ReadyBoard<BoardRules>;

// searchParams is optional so a caller without request context still gets a
// title (the removed Nextra docs build called generateMetadata({})).
export async function generateMetadata({
  searchParams,
}: {
  searchParams?: SearchParams;
}): Promise<Metadata> {
  const req = parsePickRequest((await searchParams) ?? {});
  if (isReplay(req)) return { title: "选剧资料 · 回放候选" };
  return { title: `选剧资料 · ${TAB_LABELS[req.tab]}` };
}

/** 选剧 tab 带着合法的 result：回放；别的 tab 上的 result 不算 */
function isReplay(req: PickRequest): boolean {
  return req.tab === "pick" && req.result !== "";
}

async function loadRank(req: PickRequest): Promise<RankData> {
  const meta = await loadRankMeta(req);
  const rank = req.rank;
  if (!isRsRank(rank))
    return {
      kind: "rank",
      meta,
      board: { kind: "theater", rank, page: await loadRankRows(req, meta) },
    };
  const result = await loadRsRank(req, rank);
  if (result.kind === "ledger")
    return { kind: "rank", meta, board: { ...result, rank } };
  const diagnosis =
    rank === "rs_growth" && result.rows.length === 0
      ? await loadGrowthDiagnosis(req)
      : null;
  return { kind: "rank", meta, board: { ...result, rank, diagnosis } };
}

async function loadDetail(req: PickRequest): Promise<DetailData> {
  if (!req.rowKey) return { kind: "no-row" };
  const id = reelshortId(req.rowKey);
  if (id)
    return { kind: "reelshort", id, detail: await loadReelshortDetail(id) };
  return {
    kind: "row",
    rowKey: req.rowKey,
    detail: await loadRowDetail(req.rowKey),
  };
}

async function loadTab(req: PickRequest): Promise<TabData> {
  switch (req.tab) {
    case "row":
      return loadDetail(req);
    case "rank":
      return loadRank(req);
    case "posted": {
      if (req.sd)
        return { kind: "posted-record", found: await loadPostedRecord(req.sd) };
      const [list, stats, accounts] = await Promise.all([
        loadPostedList(req),
        loadPostedStats(),
        loadAccounts(),
      ]);
      return { kind: "posted", list, stats, accounts };
    }
    case "rules":
      return { kind: "rules" };
    case "trends":
    case "search":
      // 两个观测 tab 在 PickDataPage 里经 trendsRoute、obsRoute 早分支出去（D9），不读镜像版本；走到这里就是分支漏了
      throw new Error(`观测 tab 不经镜像版本：${req.tab}`);
    default: {
      const [page, facets] = await Promise.all([
        loadPickRows(req),
        loadFacets(req),
      ]);
      return { kind: "list", page, facets };
    }
  }
}

function contextOf(board: Board): BoardContext {
  return {
    rules: board.scope.rules,
    asOf: new Date(board.scope.asOf),
    versionId: board.scope.versionId,
    freshness: freshnessOf(board.freshness),
    latestSnapshot: board.latestSnapshot,
    seriesState: board.series,
  };
}

/** 页头、横幅、tab、这一 tab 的内容、页底：列表与回放共用的外框（徽标来自版本的新鲜度） */
function BoardFrame({
  board,
  ctx,
  req,
  banners,
  pool,
  children,
}: {
  board: Board;
  ctx: BoardContext;
  req: PickRequest;
  banners: readonly Banner[];
  pool: number | null;
  children: ReactNode;
}) {
  const fresh = ctx.freshness;
  return (
    <BoardShell>
      <BoardHeader ctx={ctx} pool={pool} />
      <Banners banners={banners} />
      <BoardTabs
        req={req}
        counts={{
          pick: fresh.withSignal + fresh.rsCandidates,
          all: fresh.rows + fresh.rsCanonical,
          posted: fresh.posted,
        }}
      />
      {children}
      <Sources
        fresh={fresh}
        sources={sourcesOf(board.sources)}
        asOf={ctx.asOf}
      />
      <DifferencesNote />
    </BoardShell>
  );
}

/**
 * 版本已钉住：先 setBoardScope，再取这一 tab 的数，取完才出 JSX（返回的是同步的元素树：异步的只有这一层）。
 */
async function boardPage(
  board: Board,
  req0: PickRequest,
  sync: GatewayResult<PickSyncStatus>,
): Promise<ReactElement> {
  setBoardScope(board.scope);
  const req: PickRequest = {
    ...req0,
    v: board.scope.versionId,
    result: req0.tab === "row" && req0.from === "pick" ? req0.result : "",
  };
  const ctx = contextOf(board);
  const loaded = await guarded(() =>
    Promise.all([loadCandidatePool(), loadTab(req)]),
  );
  const banners = bannersFor({ board, sync, req, now: new Date() });
  return (
    <BoardFrame
      board={board}
      ctx={ctx}
      req={req}
      banners={banners}
      pool={loaded.ok ? loaded.value[0] : null}
    >
      {loaded.ok ? (
        <TabView data={loaded.value[1]} req={req} ctx={ctx} />
      ) : (
        <MirrorNotice notice={loaded.notice} req={req} />
      )}
    </BoardFrame>
  );
}

/** 401 已在 replayRoute 里转去登录，到这里的回放只剩这几种 */
type ReplayOutcome = Exclude<ReplayLoad, { kind: "unauthenticated" }>;

/** 回放这一页：只取这一页的行、一次查全名单的缺行（B10）；回放接口没给名单时是一条提示 */
async function replayData(
  loaded: ReplayOutcome,
  req: PickRequest,
): Promise<ReplayData> {
  if (loaded.kind !== "ok")
    return {
      kind: "notice",
      reason: loaded.kind,
      conditions: "conditions" in loaded ? loaded.conditions : null,
    };
  const plan = replayPage(loaded.answer, req.page, req.size);
  const [rows, missing] = await Promise.all([
    loadRowsByKeys(plan.pageKeys),
    loadMissingKeys(plan.allKeys),
  ]);
  const { answer, conditions } = loaded;
  return { kind: "rows", answer, conditions, plan, rows, missing };
}

/**
 * 回放的横幅：有名单时，版本怎么落的由 replayBanners 说（B11），页面自己的版本横幅让位（replayBannerBoard）；
 * 没有名单（404 / 409 / 410 / 拿不到）时是普通的横幅，链接不带 result。
 */
function replayBannerList(
  board: Board,
  loaded: ReplayOutcome,
  sync: GatewayResult<PickSyncStatus>,
  urlV: number | null,
  req: PickRequest,
): Banner[] {
  const now = new Date();
  if (loaded.kind !== "ok")
    return bannersFor({ board, sync, req: { ...req, result: "" }, now });
  const paired = loaded.answer.mirrorVersion;
  return [
    ...replayBanners({ board, answer: loaded.answer, urlV, req }),
    ...bannersFor({ board: replayBannerBoard(board, paired), sync, req, now }),
  ];
}

/** 回放的版本已钉住：同一个外框，内容换成回放视图；翻页链接带着 result 与显示的版本 */
async function replayBoardPage(
  board: Board,
  req0: PickRequest,
  sync: GatewayResult<PickSyncStatus>,
  loaded: ReplayOutcome,
): Promise<ReactElement> {
  setBoardScope(board.scope);
  const req: PickRequest = { ...req0, v: board.scope.versionId };
  const ctx = contextOf(board);
  const content = await guarded(() =>
    Promise.all([loadCandidatePool(), replayData(loaded, req)]),
  );
  const banners = replayBannerList(board, loaded, sync, req0.v, req);
  return (
    <BoardFrame
      board={board}
      ctx={ctx}
      req={req}
      banners={banners}
      pool={content.ok ? content.value[0] : null}
    >
      {content.ok ? (
        <ReplayBody data={content.value[1]} req={req} ctx={ctx} />
      ) : (
        /* 「打开当前版本」带着 result：回放在当前版本上接着做（配对版本已清理时走 B11 的回退） */
        <MirrorNotice notice={content.notice} req={req} />
      )}
    </BoardFrame>
  );
}

function NoticePage({ children }: { children: ReactNode }) {
  return (
    <BoardShell>
      <BoardTitle />
      {children}
    </BoardShell>
  );
}

/** 解析不出可读的版本：只有「同步与导入」能点，下面一条提示（「打开当前版本」同样不带 result） */
function mirrorlessPage(notice: MirrorNoticeKind, req0: PickRequest) {
  return (
    <NoticePage>
      <ImportsOnlyTabs />
      <MirrorNotice notice={notice} req={{ ...req0, result: "" }} />
    </NoticePage>
  );
}

/**
 * 回放：名单与条件先问 gateway（和 /sync 并发），再钉版本：结果配对的镜像版本优先于链接的 v（B11）。
 * gateway 说没登录（会话在两次请求之间过期）就去登录页，回来还是这份回放。
 */
async function replayRoute(
  req0: PickRequest,
  nextPath: string,
): Promise<ReactElement> {
  const [loaded, sync] = await Promise.all([
    loadReplay(req0.result),
    getPickSync(),
  ]);
  if (loaded.kind === "unauthenticated")
    redirect(buildLoginUrl(validateAuthNextPath(nextPath) ?? PICK_DATA_PATH));
  const v =
    loaded.kind === "ok" ? replayVersion(loaded.answer, req0.v) : req0.v;
  const resolved = await guarded(() => resolveBoard(v));
  if (!resolved.ok) return mirrorlessPage(resolved.notice, req0);
  if (resolved.value.state === "empty")
    return mirrorlessPage({ kind: "empty" }, req0);
  return replayBoardPage(resolved.value, req0, sync, loaded);
}

export default async function PickDataPage({
  searchParams,
}: {
  searchParams: SearchParams;
}): Promise<ReactElement> {
  const raw = await searchParams;
  const req0 = parsePickRequest(raw);
  const nextPath = pickDataNextPath(raw);
  const access = await requireBoardUser(nextPath);
  if (access.kind === "notice")
    return (
      <NoticePage>
        <AuthNotice reason={access.reason} />
      </NoticePage>
    );
  if (req0.tab === "imports")
    return (
      <NoticePage>
        <BoardTabs req={req0} />
        <ImportsView />
      </NoticePage>
    );
  if (req0.tab === "trends") return trendsRoute(req0, nextPath);
  if (isObsTab(req0.tab)) return obsRoute({ ...req0, tab: req0.tab });
  if (isReplay(req0)) return replayRoute(req0, nextPath);
  const [resolved, sync] = await Promise.all([
    guarded(() => resolveBoard(req0.v)),
    getPickSync(),
  ]);
  if (!resolved.ok) return mirrorlessPage(resolved.notice, req0);
  if (resolved.value.state === "empty")
    return mirrorlessPage({ kind: "empty" }, req0);
  return boardPage(resolved.value, req0, sync);
}
