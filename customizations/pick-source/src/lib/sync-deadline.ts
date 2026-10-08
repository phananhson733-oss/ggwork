import { AsyncLocalStorage } from "node:async_hooks";

// A signal belongs to one sync invocation, never to the whole server instance.
const scope = new AsyncLocalStorage<AbortSignal>();

export function getSyncSignal(): AbortSignal | undefined {
  return scope.getStore();
}

export function checkSyncDeadline(): void {
  getSyncSignal()?.throwIfAborted();
}

/** Cancel I/O and await its unwind; racing only the outer promise leaves writes running. */
export async function withSyncDeadline<T>(
  budgetMs: number | undefined,
  work: () => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  const parent = getSyncSignal();
  const signal = parent
    ? AbortSignal.any([parent, controller.signal])
    : controller.signal;
  const timer = budgetMs === undefined ? undefined : setTimeout(() => {
    controller.abort(new Error(`同步主动超时（预算 ${budgetMs / 1000}s）`));
  }, budgetMs);
  try {
    return await scope.run(signal, async () => {
      try {
        signal.throwIfAborted();
        const result = await work();
        signal.throwIfAborted();
        return result;
      } catch (error) {
        if (signal.aborted) throw signal.reason;
        throw error;
      }
    });
  } finally {
    clearTimeout(timer);
    controller.abort(new Error("同步任务已结束"));
  }
}
