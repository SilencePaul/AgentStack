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
