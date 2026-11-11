# AgentStack Workbench Implementation Plan

> Execute inline against the user's approved workbench design.

Goal: Extend the existing local Windows monitor to an interactive task workbench with isolated concurrent Team tasks, public execution events, answers and explicit approval.

Architecture: Retain SQLite tasks, phase workers and MM outbox. Task execution snapshots gain isolation and per-Team slot limits. A loopback HTTP service accepts authenticated same-origin operator commands. Persist questions, approvals and their decisions in the same transactions as state transitions. MM and web share these transitions.

Tech stack: Python standard library, SQLite, Git, existing HTML/CSS/JavaScript.

## 1. Persistent concurrency and workspaces
- Add per-Team concurrency/isolation settings and preserve serial behavior for historical snapshots.
- Allocate a unique Git branch and worktree per task, preserving its directory across question continuations. Non-Git projects use explicit isolated copies.
- Claim tasks transactionally within global and Team limits. Isolated waiting tasks release slots. Failed isolated tasks do not pause sibling tasks.
- Test concurrent claims, dirty source preservation, workspace reuse and legacy serial tasks.

## 2. Human controls
- Persist execution approval as waiting state before scheduling; confirmation binds the original goal snapshot.
- Add structured confirmation questions alongside existing question receipts. Rejecting ends the task; answering resumes via existing checkpoints.
- Persist command receipts for retry idempotence. The local workbench operator can answer MM-origin tasks, with an explicit audit event.
- Test stale questions, duplicate commands, rejected approval and MM/web races.

## 3. Workbench service and events
- Add same-origin POST with a process-random token obtained from the local page API. Retain loopback and Host restrictions.
- Expose safe task details and cursor-based public event stream, strip reasoning blocks and redact credentials before persistence.
- Add safe diff display, original source and execution/session history.
- Test HTTP authorization, payload bounds, unknown actions and event filtering.

## 4. User interface
- Keep Team navigation, add all parallel task cards and task creation, Team slot settings.
- Task detail contains public live activity, role/session information, goal, workspace/branch, diff and final report.
- Stable question/approval forms survive polling. Show connection loss and stale state clearly.
- Exercise actual browser interactions against local fixtures.

## 5. Delivery
- Run full regression plus meaningful process and HTTP tests.
- Build an updated Windows overlay with the pilot channel preset, workbench launcher and upgrade instructions.
- Extract ZIP and run packaging verification. Record that Windows/Qoder live deployment requires on-machine trial.
