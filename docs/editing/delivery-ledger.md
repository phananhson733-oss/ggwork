# Editing delivery ledger

Initial record: 2026-10-08. This ledger separates source, automated checks, browser/native acceptance and deployment. Update entries with an exact revision, command or artifact and observed result; a pending entry is not a pass.

## Source ledger

| Item | Recorded evidence |
| --- | --- |
| Business base | `b17623966ef81e68c202a40a009306f49ffb03c9`; business main record at implementation start |
| Repository | `phananhson733-oss/ggwork-deerflow`; local business remote `ggwork`; upstream `origin` excluded |
| Integration branch | `codex/clip-workbench-integration` |
| Parent specification | [#53](https://github.com/phananhson733-oss/ggwork-deerflow/issues/53), exact published body in [spec.md](spec.md) |
| Source mount review | [integration.md](integration.md): shared PickNav, Trends early branch, no frontend extension registry at base |
| T1 branch | `codex/clip-t1-foundation`; starts at the business base with clean worktree |

## Task delivery

| Task | Implementation evidence | Acceptance / closure |
| --- | --- | --- |
| [T1 / #54](https://github.com/phananhson733-oss/ggwork-deerflow/issues/54) | Foundation docs and source mount decision recorded; published spec byte equality, 16 relative links and whitespace check passed | Source review recorded; live deployment reconciliation pending; issue remains open |
| [T2 / #55](https://github.com/phananhson733-oss/ggwork-deerflow/issues/55) | Pending; implementation owner records tested revision and checks | Pending; issue remains open |
| [T3 / #56](https://github.com/phananhson733-oss/ggwork-deerflow/issues/56) | Pending; implementation owner records tested revision and checks | Pending; issue remains open |
| [T4 / #57](https://github.com/phananhson733-oss/ggwork-deerflow/issues/57) | Pending; implementation owner records tested revision and checks | Pending; issue remains open |
| [T5 / #58](https://github.com/phananhson733-oss/ggwork-deerflow/issues/58) | Pending; implementation owner records tested revision and checks | Pending; issue remains open |
| [T6 / #59](https://github.com/phananhson733-oss/ggwork-deerflow/issues/59) | Pending; implementation owner records tested revision and checks | Pending; issue remains open |
| [T7 / #60](https://github.com/phananhson733-oss/ggwork-deerflow/issues/60) | Pending; implementation owner records tested revision and checks | Pending; issue remains open |
| [T8 / #61](https://github.com/phananhson733-oss/ggwork-deerflow/issues/61) | Acceptance worktree based on `ca765aef`; official manager installed editing and refreshed pick snapshots; image copies both editing Skills; repeatable package/live checks in [acceptance runbook](acceptance.md) | SQLite + disposable PostgreSQL + real native suite: 129 passed; strict blocking-I/O: 149 passed; legacy filter/replay/save checks: 130 passed. Live cloud planning is blocked by configured provider DNS; browser/media completion remains open. |

## Acceptance and release gates

| Evidence layer | Required evidence | Current record |
| --- | --- | --- |
| Document integrity | Published spec byte equality, relative-link targets, scoped diff and whitespace check | Passed at T1 document revision: byte equality, 16 relative links and git diff --check; no application code changed |
| Shared task API | Owner/auth isolation, idempotency, frozen inputs, stop/publication fencing, targeted retry and persistence | Not run by T1 |
| Frontend and chat | Shared DTOs and task identity, real tool admission, owner caches, stable deep links and failure states | Not run by T1 |
| Real browser | Authenticated backend, both entry points, pick/Trends regression; 375/768/1440, keyboard and 200% zoom | Not run by T1 |
| Apple Silicon | Device auth/preparation, authorized source receipt, transcription, planning, rendering, real stop and file access | Not run by T1 |
| Media and model quality | Decoding, audio/size/duration, immutable originals/results, transcript/time references and measured narrative quality | Not run by T1; actual model configuration and thresholds remain required |
| Distribution and limits | Validated installation/update/signing conditions and measured operational bounds | Pending |
| Vercel deployment | Source SHA → deployment ID → production alias → authenticated route evidence | Not inspected or deployed by T1 |
| Railway deployment | Source SHA → deployment/service identity → configured extension and runtime evidence | Not inspected or deployed by T1 |
| Integration PR | One business-repository PR with exact test revision and explicit passed/pending gates | Not created by T1 |

A GitHub push or merged PR is not a production release: this project uses CLI deployments. Do not mark native, quality or deployment acceptance complete from unit/API tests, simulated status or prior deployment notes. Preserve outstanding gates and open issues when the integrated implementation cannot yet supply the required evidence.

## T8 isolated acceptance record

Recorded 2026-10-08 against integrated source `ca765aef` plus T8 packaging changes.
The package regression test first failed on the stale pick gate snapshot and
missing container Skills; both passed after official manager regeneration and
image updates. A fresh locked backend environment imported both extension
packages. Native development wheels were rebuilt as a matched pair and installed
in a separate environment; real Apple Silicon doctor reported ready using the
pinned synthetic model (`921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f`).

An isolated TLS Gateway, new QA account, SQLite app home and real native CLI
exercised host login/session Cookie, CSRF, device pairing/bearer, explicit grants,
directory discovery, hash verification and actual local ASR. Task
`7fbabde040ef4827a8bc1b1b7590a548` reached cloud planning. The configured Azure
provider then failed DNS resolution (`gaierror` errno 8, `APIConnectionError`).
The owned CLI was stopped after two observed failed provider attempts to prevent
unbounded automatic retries. There is no successful cloud plan, rendered output
or browser relay claim from this run. Provider failure handling and live model
availability must be resolved before marking this gate complete.

Automated commands and boundaries:

- Editing package: `EDIT_TEST_PG_URL` points to an owned ephemeral PostgreSQL 17
  cluster; `GGWORK_NATIVE_ASSETS` supplies real pinned weights and three synthetic
  episodes. **129 passed**, no PostgreSQL/native skips.
- Backend strict blocking suite: **149 passed**.
- Legacy pick query guards, selection/save receipt, mirror replay, board replay
  cases and board fixture: **130 passed**, with SQLite and disposable PostgreSQL.
- Backend broad offline suite: 18,481 passed, 174 skipped, 3 deselected,
  3 failures on the first run. Two integration guidance inventory/budget failures
  were corrected; the native subprocess test needs the current virtualenv on PATH.
  Focused rerun and frontend/browser final evidence are recorded separately.

All provider variables were loaded by exact name into the isolated Gateway.
Inherited database, pick sync, authentication and app-home variables were cleared;
primary `.env` was not sourced. Worker credentials, TLS private keys and raw logs
remain outside Git. No production database, media, deployment or signing step was
used. One process/replica remains a deployment constraint rather than a throughput
measurement.

The real owner stop request remained `stopping` while the native CLI was offline.
Restarting that same paired CLI produced its real acknowledgment and transitioned
the task to `stopped`. All three synthetic original SHA-256 values still matched
the frozen source manifest. The browser authenticated against this Gateway,
verified directory draft behavior without task submission, and followed history
into the same stopped task ID. These establish preparation/identity/stop behavior,
not successful rendering or media delivery.
