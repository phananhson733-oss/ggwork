# T5 follow-up: ordinary deadline reaches Lark worker

**Parent:** T5 / D10
**Status:** done

Propagate PickTask.ordinary_deadline to the existing Lark worker instead of reconstructing a total deadline from remaining(). Cancellation of the async await must not leave the worker running through the reserved finalization period. Preserve existing worker limits, plugin guards and single final publication. Add a regression with a total budget containing the finalization reserve, assert the actual worker receives only ordinary remaining time, and verify cancellation cleanup without live provider/Lark calls. Separate worktree after frozen T4a handoff, code/Python review and merger required.

Other full-extension failures remain tracked externally in extension-suite-triage.md: observer role/schema test setup, T4 timestamp guard and managed package drift. None are waived by this child; managed package synchronization remains T10.

Implemented in codex/pick-completion-t5-lark: original ordinary deadline flows into cold version probe, help/risk/credential setup and command process; default probe behavior stays unchanged. Synthetic actual-worker/process-group tests and independent code/Python review are required before root merge closes this follow-up.

completion_t5 owns separate codex/pick-completion-t5-lark worktree based on integrated d15973bd. T4a approved reference code remains separate; this slice owns only the Lark deadline adapter, focused tests and relevant documentation.

Completed in5d843b5989629aeb38ec3a1062ab869019d64316. Independentreal-process repro with0.3sdeadline nowtimesout0.312s withnochild/noextrareader/no late marker; all9code/8Pythonhashes andproofreceipts checkedbeforemerge. Merger44host+107extensiontests passed. Fullbackendauthor18489passed174skipped3deselected. No liveLark/model calls.
