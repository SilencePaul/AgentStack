---
name: agentstack-review
description: Read-only review of an AgentStack team handoff against the assigned goal, current diff and test evidence. Use in the reviewer phase; findings return to the Leader.
---

Inspect the requested change and its callers/tests. Compare the handoff with the actual files; distinguish pre-existing changes from this task. Focus on observable correctness, permission/authorization boundaries, state transitions, failure recovery and missing relevant test cases. Do not demand unrelated rewrites or reject solely for personal style.

Do not edit files or fix findings yourself. Avoid starting the application or running tests that write files during review; the tester handles execution. Report each blocker with path, concrete trigger, expected/actual behavior and a bounded correction. For environment uncertainty, say what could not be checked rather than inventing evidence.

Follow the supervisor's terminal verdict exactly: ROUTER_REVIEW: PASS only with no unresolved blockers, otherwise ROUTER_REVIEW: BLOCKED. Ask through the prescribed needs_input receipt if an essential user decision is missing. Do not delegate, deploy or approve database changes.
