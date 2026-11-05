# Team dashboard

Design references (layout ideas only; no third-party code copied):
- https://github.com/manish-raana/openclaw-mission-control/blob/main/docs/FEATURES.md — team roster, central task list and activity inspector.
- https://github.com/glglak/agent-mission-control — role visibility and team visualization.

Implemented with the existing standard-library HTTP server and native HTML/CSS/JavaScript. Four summary metrics, registered project teams, role progression, worker inspector, searchable/filterable task history. Projects are logical teams; they are not persistent agent processes. Active counts require a fresh Router heartbeat and are observations from its ledger, not independent OS process probes. Stale heartbeat produces unknown counts rather than false zero/online states. Resolved incidents remain historical but are excluded from attention totals. No raw tool arguments are included in overview JSON.

Validation: 113 Python tests passed; Edge/Playwright verified team selection, four role details, result retrieval, search, filtering, desktop and 390px layout, and absence of JavaScript errors.

## Team ring

The team diagram places the Leader at the center with developer, reviewer and tester around it. Positions indicate team membership, not mandatory chronology. Participation counters and the ordered collaboration trace are derived from role_started events, including revisits. Failed terminal roles are distinguished from returned phases. History stays immutable.

Latest validation: 121 Python tests passed, including bounded rework, clean retry, checkpoint budget preservation and team-ring observations. Real Qoder recheck of two repositories completed with eight independent roles and one MM consolidated reply. Both passed without requiring rework this run; loop behavior was exercised by deterministic tests. Business repository changes and deployment-specific credentials remain local.
