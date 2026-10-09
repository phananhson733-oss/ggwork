# Native selection source acceptance — 2026-10-08

## Production identity

- Repository revision: `3a82b95e9d3dac7c9d42560d617ae4d3f473447b`; incorporates migration PR #65, CPS recovery #67, export performance #68, resource routing #70, and the concurrent five-title Trends changes.
- Gateway: `417ed4b2-ca27-4f2d-9072-8459a6020f4a`. A clean Git archive containing exactly the Dockerfile source inputs was used after two full-context uploads timed out before building; neither failed upload replaced the running deployment.
- Frontend: `dpl_B2SHfDEqUUoVZmaa8L1AnykHYsNc`, promoted to `https://ggwork-deerflow.vercel.app`.
- Gateway migration head remains `0008`; private native-source migrations `001-source` and additive `002-read-indexes` are applied.
- `PICK_SOURCE_REVISION` matches the deployed revision; source health returns 200, and an anonymous request to the numeric `rs_clicks14` route returns 401.

## Live acceptance

Mirror v27 was captured at **14:43 UTC** and published at **14:48 UTC** with outcome **paired**. `v1_text`, `row_counts`, `forbidden_columns`, `mirror_text`, `references`, `control_totals`, `v1_consistency` and `empty_tables` all passed. Drift and scrub-hit counts were zero; the consecutive mirror-failure counter reset to zero. The final live manifest took 30.605 seconds, below the 60-second HTTP deadline.

The canonical frontend was inspected after promotion. It shows v27 and the new source dates. Fifty v25 rows retained the exact drama, evidence and language cells captured before cutover. The configured owner successfully opened the current-resource page; private URLs and access codes were not copied into release evidence. Unauthenticated resource-page access redirects to login. Screenshots and data-validation receipts remain private.

The original RealShort domain still returned HTTP 503. Its public website, deleted database, site indexing and disabled website schedules were not reactivated.

## Data and scheduling boundaries

The original backup was restored independently before selected business tables were copied into the private source schema. Original table counts/content checksums were verified. Subsequent collection preserved the approved retained theater's rows/signals and historical observation dates. Existing workbench versions, selections and the concurrent Trends implementation were preserved.

The cloud Feishu catalog collector completed an automatic scheduled catch-up at 13:10 UTC. After bounded transient-read recovery, a complete cloud CPS collection succeeded at 13:34 UTC. This is evidence of that complete collection, not yet of its next natural scheduled occurrence. CPS collection is configured every six hours, catalog/Queyu at 02:45 UTC, and mirror publication retains its existing 03:40/15:40 UTC slots. Subsequent natural scheduled occurrences were not observed during this release.

- **MoboReels:** its source remains inaccessible; retain existing records and original dates as explicitly approved by the owner.
- **Queyu:** its browser state is still on the owner's Mac mini and has not been connected. The visible pending-authentication receipt and original ranking dates are intentional.

## Validation

- Native source: 79 tests passed with PostgreSQL; typecheck passed.
- Latest-main pick extension: 5,041 passed, 25 dialect-specific skips; all 35 release-contract cases passed with zero skips.
- Frontend: 3,006 passed, 45 intentional skips; lint/typecheck passed.
- Independent correctness, Python, security and PostgreSQL reviews completed without remaining blockers for the released changes.

Runtime credentials, production data, database backups and browser state were never included in Git archives or repository commits. The owner explicitly authorized publication of the migrated source code.
