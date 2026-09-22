# Cursor compatibility research (2026-09-22)

Question: can SessionBell monitor and steer Cursor's agent the way it does Claude
Code and Codex? Short answer: **yes for the editor's Agent and the `agent` CLI,
via Cursor's native hooks; no need for a shared server like Codex.** Two gaps
need a different design from Claude: there is no blocking Stop hook, and there is
no terminal to capture for editor sessions.

Checked against Cursor 3.21.16 on this Mac (no `agent` CLI installed here).

## What Cursor exposes

### Hooks (`~/.cursor/hooks.json`, `<project>/.cursor/hooks.json`, `version: 1`)

Events that matter to us, with the Claude Code event we use today:

| SessionBell needs | Claude Code | Cursor native | Notes |
|---|---|---|---|
| session started | `SessionStart` | `sessionStart` | fire-and-forget; has `is_background_agent` |
| prompt sent (task running) | `UserPromptSubmit` | `beforeSubmitPrompt` | gets `prompt`; can veto with `continue:false` |
| turn finished | `Stop` | `stop` | `status` completed/aborted/error, `loop_count`; **fire-and-forget**, but a returned `followup_message` is auto-submitted as the next user message (`loop_limit` default 5, `null` = unlimited) |
| needs approval | `PermissionRequest` | `beforeShellExecution` (`permission: allow/deny/ask`), `beforeMCPExecution`, `preToolUse` | hook is blocking here; timeout per hook in seconds |
| latest reply text | transcript scan | `afterAgentResponse` (`text`), `afterAgentThought` | pushed to us, no transcript parsing |
| session ended | `SessionEnd` | `sessionEnd` | `reason`: completed/aborted/error/window_close/user_close |
| subagents | `PreToolUse` matcher Task/Agent | `subagentStart` / `subagentStop` | |

Every payload carries `conversation_id`, `generation_id`, `model`,
`workspace_roots`, `transcript_path`, `cursor_version`, `hook_event_name`.
Exit code 2 = deny. Hooks apply to the desktop Agent and the CLI; for cloud
agents only project-level hooks load.

**Claude-format hooks are also loaded.** With "Include third-party plugins,
skills and other configs" (Settings > Agents > Third-Party Imports, on by
default) Cursor reads `~/.claude/settings.json` and maps `PreToolUse`,
`PostToolUse`, `UserPromptSubmit`, `Stop`, `SubagentStop`, `SessionStart`,
`SessionEnd`, `PreCompact`. **`Notification` and `PermissionRequest` are not
mapped.** Consequence: any Cursor user who already installed SessionBell for
Claude Code has our `prompt`/`stop`/`session-end` hooks firing inside Cursor
today with a Cursor-shaped payload (`conversation_id`, not `session_id`), which
we neither expect nor test. Worth checking for spurious sessions on the phone.

### Local state (read-only observation, like the Codex desktop observer)

- Editor: `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`
  (SQLite, WAL). Cursor 3.x has a `composerHeaders` table
  (`composerId, workspaceId, createdAt, lastUpdatedAt, isArchived, isSubagent,
  recency, value(JSON head: name, unifiedMode, hasUnreadMessages …)`), plus
  `cursorDiskKV` rows `composerData:<id>` (`status`, `isAgentic`,
  `generatingBubbleIds`, `usageData`, `stopHookLoopCount`,
  `fullConversationHeadersOnly`) and `bubbleId:<composer>:<bubble>` (message
  text, `createdAt`, `tokenCount`). Verified locally: 92 composers, 2 556
  bubbles.
- Per-project files: `~/.cursor/projects/<sanitized-path>/agent-transcripts/<composerId>.jsonl`
  and `~/.cursor/projects/<sanitized-path>/terminals/<n>.txt` (each agent shell
  command with cwd, output, exit code, timestamps). The latter is a ready-made
  "terminal view" for editor sessions.
- CLI (`agent`): `~/.cursor/chats/<hash>/<session>/store.db` with a `meta`
  table (hex-encoded JSON: agentId, name, mode, createdAt, lastUsedModel,
  blobEncryptionKey) and a content-addressed `blobs` graph. Harder to read;
  prefer hooks for the CLI.

Schema is undocumented and changed in 3.0 (index moved from per-workspace
`allComposers` to the global table). Treat like Codex JSONL: implementation
detail, degrade to "unknown" when it does not parse.

### Starting a task remotely

- CLI: `agent -p "<prompt>" --output-format stream-json [--force]`,
  `agent --resume=<id>` / `agent ls`. Same shape as `claude -p`; our
  spawn path can reuse the Otty/tmux pane logic with a different binary.
- Editor: deeplink `cursor://anysphere.cursor-deeplink/prompt?text=…` opens
  a new chat with the prompt **pre-filled but not submitted** (by design).
  Good enough for "start this on my Mac, I'll confirm when I'm back", not for
  hands-off spawn.

### Usage / quota

No public per-user API; the dashboard (cursor.com, session cookie) is the only
source. Skip, or show "not available" like an API-key Codex login.

## What Cursor already does itself

Cursor for iOS (public beta since 2026-06, paid plans) + "Remote Control"
(Cursor ≥ 3.9.8, Pro and up with Cloud Agents): `/remote-control` in a desktop
Agents Window hands the session to the phone via Cursor's cloud; follow-ups,
live stream, approve/reject, push on turn end, Live Activities for up to eight
agents. Only desktop Agents Window sessions, not the CLI; requires cloud data
storage on (not available in Legacy Privacy Mode); the Mac must stay awake.

Our differentiators, same as against Codex Remote: one lock-screen panel across
Claude + Codex + Cursor on every machine, CLI sessions included, no cloud data
storage requirement, self-hostable, Windows.

## Fit with the current hook

| Capability | Claude Code today | Cursor | Effort |
|---|---|---|---|
| running / done / needs-you on the phone | hooks | hooks (`beforeSubmitPrompt`, `stop`, `beforeShellExecution`) | small: new `engine: cursor` dispatcher, parse `conversation_id` |
| approve from Lock Screen | blocking `PermissionRequest` hook (900 s) | blocking `beforeShellExecution` returning `allow`/`deny`; `ask` = fall back to Cursor's own dialog | small |
| latest reply in Progress view | transcript JSONL | `afterAgentResponse` payload | small |
| phone follow-up into a live session | blocking `Stop` hook waits up to 16 min, or watcher types into the pane | `stop` is fire-and-forget: hold the script and return `followup_message`, subject to hook timeout and `loop_limit`; CLI sessions can also be typed into via the pane | medium; needs a real test of how long Cursor lets a `stop` script run |
| Terminal view | Otty/tmux capture | CLI: same capture. Editor: read `terminals/*.txt` | small |
| spawn from phone | `claude -p` in a pane | `agent -p` in a pane; editor via deeplink (manual confirm) | small |
| quota | official usage API | none | skip |
| idle detection while at the Mac | same | same | none |

Suggested order: (1) `engine: cursor` events through the existing pipeline,
installed by `sessionbell cursor-enable` writing `~/.cursor/hooks.json` with
`beforeSubmitPrompt`, `stop`, `sessionEnd`, `afterAgentResponse`,
`beforeShellExecution`; (2) guard the existing Claude hooks against being
called by Cursor's third-party import (detect `cursor_version` in the payload,
route to the Cursor path instead of ignoring); (3) follow-up via `stop` hold
once the timeout ceiling is measured; (4) editor terminal view from
`terminals/*.txt`; (5) CLI spawn.

## Verified on this Mac (Cursor 3.21.16, 2026-09-22 16:11, local Agent, grok-4.6)

Probe hook (log-only, `stop` held 30 s with `timeout: 120`) captured, in order:
`sessionStart` → `beforeSubmitPrompt` → `preToolUse` (tool `Shell`) →
`beforeShellExecution` → `afterShellExecution` → `afterAgentResponse` → `stop`.

- `~/.cursor/hooks.json` is picked up **without restarting Cursor**
  (hooks log: "Loaded 8 user hook(s)"). The same log line "Loaded Claude user
  hooks" confirms `~/.claude/settings.json` is imported.
- Every payload carries **both `conversation_id` and `session_id`** (same
  value), `workspace_roots`, `model`; from the first tool call on,
  `transcript_path` points at
  `~/.cursor/projects/<sanitized-root>/agent-transcripts/<id>/<id>.jsonl`
  (null on `sessionStart`/`beforeSubmitPrompt`).
- The transcript is JSONL in Claude-transcript shape: `{"role":"user"|"assistant","message":{"content":[{type:text|tool_use,…}]}}`
  and a final `{"type":"turn_ended","status":"success"}`. Our Progress-view
  parser can read it with a small adapter.
- `afterAgentResponse` and `stop` carry `input_tokens`, `output_tokens`,
  `cache_read_tokens`, `cache_write_tokens` per turn, plus `status` and
  `loop_count` on `stop`. Per-session token totals for free.
- `afterShellExecution` includes the full command `output` and `duration`:
  editor sessions get a terminal view from hooks alone.
- The `stop` script was allowed to run the full 30 s (`timeout: 120`
  honoured, no kill). Whether the UI showed the turn as finished during the
  hold was not observed; the follow-up path is viable.
- **Trap:** the two earlier attempts silently went to Cloud Agents
  (`background-composer` VMs, hooks resolved under `/home/ubuntu`), where user
  hooks never load and nothing lands in the local DB. Detection: no
  `sessionStart` locally; the phone should not expect those.

Still open: does `beforeShellExecution` `ask` reopen Cursor's own dialog after
our hook alerted the phone; maximum `stop` timeout Cursor accepts; CLI
(`agent`) behaviour, not installed here.

Sources: cursor.com/docs/agent/hooks, cursor.com/docs/reference/third-party-hooks,
cursor.com/docs/cli/headless, cursor.com/docs/cli/reference/output-format,
cursor.com/docs/cloud-agent/web-and-mobile, cursor.com/docs/reference/deeplinks,
Callum-Ward/cursaves `docs/how-cursor-stores-chats.md`,
getagentseal/codeburn issue #986, local inspection of Cursor 3.21.16 state.
