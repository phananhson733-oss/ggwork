import { feedContext, loadFeedPage } from "@/lib/pick/feed";
import { handleFeedGet } from "@/lib/pick/feed-http";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

/**
 * 选剧工作台（ggwork-deerflow）的只读数据接口。PICK_FEED_TOKEN 未配置时整条路由 404；
 * 不复用 CRON_SECRET / SYNC_SECRET——那两把能触发写库，这把只能读候选池。
 *
 * 2026-09-23 起（feed v2 的 P1-4，方案 4.7）只增不改：可选的 as_of 钉住时点，可选的 fp 让每一页都在读完之后核对
 * fingerprint（不同 409、有来源在写 503 带 Retry-After）；响应顶层多一个每页都有的 fingerprint。
 * 这里只接线：鉴权、参数、状态码在纯模块 feed-http.ts，查库与核对在 lib/pick/feed.ts。
 */
export async function GET(request: Request) {
  const now = new Date();
  return handleFeedGet(request, {
    token: process.env.PICK_FEED_TOKEN,
    now,
    load: (query) => loadFeedPage(query, feedContext(now)),
  });
}
