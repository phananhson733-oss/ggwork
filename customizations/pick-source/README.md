# GGWork selection data source

This package owns collection and read-only export for GGWork after the RealShort public website is retired. It runs as an operator-enabled child of the existing pick extension, listening on loopback port 8003. No public website, playback routes, indexing submission or website cron is started.

The business queries and normalization were imported from the RealShort revision in `UPSTREAM.json`. That file records original file hashes and explicit adaptations. The PostgreSQL adapter uses CA-verified TLS and a transaction-local `pick_source` search path. Catalog content and its successful receipt commit together; a failed import preserves the previous complete catalog.

Commands (run in this directory):

```sh
pnpm install --frozen-lockfile --ignore-scripts
pnpm typecheck
pnpm test
# Optional integration tests: only a disposable local PostgreSQL URL is accepted.
PICK_SOURCE_TEST_DATABASE_URL=postgresql://localhost/pick_source_test pnpm test
pnpm migrate
# After an explicitly verified restore of the supported source schema:
pnpm migrate --adopt-restored
pnpm start
pnpm refresh:cps
pnpm refresh:catalog
```

`PICK_SOURCE_DATABASE_URL` and `PICK_SOURCE_CA_PEM` are required for remote PostgreSQL. Never put them in arguments or Git. Native collection settings, data restoration, scheduled execution and rollback are documented in [the operating guide](../../docs/pick-workbench/native-source.md).

The HTTP surface is restricted to the authenticated v1/v2 feed contracts, safe collection status, and resources read through the Gateway's configured-owner authorization. Raw financial records and resource URLs remain outside the shared mirror. Resource pages use the current source while historical candidate evidence keeps its original version.

CPS collection runs every six hours; daily catalog and Queyu slots are 02:45 UTC. Successful Queyu retries make catalog import due again. Authentication/permission failures back off for a day; other failed occurrences retry after 30 minutes. One PostgreSQL advisory lock coordinates collectors. Cancellation waits for child groups to stop before releasing the lock, and the supervisor cleans up tagged descendants before restarting.

`PICK_SOURCE_RETAIN_MOBOREELS=1` is the explicitly approved exception for its inaccessible sheet. It preserves existing MoboReels rows, signals and imported timestamps without attempting that sheet. It cannot substitute an empty source. All other source-read failures still stop catalog publication. The UI and feed freshness metadata disclose the retained source.

A missing Queyu browser state is a visible `queyu_auth_required` receipt. Retained ranking histories retain their original dates; they are not reported as newly collected. The separate Queyu receipt also distinguishes a library failure after successful rank collection.
