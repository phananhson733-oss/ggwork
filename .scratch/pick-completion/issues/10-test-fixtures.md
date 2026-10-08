# T10 prerequisite: repair extension test fixture regressions

**Parent:** T10/T11 integration gates
**Status:** done

External `extension-suite-triage.md` reproduced all eight failing/setup nodes as passing at original d62364ed on the same temporary PostgreSQL environment. These are not waived baseline failures. completion_baseline owns a separate codex/pick-completion-test-repair worktree; original baseline/candidate evidence and model budget stay untouched.

- Restore the observer fixture's intended minimal schema and restricted-role semantics. Do not grant application roles CREATEROLE or turn the observer test into a privileged test. Preserve explicit full-schema support for newer shared-query fixtures without changing legacy helper defaults silently.
- Correct the timestamp storage guard's false positives for date-only serialization while retaining verification of UTC microsecond timestamps written to storage. No whole-file waiver or weakened production code.
- Verify repaired observer/storage nodes plus shared-query/migration consumers on SQLite and the authorized disposable PostgreSQL cluster. Independent code/Python/DB review and merger required.

Lark worker deadline is a separate T5 child. Managed source snapshot drift is expected to close through the official T10 extension-manager synchronization; neither is claimed fixed by this test-only slice.

Completed in ef75d9b7 after original nested code/Python/DB reviewers independently sealed10fixedfile hashes. Exact peer-owned reports/approval JSON and handoff index are preserved externally; initial missing immutable receipts were obtained before any merge. Merger159observer/guard/fullqueryfixture/currentconsistency tests passed without skips. Lark and managed package remain separate open gates.
