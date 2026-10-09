# Agent workflow

- Issue tracker: GitHub in `phananhson733-oss/ggwork-deerflow`, explicitly selected with `--repo`; business remote `ggwork`, never upstream `origin`. See [issue tracker](issue-tracker.md) and [triage labels](triage-labels.md).
- `ready-for-agent` marks actionable scoped work. Editing tasks follow [spec #53](../editing/spec.md), the [task graph](../editing/task-graph.md) and [delivery ledger](../editing/delivery-ledger.md); delivery and issue closure run through one `codex/clip-workbench-integration` PR.
- The editing base is `b17623966ef81e68c202a40a009306f49ffb03c9`. Its extension API has no frontend UI hooks: retain packaged backend business logic and use the [documented narrow frontend mounts](../editing/integration.md). Source verification is not live deployment verification.
