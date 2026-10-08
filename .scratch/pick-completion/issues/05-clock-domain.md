# T5 follow-up: explicit clock domains across agent and query deadlines

**Parent:** T5 / D10 / D11
**Status:** done

Actual browser harness on macOS/default uvloop reproduced an immediate first-tool timeout with a fresh60s run: loop.time and time.monotonic have different epochs. Host publication, PickTask and the Lark worker use monotonic; HTTP/query/SQL/planning async deadlines use loop time. Do not compare or pass them interchangeably.

completion_t5 owns separate codex/pick-completion-clock worktree. Prefer a minimal explicit conversion at the agent/query boundary, retain one absolute per-call cap and ordinary reserve, audit all deadline producers/consumers, and document domains. Do not force the product to use asyncio or enlarge budgets. Add offset-clock and real uvloop process regressions, actual scripted Gateway/query/SQL behavior plus ordinary asyncio parity. No providers/production calls. Independent code/Python reviews and merger required.

## Final integrated verification

Clock-domain follow-up merged at 98c6dbc11aef9879b144b1c7250140dba51ed76d after independent reviews. Offset-clock regressions and real asyncio/uvloop HTTP, PostgreSQL and cancellation probes passed. The actual default-uvloop ordinary-cookie browser journey subsequently passed (browser-default-loop3); no product event-loop override is used. Installed-package browser journeys also passed. Evidence is retained in /Users/wzb/.gstack/projects/bytedance-deer-flow/implementation-pick-completion-20261008/browser-report.md and the clock-domain reports. T5 implementation is complete; whole-task model, accessibility and release gates remain separate.
