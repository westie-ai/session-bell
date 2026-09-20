# Desktop observer, approved 2026-09-20

Keep the desktop's native app-server and MCP configuration untouched. The prior
shared-desktop override passed initialization but failed `thread/start`, and has
been removed. Never reinstall that override.

## Implementation plan

1. Read the local thread index in SQLite read-only mode. Validate each rollout's
   session metadata (`originator = Codex Desktop`) and containment under CODEX_HOME.
2. Incrementally consume complete JSONL records with persistent byte offsets.
   Establish a silent initial baseline, ignore history for notifications, retry
   incomplete lines, and rebuild silently on truncation/replacement.
3. Project starts, completions, interruptions, replies and cumulative token counts
   into existing SessionBell state. Never infer a completed task from elapsed time.
   Unknown/malformed input cannot approve actions or resume a thread.
4. Retain the shared CLI bridge for its own sessions. Desktop observations are
   explicitly read-only; do not register them as managed shared-service sessions.
5. Display desktop source and per-session tokens on iPhone, and disable unsupported
   arbitrary follow-ups. Existing Claude and managed Codex controls remain intact.
6. Add minimal native permission hooks only through normal hook review/trust.
   Real desktop approvals need user-assisted validation; no automatic trust and no
   hidden desktop transport override. Structured questions and arbitrary desktop
   message submission remain unavailable until a genuine control path is verified.

## Acceptance

Notification policy confirmed by the user: Codex completion always sends a push,
even while the Mac is in use. Keep Claude and permission/attention idle policies
unchanged. Record skipped alerts and APNs HTTP results without private contents
or credentials. Historical baseline/restart suppression remains unchanged.

- Fixture regressions: silent baseline/restarts; incremental completion once;
  partial/malformed lines; truncation; non-desktop sources; path validation;
  independent turns; errors/interruption; token counters without double-counting.
- Read real desktop records with outputs limited to counts and status, not prompts.
- Install only the local relay and phone test build, preserve public assets.
- A task started in the normal desktop appears on the phone and updates when done.
- Do not describe desktop approvals or notifications as verified without checking
  their actual desktop/phone behavior. Native application remains usable throughout.
