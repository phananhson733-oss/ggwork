# Production feedback suspension

The user explicitly authorized suspending production feedback and its hourly refresh on 2026-10-10. This is a
configuration-only rollback of the [2026-10-08 activation](2026-10-08-feedback-production-activation.md). It changes no
application code, source data, mappings, stored feedback or collector settings.

## What happened

The owner of the fixed source Base reorganised it on 2026-10-09, after the last successful refresh that day. Three
tables were removed (the automatic CPS detail and both revenue roll-ups) together with four columns the published
baseline was bound to (two on the account ledger, one on the publication records, and the manual CPS table's link to a
removed roll-up). The user confirmed the removal was intentional.

The feedback-v2 contract binds every table and column by id. Every scheduled and query-time refresh after the
reorganisation was therefore refused with `schema_changed`; 27 consecutive refreshes failed before the suspension. No
partial version was published and the last complete version stayed current, as designed.

The user-visible effect was wider than stale feedback. For the configured feedback owner, a candidate query that is not
a plain ranking returns the refresh failure instead of candidates. A production query at 2026-10-10 03:53 UTC produced
no candidates; the conversation card showed only a generic failure line, and the answer check withheld the model's
relay of the reason, so nothing told the user what was wrong.

## Diagnosis

All diagnosis was read-only: gateway logs, a read-only database session inside the gateway container, and a field
listing of each source table through the production isolated Lark runner under the configured owner. The listing reads
schemas only, never records. The gateway logs nothing for a refused refresh and `schema_changed` does not record which
table or column changed, so the cause was established by comparing the live schema with the last published manifest.

## Configuration applied

| Setting | Applied production state |
|---|---|
| `PICK_FEEDBACK_ENABLED` | `0` |
| `PICK_FEEDBACK_SCHEDULE_ENABLED` | `0` |
| `PICK_FEEDBACK_OWNER_ID` | Unchanged |

Both variables were set at about 04:13 UTC. Railway configuration deployment
`65c15829-8084-452f-b734-2a756fa73309` reached SUCCESS at 04:16 UTC. No source was uploaded and no frontend deployment
was performed. The deployment guard was not run for this change, so there is no guard line for it in the progress log;
the last guarded gateway source remains the one recorded on 2026-10-09.

## Verification

- Gateway logs after the restart show the business extension loaded and application startup complete, with no
  traceback.
- Inside the running container the feedback settings resolve to disabled, unscheduled, with the owner still configured.
- No feedback run was created after the restart. The current feedback version and the lease state are unchanged.

Not verified in this record: a real candidate query by the affected user after the suspension. The suspension removes
the gate that refused it; confirmation that the retried query returns candidates is still outstanding.

## State while suspended

Candidate queries no longer attempt a refresh and carry no operating feedback. The feedback tools, status route and
manual sync report the feature as disabled. Historical candidates keep their frozen feedback evidence. Private feedback
versions, run history, the configured owner and server-side credentials are preserved; nothing was deleted.

## Path to re-enabling

1. Release the [feedback-v3 source contract](../feedback-source-contract.md), which reads the thirteen remaining tables
   and takes revenue only from the manual CPS table and the publication records' RS column. It is implemented and
   covered by synthetic tests, but not merged or deployed at the time of this record.
2. Deploy the gateway through the deployment guard.
3. Verify a real read-only double scan of the live Base under the configured owner. The v2 baseline may lose exactly the
   four reviewed columns once; any further source change since the diagnosis will be refused again.
4. With separate explicit authorization, set both flags back to `1` and confirm the first refresh publishes a complete
   feedback-v3 version.

Deploying the v3 contract and re-enabling the flags are separate production changes. Neither is authorized by this
suspension.

Raw rows, source counts, financial values, owner identifiers, credential material and feedback version or run
identifiers remain in restricted private operational artifacts rather than this public record.
