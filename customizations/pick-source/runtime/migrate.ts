import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { getPool } from "../src/db";

export async function migrate(adoptRestored = false) {
  const pool = getPool(),
    c = await pool.connect();
  const ddl = readFileSync(
    new URL("../migrations/001-source.sql", import.meta.url),
    "utf8",
  );
  const digest = createHash("sha256").update(ddl).digest("hex");
  try {
    await c.query("BEGIN");
    await c.query("SELECT pg_advisory_xact_lock(73821,1)");
    await c.query("CREATE SCHEMA IF NOT EXISTS pick_source");
    await c.query("REVOKE ALL ON SCHEMA pick_source FROM PUBLIC");
    await c.query(
      "CREATE TABLE IF NOT EXISTS pick_source.ggwp_migrations (id text primary key, hash text not null, applied_at timestamptz not null default now())",
    );
    const old = (
      await c.query(
        "SELECT hash FROM pick_source.ggwp_migrations WHERE id='001-source'",
      )
    ).rows[0];
    if (old && old.hash !== digest)
      throw new Error("Source schema migration checksum mismatch");
    if (!old) {
      const exists = (
        await c.query(
          "SELECT to_regclass('pick_source.dramas') is not null AS present",
        )
      ).rows[0].present;
      if (exists && !adoptRestored)
        throw new Error(
          "Existing source tables require explicit backup adoption",
        );
      if (!exists) await c.query(ddl.replace("CREATE SCHEMA pick_source;", ""));
      // Restored backups must have the complete export/write schema before they can be adopted.
      const expected = JSON.parse(
        readFileSync(
          new URL("../migrations/columns.json", import.meta.url),
          "utf8",
        ),
      );
      const actual = (
        await c.query(
          "SELECT table_name,column_name,data_type,udt_name,is_nullable FROM information_schema.columns WHERE table_schema='pick_source' AND table_name=ANY($1) ORDER BY table_name,ordinal_position",
          [
            [
              ...new Set(
                expected.map((r: { table_name: string }) => r.table_name),
              ),
            ],
          ],
        )
      ).rows;
      if (JSON.stringify(actual) !== JSON.stringify(expected))
        throw new Error(
          "Restored source schema does not match the supported contract",
        );
      await c.query(
        "INSERT INTO pick_source.ggwp_migrations(id,hash) VALUES('001-source',$1)",
        [digest],
      );
    }
    const indexes = readFileSync(
      new URL("../migrations/002-read-indexes.sql", import.meta.url),
      "utf8",
    );
    const indexDigest = createHash("sha256").update(indexes).digest("hex");
    const indexMigration = (
      await c.query(
        "SELECT hash FROM pick_source.ggwp_migrations WHERE id='002-read-indexes'",
      )
    ).rows[0];
    if (indexMigration && indexMigration.hash !== indexDigest)
      throw new Error("Source read-index migration checksum mismatch");
    if (!indexMigration) {
      await c.query(indexes);
      await c.query(
        "INSERT INTO pick_source.ggwp_migrations(id,hash) VALUES('002-read-indexes',$1)",
        [indexDigest],
      );
    }
    await c.query(`CREATE TABLE IF NOT EXISTS pick_source.ggwp_source_jobs (
      name text primary key CHECK (name IN ('cps','catalog','queyu')),
      status text not null CHECK (status IN ('running','success','failed')),
      attempted_at timestamptz not null, completed_at timestamptz,
      last_success_at timestamptz, error_code text
    )`);
    await c.query(
      "ALTER TABLE pick_source.ggwp_source_jobs DROP CONSTRAINT IF EXISTS ggwp_source_jobs_name_check",
    );
    await c.query(
      "ALTER TABLE pick_source.ggwp_source_jobs ADD CONSTRAINT ggwp_source_jobs_name_check CHECK(name IN ('cps','catalog','queyu'))",
    );
    await c.query("REVOKE ALL ON ALL TABLES IN SCHEMA pick_source FROM PUBLIC");
    await c.query(
      "REVOKE ALL ON ALL SEQUENCES IN SCHEMA pick_source FROM PUBLIC",
    );
    await c.query("COMMIT");
  } catch (error) {
    await c.query("ROLLBACK");
    throw error;
  } finally {
    c.release();
  }
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  try {
    await migrate(process.argv.includes("--adopt-restored"));
    console.log("Source schema ready");
  } catch {
    console.error("Source schema migration refused or failed");
    process.exitCode = 1;
  } finally {
    await getPool().end();
  }
}
