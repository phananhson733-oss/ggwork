# Confirmed feedback identity mapping release

Product PR: [#47](https://github.com/phananhson733-oss/ggwork-deerflow/pull/47). Product commit: `638f3ae18a44f2d9496234a689c380d9a95fc0b2`; the merged tree equals the reviewed and tested PR head `454d9ce2a117962a5cacc98c5421056cfeae8771`.

## Behavior

New feedback scans include the external-ID registry alongside the existing business tables in an immutable sixteen-table v2 snapshot. Catalog binding requires an explicit confirmed master identity; drama-level CPS attribution requires explicit grain and a unique compatible confirmed registry/direct relation. Unknown-scope, pending, inactive, overlapping and conflicting declarations cannot fall back to title/legacy SD links or bypass rejection through a direct link.

v2 preserves raw external-ID strings throughout native parsing and database reconstruction. v1 serialization, content hashes and historical candidate sidecars remain compatible. HTTP replies and strict candidate items are unchanged. No migration is added; the database remains at `0008`.

The Feishu schema additions, native links and four display-only formulas were read back, with all previous field definitions unchanged. Initial candidate population remains pending because the legacy SD binding is name-derived and the external keys do not yet establish a verified scope/target. Source rows and write receipts are private and are not published here. See the [operator guide](../feedback-identity-mapping.md).

## Validation

- Independent specification, Python, code/security and incremental browser-fixture reviews approved.
- Complete local business suite: 4974 passed, 25 skipped, SQLite and PostgreSQL; focused feedback suite: 356 passed on both databases.
- PR CI: 4970 passed, 29 skipped; host integration: 108 passed; authenticated PostgreSQL board reader: 45 passed. The additional CI skips are native CLI tests that ran locally.
- Frontend: 2999 passed, 45 skipped; check and production build passed. Ordinary 10/20-item projection guard and Ruff passed.
- Official extension manager updated the managed copy; all 431 source-package files matched.
- Two private v1 snapshots retained identical JSON and hashes against the pre-upgrade implementation.
- Native local user-CLI stable double-scan verified all sixteen tables, v1 schema transition, local publication, pending resume, owner isolation and the actual Agent analysis tool. Source quality remains partial. This is distinct from production owner authorization.
- Authenticated synthetic-provider browser acceptance passed once without retries; screenshots were visually reviewed. It verifies mapping changes, excluded income, unmatched new candidates and unchanged historical playback/income evidence. It is not a live-model or signed-in production-browser claim.

## Deployment

| Component | Deployment | Verification |
|---|---|---|
| Railway gateway | `028fbed0-b462-4fd6-979b-9e51825636e4` | SUCCESS; health ready; database and checkpointer OK |
| Vercel frontend | `dpl_9t6yRoHLEXTVWPG9qCUKr9UYkzhb` | READY; promoted to the canonical domain; provider metadata matches the product commit and `20261008-638f3ae` |

All 165 business Python files in the running gateway match the released source. Package digest: `sha256:d8ea4f6784bc57fc979ae6e39bf1f579eb19c3f82a3101adadfeafa64c7a04e2`. Runtime source registry has sixteen tables and transform `feedback-v2`; migration remains `0008`, the six private feedback tables remain inaccessible to checked public/reader roles, and both feedback flags remain off.

The [canonical website](https://ggwork-deerflow.vercel.app) was read back to the new deployment after promotion. An existing authenticated browser session successfully opened the data-import page, displayed feedback as disabled and produced no console errors. No production feedback refresh or model request was made. This verifies the disabled production UI, not an enabled production feedback workflow. Separate unauthenticated HTTP checks verified the login page returns 200 and feedback/history endpoints return 401.

The production data page also displayed an existing RealShort catalog-feed HTTP 503 condition, including failures predating this release. That separate upstream synchronization issue is outside the mapping-only change; the release does not claim all catalog sources are fresh or healthy.

## Guard records

- `pick-deploy-guard target=gateway commit=638f3ae18a44f2d9496234a689c380d9a95fc0b2 prod_head=0008 chain_head=0008 at=2026-10-08T09:04:15Z`
- `pick-deploy-guard target=frontend commit=638f3ae18a44f2d9496234a689c380d9a95fc0b2 at=2026-10-08T09:04:18Z`

## Operational boundary

Feedback and hourly scheduling remain disabled. No owner credentials were copied and no production Feishu scan was triggered. Existing collector services, schedules and stop markers are unchanged; this mapping-only release does not require a cron compatibility image or claim collector recovery. Preserve private snapshots and historical evidence on rollback.
