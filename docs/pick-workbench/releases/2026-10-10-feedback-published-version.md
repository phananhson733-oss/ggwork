# Queries use the published feedback version — production release

The user chose this product change on 2026-10-10, after the [re-enabling](2026-10-10-feedback-reactivation.md) showed
what the previous behaviour cost: every new candidate query waited on a scan of its own that could not finish in the
foreground. The user asked for the release once CI had passed and confirmed that it would also carry PR #78, which had
been merged to `main` shortly before and not yet deployed.

## Deployment identities

| Component | Verified identity |
| --- | --- |
| Business PR | https://github.com/phananhson733-oss/ggwork-deerflow/pull/77 |
| Product merge | `f58ecd33a393854bc854587f96acb1043c35351c` |
| Gateway | `bfaa0896-b780-413d-b4fb-7f197131adc4` — SUCCESS |
| Installed extension | `sha256:ba42543402ea551356fd667e47dfdd622cbaad2969bba13d29e61f292497cc7c`, equal to the digest of the merged managed snapshot |
| Private schema head | `0011`, unchanged; this release has no migration |
| Frontend | Not deployed: no frontend source changed |

Against the previous gateway source (`587825884ca4045dff96f3faf2d55dc5ada254a3`) the image changes four files of the
extension (`feedback/runtime.py`, `feedback/tools.py`, `middleware.py`, `tools.py`) and the two host files of PR #78
(`app/gateway/auth/pat.py`, `app/gateway/auth_middleware.py`). Nothing else that ships differs.
`PICK_SOURCE_REVISION` was set to the product merge without triggering a separate deployment. Both feedback flags
stayed `1`.

## What changed for users

- A new candidate query or feedback analysis by the feedback owner uses the published feedback version as it stands.
  It no longer starts a scan or waits for one, so candidates come on the first ask.
- A refresh that is failing or still running no longer withholds candidates. The candidate keeps the last successful
  read, with its read time; past two missed hourly slots it also carries a notice with the age and the last failure.
- A receipt left in an older conversation, expired or for a failed refresh, no longer blocks a retry there.
- Data edited in the Base reaches candidates with the next successful refresh: the hourly schedule, or 选剧资料 →
  同步与导入 → 刷新飞书反馈.
- Only a scope that has never published a version still waits on its first refresh.

`schema_changed` and `auth_required` therefore no longer stop queries. The refresh keeps failing until someone acts on
it; the status panel on the data page shows it first and the candidate notice follows after two hours.

## Verification

Before merge, on the PR head:

- CI run [38030806217](https://github.com/phananhson733-oss/ggwork-deerflow/actions/runs/38030806217) passed both
  jobs. It ran before PR #78 was merged.
- Complete extension suite on SQLite and a throwaway PostgreSQL 17, run alone: 5,507 passed, 25 skipped, zero failures.
- Synthetic-provider browser acceptance against a local gateway and frontend: 3/3, including that an unrefreshed
  candidate keeps the published version and that a changed source reaches candidates after an explicit refresh.

On the product merge itself, which adds PR #78 and documentation to that head:

- Complete extension suite on both databases, run alone: 5,509 passed, 25 skipped, zero failures. Required rollback
  files on both databases: 35 passed, zero skipped.
- PR #78's host authentication tests (`test_pat_auth.py`, `test_personal_access_tokens_repository.py`,
  `test_auth_middleware.py`): 120 passed.
- The browser acceptance was not repeated on the merge. What the merge adds to the shipped code is PR #78's two
  authentication files, covered by the tests above and not by a browser run.

After deployment:

- Gateway logs show the extension loaded and application startup complete, with no traceback.
- Inside the container: the installed digest above, feedback enabled and scheduled with the owner configured, migration
  head `0011`, `/health/ready` ready with database and checkpointer ok.
- The startup refresh ran 08:34:22–08:36:17 UTC and succeeded; the current version is feedback-v3 with thirteen tables.
- The product's own `prepare_feedback`, driven inside the container against production state through database sessions
  that cannot write: a plain query and a query carrying an old receipt both pin the current published version with
  freshness `stale` and no notice, start no refresh, and leave the run history unchanged.
- Unauthenticated requests to the pick routes return 401 through the public site, as does a request with an invalid
  bearer token.

Scheduled refreshes observed from the previous container, read back after this deployment: the 07:11 UTC occurrence
succeeded and the 08:11 UTC occurrence failed with `source_changed`. The first shows the hourly schedule repeating; the
second is the ordinary failure this change stops from reaching users.

## Not verified by this release

- No authenticated browser check was made on the production site, and no candidate was generated there by a live
  model. The affected user's retried query, an existing conversation and the saved selections are still to be confirmed
  by a logged-in user, so the rollback-matrix manual cells stay open.
- That the model states the read time and relays a notice is instruction text, not a tested behaviour. The notes panel
  shows both regardless.
- The notice itself was not seen in production: the published version is inside its window.
- PR #78 was shipped, not accepted, by this release: no `pick:read` token was created or used. Its acceptance belongs
  to its own record.

## Rollback

A revert of the product merge on `main`, deployed through the guard, restores the previous query behaviour. It is safe
for stored data: this release persists no new value, and candidates frozen with freshness `stale` remain readable by
older code. The feedback flags remain the switch that takes feedback out of queries altogether.

Guard lines are recorded in [progress.md](../progress.md). Raw rows, source counts, financial values, owner
identifiers, credential material and feedback version or run identifiers remain outside this record.
