import type { Metadata } from "next";
import type { ReactElement, ReactNode } from "react";

import { Sources } from "@/components/workspace/pick-board/sources";
import { TAB_LABELS } from "@/components/workspace/pick-board/toolbar";
import { bannersFor } from "@/components/workspace/pick-board/views/banner-rules";
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
import { TabView } from "@/components/workspace/pick-board/views/tab-view";
import type { PickSyncStatus } from "@/core/pick/sync-schema";
import {
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
  loadPickRows,
  loadPostedList,
  loadPostedRecord,
  loadPostedStats,
  loadRankMeta,
  loadRankRows,
  loadReelshortDetail,
  loadRowDetail,
  loadRsRank,
  MirrorBusy,
  MirrorMisconfigured,
  MirrorUnavailable,
  MirrorVersionGone,
  pickDataNextPath,
  type ReadyBoard,
  requireBoardUser,
  resolveBoard,
  setBoardScope,
  sourcesOf,
} from "@/server/pick-board";

/**
 * 选剧资料：RealShort 选剧台的七个 tab，读工作台库里 P2 写的镜像版本（pickm_vN），一个请求钉一个版本。
 *
 * 顺序（合成稿 P3-5）：先鉴权，再决定读哪个版本、同时问 gateway 的 /sync，钉住版本（setBoardScope）之后才取数。
 * 取数都在这里：组件对 @/server/pick-board 只能 import type（契约测试），views/ 下的视图都是同步的。
 * 镜像读不了（没配、没版本、授权缺失、忙、版本刚被清理）是提示，不进 error.tsx；别的错误照常抛给 error.tsx。
 * 回放（tab=pick&result=…）属于 P4-2：这里忽略 result，照常出列表。
 */

export const dynamic = "force-dynamic";
export const maxDuration = 60;

type SearchParams = Promise<Record<string, string | string[] | undefined>>;
type Board = ReadyBoard<BoardRules>;

// searchParams is optional: Nextra's docs page map calls generateMetadata({})
// on every app page at build time (nextra/dist/server/page-map/index.js).
export async function generateMetadata({
  searchParams,
}: {
  searchParams?: SearchParams;
}): Promise<Metadata> {
  const req = parsePickRequest((await searchParams) ?? {});
  return { title: `选剧资料 · ${TAB_LABELS[req.tab]}` };
}

/** 镜像读不了的几种：返回提示；别的错误不认，交回调用方抛出 */
function mirrorNoticeOf(error: unknown): MirrorNoticeKind | null {
  if (error instanceof MirrorUnavailable) return { kind: "unavailable" };
  if (error instanceof MirrorMisconfigured)
    return { kind: "misconfigured", reason: error.reason };
  if (error instanceof MirrorBusy) return { kind: "busy" };
  if (error instanceof MirrorVersionGone) return { kind: "gone" };
  return null;
}

async function guarded<T>(
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

/**
 * 版本已钉住：页头、横幅、tab、这一 tab 的内容、页底。先 setBoardScope，再取数，取完才出 JSX
 * （返回的是同步的元素树：异步的只有这一层）。
 */
async function boardPage(
  board: Board,
  req0: PickRequest,
  sync: GatewayResult<PickSyncStatus>,
): Promise<ReactElement> {
  setBoardScope(board.scope);
  const req: PickRequest = { ...req0, v: board.scope.versionId, result: "" };
  const ctx = contextOf(board);
  const loaded = await guarded(() =>
    Promise.all([loadCandidatePool(), loadTab(req)]),
  );
  const fresh = ctx.freshness;
  const banners = bannersFor({ board, sync, req, now: new Date() });
  return (
    <BoardShell>
      <BoardHeader ctx={ctx} pool={loaded.ok ? loaded.value[0] : null} />
      <Banners banners={banners} />
      <BoardTabs
        req={req}
        counts={{
          pick: fresh.withSignal + fresh.rsCandidates,
          all: fresh.rows + fresh.rsCanonical,
          posted: fresh.posted,
        }}
      />
      {loaded.ok ? (
        <TabView data={loaded.value[1]} req={req} ctx={ctx} />
      ) : (
        <MirrorNotice notice={loaded.notice} req={req} />
      )}
      <Sources
        fresh={fresh}
        sources={sourcesOf(board.sources)}
        asOf={ctx.asOf}
      />
      <DifferencesNote />
    </BoardShell>
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

export default async function PickDataPage({
  searchParams,
}: {
  searchParams: SearchParams;
}): Promise<ReactElement> {
  const raw = await searchParams;
  const req0 = parsePickRequest(raw);
  const access = await requireBoardUser(pickDataNextPath(raw));
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
  const [resolved, sync] = await Promise.all([
    guarded(() => resolveBoard(req0.v)),
    getPickSync(),
  ]);
  if (!resolved.ok) return mirrorlessPage(resolved.notice, req0);
  if (resolved.value.state === "empty")
    return mirrorlessPage({ kind: "empty" }, req0);
  return boardPage(resolved.value, req0, sync);
}
