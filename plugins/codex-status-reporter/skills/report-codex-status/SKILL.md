---
name: report-codex-status
description: Report agent task lifecycle events to a status bridge without changing task behavior. Use for tasks that should appear on an external status panel; do not use for ordinary questions that have no task lifecycle.
---

# Report Task Status

Use the plugin's `scripts/status_reporter.py` as a best-effort side channel. Reporting must never delay, fail, cancel, or change the task itself.

- When substantive work begins, report `running` with a concise title.
- Report `waiting` only when user input or approval is genuinely required.
- Before the final response, report `success`, `warning`, or `failure` to match the actual outcome.
- Reuse one task identifier for every event in the same task when the host exposes one. Otherwise let the reporter derive a fallback identifier.
- Never retry a failed report, inspect credentials, or treat reporting success as part of task completion.

Invoke the reporter with the active Python runtime and the script path relative to this skill: `../../scripts/status_reporter.py`. Supported states are `running`, `waiting`, `success`, `warning`, and `failure`.

The transport is host-neutral. `STATUS_BRIDGE_URL` selects a local or remote receiver, `STATUS_BRIDGE_TOKEN` optionally authenticates it, and `STATUS_SOURCE` identifies the originating tool.
