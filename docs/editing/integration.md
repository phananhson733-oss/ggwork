# Editing integration foundation

Recorded 2026-10-08 for [spec issue #53](https://github.com/phananhson733-oss/ggwork-deerflow/issues/53) and [T1 #54](https://github.com/phananhson733-oss/ggwork-deerflow/issues/54).

## Source and deployment identity

The implementation base is business `main` at `b17623966ef81e68c202a40a009306f49ffb03c9` (`docs(pick): record production feedback activation (#51)`). Work converges on `codex/clip-workbench-integration`. The canonical repository is `phananhson733-oss/ggwork-deerflow`, local remote `ggwork`; `origin` is `bytedance/deer-flow` and is not the delivery target. The newer upstream checkout and the legacy standalone GGWork app are not this implementation baseline.

This record verifies source boundaries only. The current Vercel and Railway deployment revisions and the authenticated production interface have **not** been verified for this change. A source commit or GitHub merge cannot substitute for that evidence. Track each layer in the [delivery ledger](delivery-ledger.md).

The [durable specification](spec.md) is the published issue body, copied without rewriting its requirements or outstanding acceptance gates. [Task graph](task-graph.md) records the implementation dependencies.

## Existing workbench and chosen mounts

| Source at the base revision | Observed contract | Editing integration decision |
| --- | --- | --- |
| `frontend/src/components/workspace/workspace-sidebar.tsx`, `PickNav` | Shared **选剧工作台** group has **我的选剧** (availability notice) and **选剧资料** (`/workspace/pick-data`). | Preserve both and the host mobile navigation. Add sibling `/workspace/editing` and detail `/workspace/editing/[taskId]`; do not replace the workbench shell. |
| `frontend/src/app/workspace/pick-data/page.tsx` | Auth runs before the `trends` early return; `/workspace/pick-data?tab=trends` branches before catalog/mirror resolution. | Keep Trends intact. Editing history and task details must read their own task service without mirror version, pick-result or catalog prerequisites. |
| `frontend/src/components/workspace/pick-board/toolbar.tsx` | Board-specific tabs and query/replay contracts. | Keep filtering, sorting, pagination and return context. A contextual editing link may carry drama context without making clipping a mirror-backed board tab. |
| `backend/packages/extension-api/deerflow_extension_api/contracts.py`, `ExtensionRegistry` | Services, eager HTTP routers, middleware, lifecycle and observer contributions; no frontend UI registration hook. | Package editing business services under `customizations/ggwork-edit`. Use narrow explicit frontend route, sidebar and message-card mounts supported by this base. Do not pretend a newer frontend extension hook exists or rebuild the core runtime. |
| `frontend/src/components/workspace/messages/message-group.tsx` | Specialized tool cards are routed explicitly and kept visible outside collapsed execution steps. | Add a thin editing card adapter reading the same owner-scoped task state as the page; show a source conversation only when it exists. |

## Service and authorization boundaries

- Use host-resolved identity for all task, device, attempt and output access. User IDs in model arguments, request bodies or transcript text never confer authority.
- Use the host session factory with private editing metadata/migrations; do not bind editing history to pick mirror availability or require a host conversation for page-created tasks.
- Keep authenticated frontend requests on `core/api/fetcher`, validate public DTOs, and scope caches to the current owner. Workspace fallback users are not sufficient admission for a business page.
- Native device authentication requires a real admitted host boundary. A worker bearer string does not automatically pass Gateway authentication. The agreed implementation direction is a generic bearer-only contributed-router admission seam with owner/device validation, not a business auth bypass; its implementation and tests remain required.
- Existing `PickModelGate` and `PickToolGate` filter tools separately. Editing chat tools must survive both through narrow registration/capability checks. A YAML tool entry or slash alias alone is not working chat integration.
- Reuse the shared slash parser and actual enabled/authorized skill state. Public skill enablement is global in this base, so per-owner execution admission needs its own authoritative gate.

## Verification boundary

The agreed primary test seam is the authenticated shared task API, exercised with real business service and temporary persistence. Thin page/chat tests cover DTOs, owner cache isolation, links and consistent state. Real Apple Silicon execution, process-stop acknowledgement, media decoding, byte delivery and model quality each require separate evidence. Do not infer them from synthetic task-state tests or a mocked browser demo.

T1 is documentation-only: source checks, published-spec byte equality, relative links and `git diff --check` are appropriate verification. It does not satisfy the native, quality, browser or deployment gates in the specification.
