# Editing extension

- Business source lives here, independent of the pick mirror. Never edit a managed installed snapshot.
- Host `session_factory` supplies persistence. `ggwe_` tables and `ggwe_alembic_version` are private; never attach them to host metadata/migrations.
- Public HTTP and owner-scoped repository methods are the shared page/tool/worker seam. Owner comes from the host principal/runtime context, never a model/body field.
- Browser routes reject device subjects. Worker routes require the host's exact bearer-router admission plus matching owner/device subject; credentials are digest-only and rechecked after revocation.
- Every owner mutation uses database serialization (SQLite writer transaction or PostgreSQL transaction advisory lock), including idempotency receipts. Keep stop, publication and retry within that transaction.
- Source paths are native grant-relative data. Gateway never reads them. Only authenticated devices verify exact selected manifests; source sets freeze on admission.
- Delivered result metadata is immutable. A stop fence blocks publication until a real stopped acknowledgment. Offline/expired lease alone never asserts process termination.
- Native code may import lightweight contracts. Keep host imports inside extension install; native workers never receive host/model credentials.
- Tests use real routes/services and disposable databases. Synthetic result reports prove protocol behavior, not decoding or native process exit. Real media/native acceptance remains a separate gate.
- Text planners use the configured host model factory and strict `planner.Plan`; transcript-only inputs exclude source paths and credentials. Never clamp ranges, skip unknown sources, or publish partially validated plans.
- `ConfiguredPlanner` sets availability only after constructing the named provider; its owner policy intersects live host account role, model authorization and per-user Skill storage. Keep native bearer subjects narrow; any owner policy lookup is an additional check, not restored host authority.
- Conversation tool source-thread provenance comes only from `EditingLifecycle` TaskInfo. Keep registered aliases tied to the shared slash resolver. Model/tool gates admit the actual registered callable identity, including host description clones, never a matching untrusted name.
