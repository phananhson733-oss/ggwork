import assert from "node:assert/strict";
import test from "node:test";
import { sql } from "drizzle-orm";
import { getDb, getPool, atomic } from "../src/db";
import { writeCatalogAtomic } from "../runtime/catalog-write";
import { routeRequest } from "../runtime/http";
import { loadFeedPage, feedContext } from "../src/lib/pick/feed";
import { loadExportPage, exportContext } from "../src/lib/pick/export-v2";
import { notBot } from "../src/lib/observe/queries";
import { BOT_UA_PATTERNS } from "../src/lib/observe/bot-ua";
const enabled = Boolean(process.env.PICK_SOURCE_TEST_DATABASE_URL);
test(
  "click classification preserves every bot pattern, nulls and real Cubot browsers",
  { skip: !enabled },
  async () => {
    const humans = [
      null,
      "",
      "Mozilla/5.0 Cubot Android Chrome/130",
      "Mozilla/5.0 Safari/605",
    ];
    const bots = BOT_UA_PATTERNS.map(
      (p) => `Mozilla ${p.replaceAll("%", "").toUpperCase()} end`,
    );
    const samples = [...humans, ...bots];
    const values = samples.map((ua, i) => sql`(${i}::int, ${ua}::text)`);
    const r = await getDb().execute<{ n: number; human: boolean }>(sql`
    SELECT n, (${notBot()}) AS human FROM (VALUES ${sql.join(values, sql`, `)}) AS outbound_clicks(n,user_agent) ORDER BY n`);
    assert.deepEqual(
      r.rows.map((row) => row.human),
      samples.map((_, i) => i < humans.length),
    );
  },
);
if (enabled) {
  const u = new URL(process.env.PICK_SOURCE_TEST_DATABASE_URL!);
  if (!["localhost", "127.0.0.1"].includes(u.hostname))
    throw new Error("Tests require disposable local PostgreSQL");
  process.env.PICK_SOURCE_DATABASE_URL = u.toString();
}
test.before(async () => {
  if (!enabled) return;
  const { migrate } = await import("../runtime/migrate");
  await migrate();
  await getDb().execute(
    sql`INSERT INTO catalog_rows(row_key,platform,source_table,title,lang,has_signal) VALUES('fixture-synthetic','kalos','synthetic fixture','Synthetic Drama','英语',true) ON CONFLICT(row_key) DO NOTHING`,
  );
  await getDb().execute(
    sql`INSERT INTO catalog_signals(row_key,kind,ord,evidence_on,rank) VALUES('fixture-synthetic','kd',0,'2026-10-07',1) ON CONFLICT DO NOTHING`,
  );
});
test(
  "read indexes are valid and their migration is idempotent",
  { skip: !enabled },
  async () => {
    const { migrate } = await import("../runtime/migrate");
    await migrate();
    const indexes = await getPool().query(
      "SELECT indexrelid::regclass::text AS name,indisvalid FROM pg_index WHERE indexrelid IN ('pick_source.dramas_group_locale_cover_idx'::regclass,'pick_source.observations_verified_day_idx'::regclass,'pick_source.clicks_human_created_drama_idx'::regclass)",
    );
    assert.equal(indexes.rows.length, 3);
    assert.ok(indexes.rows.every((r) => r.indisvalid));
  },
);
test(
  "PG adapter rolls back all source tables and preserves the failed receipt",
  { skip: !enabled },
  async () => {
    await getDb().execute(
      sql`create table if not exists ggwp_atomic_fixture (key text primary key, value text)`,
    );
    await getDb().execute(sql`truncate ggwp_atomic_fixture`);
    await getDb().execute(
      sql`insert into ggwp_atomic_fixture values ('row','old'),('signal','old'),('receipt','old')`,
    );
    await assert.rejects(
      writeCatalogAtomic({
        begin: async () => {
          await getDb().execute(
            sql`update ggwp_atomic_fixture set value='running' where key='receipt'`,
          );
          return "attempt";
        },
        writeRows: async () => {
          await getDb().batch([
            getDb().execute(
              sql`update ggwp_atomic_fixture set value='new' where key='row'`,
            ),
          ]);
        },
        writeSignals: async () => {
          await getDb().execute(
            sql`update ggwp_atomic_fixture set value='new' where key='signal'`,
          );
        },
        writePosted: async () => {
          throw new Error("injected missing page");
        },
        verify: async () => ({}),
        complete: async () => {
          throw new Error("must not publish");
        },
        fail: async () => {
          await getDb().execute(
            sql`update ggwp_atomic_fixture set value='failed' where key='receipt'`,
          );
        },
        log: () => {},
      }),
    );
    assert.deepEqual(
      (
        await getDb().execute(
          sql`select key,value from ggwp_atomic_fixture order by key`,
        )
      ).rows,
      [
        { key: "receipt", value: "failed" },
        { key: "row", value: "old" },
        { key: "signal", value: "old" },
      ],
    );
    await getDb().execute(sql`drop table ggwp_atomic_fixture`);
  },
);
test(
  "PG batch is atomic outside a surrounding transaction",
  { skip: !enabled },
  async () => {
    await getDb().execute(
      sql`create table ggwp_batch_fixture (n integer primary key)`,
    );
    try {
      await assert.rejects(
        getDb().batch([
          getDb().execute(sql`insert into ggwp_batch_fixture values (1)`),
          getDb().execute(sql`insert into ggwp_batch_fixture values (1)`),
        ]),
      );
      assert.equal(
        (
          await getDb().execute(
            sql`select count(*)::int as n from ggwp_batch_fixture`,
          )
        ).rows[0]!.n,
        0,
      );
    } finally {
      await getDb().execute(sql`drop table ggwp_batch_fixture`);
    }
  },
);
test(
  "PG snapshot preserves date strings and authenticates both feed versions",
  { skip: !enabled },
  async () => {
    const d = await getDb().execute(sql`select date '2026-10-07' as day`);
    assert.equal(d.rows[0]!.day, "2026-10-07");
    const deps = {
      feedToken: "fixture1",
      exportToken: "fixture2",
      feed: (q: any) => loadFeedPage(q, feedContext(new Date())),
      export: (q: any) => loadExportPage(q, exportContext(new Date())),
    };
    const r = await routeRequest(
      new Request("http://localhost/api/pick-feed?limit=1", {
        headers: { authorization: "Bearer fixture1" },
      }),
      deps,
    );
    assert.equal(r.status, 200);
    const body = await r.json();
    assert.equal(body.ok, true);
    assert.equal(body.rows.length, 1);
    const v = await routeRequest(
      new Request(
        "http://localhost/api/pick-feed/v2/manifest?as_of=" +
          encodeURIComponent(
            new Date(
              Math.floor(Date.now() / 60000) * 60000 - 120000,
            ).toISOString(),
          ),
        { headers: { authorization: "Bearer fixture2" } },
      ),
      deps,
    );
    assert.equal(v.status, 200);
    const manifest = await v.json();
    assert.equal(manifest.version, "pick-export-v2");
    assert.ok(manifest.fingerprint);
    assert.ok(manifest.rows[0].meta);
  },
);
test.after(async () => {
  if (enabled) await getPool().end();
});

test(
  "MoboReels retention preserves its exact rows, signals and imported timestamp during a real catalog import",
  { skip: !enabled },
  async () => {
    const { mkdtemp, writeFile, rm } = await import("node:fs/promises");
    const { tmpdir } = await import("node:os");
    const { join } = await import("node:path");
    const { spawn } = await import("node:child_process");
    await getDb().execute(
      sql`INSERT INTO catalog_rows(row_key,platform,title,lang,has_signal,imported_at) VALUES('mobo-retained','moboreels','Retained synthetic drama','英语',true,'2026-10-01T00:00:00Z') ON CONFLICT DO NOTHING`,
    );
    await getDb().execute(
      sql`INSERT INTO catalog_signals(row_key,kind,ord,grade,note) VALUES('mobo-retained','mg',0,'A','Original retained note') ON CONFLICT DO NOTHING`,
    );
    const before = await getDb().execute(
      sql`SELECT row_to_json(r) AS value FROM catalog_rows r WHERE row_key='mobo-retained'`,
    );
    const sigBefore = await getDb().execute(
      sql`SELECT row_to_json(r) AS value FROM catalog_signals r WHERE row_key='mobo-retained'`,
    );
    const dir = await mkdtemp(join(tmpdir(), "ggwork-native-import-"));
    try {
      await writeFile(
        join(dir, "catalog.json"),
        JSON.stringify({
          built: new Date().toISOString(),
          rows: [
            {
              k: "fixture-synthetic",
              p: "kalos",
              src: "fixture",
              t: "Fresh synthetic drama",
              lang: "英语",
              sig: [{ s: "kd", r: 1, d: "2026-10-08" }],
            },
          ],
        }),
      );
      await writeFile(
        join(dir, "posted.json"),
        JSON.stringify({ dramas: [], accounts: [] }),
      );
      const code = await new Promise<number | null>((resolve, reject) => {
        const child = spawn(
          process.execPath,
          [
            "--conditions=react-server",
            "--import",
            "tsx",
            "scripts/import-catalog.ts",
            "--dir",
            dir,
          ],
          {
            cwd: process.cwd(),
            env: { ...process.env, PICK_SOURCE_RETAIN_MOBOREELS: "1" },
            stdio: "ignore",
          },
        );
        child.on("error", reject);
        child.on("close", resolve);
      });
      assert.equal(code, 0);
      assert.deepEqual(
        (
          await getDb().execute(
            sql`SELECT row_to_json(r) AS value FROM catalog_rows r WHERE row_key='mobo-retained'`,
          )
        ).rows,
        before.rows,
      );
      assert.deepEqual(
        (
          await getDb().execute(
            sql`SELECT row_to_json(r) AS value FROM catalog_signals r WHERE row_key='mobo-retained'`,
          )
        ).rows,
        sigBefore.rows,
      );
      assert.equal(
        (
          await getDb().execute(
            sql`SELECT title FROM catalog_rows WHERE row_key='fixture-synthetic'`,
          )
        ).rows[0]!.title,
        "Fresh synthetic drama",
      );
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  },
);
