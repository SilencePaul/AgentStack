# External integration contract

The public repository contains Router adapters, not the organization-owned ai-crew distribution. Obtain an authorized CLI installation separately. Configure its script path and gateway base in local configuration; never commit credentials.

Required commands: `mm-get PATH --scrub`, `mc-get PATH --scrub`, `say CHANNEL TEXT --root POST_ID`, `issue-status ISSUE_ID STATUS --workspace WORKSPACE`, and `issue-comment ISSUE_ID TEXT --workspace WORKSPACE`. GET responses must be JSON `{"ok": true, "data": ...}`; writes must return `{"ok": true, ...}`. Nonzero exit or `ok != true` is failure. See router/mm.py and router/multica.py for exact payload handling and pagination. The issue-status and issue-comment extensions may not exist in the stock CLI; an administrator must provide a compatible version before enabling Multica writes.

Use separate bot and personal credential caches. No tokens are inherited by execution workers. Local Windows user access still applies; this is not an OS sandbox.

Start with config.example.json for local execution. Optional `mm` configuration requires `script`, `base`, `bot_id`, `channels`, and `allowed_user_ids`. Optional `multica` configuration requires `script`, `base`, `workspace`, `project_ids`, and `expected_user_id`. Do not enable connectors until identity and isolated test scope have been verified.
