import { spawn } from "node:child_process";
/** Rejection means the writer's process group has stopped, not merely that cancellation was sent. */
export async function command(
  cmd: string,
  args: string[],
  cwd: string,
  env: NodeJS.ProcessEnv,
  signal: AbortSignal,
) {
  signal.throwIfAborted();
  await new Promise<void>((resolve, reject) => {
    const child = spawn(cmd, args, {
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
      detached: true,
    });
    let output = "";
    let closed = false,
      code: number | null = null,
      aborted = false,
      reaped = false,
      spawnFailed = false,
      timer: NodeJS.Timeout | undefined;
    const kill = (sig: NodeJS.Signals) => {
      if (child.pid) {
        try {
          process.kill(-child.pid, sig);
        } catch (e) {
          if ((e as NodeJS.ErrnoException).code !== "ESRCH") throw e;
        }
      }
    };
    const finish = () => {
      if (!closed || (aborted && !reaped)) return;
      signal.removeEventListener("abort", abort);
      if (timer) clearTimeout(timer);
      if (!aborted && !spawnFailed && code === 0) resolve();
      else
        reject(
          Object.assign(new Error("collector_step_failed"), {
            output,
            command: cmd,
          }),
        );
    };
    const abort = () => {
      if (aborted) return;
      aborted = true;
      kill("SIGTERM");
      timer = setTimeout(() => {
        kill("SIGKILL");
        reaped = true;
        finish();
      }, 2000);
    };
    for (const stream of [child.stdout, child.stderr])
      stream!.on("data", (d) => {
        output = (output + d.toString()).slice(-16000);
      });
    child.on("error", () => {
      spawnFailed = true;
    });
    child.on("close", (c) => {
      closed = true;
      code = c;
      finish();
    });
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}
