# 2026-10-09 — Pick completion production release

The approved workflow is live at https://ggwork-deerflow.vercel.app: selection and evidence checks, personal selections, versioned planning, execution preview/export, and review/linking of existing published data. This release preserves native sources, protected resources, historical Radar and the existing controlled Trends scheduler.

## Deployment identities

| Component | Verified identity |
| --- | --- |
| Business PR | https://github.com/phananhson733-oss/ggwork-deerflow/pull/73 |
| Product merge | `2f12e68cfbbf9cb8b4186ff1428f72134a7afd9b` |
| Gateway | `f01793fc-c7cc-46c1-a051-359881412bc4` — SUCCESS |
| Vercel production | `dpl_HMhQt44Qe2PZbmYHxGr41EiqEsoV` — READY, exact staged build promoted |
| Canonical alias | `ggwork-deerflow.vercel.app`, reread against the promoted deployment |
| Trends compatibility image | `70ba9db3-21c5-42f4-8f86-5382a2373bb5` — SUCCESS, built from `954d0eb415b536a91b3da40102578b992e9a7e24` |
| Installed extension | `sha256:4bd745bba1c491ad58879acdf08a67735b12063c553fbc18c67333f7161d814e` |
| Private schema head | `0011` |

The Trends source differs from the product merge only by the preceding release record. Docker inputs are byte-identical. Its service settings and actual deployment manifest match the repository definition; collection settings and secret fingerprints were reread unchanged. The same installed package passed the observer-role compatibility self-check at head0011 inside Gateway. No collector was manually run and no new natural scheduled execution is claimed by this release.

## Verification

Final PR CI run [37883377707](https://github.com/phananhson733-oss/ggwork-deerflow/actions/runs/37883377707) passed on `21587cf7`, whose product content was merged unchanged:

- Complete SQLite/PostgreSQL extension suite:5,450 passed,29 expected skips,zero failures.
- Gateway entrypoint,JSON sanitizer and account CLI:108 passed.
- Real reader/common-query/rank integration:5 files,51 cases,zero skipped; parity harness6 tests plus12 subtests passed.
- Local Node24 frontend:3,112 passed,52 intentional gates; lint,typecheck and build passed. Explicit rollback5 and contract fixtures12 passed.
- Native source:79 passed,zero skipped; typecheck passed. Required backend rollback files:35 passed,zero skipped.
- Installed artifact:497 unique tracked source files match managed;204 runtime files match the physical non-editable installation. Installed-only persistence/export/owner-isolation/legacy-null smoke loaded98 package modules.

Initial CI exposed missing pnpm and password-free test reader fixtures masked by local trust authentication. Both were repaired and independently reviewed;81 fixture consumers then passed on an owned SCRAM cluster. These fixes changed tests/CI only. Earlier local Node26 and SQLite-lock failures remain in private logs; final remote full suite passed in one run.

Production checks confirmed health readiness, successful extension startup, actual package path/digest and exact source revision. Five installed query domains passed against production data in read-only transactions in0.526–1.508seconds. Seven new tables deny PUBLIC,anon,authenticated,board/query readers and observer. Existing15 selections and67 candidate sets remained,with native source and feedback enabled and mirror29 readable.

Authenticated formal-domain browser checks covered:

- New navigation and personal planning list.
- Existing personal selections, notes and the selection-to-plan prefilled form, without creating a production draft.
- An existing conversation and all20 frozen candidate cards,including unknown facts and snapshot notices.
- Native source/mirror29,shared published records and the historical Radar (6,716 records,2,816 with curves,3,900 without usable curves).
- The published-review source-authorization boundary:the QA session correctly displayed its missing source authorization;shared posted records remained readable.

## Data and configuration protection

Before migration,137 tables (2,715,642 rows),18 sequences and constraints were backed up and restored in an owned local PostgreSQL17 database. A production-clone0008→0011 rehearsal added seven private tables;all136 old tables except the migration-version table retained exact per-row COPY hashes in UTC. Schema ACL/owner archive and password-free role metadata are retained privately. This is database recovery scope;the existing Railway/data volume was preserved.

Gateway now uses a dedicated `ggwork_query_reader` login that inherits the existing `pick_board_reader` permission set. Membership has INHERIT=true,SET=false,ADMIN=false;the role is not privileged,uses default read-only transactions,8-second statements,15-second idle transactions,UTC,and a20-connection limit matching Supavisor. Verified TLS and real login/read/negative permission checks passed. Existing passwords and model configuration were not rotated. Railway reader URL/CA and native source revision were set narrowly and read back.

Frontend deployment used the guard's Git archive and only the verified `.vercel/project.json`;private environment/config files were not uploaded. The new production build was staged,checked for application authentication boundaries,and then promoted.

## Remaining acceptance boundaries

The original40 Agent attempts and seven approved follow-ups remain immutable (47 total). The seven follow-ups satisfy20 criteria;this release adds no model/provider calls and makes no claim of a new production model conversation. Native VoiceOver remains explicitly deferred/unverified. The existing Queyu authorization requirement and approved MoboReels retained-source exception remain visible with their original data dates.

Guard lines and deployment ordering are recorded in [progress.md](../progress.md). Full private backup,hash,review and execution receipts are retained outside Git;no credentials or production data are included in this document.
