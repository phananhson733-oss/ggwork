# Native selection data source

## Ownership and data flow

The pick extension can start `customizations/pick-source` as a loopback-only Node process. It uses PostgreSQL schema `pick_source` for source data; existing `pick_mirror`, `pickm_v*`, candidate, selection and replay records keep their contracts. The same mirror writer publishes paired candidate/knowledge batches through its existing gates. With `PICK_SOURCE_ENABLED=1`, `SyncSettings` selects `http://127.0.0.1:8003` instead of the retired RealShort URL.

Feishu exports run through the configured owner's existing isolated Lark runner. Only the registered sheets/tables in `catalog_sources.py` can be exported. CLI artifacts are bounded regular files: 32 MiB for catalog exports, while operational feedback retains its 8 MiB limit. Source files and collector diagnostics live in the private volume; they are never committed or sent to the model.

The raw source retains private resource and billing fields. `/api/pick/resources` admits only the configured source owner, forwards to the loopback service, and disables caching. Shared mirror scrubbing remains intact. The protected `/workspace/pick-resources` page replaces retired website resource links; it explicitly reads current resources, not historical candidate facts. Owner-private `ggwp_feedback_*` data is unchanged and is not published as a shared catalog.

## Operator configuration

| Variable | Meaning |
| --- | --- |
| `PICK_SOURCE_ENABLED=1` | Start native source and select its loopback feed; absent disables it |
| `PICK_SOURCE_DATABASE_URL` | Session-stable source PostgreSQL connection (Supabase port 5432), without a schema override |
| `PICK_SOURCE_REVISION` | Exact deployed Git commit; included in feed fingerprints |
| `PICK_SOURCE_CA_PEM` | Trusted database CA; certificate and hostname verification remain enabled |
| `PICK_SOURCE_OWNER_ID` | Existing authenticated user's ID; default/shared identities are rejected |
| `PICK_SOURCE_DATA_DIR` | Private persistent working data, default `/data/pick-source` |
| `PICK_SOURCE_SCHEDULE_ENABLED=1` | Enable durable-receipt scheduled collection |
| `PICK_SOURCE_RETAIN_MOBOREELS=1` | Explicitly retain its inaccessible source, preserving original rows/dates |
| `QUEYU_STATE_FILE` | Operator-provided browser state for Queyu; no state means a visible authentication requirement |
| `CPS_ACCOUNT`, `CPS_PASSWORD` | Existing source account, stored only as runtime secrets |
| `CPS_BASE_URL`, `CPS_APP`, `CPS_DETAIL_BOOK_TYPE` | Optional existing provider configuration; blank values use provider defaults |

The v1/v2 read tokens reuse `PICK_REALSHORT_FEED_TOKEN` and `PICK_REALSHORT_EXPORT_TOKEN` inside the process. `PICK_SOURCE_ROOT` is an operator override for local development only. Native children receive only source, Lark, TLS and runtime environment entries, not model-provider secrets. Never set `PICK_SOURCE_LOCAL_LARK=1` in production: it exists for explicit local operator validation.

## Restore and cutover

1. Read the current deployment identities and active sync/source jobs. Do not start a second restore or collection while one is running.
2. Preserve and validate existing workbench versions, candidate/selection records and source backup. Verify every archive or logical-backup checksum and row count. Keep backup and restore receipts outside Git in a mode-700 directory.
3. Restore the original database into a separate local database first. Select source business tables; do not restore old authentication, public-site usage reports, indexing queues or playback/rate-limit records into the running workbench.
4. Create the new private `pick_source` schema without altering existing schemas. Restore `dramas`, `legacy_urls`, `catalog_rows`, `catalog_signals`, `catalog_posted`, `catalog_accounts`, `cps_bill_daily`, `drama_observations`, `observe_sources`, `outbound_clicks` and `sync_runs`. Preserve original timestamps, identities and historical gaps. The empty `chapters` table exists for schema compatibility; native collection does not repopulate playback chapters.
5. Compare table counts and content checksums, primary/unique keys, constraints, and sequence positions. Verify that PUBLIC and client roles have no access to the source schema. Then run `pnpm migrate --adopt-restored`; it refuses schema column/type/nullability drift.
6. Validate source v1/v2 contracts, CA-verified DB access, collection, and mirror gates before enabling native mode. A failed/in-progress CPS catalog blocks even unpinned v1 reads, preventing fallback publication of a partial list.
7. Use the repository deployment guard and a clean archive. After deployment, confirm source health, source-specific receipts, a complete paired mirror publication, default browsing, old-version replay, and private-resource owner checks. A configured schedule is not proof of a natural scheduled occurrence; record that occurrence separately.

## Source freshness

CPS metrics are collected before a new daily observation can be created. Restoring an old row must never stamp its old values as today's observation. Existing daily observations and missing historical dates stay unchanged. The public site's clicks and search measurements keep their original dates; no traffic is fabricated after website retirement.

MoboReels is the named retained-source exception approved on 2026-10-08. Its rows and signal records are not rewritten during another theater's import. The source receipt carries `moboreels_retained` and the workbench displays that warning. Other missing sources never silently become empty tables. Queyu ranking files reconstructed from retained signal histories explicitly identify their backup provenance and keep observed dates; missing login and incomplete library collection are visible independently.

## Publication after collection

Collection writes `pick_source`; the workbench page and the agent read published versions, which only a sync run produces. Besides the two daily slots (03:40 / 15:40 UTC), the gateway asks the loopback `/status` every five minutes (`ggwork_pick/schedule.py`, `run_collection_watch`). When a job's `last_success_at` is later than the latest sync run's `started_at` and no job is running, it starts one run with trigger `collect`. The source process never calls the gateway and still holds no gateway credential.

- One run per finished collection. A run of any status that started after the collection counts, so a failed or degraded run is not repeated for the same collection; the slots remain the retry.
- A job still `running` postpones the run: the source would answer `source_busy`, and the next job's rows would need a second run.
- With the current cadence this adds about five runs a day (four CPS collections, one catalog import). Each publishes a mirror version, so the retention rules in `mirror/retention.py` turn over faster: the latest three versions always stay, and versions kept only because a recent candidate snapshot names them give way sooner once more than ten are held.
- A manifest request that gets no response is asked once more after 90 seconds before the run falls back to v1 (see [mirror-runbook.md](mirror-runbook.md), section 4).

## Recovery

- A failed catalog transaction preserves all previous content. Fix the reported source, then run the corresponding collector once; do not bypass shrink/completeness checks.
- If native service code must be rolled back, disable native collection first and preserve `pick_source` and the mirror versions. Restore the previous application revision according to the deployment guard. Disabling native mode alone restores the old feed URL, which still points to the intentionally paused website, so it does **not** resume collection.
- Never resume the RealShort Vercel project, reconnect its removed database, or change the Mac mini's existing scheduled runner as an incidental rollback.
- The original shutdown backup remains the recovery source for excluded website data. This migration does not restart those website features.
