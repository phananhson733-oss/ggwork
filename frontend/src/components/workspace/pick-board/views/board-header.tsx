// PORTED_FROM: realshort@816ca2e src/app/admin/(protected)/pick/page.tsx
// 本地改动：Header 改名「选剧资料」，新鲜度全部取自版本（versions.freshness、latest_snapshot、series_state），
// 加上镜像版本号与采集时间、曲线截至哪天（min(latest_snapshot, series_state.through)）、「不是实时数据」和智能体候选池 N。
import { formatObservedAt } from "@/core/pick-board/metrics";
import { QUEYU_INDEX } from "@/core/pick-board/queyu";

import { ExternalLink } from "../links";

import type { BoardContext } from "./board-data";

const TITLE = "text-[20px] font-bold tracking-[-.01em]";

export function BoardTitle() {
  return (
    <div className="mb-3.5">
      <h1 className={TITLE}>选剧资料</h1>
    </div>
  );
}

/** 曲线画到哪天：版本的最新快照日与折叠后的 series 截至日取早的一个；都没有时 null */
export function curveThrough(
  latestSnapshot: string | null,
  through: string | null,
): string | null {
  if (latestSnapshot === null) return through;
  if (through === null) return latestSnapshot;
  return latestSnapshot < through ? latestSnapshot : through;
}

function freshnessLine(ctx: BoardContext): string {
  const f = ctx.freshness;
  const imported = f.importedAt
    ? `剧单导入于 ${formatObservedAt(f.importedAt)}`
    : "还没有导入过剧单";
  const curve = curveThrough(
    ctx.latestSnapshot,
    ctx.seriesState?.through ?? null,
  );
  return [
    imported,
    `${f.rows.toLocaleString("en-US")} 行`,
    `ReelShort 指标采集 ${formatObservedAt(f.rsSyncedAt)}`,
    `镜像 v${ctx.versionId} 采集于 ${formatObservedAt(ctx.asOf)}`,
    `曲线截至 ${curve ?? "日期未知"}`,
  ].join(" · ");
}

export function BoardHeader({
  ctx,
  pool,
}: {
  ctx: BoardContext;
  /** 智能体候选池：has_signal 且未下架的行数；读不到时 null */
  pool: number | null;
}) {
  return (
    <div data-testid="board-header" className="mb-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className={TITLE}>选剧资料</h1>
        <p className="text-helper text-[12px]">
          {freshnessLine(ctx)} ·{" "}
          <ExternalLink
            href={QUEYU_INDEX}
            rel="nofollow sponsored noopener"
            className="text-brand hover:underline"
          >
            鹊娱剧库 ↗
          </ExternalLink>
        </p>
      </div>
      <p className="text-helper mt-1 text-[12px]">
        截至 {formatObservedAt(ctx.asOf)}，不是实时数据 · 智能体候选池{" "}
        {pool === null ? "—" : pool.toLocaleString("en-US")} 部（不等于 tab
        徽标：徽标含已下架行）
      </p>
    </div>
  );
}
