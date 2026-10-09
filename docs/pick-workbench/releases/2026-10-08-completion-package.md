# Completion package candidate — 2026-10-08

## Final reviewed refresh and legacy-detail repair

The final source includes reviewed fix `5519fdb7ce2aa11b28a8d9f1213357371520d285`.
The official manager refreshed the independent final-package worktree; source,
managed snapshot and physical noneditable installed package now share digest:

`sha256:cd6ad51fd738ce52833ed94418e351bf951f0a2a68752fca5a4b678db1dc8f72`

All478 tracked source-package files equal the managed snapshot, and all192
runtime paths and per-file hashes match across the three layers. The additional
runtime file relative to the earlier191-file package is `query_facts.py`.
Package version remains0.3.0; backend dependency declarations and lock bytes are
unchanged. The complete782-file host/API/app hash ledger and frontend tree are
unchanged from reviewed source `60fb648261f45d57ae8fed257cb347d1f2a3df2f`.
Private migration ancestry remains0001–0011, with installed initialization at0011.

The full source run exposed a legacy-detail regression introduced by the
optional historical-source supplement: deleting an old result's import-batch
row caused the detail tool to replace otherwise readable frozen content with
an error. The approved repair preserves the authorized frozen item and null
provenance, adds an explicit historical-source-unavailable notice, and omits
unsupported source facts. It does not replace the historical pin with current
data or waive owner/item validation, deadlines or cancellation.

The actual installed package reproduces this legacy-null scenario through the
real detail tool and result/list HTTP routes: all three return null provenance,
the stored item stays unchanged, no episode fact is emitted, and a wrong item
is rejected. Its90 loaded extension modules resolve physically within the
same site-packages root. Installed old/new query, plan, preview and immutable
export operations pass, and frontend schemas decode a preserved legacy fixture
plus five installed HTTP/runtime records together.

Final validation is deliberately recorded as composite evidence:

- Source broad run before the legacy repair:5380passed,29skipped,1failed.
  The unchanged original failing module reproduced13passed/1failed under a
  clean test environment. After the approved repair,11 affected full modules
  covering legacy detail, historical pins, owner/reference rules, deadlines,
  query consistency and managed parity passed173tests,0skipped.
- Host broad run:18344passed,169skipped,3deselected,159failed. The test invocation
  accidentally inherited the manager's dedicated configuration and extras;
  failure traces show its PostgreSQL-without-DSN, pick-auth and sandbox settings
  overriding unit fixtures. Removing those manager selectors in an isolated
  test environment passed all17 affected whole modules,997tests. No host code
  changed to repair the invocation. Original logs and failed-node lists remain.
- Strict blocking-I/O149passed; frontend3101passed/52gatedskips; frontend lint,
  typecheck and default Turbopack build passed; original projection benchmark
  passed `--require-target`. These bytes are unchanged by the narrow repair.
- The first broad runs began on60fb6482 with the generated snapshot, then a
  snapshot-only commit advanced HEAD toed27a6db while they ran. Source, host and
  installed bytes did not change. Post-repair affected checks ran on a stable
  checkout; this is not presented as one pristine all-green broad invocation.

The required new-digest installed browser rerun passed: checked chat through
selection, revision-bound plan and CSV65.252s; published review and explicit manual
association3.848s. Both Gateway starts verified the physicalcd6 package digest,
79 initially loaded extension modules, default uvloop and CA-validating TLS.
The782-file host manifest matched after the first start and in the post-run
checkpoint observing the second start; these timing points are recorded rather
than called atomic checks at process launch. The separate192-file runtime ledger
proves whole-package assembly. The successful harness exited0 and cleaned its own
processes, database and roles, retaining the shared test cluster. Models were
scripted; no provider or Lark call occurred. Native VoiceOver is still unverified.

The previous final-package digest `1ebd5924…` and its192 installed code files,
package metadata and proof ledgers were archived before the official refresh.
The original T10 checkout and `996ce8e9…` package below remain separate retained
artifacts. Real-model acceptance used that original996 package: the40-attempt
allowance was exhausted, and candidate publication results were17incomplete,
2partial and1confirmed. Full semantic acceptance was not met. Those results
are not relabeled as evaluation of this repaired package. Additional proposed
model runs have no authorization yet. Native VoiceOver remains unverified.
There is no production deployment or production migration in this record.

## Original T10 artifact and retained evidence

This is local package/assembly evidence. It does not record a deployment, a
production migration, a live-provider acceptance run, or native screen-reader
verification. Final browser harness review and whole-task model/accessibility
gates are tracked separately.

The official extension manager upgraded the assembled source from an isolated
worktree, using uv0.11.1, an independent backend virtual environment, and private
scratch configuration/home/cache. No source files were copied manually into the
managed snapshot. The only source-test setup correction makes the documented
root pytest command resolve the unpackaged host `app` from this checkout.
Backend dependency declarations and uv.lock remain unchanged because this is a
code-only refresh of ggwork-pick0.3.0.

Source, managed snapshot and the actual noneditable site-packages installation
have the same runtime package digest:

`sha256:996ce8e9d15f5455efa82285460dfed30a65cc9087af88c98d34ca487bb6a730`

Every runtime file's name and SHA256 was also compared separately. The entry
point remains `ggwork-pick = ggwork_pick:install`. Each private migration chain
is continuous0001–0011, and installed-only SQLite initialization reached0011.
The host and private migration histories remain independent. A neutral `python
-I` probe excludes customization/test imports and verifies site-packages paths;
the host's unpackaged `app` uses only the explicit backend root.

Installed-only operations preserved a strict legacy candidate record while
running current query, plan, preview and immutable CSV export operations. The
frontend's actual schemas decoded a preserved legacy fixture and five installed
runtime records together: candidate HTTP response, query projection, plan,
preview and export receipt. The HTTP response boundary supplies run status;
internal candidate service views are not substituted for that public DTO.

The installed browser probe used the same digest, default uvloop and the actual
CA-validating QueryReader.from_env against an owned TLS PostgreSQL fixture.
All loaded ggwork_pick modules were confined to the same site-packages root.
Its two journeys passed: checked chat → candidate/save → plan/revision/CSV, and
actual post review → explicit manual plan association. Fixtures and scripted
models were local; no live provider or Lark service was invoked. Test-only TLS
transport injection was not used for this installed-browser evidence.

## Reproducible verification environment

Use an owned worktree/environment and a scrubbed child environment, never a
symlink to another checkout's backend/.venv. The manager always targets the
selected checkout and ignores UV_PROJECT_ENVIRONMENT. Pin uv0.11.1 before it
runs. Select only synthetic/disposable database descriptors; never run the
production deploy-guard CLI as an offline candidate check.

The test PATH must contain this worktree's backend/.venv/bin, the pinned uv bin,
node/pnpm, PostgreSQL client tools, and standard system bin/sbin directories.
Launching a venv Python by absolute path does not make literal `python` calls
inside subprocess tests use that environment. Likewise, missing psql/pnpm is a
fixture setup failure, not a business test pass. The source conftest now resolves
backend/app without relying on ambient PYTHONPATH. Installed-mode probes must
continue to exclude the customization source path.

The artifact verification runs importlib metadata/path checks, per-file hashes,
managed parity, installed migrations and mixed DTO decoding separately. Source
pytest conftest intentionally selects source, so its pass cannot replace the
installed-mode check. The deployment guard's pure local migration-chain check
was exercised without fetching main or opening a production connection.

## Recorded local gates

- Source extension broad run:5278passed,30skipped,6failed,55setup errors. Missing
  psql/pnpm in the initial scrubbed PATH caused60of the61failed/error cases;
  corrected-path focused retry passed those60. The remaining inventory failure
  identified the real completion privacy-runbook gap below. No failing case was
  waived. The initial logs remain part of the evidence.
- Completion privacy runbook after its repair:29passed,0skipped, including every
  newly protected JSON field, plan title, BOM/invalid-UTF8 CSV and0004 schema
  compatibility. The initial broad source skip for missing sha256sum was also
  closed by including the system sbin directory. Other explicitly gated broad
  skips are not represented as passed acceptance gates.
- Backend offline broad run:18488passed,174skipped,3live cases deselected,1failure.
  Its shell test could not find the venv's `python`; the complete affected module
  passed57cases with the correct PATH. This is composite verification, not a
  claim that the original broad invocation was all green.
- Separate strict blocking-I/O suite:149passed.
- Frontend broad suite:3094passed,52gated skips,0failed. The later five-file
  control-style merge passed24affected component cases. Frontend lint/type check
  and the default Turbopack production build passed with an owned frozen/offline
  dependency installation; an external node_modules symlink was rejected during
  setup and replaced with that local installation.
- Original model-projection benchmark passed `--require-target`; its golden
  fixture bytes were unchanged. Installed-mode schema/path/digest proofs and the
  real TLS browser journeys are separate evidence from this benchmark.

## Privacy runbook compatibility

The schema inventory test found nine new completion JSON columns not covered by
privacy inspection. `pan-check.sql` now detects their contents, plan titles and
immutable CSV bytes, with58total locations and19detect-only protected locations.
Binary matching uses the existing escaped-byte pattern, so it does not assume
that CSV storage is valid UTF8. Output remains location/count information.

The generic redactor does not alter these new fields. Rewriting frozen source
facts, original command/preview/export/link receipts, link bases, or CSV bytes
would falsify provenance or invalidate the recorded SHA256. Actual PostgreSQL
tests preserve the original JSON text, byte sequences, receipt hashes and
records before/after redaction. Protected hits require owner-scoped incident
handling or a separately authorized replacement/quarantine/revocation plan;
generating a new draft/export does not remove historical hits. No production
SQL or data-cleanup operation was performed for this repair.

The protected artifact tables still have no public/browser/reader grants.
Managed tests were refreshed through the official manager after the source-test
repair; runtime package bytes and the installed-browser digest did not change.

## Subsequent accepted follow-up

The later user-authorized seven-case real-model verification and frontend-only version disclosure are recorded in [completion follow-up](2026-10-08-completion-followup.md). Those selected cases satisfy20/20 criteria; the original40 records remain unchanged. NativeVoiceOver is user-deferred, not passed. Backend artifact cd6 is unchanged; model runs used3e4540e and later rendered display usedf2df7b32. Worktree cleanup preserves archived artifacts and the self-contained integration environment. Earlier pending statements above describe their historical checkpoint, not an additional unconsumed model allowance.
