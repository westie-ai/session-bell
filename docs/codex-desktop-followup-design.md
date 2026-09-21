# Native desktop follow-up: feasibility checkpoint

## Approved scope

The user approved continuing the original native desktop conversation from the
phone. Do not move the conversation into a second app-server, replace the desktop
transport, modify the application bundle, or reuse private login credentials.
First implementation target is a follow-up after the current turn completes.

## Verified observations, 2026-09-20

- The installed desktop contains an IPC router at `CODEX_HOME/ipc/ipc.sock`.
  The observed socket is owned by the current user with mode 0600; its parent is
  owned by the same user with mode 0700.
- This is an internal desktop protocol, not the public app-server JSON-RPC API.
  It uses four-byte little-endian length-prefixed JSON messages.
- Static inspection found `thread-owner-discovery` version 1 and
  `thread-follower-start-turn` version 2 for local-host requests. The desktop
  forwards a follower request to the owning client, which invokes its normal
  conversation manager and app-server turn submission path.
- A read-only probe using its own `sessionbell-probe` client identity completed
  initialization and found an owner for the most recently observed desktop
  conversation. Two older candidate owner queries timed out; their availability
  is not established.
- The probe sent only initialization, owner queries and negative capability
  discovery replies. It did not send a turn, resume a conversation, change
  settings, or persist any conversation contents.

Classification: **depends on a private interface; corrected second protocol test
passed with no new frontend error logs; user confirmed visible results and continued desktop input**.
The first native protocol request
executed a turn, but triggered the desktop error screen. This is NOT a successful
original-conversation integration. The user subsequently confirmed recovery and
authorized a corrected second experiment after offline validation.

## Native follow-up experiment result

After the user created the dedicated test chat and confirmed READY, the
`--send-once` experiment returned all of these checks as true:

- Original desktop owner accepted the follower request; response owner matched.
- The same original transcript completed with exactly one new user message and
  one new turn, returning the exact expected reply `FOLLOWUP_OK`.
- The recorded turn-context settings digest remained unchanged (model, cwd,
  effort, approval policy and sandbox policy where present).
- The turn completed after the test follower connection had been closed.

No desktop restart, alternate app-server, app patch, transport change or login
credential reuse was involved. This verifies the local protocol leg, not the
SessionBell phone/Worker command delivery path. At that checkpoint the phone
still showed read-only controls; the later phone integration pilot is described below.

### Desktop failure overrides protocol success

The user supplied a screenshot of the desktop's "ChatGPT hit a snag" error screen.
Desktop logs at 2026-09-21 05:56:43.593Z and 05:56:49.190Z record error boundaries
in `LocalConversationPage` and `AppRoutes`, both with
`Cannot read properties of undefined (reading 'length')`.
The available error stacks do not identify the exact missing field. The cause
must not be attributed to a particular request field without additional evidence.
Do not re-submit, modify desktop data, patch the app, or change transport to hide
the failure. The one-shot process had already exited; this path was never
installed in the relay or enabled in the phone UI. The user confirmed recovery.

### Confirmed input-contract defect and offline regression

The submitted text item omitted `text_elements`. The installed desktop's
conversation-to-display converter (`dj` -> `JQn` / `YQn` -> `pin` -> `mrn`)
reads `text_elements.length` without defaulting a missing value. Extracting only
that pure decoder from the pinned frontend into an isolated Node VM reproduces
the exact logged exception with the original input; the same decoder accepts
the corrected plain-text item with `text_elements: []`.

The second experiment builds a strict, fixed test input with an empty
`text_elements` array and empty attachment arrays. It forbids thread mismatch,
arbitrary message replacement and model/permission overrides. It must pass the
actual pinned native decoder check before opening IPC. Its marker and no-retry
receipt are distinct from the first attempt, so it cannot resend the old turn.
The user must explicitly prepare the second-round marker and READY_2 response.

Additional verification now records the existing desktop log offsets and checks
for new error boundaries/render-process failures, including ten seconds after
completion. Any new error fails the test. A clean interval is still not a
substitute for the user's visible-desktop and subsequent-input confirmation.

Offline checks passed: 55 Python tests, including six input-contract guards;
`codex_desktop_input_contract.mjs` reproduced the old missing-field failure and
accepted the corrected payload using the installed frontend's actual decoder.
The second top-level UI error could have been a cascading effect; that is not
yet independently established.

### Second native experiment

The user prepared the second-round chat. Copying the marker preserved a literal
Markdown escape in READY\\_2, so the test now accepts only the exact original
marker or its escaped-underscore equivalent, not fuzzy/substring matches. A
seventh contract regression covers this case; all 56 Python tests passed.

After offline validation, exactly one corrected follower request was submitted.
The same original chat completed with one new message, one new turn and the
expected `FOLLOWUP_OK_2` reply. Recorded settings matched, completion happened
after follower disconnection, and there were no new desktop error boundaries or
render-process failure logs through the ten-second post-completion window.
The user confirmed the visible result and that the desktop can continue the
conversation. This closes the native-UI acceptance gate. Phone queue/UI acceptance
remains separate from the local protocol test.

## Consent gate

The user accepted the private-protocol local pilot and requested a real test.
The user created the dedicated test conversation and confirmed it was ready
before the single test message was submitted.
`tests/smoke_codex_desktop_followup.py` defaults to read-only inspection; its
explicit `--send-once` mode checks the pinned desktop frontend, exact test prompt,
READY completion, owner identity, unchanged transcript and a persisted no-retry
receipt before sending one fixed test message.

Before implementing the adapter or submitting a test turn, confirm that the user
accepts a local pilot depending on the version-sensitive internal protocol. On
unsupported versions or failed capability checks, fall back to read-only. Never
silently try a separate app-server. Do not enable official cloud Remote Control
or change account security settings as a side effect.

## Next verification, after consent

1. Ask the user to open a dedicated desktop test conversation and finish one turn.
2. Validate its metadata, discover its current owner and confirm it is idle.
3. Send one identified test follow-up through that owner, preserving thread
   settings and native permission handling. Do not test on the agent's own active
   conversation or unrelated work.
4. Confirm the same conversation gets one new turn, the desktop shows the user
   message and response without restarting, and the user can continue from the
   desktop afterward.
5. Verify disconnects do not stop desktop work. Only then wire the existing
   SessionBell phone command queue to the adapter.

## Implementation constraints if the experiment passes

- Pin supported protocol/application versions; validate socket type, ownership,
  permissions, message schemas, routing identity and bounded frame lengths.
- Scope commands by host and original conversation ID. Re-discover the owner
  after a disconnect instead of reusing a stale target-client ID.
- Persist command receipts and distinguish queued, accepted, running, completed,
  failed and unconfirmed delivery. Never blindly resend an ambiguous submission.
- Queue while running; do not use ordinary follow-ups to answer approvals or
  structured questions. Pause queued input for review if desktop activity changes
  the expected conversation context.
- Keep the existing read-only observer as a fallback. A connection failure must
  disable controls, not disable monitoring or interrupt native desktop tasks.

Reference: https://learn.chatgpt.com/docs/app-server documents public turn APIs
but does not establish support for this desktop-internal IPC interface.

## Phone integration pilot, 2026-09-21

- Opt in only on this Mac with `codex_desktop_followup: true`. Both the native
  frontend and IPC router assets are SHA-256 pinned. Unknown builds fall back to
  monitoring; no bundle/config/transport patching or shared-service resume.
- An independent desktop command worker consumes the existing `/api/codex`
  mailbox. The CLI worker explicitly leaves desktop commands alone, including
  when the shared CLI service is offline. Claude routes remain unchanged.
- Re-validate native metadata and complete lifecycle history before dispatch;
  wait while the current turn is running, reject interrupted/unknown turns.
  Commands expire after 15 minutes. If a newer native turn started after the
  backend accepted the message, reject it for user review instead of injecting
  an old queued message into a changed conversation.
- Re-discover the owner, obtain the backend dispatch acknowledgement, fsync a
  content-free per-command ledger under a nonblocking file lock, persist the
  local receipt, then recheck the transcript and build immediately before the
  sole native write. A timeout, invalid reply or restart after dispatch is
  uncertain and never auto-retried. Require a confirmed native turn ID to label
  delivery successful. Delivery is not completion; the observer owns completion
  notifications and token usage.
- There is no atomic expected-turn guard in this private start-turn protocol.
  The final fingerprint check narrows, but cannot eliminate, the race with a
  simultaneous desktop submission. This is a pilot limitation, not a guarantee.
- iOS shows the original-conversation composer only for a supported, fresh Mac
  capability and a running/done session. Disable duplicate taps while waiting
  for a receipt and disable more input while an observed follow-up is queued.
  Queue count and the last delivery result remain visible in the task detail.
  Cancellation, native approval/question answers, and archived-session reopening
  are not included in this pilot.
- Capability freshness uses a per-session `desktop_verified_at` timestamp from a
  successful desktop observation, not the enclosing host heartbeat. Timestamp-only
  renewals are persisted without triggering extra dashboard/APNs updates.
- Codex notification replies go directly to `/api/codex`, preserving the original
  server acceptance time for expiry/context checks. Native desktop replies left
  in the legacy mailbox by older apps are rejected with a visible delivery reason;
  converting them would incorrectly reset their age. Claude retains its existing route.
- Existing backend session fields carry capability/queue/delivery status without
  a schema migration. The follow-up protocol itself reuses `/api/codex`; the
  later pre-landing retry fix also adds `/api/command/restore`, so deploy the updated
  Worker before the updated relay as described in the integration guide. Public
  asset release and app version bumps remain separate from the local pilot.
