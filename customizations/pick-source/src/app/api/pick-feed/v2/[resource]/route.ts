import { exportContext, loadExportPage } from "@/lib/pick/export-v2";
import { handleExportGet } from "@/lib/pick/feed-http";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

/**
 * 选剧工作台镜像导出（feed v2，`pick-export-v2`）：manifest 与九个行资源，keyset 分页，每页先读后核 fingerprint。
 * 方案见 ggwork-deerflow 的 docs/plans/2026-09-23-supabase-pick-board-plan.md 第 4 节（第 13、14 节优先）。
 *
 * PICK_EXPORT_TOKEN 未配置时整条路由 404（惰性：v2 没人调用时不查库）。不与 v1 的 PICK_FEED_TOKEN 共用，
 * 更不复用 CRON_SECRET / SYNC_SECRET——那两把能触发写库，这把只能读。
 * 这里只接线：鉴权、参数、状态码与响应头在纯模块 feed-http.ts（tests/pick-feed.test.ts 跑整张状态码矩阵），
 * 查库、清洗与核对在 export-v2.ts。
 */
export async function GET(request: Request, context: { params: Promise<{ resource: string }> }) {
  const { resource } = await context.params;
  const now = new Date();
  return handleExportGet(request, resource, {
    token: process.env.PICK_EXPORT_TOKEN,
    now,
    load: (query) => loadExportPage(query, exportContext(now)),
  });
}
