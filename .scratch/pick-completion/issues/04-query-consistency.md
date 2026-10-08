# T4b: Canonical query facts and predicate consistency

**Parent:** T4
**Blocked by:** Integrated T4 core/UI; implementation ready
**Status:** in-progress

Confirmed audit: external CTX/query-provenance-audit.md at c0290816. Same owner/pin canonical facts are preserved in responses but eligibility/source/exclude-selected SQL assumes only raw RealShort semantics. Preserve all existing production-feed behavior and unknown values; repair the broader accepted paired state without changing frozen QA identities or claiming production regression.

- Derive canonical identity/source/confirmed-eligibility key sets from the same owner-checked immutable catalog pin. Apply predicates before counts/facets/page ordering. Raw explicit delisting/denial must win; raw youtube boolean must never certify active or allowed.
- Preserve source identity namespace and owner-specific selected exclusions. Reject ambiguous unsupported mapping rather than silently merging identities or manufacturing zero.
- Keep legacy status/notice refusal compatibility while retaining safe bounded QueryFailure code/retryable across common tool/Selection adapters. No raw SQL/auth/exception bodies, new model calls or budget changes.
- Add actual PostgreSQL API/tool regressions for returned-fact vs filtered-count coherence, facets/pagination, scope/source/selected identities, unknown/denied/delisted and fixed version. Re-run existing production-shaped parity and projection/deadline checks.
- Coordinate tools.py ownership with T4a: this child owns only error adapter handling; T4a owns reference binding/selection. Separate worktree; merge latest integration and obtain code/Python/DB reviews before merger.

Candidate fixture concerns remain separate: baseline raw mirror metadata and status spellings were synthetic and did not traverse production G2–G9. Any QA normalization needs its own before/after receipt and cannot rewrite baseline or original 20 questions.
