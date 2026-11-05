# MM operations implementation plan

**Goal:** Validate role configurations and operate/diagnose AgentStack from MM, with a separate coding team limited to AgentStack.

**Architecture:** An independent operations process polls MM and reads business diagnostics even when the business router is down. It owns one configured router child and one separate maintenance queue/workspace. Existing business project isolation and acceptance remain in force.

**Scope confirmed:** Maintenance only edits AgentStack. No automatic deployment of maintenance changes, no blind replay of interrupted developer work. Tool restrictions are CLI restrictions, not an OS sandbox (Bash can access the host).

## Steps

- [ ] Add validated per-role tool lists, CLI enforcement and actual startup inventory checks; keep Leader tools empty.
- [ ] Add independent /ops status, start, restart, repair, show/report and answer commands. Control actions are durable, deduplicated and processed outside MM transactions. Only owned processes may be restarted; active business jobs block restart.
- [ ] Configure all registered teams with explicit tools, skills and role instructions; snapshot previous configuration locally.
- [ ] Run automated regression and real configured-team smoke including coding, review, testing and MM reports.
- [ ] Exercise MM diagnostics while business router is stopped and restart it; verify separate maintenance coding task and document limits/evidence.

## Checks

Unauthorized messages and unrelated channels are rejected by Poller. Duplicate messages cannot repeat a restart or task. Restart never kills an unowned PID. No raw credentials or reasoning in MM reports. Skill and tool inventories must match launch restrictions. Repair tasks have a separate workspace/runtime and cannot deploy themselves.

## Scope extension and verification ledger

User additionally requested MM-authored teams/rules/skills and webhook routing, and selected a simulated GitLab MR first. Added tool-free configuration drafting with same-thread owner confirmation; approved drafts are versioned. Added loopback authenticated MR ingress, exact-SHA deduplication and MM notification. Auto-merge and remote GitLab hook registration remain explicit unsupported operations pending separate credentials/policy implementation.

Local automated suite: 175 tests passed. Real Qwen3.8-Flash configuration smoke completed all five phases with expected skills and restricted tools. MM confirmed business-down diagnostics, managed start, natural-language team draft/apply, webhook draft/apply, maintenance coding/report retrieval. Local HTTP repeated event returned one task; bad token returned403. Maintenance Qoder added exactly one test file in separate checkout; independent rerun passed3 tests. Remaining: live business checks/question continuation, final service reload and managed restart verification.

Review fixes: transactional paused/shutdown check prevents claim/stop race; runtime overlap rejected before opening DB; stop no longer mutates paused; partial role updates preserve unspecified identity/rules/tools; webhook revision checked before update. Live startup uncovered inherited PSModulePath incompatibility; Windows DPAPI subprocess now uses default module search. Live delivery uncovered dot in control notice dedup key; transport-safe key regression added.
