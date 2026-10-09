import { getPool } from "../src/db";
import { refresh, type Job } from "./refresh";

/** Latest scheduled occurrence, UTC. Collection receipts survive process restarts. */
export function latestSlot(job: Job, now: Date): Date {
  const d = new Date(now);
  d.setUTCSeconds(0, 0);
  if (job === "cps") {
    d.setUTCHours(Math.floor(d.getUTCHours() / 6) * 6, 0, 0, 0);
    return d;
  }
  d.setUTCHours(2, 45, 0, 0);
  if (d > now) d.setUTCDate(d.getUTCDate() - 1);
  return d;
}
export async function tick(now = new Date()) {
  for (const job of ["cps", "queyu", "catalog"] as const) {
    const last = (
      await getPool().query(
        "SELECT status,attempted_at,last_success_at,error_code FROM pick_source.ggwp_source_jobs WHERE name=$1",
        [job],
      )
    ).rows[0];
    const ranking =
      job === "catalog"
        ? (
            await getPool().query(
              "SELECT attempted_at,status FROM pick_source.ggwp_source_jobs WHERE name='queyu'",
            )
          ).rows[0]
        : null;
    const ranksChanged =
      ranking &&
      ranking.status !== "running" &&
      (!last?.last_success_at ||
        new Date(ranking.attempted_at) > new Date(last.last_success_at));
    if (
      last?.last_success_at &&
      new Date(last.last_success_at) >= latestSlot(job, now) &&
      !ranksChanged
    )
      continue;
    const backoff =
      last?.error_code === "source_access_denied" ||
      last?.error_code === "queyu_auth_required"
        ? 24 * 60 * 60_000
        : 30 * 60_000;
    if (
      last?.status !== "success" &&
      last?.attempted_at &&
      now.getTime() - new Date(last.attempted_at).getTime() < backoff
    )
      continue;
    try {
      await refresh(job);
    } catch {
      console.error("[pick-source] collection failed", job);
    }
  }
}
export function startSchedule() {
  let stopped = false;
  let timer: NodeJS.Timeout | undefined;
  const run = async () => {
    try {
      await tick();
    } catch {
      console.error("[pick-source] scheduler unavailable");
    } finally {
      if (!stopped)
        timer = setTimeout(() => {
          void run();
        }, 60_000);
    }
  };
  timer = setTimeout(() => {
    void run();
  }, 60_000);
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}
