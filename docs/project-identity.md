# GGWork project identity

The canonical project name is **ggwork-deerflow**. Use this mapping for development and releases.

| Component | Canonical location |
| --- | --- |
| Primary local checkout | `/Users/wzb/Code/ggwork-deerflow` |
| Local worktrees | `/Users/wzb/Code/ggwork-deerflow-wt/` |
| Business GitHub repository | https://github.com/phananhson733-oss/ggwork-deerflow |
| Business branch | `main` |
| Vercel project | `ggwork-deerflow` (`prj_pAeTz6xYcA9qQsVUR7toec1K8Mks`) |
| Vercel root directory | `frontend` |
| Website | https://ggwork-deerflow.vercel.app/workspace/chats/new |
| Railway project | `ggwork-deerflow` (`449df38f-9bce-40ad-90c8-8feac355881d`) |
| Railway services | `gateway`, `pick-obs-trends` |
| Upstream foundation | https://github.com/bytedance/deer-flow |

## Local Git convention

In the existing shared checkout and its worktrees, `ggwork` is the business remote short name. Its URL is now `https://github.com/phananhson733-oss/ggwork-deerflow.git`. `origin` remains the DeerFlow upstream. Keeping the short names preserves branch tracking and existing deployment commands; `ggwork/main` is a remote-tracking ref, not the old local project.

The primary checkout may still be on an older feature branch. Use an isolated worktree based on freshly fetched `ggwork/main`; do not reset an existing development checkout. A fresh clone of the business repository normally calls its business remote `origin`; use `--remote origin` with the deployment guard in that clone.

## Deployment boundary

The Vercel frontend and Railway services currently use CLI deployments, with no connected Git source. Renaming the business repository does not enable automatic deployment. Run the deployment guard before releases; it accepts the canonical `phananhson733-oss/ggwork-deerflow` repository and rejects the retired repository name and the legacy implementation. See [the guard runbook](pick-workbench/observe-runbook/deploy-guard.md).

This naming migration does not deploy application code, restart services, migrate databases, enable collectors, or change the public website address. Historical release records keep their original URLs and commit identifiers. Internal module names such as `ggwork_pick`, `ggwp_`, and `customizations/pick-workbench` remain stable API, database, and package identifiers.

## Legacy implementation

`/Users/wzb/Code/ggwork` and https://github.com/phananhson733-oss/pick-workbench contain the earlier standalone workbench. Retain them for reference and recovery; they are not the source for this website. The legacy repository is not archived as part of this naming migration.

## Codex project entry

Use the saved project whose path is `/Users/wzb/Code/ggwork-deerflow`. Its display label should be `ggwork-deerflow`. Older chats may retain worktrees under `/Users/wzb/.codex/worktrees/.../ggwork` tied to the legacy repository; do not assume a chat's label changes its checkout. Check `git rev-parse --show-toplevel` and `git remote -v` before editing.
