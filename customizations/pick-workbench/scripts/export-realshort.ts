// Operator-only read-only snapshot export. Never run catalog-import or catalog-refresh here.
import { createRequire } from "node:module";
import { writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";
const [sourceArg, outputArg] = process.argv.slice(2);
if (!sourceArg || !outputArg)
  throw new Error(
    "Usage: export-realshort.ts REALSHORT_CHECKOUT PRIVATE_OUTPUT_DIRECTORY",
  );
const source = resolve(sourceArg),
  output = resolve(outputArg);
mkdirSync(output, { recursive: true, mode: 0o700 });
const sourceFile = (path: string) => pathToFileURL(resolve(source, path)).href;
const require = createRequire(resolve(source, "package.json"));
require("dotenv").config({ path: resolve(source, ".env.local"), quiet: true });
async function main() {
  const { unionRows, toRowWithFlags, loadSignalsFor } = await import(
    sourceFile("src/lib/pick/queries.ts")
  );
  const { getDb } = await import(sourceFile("src/db/index.ts"));
  const { sql } = require("drizzle-orm");
  const { withSyncDeadline } = await import(
    sourceFile("src/lib/sync-deadline.ts")
  );
  const page = await withSyncDeadline(90000, async () => {
    console.log("reading candidate rows");
    const raw = await getDb().execute(
      sql`SELECT rows.* FROM ${unionRows()} WHERE rows.has_signal AND rows.off_on IS NULL ORDER BY rows.row_key LIMIT 10001`,
    );
    if (raw.rows.length > 10000) throw new Error("Snapshot too large");
    console.log("candidate rows read", raw.rows.length);
    const base = raw.rows.map(toRowWithFlags);
    const signals = new Map();
    for (let start = 0; start < base.length; start += 200) {
      for (const [k, v] of await loadSignalsFor(
        base.slice(start, start + 200).map((r) => r.rowKey),
      ))
        signals.set(k, v);
    }
    console.log("signals read", signals.size);
    return {
      rows: base.map((r) => ({ ...r, signals: signals.get(r.rowKey) ?? [] })),
      total: base.length,
      hasMore: false,
    };
  });
  if (page.hasMore || page.rows.length !== page.total)
    throw new Error("Incomplete snapshot");
  const { LANG_LOC } = await import(
    sourceFile("src/lib/pick/catalog-import.ts")
  );
  const { PLATFORM_RULES } = await import(
    sourceFile("src/lib/pick/platforms.ts")
  );
  const capturedAt = new Date().toISOString();
  const rows = page.rows.map((r) => {
    const ref =
      "https://dramashortstv.com/admin/pick?tab=row&row=" +
      encodeURIComponent(r.rowKey);
    const signals = r.signals.map((s) => ({
      kind: s.kind,
      source_ref: ref,
      observed_at: s.evidenceOn,
      value: JSON.stringify({ rank: s.rank, grade: s.grade, note: s.note }),
    }));
    for (const [kind, present] of Object.entries(r.rsFlags ?? {}))
      if (present)
        signals.push({
          kind,
          source_ref: ref,
          observed_at: null,
          value:
            "RealShort候选条件命中；仅表示该类依据存在，不表示指标数值或统计周期",
        });
    return {
      source: "realshort-pick",
      source_id: Buffer.from(r.rowKey).toString("base64url"),
      language: LANG_LOC[r.lang] ?? (r.lang || "und"),
      title: r.title,
      theater: PLATFORM_RULES[r.platform].name,
      tags: [],
      listed_at: r.listedOn,
      availability: r.offOn ? "delisted" : "unknown",
      signals,
      channel_rules: {
        youtube: PLATFORM_RULES[r.platform].yt === "no" ? "denied" : "unknown",
      },
      detail_url: ref,
    };
  });
  writeFileSync(
    resolve(output, "realshort-catalog.json"),
    JSON.stringify(rows),
    { mode: 0o600 },
  );
  writeFileSync(
    resolve(output, "realshort-source.json"),
    JSON.stringify(
      {
        sourceRevision: execFileSync(
          "git",
          ["-C", source, "rev-parse", "HEAD"],
          { encoding: "utf8" },
        ).trim(),
        capturedAt,
        scope: "RealShort选剧候选池：有来源信号且未标注下架；不是全部剧库",
        total: page.total,
        freshness:
          "See source-specific evidence dates; no global freshness guarantee",
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  writeFileSync(
    resolve(output, "realshort-rules.md"),
    "# RealShort 选剧快照\n\n采集时间：" +
      capturedAt +
      "\n\n范围：有来源信号且未标注下架的选剧候选池，不是全部剧库。当前为人工同步快照，不是实时查询。个人已选仅指本工作台保存的记录，不代表团队已发布或已排期。渠道规则有附加条件，不能仅据候选收录就认定允许发布。\n\n" +
      Object.values(PLATFORM_RULES)
        .map(
          (r) =>
            "## " +
            r.name +
            "\n来源：" +
            r.doc +
            "\n规则核对日期：" +
            r.updated +
            "\nYouTube：" +
            r.yt +
            "；" +
            r.ytNote +
            "\n报备：" +
            r.report +
            "\n必带标签：" +
            r.tag,
        )
        .join("\n\n"),
    { mode: 0o600 },
  );
  console.log(
    JSON.stringify({
      total: rows.length,
      english: rows.filter((r) => r.language === "en").length,
      bytes: Buffer.byteLength(JSON.stringify(rows)),
      capturedAt,
    }),
  );
}
main().catch((e) => {
  console.error("Source probe failed:", e.name);
  process.exitCode = 1;
});
