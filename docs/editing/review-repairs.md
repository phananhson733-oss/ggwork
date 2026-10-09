# User-review repairs after d88364c2

The subsequent user-requested review found four reachable bugs that the earlier
acceptance cases did not cover. All four received a failing regression before the
fix. Tests use the shared HTTP/tool boundary, real temporary SQLite/PostgreSQL,
real native probing of existing synthetic media and actual owner Skill storage.
No production data or additional paid model request was involved.

| Finding | Observed failure | Repair and regression |
| --- | --- | --- |
| Attempt cancellation leaked after stop | Four SQLite/PostgreSQL normal/lost-ACK cases left the next valid directory waiting with HTTP 422 | Terminal/acknowledged attempt cleanup releases only its cancellation event after native processing exits. The same WorkerSession prepares the next task; local shutdown and actual owner revocation stay set. |
| Removing a broken directory file lost provenance | Both databases left the remaining valid file waiting with `source_receipt_missing` | Preserve directory provenance only for an exact subset of native-discovered identities on the same device and grant. Added IDs, names, episodes, paths, sizes, grants and device changes cannot inherit it. An explicit new-device directory request discards the old manifest and discovers anew. |
| Unconfirmed plan accepted output-only retry | Both databases returned HTTP 200 then claimed an unconfirmable rendering attempt | API and `clip_retry` require a confirmed reusable plan for output-only retry; otherwise return a conflict directing `stage=planning`. The full planning retry → new plan → explicit confirmation path succeeds; confirmed output retry retains its approved plan identity. |
| Multiline plugin alias lost activation | Six real owner-storage newline/tab/nonbreaking-space cases returned no Skill | Normalize only the exact whitelisted first token using the same whitespace boundary as the shared slash resolver. Both profiles, spaces/no-argument calls and the actual PickModelGate pass; spoofed providers/path suffixes remain rejected. |

Final canonical extension suite: **319 passed, zero skips**, including both
SQLite and disposable PostgreSQL plus real FFmpeg/whisper test assets. The
tool/Skill suite passed **38** tests. These counts overlap; do not add them.
All four original reviewers independently rechecked source `27bae0d9` and approved
their respective fixes with no new bug finding. Distribution/CLI/guidance tests
passed **22**; blocking-I/O passed **149**. Guidance against the pre-repair
`d88364c2` baseline reported zero errors and zero warnings. Ruff check and format
passed for all 38 canonical Python files. No frontend source changed; the directory
repair reproduces the existing browser payload through actual HTTP/native code.

The new paired development wheels were installed into a fresh **13-package**
native environment. All **25** installed editing Python files matched the source,
including the updated cancellation cleanup; no FastAPI, SQLAlchemy, LangChain or
harness package was installed. The real native CLI doctor loaded the pinned model
and returned ready with a synthetic grant. Bundle contents are both wheels,
native README, SHA256SUMS and safe install/doctor evidence:

- Editing wheel SHA-256: `92e7e56921616ca391ff0d96456bd2ddd8bc659d963bb3bafa2105df72b2c717`
- Matching extension API wheel SHA-256: `c54f6d145b3c439284db77bc5eccd284d55c9b4c327fc423817b9355e5330da1`

These unsigned development wheels retain their existing version numbers. Stop
the foreground worker and use `pip install --force-reinstall` with **both** local
wheels, or create a fresh virtualenv. A plain same-version installation may skip
the fix. Preserve the existing worker state home and restart with it; do not rerun
setup or remove grants, receipts, journals or artifact indexes. See the
[native guide](../../customizations/ggwork-edit/ggwork_edit/worker/README.md).

The four source fixes do not change the one-Gateway-process relay restriction,
cloud text-only boundary, source/output preservation or the recorded eight paid
acceptance calls. Previous functional/content evidence remains historical proof;
this repair run did not claim a new live provider/browser round. The known
unrelated feedback CI race is outside this repair scope.
