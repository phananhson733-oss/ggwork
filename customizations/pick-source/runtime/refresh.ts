import { command } from "./command";
import {
  chmod,
  mkdir,
  mkdtemp,
  readFile,
  writeFile,
  cp,
  rm,
} from "node:fs/promises";
import { resolve, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { getPool } from "../src/db";
import { runLocked } from "./jobs";
import { rankHistory, type SavedRank } from "./queyu-history";
const shutdown = new AbortController();
export function stopCollections() {
  shutdown.abort();
}
const ROOT = fileURLToPath(new URL("..", import.meta.url));
const HOME = process.env.PICK_SOURCE_DATA_DIR ?? "/data/pick-source";
export type Job = "cps" | "catalog" | "queyu";

async function catalog(signal: AbortSignal) {
  if (
    !process.env.PICK_SOURCE_OWNER_ID &&
    process.env.PICK_SOURCE_LOCAL_LARK !== "1"
  )
    throw new Error("source_owner_required");
  await mkdir(HOME, { recursive: true, mode: 0o700 });
  const stage = await mkdtemp(join(HOME, "catalog-"));
  await chmod(stage, 0o700);
  const env = { ...process.env, PICK_SOURCE_WORKDIR: stage };
  try {
    for (const name of ["fetch.sh", "fetch_source.py"])
      await cp(join(ROOT, "scripts/juyuantai", name), join(stage, name));
    await cp(join(HOME, "queyu"), join(stage, "queyu"), {
      recursive: true,
    }).catch((e: NodeJS.ErrnoException) => {
      if (e.code !== "ENOENT") throw e;
    });
    await command("bash", ["fetch.sh"], stage, env, signal);
    await command(
      "python3",
      [join(ROOT, "scripts/juyuantai/build.py")],
      stage,
      env,
      signal,
    );
    await command(
      "python3",
      [join(ROOT, "scripts/juyuantai/posted.py")],
      stage,
      env,
      signal,
    );
    await command(
      process.execPath,
      [
        "--conditions=react-server",
        "--import",
        "tsx",
        "scripts/import-catalog.ts",
        "--dir",
        stage,
      ],
      ROOT,
      env,
      signal,
    );
  } finally {
    await rm(stage, { recursive: true, force: true });
  }
}

export async function seedRankingHistory() {
  const rows = (
    await getPool().query<SavedRank>(
      "SELECT s.kind,r.platform,r.title,r.lang,s.evidence_on,s.rank,s.payload FROM pick_source.catalog_signals s JOIN pick_source.catalog_rows r USING(row_key) WHERE s.kind IN ('qc','qr')",
    )
  ).rows;
  const dir = join(HOME, "queyu");
  await mkdir(dir, { recursive: true, mode: 0o700 });
  for (const day of rankHistory(rows)) {
    try {
      await writeFile(
        join(dir, "rank-" + day.date + ".json"),
        JSON.stringify(day),
        { flag: "wx", mode: 0o600 },
      );
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    }
  }
}
async function queyu(signal: AbortSignal) {
  if (!process.env.QUEYU_STATE_FILE) throw new Error("queyu_auth_required");
  await command(
    process.execPath,
    [
      "--conditions=react-server",
      "--import",
      "tsx",
      "scripts/juyuantai/queyu.ts",
    ],
    ROOT,
    { ...process.env, PICK_SOURCE_WORKDIR: HOME },
    signal,
  );
  const receipt = JSON.parse(
    await readFile(join(HOME, "queyu", "last-run.json"), "utf8"),
  );
  if (receipt.libraryProblem) throw new Error("queyu_library_incomplete");
}

export async function refresh(job: Job): Promise<boolean> {
  const c = await getPool().connect();
  try {
    return await runLocked(
      {
        acquire: async () =>
          (await c.query("SELECT pg_try_advisory_lock(73821,2) AS held"))
            .rows[0].held,
        release: async () => {
          await c.query("SELECT pg_advisory_unlock(73821,2)");
        },
      },
      async () => {
        await c.query(
          "INSERT INTO pick_source.ggwp_source_jobs(name,status,attempted_at) VALUES($1,'running',now()) ON CONFLICT(name) DO UPDATE SET status='running',attempted_at=now(),completed_at=NULL,error_code=NULL",
          [job],
        );
        try {
          const signal = AbortSignal.any([
            shutdown.signal,
            AbortSignal.timeout(30 * 60_000),
          ]);
          if (job === "catalog") {
            await seedRankingHistory();
            await catalog(signal);
          } else if (job === "queyu") await queyu(signal);
          else
            await command(
              process.execPath,
              [
                "--conditions=react-server",
                "--import",
                "tsx",
                "runtime/sync-cps.ts",
              ],
              ROOT,
              process.env,
              signal,
            );
          await c.query(
            "UPDATE pick_source.ggwp_source_jobs SET status='success',completed_at=now(),last_success_at=now(),error_code=$2 WHERE name=$1",
            [
              job,
              job === "catalog" &&
              process.env.PICK_SOURCE_RETAIN_MOBOREELS === "1"
                ? "moboreels_retained"
                : null,
            ],
          );
        } catch (error) {
          if (error && typeof error === "object" && "output" in error) {
            await mkdir(HOME, { recursive: true, mode: 0o700 });
            await writeFile(
              join(HOME, "last-failure.log"),
              String(error.output),
              { mode: 0o600 },
            ).catch(() => {});
          }
          const known = [
            "source_owner_required",
            "queyu_auth_required",
            "queyu_library_incomplete",
            "collector_process_failed",
            "collector_step_failed",
          ];
          const code =
            error &&
            typeof error === "object" &&
            "output" in error &&
            String(error.output).includes("source_access_denied")
              ? "source_access_denied"
              : error instanceof Error && known.includes(error.message)
                ? error.message
                : "collection_failed";
          await c.query(
            "UPDATE pick_source.ggwp_source_jobs SET status='failed',completed_at=now(),error_code=$2 WHERE name=$1",
            [job, code],
          );
          throw new Error(code);
        }
      },
    );
  } finally {
    c.release();
  }
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  try {
    const job = process.argv[2];
    if (job !== "cps" && job !== "catalog" && job !== "queyu")
      throw new Error("Unknown job");
    console.log(
      (await refresh(job))
        ? "Source collection completed"
        : "Source collection already running",
    );
  } catch (error) {
    console.error(
      error instanceof Error ? error.message : "Source collection failed",
    );
    process.exitCode = 1;
  } finally {
    await getPool().end();
  }
}
