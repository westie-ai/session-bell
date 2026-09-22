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

## Open items to verify on a real machine

- Max `timeout` Cursor allows on a `stop` hook and whether a long-held `stop`
  blocks the UI (docs say it does not wait, but the follow-up is submitted).
- Whether `beforeShellExecution` `ask` re-opens Cursor's approval dialog after
  our hook has already alerted the phone, or is treated as deny.
- Whether `~/.cursor/hooks.json` changes need a Cursor restart.
- Exact `transcript_path` contents (JSONL, updated live?).

A log-only probe to answer the first three: put this in `~/.cursor/hooks.json`,
run one agent turn that executes a shell command, read
`~/.sessionbell/cursor-hooks.log`.

```json
{"version":1,"hooks":{
 "sessionStart":[{"command":"~/.sessionbell/cursor_probe.sh","timeout":5}],
 "beforeSubmitPrompt":[{"command":"~/.sessionbell/cursor_probe.sh","timeout":5}],
 "beforeShellExecution":[{"command":"~/.sessionbell/cursor_probe.sh","timeout":5}],
 "afterAgentResponse":[{"command":"~/.sessionbell/cursor_probe.sh","timeout":5}],
 "stop":[{"command":"~/.sessionbell/cursor_probe.sh","timeout":5}],
 "sessionEnd":[{"command":"~/.sessionbell/cursor_probe.sh","timeout":5}]}}
```

```bash
#!/bin/bash
# ~/.sessionbell/cursor_probe.sh — log the payload, never block
d=$(cat); printf '%s %s\n' "$(date '+%F %T')" "$d" >> "$HOME/.sessionbell/cursor-hooks.log"
echo '{}'
```

Sources: cursor.com/docs/agent/hooks, cursor.com/docs/reference/third-party-hooks,
cursor.com/docs/cli/headless, cursor.com/docs/cli/reference/output-format,
cursor.com/docs/cloud-agent/web-and-mobile, cursor.com/docs/reference/deeplinks,
Callum-Ward/cursaves `docs/how-cursor-stores-chats.md`,
getagentseal/codeburn issue #986, local inspection of Cursor 3.21.16 state.
