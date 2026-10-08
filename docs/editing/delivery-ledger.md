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
| [T8 / #61](https://github.com/phananhson733-oss/ggwork-deerflow/issues/61) | Pending; implementation owner records tested revision and checks | Pending; issue remains open |

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
