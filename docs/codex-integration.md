# Codex integration

Scope: native macOS desktop read-only monitoring (task state, completion, replies and cumulative tokens); shared CLI control (approvals, structured questions, follow-ups and new tasks); official account quota. Desktop arbitrary control is not available. The integration is a single-device pilot, not a public release.

## Single-device pilot, 2026-09-20

- Branch `feat/codex-integration` includes main at `83f84be` via merge `67e8a7d`.
- Development iOS App and Widget 1.5 (10) installed and launched on the paired iPhone. No App Store / TestFlight upload.
- Cloudflare Worker version `4888c1bb-b112-483f-9d0d-0312244f42df` deployed; prior rollback version `276a6563-60d6-4344-8c1a-7607b9514997` retained. D1 schema, bindings, secrets and public assets were not changed. Public HTML differs per response only by Cloudflare's injected hidden link; normalized content matched the unchanged source.
- The production command-claim endpoint and board wake lock were absent even from the fetched main. They were restored into this branch before deployment, preserving the actual production behavior.
- Only this Mac's installed relay was updated. Its existing atomic legacy-command claim behavior is retained, and idle Codex connection state is refreshed without extra APNs updates. `auto_update: false` prevents the unchanged public script from replacing this pilot relay.
- Pre-update local rollback copy: `~/.sessionbell/relay-backup-20260920-032402/` (script, configuration and launch agent). Later timestamped backups also exist.
- Validation: 16 legacy API scenarios matched the downloaded production Worker; 22 live HTTP checks passed in an isolated tenant and their records were deleted. The actual installed relay completed both a new Codex task and a follow-up through production APIs, including duplicate-upload checks, completion state and reply synchronization. Official quota was present in backend state. Only the test conversation was archived.
- Notification display, lock-screen presentation and phone approval/question interactions remain manual verification items. Do not infer APNs display success merely from a task completing.
- The original shared-desktop override broke real new-chat creation (`invalid transport in mcp_servers.codex_app`) despite successful initialization. It was removed, backed up under `~/.sessionbell/desktop-rollback-20260920-040148/`, and the user confirmed the native desktop works again. Do not reinstall this override.

## Connection model

CLI sessions connect to a shared local Codex app-server. SessionBell attaches as another client; its relay connects to Worker / D1 and the iPhone. The phone never connects directly to Codex. The Unix socket must belong to the current user and its directory cannot be writable by other users. No TCP listener or Codex cloud remote-control enrollment is added. OAuth credentials stay with Codex.

The desktop keeps its own native server and MCP configuration. The relay opens its local thread index in SQLite read-only mode and incrementally reads complete rollout records. It verifies `session_meta.originator = Codex Desktop`; `source = vscode` alone is not enough. Native observations are never resumed on the shared service or marked managed. JSONL is an implementation detail and may change; missing lifecycle information is unknown, not completed. Cumulative token snapshots are replaced, never summed. Initial history and rewritten files do not generate completion alerts. See [the approved design](codex-desktop-design.md).

## Install and release order

1. Deploy the updated `backend-cf` Worker. `/api/codex` must exist before phone controls can work. For a pilot, reuse existing assets with Cloudflare's `keep_assets` upload metadata; do not publish the newer public hook or installer. A normal unrestricted `wrangler deploy` also updates public assets and is not the pilot workflow.
2. Build and install the updated iOS app and Widget. Legacy Claude endpoints remain compatible.
3. After pairing, run `python3 ~/.sessionbell/sessionbell_hook.py codex-enable`, or use `python3 mac/sessionbell_hook.py codex-enable` from the source checkout.
4. Install the updated hook at the relay's configured path and restart the relay. A development relay can point directly to the repository script. Set `auto_update: false` during a hosted-install development test so the published asset cannot replace newer local code.
5. Leave the desktop running normally; no transport setting or restart is required. Use `codex --remote unix://` or the updated `sessionbell codex` wrapper for CLI. Never resume an active independent conversation on a second server.

`SB_CODEX=1` opts into Codex setup in the Mac installer. Setup backs up configuration and launch settings in `~/.sessionbell/codex-backup-*`. It preserves any already-running shared daemon, without restarting active Codex work.

Shared sessions use app-server events and native approval requests; SessionBell command-hook trust is not required for those sessions. `codex-setup` registers only an optional native desktop PermissionRequest handler, guarded by validated desktop transcript metadata, and uses Codex's normal hook trust review. Other SessionBell lifecycle hooks are removed to avoid duplicate monitoring; unrelated hooks are preserved and never automatically trusted. Real desktop permission round trips remain pending validation.

## Behavior

The controls below apply to shared CLI/phone-created sessions. Desktop pages explicitly show monitoring-only and hide the follow-up composer. They show cumulative session tokens separately from account quota; those counters are not a billing estimate. Native desktop structured questions and arbitrary message submission are not supported.

- New-task UI has a Claude / Codex selector. Phone-created Codex tasks use workspace-write, on-request approval, and a human reviewer.
- Follow-ups preserve line breaks and wait until the current turn is idle.
- UUID commands prevent duplicate HTTP uploads. Persisted dispatch receipts stop an ambiguous disconnect from automatically replaying a turn. UI outcomes distinguish queued, delivered, failed, and uncertain.
- Shell/file approvals and requested permission grants can be answered on the phone. Grants apply to the requested turn; no session-wide rule is added. Native resolution removes the matching phone request.
- Structured answers map to question IDs; secret answers use secure input. Specialized unsupported requests remain with the Mac.
- Fast turns missed during initial discovery are hydrated from the latest turn page, not the entire conversation.
- Quota comes from `account/rateLimits/read` and preserves actual bucket/window durations, percentages, and reset times. Missing quota is not zero; API-key logins do not invent subscription quota. Stale cached data is identified.
- Concurrent hook and relay saves merge changes under a lock, then atomically replace state, preserving another session's updates and command cursor.

## Validation

- `python3 -B -m unittest discover -s tests -v`: protocol mappings, approvals, questions, timeout, state merging, engine identity, busy queueing, duplicate and ambiguous dispatch.
- `node --test tests/codex_backend.test.mjs`: queue retention, duplicate uploads, terminal receipts, validation, and host/tenant isolation.
- `python3 -B tests/smoke_codex_socket.py`: isolated unauthenticated socket handshake and two clients seeing one loaded thread; no model calls.
- `python3 -B tests/smoke_codex_live.py`: opt-in authenticated test of model completion, second-client resume, an actual CLI follow-up, and SessionBell spawn/receipt/completion/reply extraction. Only its own test conversations are created and archived.
- iOS App and Widget compile for iOS Simulator with code signing disabled.
- `wrangler deploy --dry-run` successfully builds the Worker and its assets without deploying them.
- `python3 -B tests/smoke_codex_desktop.py`: read actual native desktop records into disposable state; print only counts/status and assert historical notifications stay silent.
- Desktop shared initialization was insufficient validation: real task creation failed. This path has been removed. Desktop UI interaction is user-assisted because computer use prohibits controlling its own app.

The production queue and real relay round trips are verified as described above. Physical notification display, approvals and lock-screen verification remain pending; the iPhone installation and launch check alone do not establish those behaviors.

## Roll back local settings

The native observer installs no desktop environment override or dedicated desktop launch agent. Restore the previous relay script/configuration from its timestamped backup to disable it. If optional hooks were installed, restore the exact pre-install `hooks.json.sessionbell-backup-*` after checking for later unrelated changes. Other Codex settings, login, and conversation history remain intact.

Only unload `dev.piper.sessionbell.codex` after its active tasks finish: stopping it interrupts clients using the service. Original relay/configuration backups restore the previous SessionBell installation.

References: [Hooks](https://developers.openai.com/zh-Hans/docs/hooks) and [App Server](https://developers.openai.com/zh-Hans/docs/app-server).
