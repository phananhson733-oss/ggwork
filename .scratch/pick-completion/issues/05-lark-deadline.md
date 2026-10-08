# T5 follow-up: ordinary deadline reaches Lark worker

**Parent:** T5 / D10
**Status:** ready-for-agent

Propagate PickTask.ordinary_deadline to the existing Lark worker instead of reconstructing a total deadline from remaining(). Cancellation of the async await must not leave the worker running through the reserved finalization period. Preserve existing worker limits, plugin guards and single final publication. Add a regression with a total budget containing the finalization reserve, assert the actual worker receives only ordinary remaining time, and verify cancellation cleanup without live provider/Lark calls. Separate worktree after frozen T4a handoff, code/Python review and merger required.

Other full-extension failures remain tracked externally in extension-suite-triage.md: observer role/schema test setup, T4 timestamp guard and managed package drift. None are waived by this child; managed package synchronization remains T10.
