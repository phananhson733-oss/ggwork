# Local issue tracker: pick completion

Integration branch: `codex/pick-completion-20261008`.

Status transitions: ready-for-agent -> in-progress -> done. A ticket becomes done only after its acceptance evidence is recorded and its tested commit is merged into the integration branch. Missing external acceptance leaves the ticket open with its blocker. Original T1–T12 and design DT01–DT08 remain in the approved spec; no requirement is waived by restructuring the graph.

T1 -> T2 -> T3 -> T4 -> T5 -> T7 -> T8 -> T9
T1 -> T6 -> T12
T9 + T12 -> T10 -> T11

T11 baseline phase starts after T1 locks20 prompts and before implementation changes; final phase follows T10. Both phases together have a hard40 Agent-run limit, all attempts retained. Deterministic/API/browser tests are separate from model runs.

Shared file ownership follows the approved lanes. All implementation ticket branches merge the latest integration before reporting done; only the merger updates integration code. Root owns tracker state. No remote issues are created by this tracker.
