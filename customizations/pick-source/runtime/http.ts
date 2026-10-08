import {
  handleExportGet,
  handleFeedGet,
  type FeedRouteDeps,
  type ExportRouteDeps,
} from "../src/lib/pick/feed-http";
import { feedAuthorized } from "../src/lib/pick/feed-map";
export type HttpDeps = {
  feedToken?: string;
  exportToken?: string;
  status?: () => Promise<unknown>;
  resource?: (rowKey: string) => Promise<unknown>;
  feed: FeedRouteDeps["load"];
  export: ExportRouteDeps["load"];
};
/** Only the two authenticated, read-only contracts consumed by the existing mirror. */
export async function routeRequest(
  request: Request,
  deps: HttpDeps,
): Promise<Response> {
  const path = new URL(request.url).pathname;
  if (path === "/health" && request.method === "GET")
    return Response.json({ ok: true });
  if (path === "/status" && request.method === "GET") {
    if (
      !deps.exportToken ||
      !feedAuthorized(request.headers.get("authorization"), deps.exportToken)
    )
      return new Response(null, { status: 401 });
    return Response.json(
      (await deps.status?.()) ?? { enabled: true, jobs: [] },
      { headers: { "cache-control": "no-store" } },
    );
  }
  if (path === "/resource" && request.method === "GET") {
    if (
      !deps.exportToken ||
      !feedAuthorized(request.headers.get("authorization"), deps.exportToken)
    )
      return new Response(null, { status: 401 });
    const row = new URL(request.url).searchParams.get("row");
    if (!row) return new Response(null, { status: 400 });
    const value = await deps.resource?.(row);
    return Response.json(value ?? { error: "not_found" }, {
      status: value ? 200 : 404,
      headers: { "cache-control": "no-store" },
    });
  }
  const v1 = path === "/api/pick-feed";
  const match = /^\/api\/pick-feed\/v2\/([a-z_]+)$/.exec(path);
  if (!v1 && !match) return new Response(null, { status: 404 });
  if (request.method !== "GET")
    return new Response(null, { status: 405, headers: { allow: "GET" } });
  const now = new Date();
  return v1
    ? handleFeedGet(request, { token: deps.feedToken, now, load: deps.feed })
    : handleExportGet(request, match![1]!, {
        token: deps.exportToken,
        now,
        load: deps.export,
      });
}
