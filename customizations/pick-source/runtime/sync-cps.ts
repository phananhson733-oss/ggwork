import { runSync } from "../src/lib/sync";
import { getPool } from "../src/db";
try {
  await runSync({ detailBudget: 150, timeBudgetMs: 25 * 60_000 });
  const rows = (
    await getPool().query(
      "SELECT source,status FROM pick_source.observe_sources WHERE source IN ('catalog','snapshot','bill')",
    )
  ).rows;
  if (rows.length !== 3 || rows.some((r) => r.status !== "success"))
    throw new Error("CPS source incomplete");
  console.log("CPS collection verified");
} catch {
  console.error("CPS collection incomplete; see source receipts");
  process.exitCode = 1;
} finally {
  await getPool().end();
}
