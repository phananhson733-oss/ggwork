export type SourceReceipt = { status: string; completedAt: string | null };
export function jobDue(
  last: SourceReceipt | null,
  now: Date,
  hours: number,
): boolean {
  if (!last || last.status !== "success" || !last.completedAt) return true;
  const at = Date.parse(last.completedAt);
  return !Number.isFinite(at) || now.getTime() - at >= hours * 3_600_000;
}
export async function runLocked(
  lock: { acquire: () => Promise<boolean>; release: () => Promise<void> },
  work: () => Promise<void>,
) {
  if (!(await lock.acquire())) return false;
  try {
    await work();
    return true;
  } finally {
    await lock.release();
  }
}
