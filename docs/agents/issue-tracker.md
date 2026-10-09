# Issue tracker

- Provider: GitHub Issues.
- Canonical repository: [`phananhson733-oss/ggwork-deerflow`](https://github.com/phananhson733-oss/ggwork-deerflow).
- Local business remote: `ggwork`. Always pass `--repo phananhson733-oss/ggwork-deerflow` to issue/PR commands; `origin` points to upstream `bytedance/deer-flow` and must not receive business tickets or delivery PRs.
- Ready label: `ready-for-agent`; semantics are in [triage labels](triage-labels.md).
- Editing parent: [#53](https://github.com/phananhson733-oss/ggwork-deerflow/issues/53); durable [spec](../editing/spec.md), [task graph](../editing/task-graph.md) and [delivery ledger](../editing/delivery-ledger.md).

For editing, integrate task branches into `codex/clip-workbench-integration` and submit one integration PR to business `main`. A task branch commit, local merge or passing focused test is not issue closure. The integration PR may use `Closes #N` only for issues whose stated acceptance is fully met; retain `Refs #N` and open issues for missing native, quality, browser or deployment evidence. Close the parent only when the full specification is met. If code lands before a required gate can be run, keep that gate and its owner visible in the ledger and issue.

Before reporting a task, merge the current integration tip into its branch, preserving concurrent changes; never reset or clean another agent's work. Record the tested revision and remaining limitations. The integration owner controls the final PR, push and closure bookkeeping.
