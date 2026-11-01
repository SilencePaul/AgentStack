# Multi-project Router approved design and implementation plan

Goal: User gives goals; a Router delegates to independent project teams, aggregates reports, and escalates issues. Deterministic monitoring limits Router load.
Architecture: Python/SQLite durable queue and outbox; one active task per project, configurable global concurrency (default 2); isolated Qoder processes, project directories and versioned rule/context snapshots. Existing v4.1 stays separate. Multi-project service is explicitly started, never consumes historical work automatically. No deployment or external changes inferred from task completion.
Rules: immutable common base + project supplements + task goal; shared requirements cannot be relaxed. Reports distinguish model claims from independent checks. Monitor process exit, progress idle, execution deadline; no automatic rerun of potentially side-effecting work. Persist incidents; retry message delivery only with deduplication.
Current scope adds tool-free natural-language planning, bounded format correction, durable clarification follow-up, immutable original goal constraints, atomic cross-project dispatch and consolidated reporting. Ambiguous project selection does not execute work.
Tech: Python 3.13 standard library; existing installed Qoder CLI in headless mode with native asynchronous delegation disabled and four supervised independent role sessions; MM transport through ai-crew CLI using a separate runtime and bot credential. User personal credentials remain separate.

Implementation tasks:
1. store.py: SQLite projects/tasks/events/outbox; atomic claims, dedup input, per-project serialization, immutable project/config snapshot; tests for restart and concurrency.
2. worker.py: Qoder prompt/rules builder, independent process/session/run directory, output evidence, timeout and child cleanup; tests with fake executable. Contract: Worker(command, task, run_dir, on_event); start(), poll()->dict|None, stop(reason); poll result {status,summary,evidence}; on_event(kind,detail). task has id, project_id, goal, project snapshot {path,rules,context,model,timeout,idle_timeout}, rules_snapshot; no automatic retry.
3. service.py/cli.py: Router commands, scheduler, watchdog and reports; no user credential inheritance in workers; persistent notifications; local CLI project-add/submit/status/show/serve.
4. mm.py: ai-crew CLI adapter, source authorization, message routing and idempotent delivery, channel+DM integration; read cursor persisted only after transactional ingestion; network errors visible and bounded retries.
5. Verify fake-worker end-to-end, real Qoder two-project smoke, MM routing, rules isolation and report delivery. Document current limits and startup.

Validation: python -m unittest discover -s tests -v; compileall; temporary test projects; no business repo available yet. Worker failure must not block another project. Restart marks executing work interrupted, never silently replays it.

Ruling after real native-team smoke: Qoder --print exits after starting a native asynchronous teammate and reporting waiting. Replace native async delegation with program-supervised developer/reviewer/tester/lead-report independent sessions; preserve per-project isolation and verify phase outcomes. File condition success alone must not claim native team completion.

Integration expansion: isolated Multica workspace opt-in issue tags trigger idempotent local tasks, claim/result comments and completion status. Compact deterministic remote notifications avoid embedding raw model/tool transcripts; complete evidence remains local. Verify live remote receipts and restart deduplication before reporting completion.
