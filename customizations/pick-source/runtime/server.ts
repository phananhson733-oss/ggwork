import { createServer } from "node:http";
import { loadFeedPage, feedContext } from "../src/lib/pick/feed";
import { loadExportPage, exportContext } from "../src/lib/pick/export-v2";
import { getPool } from "../src/db";
import { routeRequest } from "./http";
import { startSchedule } from "./schedule";
import { resource } from "./resources";
import { stopCollections } from "./refresh";

if (!process.env.PICK_FEED_TOKEN || !process.env.PICK_EXPORT_TOKEN)
  throw new Error("Source read tokens are required");
await getPool().query("SELECT 1 FROM pick_source.observe_sources LIMIT 1");
const stopSchedule =
  process.env.PICK_SOURCE_SCHEDULE_ENABLED === "1" ? startSchedule() : () => {};
async function collecting() {
  return (
    await getPool().query(
      "SELECT EXISTS(SELECT 1 FROM pick_source.ggwp_source_jobs WHERE status='running' AND attempted_at>now()-interval '31 minutes') OR EXISTS(SELECT 1 FROM pick_source.observe_sources WHERE source='catalog' AND status<>'success') AS busy",
    )
  ).rows[0].busy;
}
const server = createServer(async (req, res) => {
  try {
    const request = new Request(new URL(req.url ?? "/", "http://localhost"), {
      method: req.method,
      headers: new Headers(
        Object.entries(req.headers).flatMap(([k, v]) =>
          v
            ? [[k, Array.isArray(v) ? v.join(",") : v] as [string, string]]
            : [],
        ),
      ),
    });
    const result = await routeRequest(request, {
      feedToken: process.env.PICK_FEED_TOKEN,
      exportToken: process.env.PICK_EXPORT_TOKEN,
      resource,
      status: async () => ({
        enabled: true,
        jobs: (
          await getPool().query(
            "SELECT name,status,attempted_at,completed_at,last_success_at,error_code FROM pick_source.ggwp_source_jobs ORDER BY name",
          )
        ).rows,
      }),
      feed: async (q) =>
        (await collecting())
          ? { status: 503, error: "source_busy", retryAfter: 60 }
          : loadFeedPage(q, feedContext(new Date())),
      export: async (q) =>
        (await collecting())
          ? { status: 503, error: "source_busy", retryAfter: 60 }
          : loadExportPage(q, exportContext(new Date())),
    });
    res.writeHead(result.status, Object.fromEntries(result.headers));
    res.end(Buffer.from(await result.arrayBuffer()));
  } catch {
    res.writeHead(503, { "content-type": "application/json" });
    res.end('{"ok":false,"error":"read_failed"}');
  }
});
server.requestTimeout = 65_000;
server.headersTimeout = 10_000;
server.listen(
  Number(process.env.PICK_SOURCE_PORT ?? 8003),
  process.env.PICK_SOURCE_BIND ?? "127.0.0.1",
);
for (const event of ["SIGTERM", "SIGINT"] as const)
  process.on(event, () => {
    stopSchedule();
    stopCollections();
    server.close(() => {
      void getPool()
        .end()
        .then(() => process.exit(0));
    });
  });
