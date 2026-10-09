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
The independent follow-up review and refreshed development-wheel proof will be
recorded in the [delivery ledger](delivery-ledger.md) before PR handoff.

The four source fixes do not change the one-Gateway-process relay restriction,
cloud text-only boundary, source/output preservation or the recorded eight paid
acceptance calls. Previous functional/content evidence remains historical proof;
this repair run did not claim a new live provider/browser round. The known
unrelated feedback CI race is outside this repair scope.
