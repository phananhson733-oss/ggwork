# feedback-v3 source contract and candidate-card reason — production release

Feedback was re-enabled after this release; see the [re-enabling record](2026-10-10-feedback-reactivation.md). The text
below is the state at release time.

The user asked for this deployment on 2026-10-10. It releases the reviewed feedback-v3 source contract to the gateway
and the candidate card's failure reason to the frontend. It does **not** re-enable feedback: both feedback flags stay
`0`, as set by the [suspension](2026-10-10-feedback-suspension.md), and no feedback-v3 version has been published.

## Deployment identities

| Component | Verified identity |
| --- | --- |
| Business PR | https://github.com/phananhson733-oss/ggwork-deerflow/pull/76 |
| Product merge | `74c48c8e38770eabc764211b2ef4bf26d08f2967`, tree identical to the tested head `e85f8aa6` |
| Gateway | `732d59a1-91a2-4b35-9b34-612c73ae766b` — SUCCESS |
| Vercel production | `dpl_AjwspRbjQMXm9qENwhYRQdJN9ceV` — READY, exact staged build promoted |
| Canonical alias | `ggwork-deerflow.vercel.app`, reread against the promoted deployment |
| Installed extension | `sha256:f33e193a76ffe00fa2081275825689f77b8b31942559546f670f298d550e2e68`, equal to the digest of the merged managed snapshot |
| Private schema head | `0011`, unchanged; this release has no migration |

Against the previous gateway source the image changes seven files of the extension's `feedback` package and nothing
else that ships. `customizations/pick-source`, the Dockerfile and the Railway definition are unchanged.
`PICK_SOURCE_REVISION` was set to the product merge without triggering a separate deployment. The Trends cron was not
redeployed because the migration head did not move.

## What changed for users

- A candidate query that produces no candidates because operating feedback could not be refreshed now shows the reason
  on the conversation card instead of one generic failure line.
- The gateway can read the reorganised source Base: thirteen tables, revenue from the manual CPS table and the
  publication records' RS column only. Nothing reads it yet, because feedback is still switched off.

## Verification

Before merge, on the tested head:

- PR CI run [38025442610](https://github.com/phananhson733-oss/ggwork-deerflow/actions/runs/38025442610) passed both
  jobs (`pick-workbench-tests`, `pick-board-integration`).
- Complete extension suite on SQLite and a throwaway PostgreSQL 17, run alone: 5,478 passed, 25 skipped, zero failures.
  Required rollback files on both databases: 35 passed, zero skipped.
- Frontend: 3,119 passed; lint and typecheck clean; explicit rollback 5 and contract fixtures 12 passed.
- Synthetic-provider browser acceptance against a local gateway and frontend, repointed at the v3 scan: 3/3 passed.
- Source, managed snapshot and installed package identical after the extension manager refresh.

Against the real source Base, read-only, under the configured owner through the production isolated Lark runner:

- Field binding of the published feedback-v2 baseline with the v3 contract: all thirteen scanned tables bind, exactly
  the four reviewed columns are dropped, and the strict second pass matches. Run once before merge with the changed
  files overlaid on the installed package in a temporary directory, and once after deployment with the installed
  package itself.
- One complete double scan with the v3 contract before merge: both scans matched, all thirteen tables complete,
  publication revalidation reproduced the same content hash, normalisation yielded only the `cps_manual` and `post_rs`
  revenue lanes, and the default analysis returned a non-empty result. Source quality is **partial**, as it was under
  feedback-v2. The scan ran in memory in a separate process; nothing was published or written.

After deployment:

- Gateway logs show the extension loaded and application startup complete, with no traceback or failed service start;
  the native source's loopback health check returned 200; `/health/ready` returns 200.
- Inside the container: the installed digest above, `feedback-v3` with thirteen scan tables, feedback disabled and
  unscheduled with the owner still configured, migration head `0011`, no feedback run since the suspension, the current
  stored version still feedback-v2 with sixteen tables, no lease held.
- The staged frontend build redirected the workspace to login and returned 401 for unauthenticated pick routes before
  promotion; the canonical alias behaves the same after promotion.

## Not verified by this release

- No authenticated browser check was made on the production site: no existing conversation, saved selection or new
  candidate query was opened after the release. The rollback-matrix manual cells are therefore still open.
- The new card text has not been seen in production, since no query is currently refused.
- No feedback-v3 version exists. The first real refresh after re-enabling, and any later hourly occurrence, must be
  observed separately. The source Base may change again before then; an unreviewed change will be refused as before.
- The external-ID mapping table is still read and validated although, without the automatic CPS lane, no revenue row
  is attributed through it. Removing it from the source would be another reviewed contract change.

## Rollback

Old code cannot read a feedback-v3 version. While no v3 version exists, the previous gateway revision can be restored
through a revert on `main` and the deployment guard. Once a v3 version has been published, switch feedback off before
rolling the gateway back; stored versions are never deleted.

Guard lines are recorded in [progress.md](../progress.md). Raw rows, source counts, financial values, owner
identifiers, credential material and feedback version or run identifiers remain outside this record.
