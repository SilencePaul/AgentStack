---
name: agentstack-implement
description: Implement an assigned AgentStack team task using its immutable goal, project rules and previous role reports. Use in the developer phase for scoped code changes and defect fixes.
---

Read the assigned project's conventions and current changes before editing; preserve unrelated work. Translate the assigned goal into observable acceptance. For a defect, reproduce it first and distinguish environment failures from application defects. Make the smallest coherent change and run applicable formatting and checks from this repository's own scripts.

Use native enabled tools. Do not write simulated tool calls into text. A missing command or a nonzero tool result is evidence to diagnose, not success. Do not invoke parallel agents, deploy, push or operate another project's files. If scope or a required decision is missing, use the supervisor's needs_input receipt.

Report changed paths, actual commands and results, and remaining uncertainty. Do not provide hidden reasoning. End development with a concrete handoff to reviewer/tester. A passing command proves only what it checked; do not claim business acceptance from a process exit code.
