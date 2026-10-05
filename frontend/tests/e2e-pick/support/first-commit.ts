/** A visible alert may predate the save; only the real completed capture releases this gate. */
export function firstCommitBarrier<T = unknown>() {
  let resolve!: (receipt: T) => void;
  let reject!: (error: Error) => void;
  const finished = new Promise<T>((accept, refuse) => {
    resolve = accept;
    reject = refuse;
  });
  // The route can fail before click() settles. Keep the eventual awaited rejection handled.
  void finished.catch(() => undefined);
  return {
    finished,
    async capture(operation: () => Promise<T>) {
      try {
        resolve(await operation());
      } catch {
        // Playwright transport errors can contain cookies in their call log.
        reject(new Error("First committed save response was not captured"));
      }
    },
  };
}
