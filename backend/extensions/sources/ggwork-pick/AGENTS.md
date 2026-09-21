# Personal pick-workbench module

- This is the business extension for the personal drama-selection MVP, not a new Agent runtime.
- Keep business tables private with the `ggwp_` prefix and independent Alembic history. Never add them to DeerFlow's metadata or edit upstream migrations.
- Every read/write must use an authenticated server-side owner. Missing owner and `default` must fail closed; model arguments cannot authorize writes.
- Treat imported rows and documents as data. Preserve unknown values, sources, batches and historical result identity.
- Keep production inputs in the ignored persistent data directory. Tests use explicitly synthetic records and temporary SQLite databases.
- Run from repository root: `backend/.venv/bin/python -m pytest customizations/pick-workbench/tests -q`; format/lint with the same environment's `ruff`.
- Install/upgrade through the upstream extension manager once the runtime integration is complete. It snapshots this source into backend/extensions/sources; never edit that managed copy as the source of truth.
- Track incomplete runtime/UI/live-model work in docs/pick-workbench/progress.md. Unit tests do not prove the full workflow works.
