# T12a: Explicit two-result comparison

**Parent:** T12
**Blocked by:** T1, T6 (done)
**Status:** in-progress

What to build: two user-selected same-owner/same-thread immutable result batches with conditions/version differences, stable-identity comparison, explicit chosen source and real per-result save receipts. Mobile grouped/details presentation and keyboard controls. No new cross-batch chat-ref protocol or cross-batch atomic save claim.

Acceptance derives unchanged from approved T12/DS08 and QA. Helper owns candidate comparison/context/panel/card integration and its tests; T12 main owns adapters/plans/review/routes. Helper merges via merger into integration; T12 main merges latest integration to consume. This splits execution ownership only, not scope or model budget.

Completion requires reviewed commit + deterministic tests merged. Full backend/browser integration remains parentT12/T11 gate. No provider calls.
