import { atomic } from "../src/db";
import type { CatalogWriteSteps } from "../src/lib/pick/catalog-import";
/** The running/failed receipt survives rollback; all content and success commit together. */
export async function writeCatalogAtomic<A>(
  steps: CatalogWriteSteps<A>,
): Promise<void> {
  const attempt = await steps.begin();
  try {
    await atomic(async () => {
      await steps.writeRows(attempt);
      await steps.writeSignals(attempt);
      await steps.writePosted(attempt);
      await steps.complete(attempt, await steps.verify());
    });
  } catch (error) {
    try {
      await steps.fail(attempt);
    } catch {
      steps.log("Could not record catalog failure");
    }
    throw error;
  }
}
