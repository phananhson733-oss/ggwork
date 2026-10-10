# Production feedback re-enabled on feedback-v3

The user explicitly authorized switching production feedback and its hourly refresh back on on 2026-10-10, after the
[feedback-v3 release](2026-10-10-feedback-v3-release.md). This is a configuration-only change that ends the
[suspension](2026-10-10-feedback-suspension.md). It changes no application code, source data, mappings or collector
settings.

## Configuration applied

| Setting | Applied production state |
|---|---|
| `PICK_FEEDBACK_ENABLED` | `1` |
| `PICK_FEEDBACK_SCHEDULE_ENABLED` | `1` |
| `PICK_FEEDBACK_OWNER_ID` | Unchanged |

Both variables were set at 05:40:37 UTC. Railway configuration deployment `e4f2d20c-8fb7-4f0e-8fa8-4358321ee61c`
reached SUCCESS at about 05:41 UTC. It reused the gateway source uploaded for the feedback-v3 release
(`74c48c8e38770eabc764211b2ef4bf26d08f2967`); `PICK_SOURCE_REVISION` still names that commit. The deployment guard ran
at `0013cd0278622734d7f96cee2f2e7338a35ae840`, which differs from the released commit only by documentation. No
frontend deployment was performed.

- `pick-deploy-guard target=gateway commit=0013cd0278622734d7f96cee2f2e7338a35ae840 prod_head=0011 chain_head=0011 at=2026-10-10T05:40:29Z`

## First production result

The gateway's own scheduler started its startup refresh at **05:41:15 UTC** and it finished successfully at
**05:43:06 UTC**. Read back from the production database through a read-only session inside the gateway container:

- The run is `scheduled` / `success` with no error code, and the version it produced is the current version.
- The current version is feedback-v3 with all thirteen tables complete. No lease is held and no run is left running.
- It is the only feedback run since the restart.

This was the single refresh in which the feedback-v2 baseline was allowed to lose the four reviewed columns. The
feedback-v3 version is now the strict baseline. A field listing of the live Base bound against that new baseline
afterwards (schemas only, no records, nothing written): all thirteen tables bind with no column dropped, and a second
pass matches.

Source quality is **partial**, as it was under feedback-v2. Complete table ingestion is not proof that every business
metric or mapping is complete; pending mappings stay excluded from attribution.

Gateway logs for the new deployment show the business extension loaded, the feedback routes mounted and application
startup complete, with no traceback. Inside the container `/health/ready` returns ready with database and checkpointer
ok, the feedback settings resolve to enabled and scheduled with the owner configured, the installed extension digest is
unchanged from the release and the private schema head is still `0011`. The public pick feedback status route returns
401 without a session.

## What users will see

For the configured feedback owner, a new candidate query that is not a plain ranking again waits on operating
feedback. It starts or joins a refresh and waits at most twenty seconds in the foreground, while a complete double scan
of the live Base has taken close to two minutes every time. The first ask of a new query is therefore expected to
return no candidates and the card line that feedback is still refreshing. Asking again in the same conversation
continues from that refresh only if the model passes the receipt it was given, and only from the moment the refresh
has finished until five minutes later; without the receipt a second ask starts another scan and ends the same way, and
a refresh that failed (for instance because the Base was edited while it was read) is reported as a failure. A query
that refines an existing candidate keeps that candidate's frozen feedback and does not refresh.

This is the designed behaviour described in the [runbook](../feedback-runbook.md), not a regression of this change, but
it is the first thing a user meets after re-enabling, and with feedback switched off the same query does not wait on
any refresh. Whether queries should instead use the most recent scheduled version is an open product decision.

## Not verified by this record

- No authenticated browser check was made on the production site. The affected user's retried candidate query, an
  existing conversation and the saved selections are still to be confirmed by a logged-in user, so the rollback-matrix
  manual cells stay open.
- No candidate was generated against the feedback-v3 version, and the feedback analysis tool was not exercised against
  it. Normalisation and analysis of the same source under this contract were checked only in the pre-merge in-memory
  scan.
- The ask-again path has never completed in production with a live model: the run history holds no successful
  query-triggered refresh, and no stored candidate carries feedback evidence, under any contract version.
- The next hourly occurrence, expected around 06:41 UTC, has not been observed.
- Access for a different signed-in user was not re-tested; only the unauthenticated 401 was.
- The external-ID mapping table is still read and validated although no revenue row is attributed through it.

## Rollback

Set both flags to `0`, let the gateway restart, and verify the disabled state. Because a feedback-v3 version is now
published, feedback must be switched off before the gateway is ever rolled back to code older than the feedback-v3
release; stored versions are never deleted.

Raw rows, source counts, financial values, owner identifiers, credential material and feedback version or run
identifiers remain in restricted private operational artifacts rather than this public record.
