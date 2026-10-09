# Production feedback activation

The user explicitly authorized production feedback and hourly refresh on 2026-10-08. This operation enables the existing mapping release; it does not change application code, source mappings, financial values or collector settings.

## Configuration and identity

The configured owner was resolved to the existing production administrator with server-side Feishu credentials. Live user authorization and a read through the production isolated Lark runner succeeded before activation. The browser QA account was not granted feedback access. No local credentials were copied to the server and no new access token was issued for verification.

| Setting | Applied production state |
|---|---|
| `PICK_FEEDBACK_ENABLED` | `1` |
| `PICK_FEEDBACK_OWNER_ID` | Explicit authorized host user; identity retained only in private operational records |
| `PICK_FEEDBACK_SCHEDULE_ENABLED` | `1` |

Railway configuration deployment `6bbb7959-91d6-44a2-9769-a6dcd87f0319` reached SUCCESS. It reused the existing uploaded gateway source. The gateway source paths at the deployment-guard commit `7f462e86f1ff4f4ace6f7cc8be05dcf96f55c7ef` were identical to product release `638f3ae18a44f2d9496234a689c380d9a95fc0b2`; intervening product changes were frontend-only. No frontend deployment was performed for activation.

- `pick-deploy-guard target=gateway commit=7f462e86f1ff4f4ace6f7cc8be05dcf96f55c7ef prod_head=0008 chain_head=0008 at=2026-10-08T09:51:21Z`

## First production result

The gateway's actual hourly scheduler initiated the first run at **2026-10-08 09:55:10 UTC**. It completed successfully at **09:59:12 UTC**. The production status route, read through existing internal authentication scoped to the configured owner, returned 200 with enabled/configured true, a current immutable feedback-v2 version, all sixteen tables complete, no running job, and no expired lease.

Source quality is **partial**. Complete table ingestion is not proof that every business metric or mapping is complete. Pending mappings remain pending and excluded from unearned candidate/revenue attribution.

The production `pick_analyze_feedback` callable was executed under the configured owner's controlled server runtime, using the completed run receipt and the actual production database. It returned nonempty genre analysis, the same production feedback version and source evidence. The validation used read-only database connections and did not start another refresh, create candidates or invoke an LLM. A country query correctly returned `unsupported_dimension`.

Access checks returned **403** for another existing user and **401** for an unauthenticated request. Gateway readiness, database and checkpointer were healthy after activation.

## Schedule and remaining boundaries

The existing scheduler refreshes on startup and then every 3600 seconds. Its first automatic execution is verified. The next hourly execution is expected around 2026-10-08 10:55 UTC; that later occurrence has not been observed as part of this record. Manual and Agent requests share the durable lease with scheduled runs.

This verifies the real production source, scheduler's initial execution and feedback tool's data path. It does not claim autonomous live-model tool selection, that all mappings are confirmed, that every source metric is complete, or that the independent RealShort feed 503 condition is repaired. Existing historical candidate evidence remains unchanged.

Raw rows, source coverage counts, financial results, owner identifiers, credential material, feedback version/run identifiers and configuration receipts remain in restricted private operational artifacts rather than this public record.

## Rollback

To suspend this feature, set both feedback feature and schedule flags to `0`, allow the gateway to restart normally, and verify the disabled state. Preserve the configured owner, credentials, private feedback snapshots and historical candidate evidence. Do not delete data or change unrelated collector schedules to roll back this activation.
