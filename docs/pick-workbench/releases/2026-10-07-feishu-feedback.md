# Feishu operational feedback release — PR #44

Product commit: `3b55cefed66cf8436a69e74d09f6600c45ad26d7` ([PR #44](https://github.com/phananhson733-oss/ggwork/pull/44)). Released on 2026-10-07 America/Chicago (2026-10-08 UTC). The merged tree equals the verified PR head `b9628c84`.

## Publication scope

The repository remains public. Full internal inventory and live-source reports, raw exports, account information and business aggregates remain in local restricted storage. Public documents contain engineering contracts and verification summaries only. No Feishu fields or records were changed.

## CI and local gates

- Final PR CI [37723715291](https://github.com/phananhson733-oss/ggwork/actions/runs/37723715291) succeeded: business suite 4,871 passed / 29 skipped; host entrypoint/sanitizer/create-user suite 108 passed; PostgreSQL frontend reader integration 45 passed / zero skips.
- The earlier CI failure exposed a test assumption that an owned receipt lookup finishes within 50 ms. Runtime behavior was correct. The test now deterministically covers pending without an ID, drains actual cancelled waiters, and still detects orphaned polling. An in-memory cancellation-omission mutation failed all eight combinations. No product timeout was widened and no test was skipped to obtain a pass.
- Local feedback plus managed-copy regression: 260 passed on SQLite/PostgreSQL. Frontend full suite: 2,998 passed / 45 skipped; check/typecheck/build passed. Compatibility matrix: backend 35 passed / zero skips; frontend old/new/mixed/stored cases and contract fixtures passed.
- Synthetic-source browser acceptance passed 3/3 before release. This is distinct from authenticated production feedback acceptance.

## Deployment evidence

| Component | Deployment | Verified result |
|---|---|---|
| Railway gateway | `3a4d03b1-3365-4da6-8ebd-eb30a98e2304` | SUCCESS; health ready; database/checkpointer OK; extension 1/1 loaded; feedback routes mounted |
| Vercel frontend | `dpl_3nVo9xu1MGzxbXW5VwyFXMMgKChY` | READY; promoted to [production](https://ggwork-deerflow.vercel.app); provider metadata records the product commit |
| Existing Trends cron compatibility image | `8a166c73-2e4b-409a-9a4a-aba455c47851` | SUCCESS; service and deployment settings match the existing cron configuration |

Gateway live verification confirmed migration `0008`, all 164 business Python files matching the released source, and six private feedback tables with no SELECT grants to the checked public/reader roles. Package digest: `sha256:684ec6066951fa86ee1061ea50f0920c8c6dc0ba310780dde6cf1354e6b3df10`.

The frontend was built with `NEXT_PUBLIC_APP_VERSION=20261007-3b55cef`. The canonical domain was read back to the new deployment after promotion. Browser verification reached the login page with zero console errors; navigation returned 200 and loadEventEnd was 1.68 seconds; HTTP login returned 200 with noindex/nofollow, while unauthenticated feedback and historical-notes reads returned 401. The browser was not authenticated, so the About screen, signed-in cards and live feedback interaction were not production-browser verified in this release.

## Operational boundary

- `PICK_FEEDBACK_ENABLED` and the feedback schedule remain off; no owner credentials were copied and no production Feishu scan was triggered.
- Collector verification in this release is limited to guarded image publication and unchanged deployment settings, not an executed collector selfcheck/preflight or real collection.
- Existing collector stop markers were present before this release. The compatibility image preserves configuration and stop state; this is not collector recovery, a new qualification night, or evidence of successful Google collection. No stop reset or collection-parameter change was made.
- Identity-mapping design and daily-series scope remain deferred. Deployment does not imply those business capabilities are complete.
- Preserve the private feedback migration and historical evidence on rollback; do not downgrade or delete production tables.

## Guard records

- `pick-deploy-guard target=gateway commit=3b55cefed66cf8436a69e74d09f6600c45ad26d7 prod_head=0007 chain_head=0008 at=2026-10-08T04:02:56Z`
- `pick-deploy-guard target=frontend commit=3b55cefed66cf8436a69e74d09f6600c45ad26d7 at=2026-10-08T04:02:41Z`
- `pick-deploy-guard target=cron:trends commit=3b55cefed66cf8436a69e74d09f6600c45ad26d7 prod_head=0008 chain_head=0008 at=2026-10-08T04:06:23Z`
