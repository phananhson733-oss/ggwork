# Feedback identity mapping implementation plan

> Execution: use subagent-driven development with one implementation owner at a time, followed by specification and code review.

**Goal:** Implement the approved Feishu master identity, external ID registry and CPS drama links, then consume confirmed mappings in versioned feedback without changing historical candidates.

**Architecture:** Feishu remains the mapping maintenance source. New full scans include the mapping table as snapshot transform v2; the existing fifteen-table v1 reader, content hashes and frozen evidence remain compatible. Candidate/result HTTP contract stays feedback-v1. The Agent remains read-only; this authorized one-time schema/backfill operation is not a new runtime writer.

**Stack:** Existing Python extension, immutable SQL snapshots, native Feishu Base fields, existing frontend evidence/status UI.

## Scope and authority

- Approved master fields: `选剧台剧集ID` (existing canonical JSON identity, not a result ID) and `选剧台对应状态`.
- Approved external registry: one external identity per source/system, theater, ID type, explicit global/account scope and exact string ID; single linked master record, confirmation state and audit evidence.
- CPS auto/manual: add `关联剧集`. Manual CPS also needs explicit `数据粒度`; an association alone never proves that all money in a row belongs to one drama.
- Preserve all original columns and data. No title-only financial attribution, splitting aggregate money, new ranking weights, geographic inference, collector restart or feedback/scheduler activation.
- Public repository receives code and sanitized engineering documentation only. Real schemas/rows, candidate matches and write receipts remain in restricted ignored artifacts.

## Task 1 — Feishu schema and receipt inventory

1. Resolve the exact user-supplied Base as the existing user identity; inventory current tables and relevant field schemas.
2. Create only absent approved fields and the external mapping table; persist actual IDs and partial-write receipts. Never retry an unknown mutation blindly.
3. Add display-only linked-name/platform/language/identity references where supported. Enforce single-drama cardinality in the consumer; native link cells are sets.
4. Read back the final schema once all additions settle. Compare unchanged existing fields and verify new links target the master table.

## Task 2 — Snapshot and schema transition

Files: `feedback/contracts.py`, `source.py`, `fields.py`, `feishu.py`, `sync.py`, related `tests/feedback/` source/contract tests.

1. Pin a literal v1 snapshot/hash regression before changing the table registry.
2. Keep exact fifteen-table validation and default transform for v1; explicitly emit v2 scans with the new real table ID. The API reply version remains v1.
3. Preserve the old hash algorithm and avoid new default serialization fields on old record/field models.
4. Permit only this reviewed additive field/table transition from a stored v1 baseline; retain existing field IDs, types, semantic names and properties. Never clear the entire baseline. New link targets and required types must match the verified schema.
5. After the first successful v2 scan, apply the normal strict schema baseline again. Any failure/incomplete page or changing second scan retains the prior current version.

## Task 3 — Deterministic confirmed mapping

Files: `feedback/normalize.py`, `identity.py`, `analytics.py`, focused identity/normalization/evidence tests.

1. Leave v1 interpretation unchanged. v2 uses the confirmed master identity fields and snapshot registry; do not consult the mutable database identity-link side path.
2. Treat catalog identity as its existing three-string JSON identity. Validate language/platform compatibility and one-to-one master/catalog claims.
3. External IDs remain exact strings. Require source system, theater, supported ID type and explicit scope; empty/unknown scope cannot mean a global ID. Only unique confirmed mappings to one extant compatible drama are eligible.
4. Pending, inactive, conflicting, duplicate, multi-target and dangling relations must not be resurrected by legacy title/RSBoost fallback. Conflicting direct CPS links and registry mappings remain ambiguous.
5. Only explicitly drama-grained income is attributable. Keep account/platform/unknown rows unallocated; preserve source lanes, currency and amount-basis constraints.
6. Include the used mapping/master and CPS source references in evidence. Mapping audit text/URLs are data, never commands and never automatically fetched. Historical evidence remains frozen when a mapping changes.

## Task 4 — Initial controlled population

1. Read only the fields needed for identity resolution from current Feishu and the current authorized catalog.
2. Trace the provenance of existing SD bindings before using them. The upstream `toPostedRows` implementation derives them from title keys with platform/language filtering, so even unique SD bindings are pending candidates, not independent confirmation. Reject competing claims and never confirm from title resemblance.
3. Populate registry keys from actual external IDs as pending when there is no reliable identity proof. Use conservative explicit account scope when global uniqueness is unproven.
4. Before a write, check current target values and preserve existing user edits. Confirm only evidence-backed links; retain unknown/conflict rows for review.
5. Read back changed fields, compare receipts, and verify a new feedback snapshot uses the same mapping version. Do not add source data to Git.

## Task 5 — Integration verification and delivery

- Test v1 reconstruction/hash and frozen candidate equality after publishing v2 and changing a mapping.
- Test sixteen-table completeness, schema transition refusal cases, ID leading zeros/namespaces/scopes, conflicting states, manual grain, evidence and absence-vs-zero.
- Keep ordinary model projection benchmark and strict result-item contracts unchanged. Add user-facing warning labels only for newly introduced mapping states.
- Upgrade the managed extension through the official manager, verify SQLite/PostgreSQL and the relevant frontend/HTTP path, then perform independent reviews.
- Commit/push/deploy the approved integration through the current project guard and CI workflow. Preserve existing production feature/schedule flags; distinguish schema creation, verified mappings, code release and enabled feedback in the final report.

## Completion evidence

- [x] Feishu schema created and read back; actual mapping table ID recorded privately and in the fixed source registry. Existing field definitions were unchanged; new links and four display-only formulas were verified.
- [x] Backward-compatible v1/v2 scan and schema baseline transition verified. Fixed v1 golden hash and two private historical snapshot JSON/hash comparisons passed; v2 raw values survive adapter, scan and SQLite/PostgreSQL reconstruction.
- [x] Confirmed identity and revenue attribution rules verified without fallback leakage. Independent specification, Python and code/security reviews approved; full feedback suite passed on both databases.
- [x] Initial mappings/remaining unknowns recorded with evidence, without overwriting existing data. Candidate identities and real external keys remain pending; native sixteen-table double-scan readback verified the writes and deliberately produced no unearned confirmed attribution. Detailed quantities/receipts remain private.
- [x] Managed copy, local/CI checks, independent reviews and deployment completed with explicit scope limits. PR #47 merged, the guarded gateway and frontend release passed runtime/source/alias verification, and the authenticated disabled-state UI was checked. See the [release record](../pick-workbench/releases/2026-10-08-feedback-identity-mapping.md); production owner activation and scheduling remain outside this release.
