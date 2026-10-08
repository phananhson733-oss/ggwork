# T4-ui: Shared-query frontend cutover and PostgreSQL parity

**Parent:** T4/T12
**Blocked by:** T1, T6, T12 frontend code (integrated); backend validation waits for T4 API implementation
**Status:** done

Migrate declared common catalog/candidate/rank/posted/rules data readers to existing authenticated queryPickBoard POST seam, preserving incoming owner session/CSRF, fixed version/replay/pagination/filters/counts/period/errors. Special source-derived views outside declared common domains stay legacy with explicit boundary. Do not activate a field-losing wrapper or silently mix legacy facts with new result version.

Use exact fixed synthetic PostgreSQL mirrors and real Gateway to compare legacy TypeScript query outputs with newservice, including facets/ties/archived/unknown/weeklyyear/RS domain semantics and errors. Keep oldreads only until eachdomain proven; finalT4 completion requires actualcallsitecutover, not merelyexportedunusedadapter. No productionDSN/modelcalls.

Owner frontendserver/pick-board adapters, parityharness and scopedtests only. BackendT4ownsqueryservice/sharedadditiveDTOs; coordinate andconsume via integrationmerges. T4 parentremainsopen untilthisgatepasses. Test allnew reader behavior throughboundaries, not tautologicalexpectedvaluescopiedfromnewimplementation.

## Completion evidence

Integrated exact 7838e1ca213efec802333d606e3d4a462b12bf25. Final independent review approved 24 file hashes plus two DTO/fixture dependencies, 70 focused tests and pnpm check. Author ordinary-user real Gateway/T5 ASGI/PostgreSQL/default-export matrix passed all five domains; the fixture/harness isolation received independent Python review. Merger passed 89 frontend tests, 69 host tests, two real PostgreSQL cancellation/reuse tests, typecheck and diff check. The operator card preserves actual scope, sorting, periods, metrics, omissions and source references without raw audit fields or save authority. Full product browser/design acceptance remains T12/T11.
