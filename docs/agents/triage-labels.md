# Triage labels

`ready-for-agent` means an issue has an actionable scope, agreed acceptance boundary and dependencies. It authorizes implementation within that scope; it does not mean dependencies are finished, production access is approved, or the feature is accepted.

For the editing workstream:

1. Read the issue, [published spec copy](../editing/spec.md) and [task graph](../editing/task-graph.md). Start dependent work only after the required contract or prerequisite is available.
2. Record ownership, branch and progress on the issue/ledger. Keep implementation, integration, verified acceptance and release status distinct; do not invent extra labels as state evidence.
3. If an external gate prevents acceptance, document the exact missing evidence and preserve the issue as open. `ready-for-agent` never waives native execution, output quality or deployment checks.
4. Closure belongs to the single integration PR under the [tracker policy](issue-tracker.md). Remove readiness when no actionable implementation remains; keep incomplete acceptance visible instead of closing it as completed.
