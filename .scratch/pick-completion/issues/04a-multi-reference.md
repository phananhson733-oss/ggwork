# T4a: Explicit immutable two-result chat references

**Parent:** T4/T12, original P1 multi-result requirement and frozen D9-16
**Blocked by:** T4, T12a
**Status:** in-progress

Deliver the second stage already required by the approved spec: after visual comparison, the user explicitly chooses two result/item groups for a chat turn; server validates same owner/current thread and each immutable identity/version. Do not feed a raw legacy refs array into the current single-reference protocol. Preserve legacy single-reference behavior and make regenerate/edit/branch handling explicit. Comparison reads each result's frozen evidence; never mix their source periods into a live query's one-pin semantics or use a newer batch silently. A normal query requiring an ambiguous base must require an explicit choice rather than infer one.

- [ ] Versioned plural input contract, bounded to two explicitly selected results/items; forged foreign, wrong-thread, invalid item, duplicate/removed result rejected without existence leakage.
- [ ] Each referenced result's evidence/source timing survives into actual model tools and checked final with unambiguous citations; D9-16 is supported through the true request path.
- [ ] Single-reference callers/history remain compatible; restore/regenerate/edit use original turn refs; cross-thread branch must not copy invalid refs.
- [ ] UI context selection and trusted server validation are both tested; visual comparison does not implicitly modify chat refs.
- [ ] Deterministic runtime/browser regression before candidate real-model phase; no extra provider runs outside D9.

Ownership allocated when frontier opens: backend reference parsing/context/tool read changes plus focused frontend reference serialization/UI explicit action. Coordinate with T12, preserve sharedquery and publication invariants. This is an implementation split of existing accepted scope, not added feature scope or a changed model-test budget.

Integration T10 additionally depends on T4a. No completion credit for visual-only comparison.

Author implementation underway in codex/pick-completion-t4a from verified T4-UI7838e1ca. Strict plural input, per-snapshot readonly UI refs, frozen replay context, and authenticated HTTP/runtime/mirror/browser tests are implemented; independent reviews and root integration merge are required before closure.
