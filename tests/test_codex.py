"""Protocol and mixed-engine regressions; no account, APNs, or network required."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location(
    "sessionbell", Path(__file__).resolve().parents[1] / "mac/sessionbell_hook.py")
sb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sb)


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in {
            "CONFIG_DIR": str(self.root),
            "SESSIONS_PATH": str(self.root / "sessions.json"),
            "PENDING_APPROVAL_PATH": str(self.root / "pending.json"),
            "LOG_PATH": str(self.root / "log"),
        }.items():
            p = patch.object(sb, name, value)
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(os.environ, {"SESSIONBELL_ENGINE": "codex",
                                     "CODEX_HOME": str(self.root / "codex")})
        env.start()
        self.addCleanup(env.stop)

    def check_watcher_sync(self, suspend_during_poll):
        class StopWatcher(BaseException):
            pass

        clock = [1000.0]
        events = []
        polls = []

        def sleep(seconds):
            events.append("sleep")
            clock[0] += seconds

        def poll(*args):
            polls.append(args)
            if len(polls) == 2:
                raise StopWatcher()
            if suspend_during_poll:
                clock[0] += 300
            return {"commands": {}}

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sb.time, "time", side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(sb.time, "sleep", side_effect=sleep))
            stack.enter_context(patch.object(sb, "backend_poll", side_effect=poll))
            backend = stack.enter_context(patch.object(sb, "backend_call", return_value={}))
            sync = stack.enter_context(patch.object(sb, "sync_peers", side_effect=lambda *args: events.append("sync")))
            stack.enter_context(patch.object(sb, "host_label", return_value="test-mac"))
            for name in ("self_update", "usage_summary", "refresh_codex_usage", "prune_sessions"):
                stack.enter_context(patch.object(sb, name))
            with self.assertRaises(StopWatcher):
                sb.run_watcher({})
            self.assertEqual(events[0], "sync", "Initial heartbeat must precede any sleep")
            self.assertEqual(sync.call_count, 2 if suspend_during_poll else 1)
            self.assertEqual(backend.call_count, 1 if suspend_during_poll else 0)
            self.assertEqual(polls[0][2:], (sb.LP_WAIT, 0))

    def test_merged_watcher_publishes_immediate_heartbeat(self):
        self.check_watcher_sync(suspend_during_poll=False)

    def test_merged_watcher_resyncs_after_sleep_during_long_poll(self):
        self.check_watcher_sync(suspend_during_poll=True)

    def test_legacy_claim_requires_backend_confirmation(self):
        for response, expected in ((None, False), ({}, False), ({"claimed": False}, False), ({"claimed": True}, True)):
            with self.subTest(response=response), patch.object(sb, "backend_call", return_value=response) as backend:
                self.assertEqual(sb.claim_command({}, "claude-1", 123), expected)
                self.assertEqual(backend.call_args.args[3], {"key": "claude-1", "ts": 123})

    def test_legacy_desktop_reply_is_rejected_without_resetting_acceptance_time(self):
        class StopWatcher(BaseException):
            pass
        for won in (False, True):
            with self.subTest(claimed=won), contextlib.ExitStack() as stack:
                state = sb.load_sessions()
                state["local"]["desktop"] = {"engine": "codex", "source": "desktop",
                    "status": "running", "desktop_started_at": 1900, "cmd_ts": 0}
                sb.save_sessions(state)
                stack.enter_context(patch.object(sb.time, "time", return_value=2000))
                stack.enter_context(patch.object(sb.time, "sleep"))
                stack.enter_context(patch.object(sb, "host_label", return_value="test-mac"))
                for name in ("self_update", "usage_summary", "refresh_codex_usage", "sync_peers", "prune_sessions"):
                    stack.enter_context(patch.object(sb, name))
                stack.enter_context(patch.object(sb, "backend_poll", side_effect=[
                    {"commands": {"desktop": {"ts": 1000000, "text": "old instruction"}}}, StopWatcher()]))
                claim = stack.enter_context(patch.object(sb, "claim_command", return_value=won))
                backend = stack.enter_context(patch.object(sb, "backend_call"))
                inject = stack.enter_context(patch.object(sb, "type_into_terminal"))
                with self.assertRaises(StopWatcher):
                    sb.run_watcher({})
                claim.assert_called_once_with({}, "desktop", 1000000)
                backend.assert_not_called()
                inject.assert_not_called()
                entry = sb.load_sessions()["local"]["desktop"]
                self.assertEqual(entry["cmd_ts"], 1000000 if won else 0)
                self.assertEqual("desktop_delivery" in entry, won)

    def test_legacy_claude_reply_still_reaches_terminal(self):
        class StopWatcher(BaseException):
            pass
        state = sb.load_sessions()
        state["local"]["claude"] = {"engine": "claude", "pid": 123, "term_type": "test", "cmd_ts": 0}
        sb.save_sessions(state)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sb.time, "time", return_value=2000))
            stack.enter_context(patch.object(sb.time, "sleep"))
            stack.enter_context(patch.object(sb, "host_label", return_value="test-mac"))
            for name in ("self_update", "usage_summary", "refresh_codex_usage", "sync_peers", "prune_sessions"):
                stack.enter_context(patch.object(sb, name))
            stack.enter_context(patch.object(sb, "backend_poll", side_effect=[
                {"commands": {"claude": {"ts": 2000000, "text": "continue"}}}, StopWatcher()]))
            stack.enter_context(patch.object(sb, "claim_command", return_value=True))
            stack.enter_context(patch.object(sb, "pid_alive", return_value=True))
            backend = stack.enter_context(patch.object(sb, "backend_call"))
            inject = stack.enter_context(patch.object(sb, "type_into_terminal", return_value=(True, "")))
            with self.assertRaises(StopWatcher):
                sb.run_watcher({})
            inject.assert_called_once()
            self.assertEqual(inject.call_args.args[1], "continue")
            backend.assert_not_called()
            self.assertEqual(sb.load_sessions()["local"]["claude"]["cmd_ts"], 2000000)

    def test_raw_terminal_input_cancels_legacy_mailbox(self):
        state = {"local": {"claude-1": {"cmd_ts": 0}}}
        with patch.object(sb, "type_into_terminal", return_value=(True, "")), patch.object(sb, "claim_command") as claim:
            sb.handle_type({}, state, json.dumps({"sid": "claude-1", "text": "continue"}))
            claim.assert_called_once_with({}, "claude-1")
            self.assertGreater(state["local"]["claude-1"]["cmd_ts"], 0)

    def test_watcher_cannot_spawn_without_winning_claim(self):
        class StopWatcher(BaseException):
            pass
        for won in (False, True):
            with self.subTest(won=won), contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(sb.time, "time", return_value=1000))
                stack.enter_context(patch.object(sb.time, "sleep"))
                stack.enter_context(patch.object(sb, "host_label", return_value="test-mac"))
                stack.enter_context(patch.object(sb, "load_sessions", side_effect=lambda: {"local": {}, "_cursors": {}}))
                stack.enter_context(patch.object(sb, "save_sessions"))
                for name in ("self_update", "usage_summary", "refresh_codex_usage", "sync_peers", "prune_sessions"):
                    stack.enter_context(patch.object(sb, name))
                stack.enter_context(patch.object(sb, "backend_poll", side_effect=[
                    {"commands": {"_spawn-testmac": {"ts": 1000000, "text": "test"}}}, StopWatcher()]))
                claim = stack.enter_context(patch.object(sb, "claim_command", return_value=won))
                spawn = stack.enter_context(patch.object(sb, "handle_spawn"))
                with self.assertRaises(StopWatcher):
                    sb.run_watcher({})
                claim.assert_called_once_with({}, "_spawn-testmac", 1000000)
                self.assertEqual(spawn.call_count, 1 if won else 0)

    def test_official_windows_are_not_assumed_to_be_five_hours(self):
        usage = sb.normalize_codex_usage({"rateLimits": {
            "primary": {"usedPercent": 31, "windowDurationMins": 15, "resetsAt": 123},
            "planType": "pro"}, "accountId": "must-not-leave-machine"}, now=100)
        self.assertTrue(usage["available"])
        self.assertEqual(usage["limits"][0]["windows"][0]["window_minutes"], 15)
        self.assertEqual(usage["updated_at"], 100)
        self.assertNotIn("must-not-leave-machine", json.dumps(usage))

    def test_multiple_buckets_and_zero_usage(self):
        usage = sb.normalize_codex_usage({"rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 0}},
            "review": {"secondary": {"usedPercent": 90, "windowDurationMins": 10080}}
        }, "ordinaryUsageAllowed": False})
        self.assertEqual(len(usage["limits"]), 2)
        self.assertEqual(usage["limits"][0]["windows"][0]["used_pct"], 0)
        self.assertFalse(usage["ordinary_usage_allowed"])

    def test_missing_quota_is_not_zero(self):
        usage = sb.normalize_codex_usage({"rateLimits": {"primary": {}}})
        self.assertFalse(usage["available"])
        self.assertEqual(usage["limits"], [])

    def test_usage_cache_staleness(self):
        (self.root / "codex-usage.json").write_text(json.dumps({"updated_at": 1000, "available": True}))
        with patch.object(sb.time, "time", return_value=2000):
            self.assertTrue(sb.cached_codex_usage()["stale"])
        with patch.object(sb.time, "time", return_value=9000):
            self.assertEqual(sb.cached_codex_usage(), {})

    def test_concurrent_sessions_do_not_overwrite_each_other(self):
        first, second = sb.load_sessions(), sb.load_sessions()
        first["local"]["claude-1"] = {"engine": "claude", "status": "running"}
        second["local"]["codex-1"] = {"engine": "codex", "status": "running"}
        sb.save_sessions(first)
        sb.save_sessions(second)
        self.assertEqual(set(sb.load_sessions()["local"]), {"claude-1", "codex-1"})
        self.assertNotIn("_baseline", json.loads((self.root / "sessions.json").read_text()))

    def test_status_update_preserves_concurrent_command_cursor(self):
        initial = sb.load_sessions()
        initial["local"]["one"] = {"status": "running", "cmd_ts": 10}
        sb.save_sessions(initial)
        hook, watcher = sb.load_sessions(), sb.load_sessions()
        watcher["local"]["one"]["cmd_ts"] = 20
        hook["local"]["one"]["status"] = "done"
        sb.save_sessions(watcher)
        sb.save_sessions(hook)
        self.assertEqual(sb.load_sessions()["local"]["one"], {"status": "done", "cmd_ts": 20})

    def test_stale_prune_does_not_remove_a_new_turn(self):
        initial = sb.load_sessions()
        initial["local"]["one"] = {"status": "done"}
        sb.save_sessions(initial)
        stale, fresh = sb.load_sessions(), sb.load_sessions()
        stale["local"].pop("one")
        fresh["local"]["one"]["status"] = "running"
        sb.save_sessions(fresh)
        sb.save_sessions(stale)
        self.assertEqual(sb.load_sessions()["local"]["one"]["status"], "running")

    def test_setup_preserves_other_handlers_and_is_idempotent(self):
        path = self.root / "codex/hooks.json"
        path.parent.mkdir()
        other = {"type": "command", "command": "other-tool"}
        path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [other, {
            "type": "command", "command": "python sessionbell_hook.py stop"}]}]}}))
        with contextlib.redirect_stdout(io.StringIO()):
            sb.cmd_codex_setup()
            first = json.loads(path.read_text())
            sb.cmd_codex_setup()
        self.assertEqual(first, json.loads(path.read_text()))
        self.assertEqual(first["hooks"]["Stop"][0]["hooks"], [other])
        self.assertGreater(first["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"], 600)
        self.assertNotIn("Notification", first["hooks"])
        self.assertEqual(len(list(path.parent.glob("*.sessionbell-backup-*"))), 2)

    def test_setup_does_not_overwrite_invalid_json(self):
        path = self.root / "codex/hooks.json"
        path.parent.mkdir()
        path.write_text("invalid")
        with self.assertRaises(SystemExit):
            sb.cmd_codex_setup()
        self.assertEqual(path.read_text(), "invalid")

    def event(self, kind, hook, force=False):
        cfg = {"backend_url": "https://example.invalid", "backend_secret": "test",
               "bundle_id": "test", "environment": "sandbox"}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sb.sys, "argv", ["hook.py", kind]))
            stack.enter_context(patch.object(sb.sys, "stdin", io.StringIO(json.dumps(hook))))
            stack.enter_context(patch.dict(os.environ, {"SESSIONBELL_FORCE": "1" if force else ""}))
            stack.enter_context(patch.object(sb, "load_config", return_value=cfg))
            stack.enter_context(patch.object(sb, "self_update_from_hook"))
            stack.enter_context(patch.object(sb, "host_label", return_value="test-mac"))
            stack.enter_context(patch.object(sb, "engine_pids", return_value=(42, None)))
            stack.enter_context(patch.object(sb, "terminal_handle", return_value=("terminal", "/dev/test")))
            stack.enter_context(patch.object(sb, "project_root", side_effect=lambda x: x))
            stack.enter_context(patch.object(sb, "pid_alive", return_value=True))
            stack.enter_context(patch.object(sb, "sync_peers"))
            stack.enter_context(patch.object(sb, "push_dashboard"))
            stack.enter_context(patch.object(sb, "make_jwt", return_value=""))
            stack.enter_context(patch.object(sb, "mac_idle_seconds", return_value=0))
            stack.enter_context(patch.object(sb, "resolve_device_tokens", return_value=["test-device"]))
            push = stack.enter_context(patch.object(sb, "send_push", return_value=(200, "")))
            inject = stack.enter_context(patch.object(sb, "try_inject_command"))
            transcript = stack.enter_context(patch.object(sb, "last_assistant_text"))
            sb.main()
            inject.assert_not_called()
            transcript.assert_not_called()
            return push

    def test_prompt_stop_preserve_identity_and_terminal(self):
        hook = {"session_id": "codex-1", "cwd": "/tmp/project", "prompt": "A task"}
        self.event("prompt", hook)
        self.event("stop", dict(hook, last_assistant_message="Completed"))
        state = sb.load_sessions()
        entry = state["local"]["codex-1"]
        self.assertEqual(entry["engine"], "codex")
        self.assertEqual(entry["status"], "done")
        self.assertEqual(entry["latest_reply"], "Completed")
        self.assertEqual(entry["term_handle"], "/dev/test")
        self.assertIn("codex-1", state["codex_sessions"])

    def test_stop_push_uses_documented_message(self):
        push = self.event("stop", {"session_id": "codex-1", "cwd": "/tmp/project",
                                   "last_assistant_message": "**Done**"}, force=True)
        payload = push.call_args.args[3]
        self.assertEqual(payload["sb"]["md"], "**Done**")
        self.assertEqual(payload["sb"]["engine"], "codex")
        self.assertNotIn("Claude", json.dumps(payload))

    def test_codex_hook_completion_notifies_while_at_keyboard(self):
        push = self.event("stop", {"session_id": "codex-1", "cwd": "/tmp/project",
                                   "last_assistant_message": "Completed"})
        push.assert_called_once()

    def test_codex_completion_bypasses_idle_but_other_alerts_do_not(self):
        cfg = {"backend_url": "https://example.invalid", "backend_secret": "test", "bundle_id": "test"}
        for source in ("desktop", ""):
            for kind in ("stop", "permission", "notification"):
                with self.subTest(source=source, kind=kind), contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(sb, "load_sessions", return_value={"local": {
                        "test": {"source": source, "project": "test"}}}))
                    idle = stack.enter_context(patch.object(sb, "mac_idle_seconds", return_value=0))
                    stack.enter_context(patch.object(sb, "host_label", return_value="test"))
                    stack.enter_context(patch.object(sb, "make_jwt", return_value="test"))
                    stack.enter_context(patch.object(sb, "resolve_device_tokens", return_value=["test-token"]))
                    push = stack.enter_context(patch.object(sb, "send_push", return_value=(200, "")))
                    sb.codex_alert(cfg, "test", kind, "test completion")
                    self.assertEqual(push.call_count, 1 if kind == "stop" else 0)
                    if kind == "stop":
                        idle.assert_not_called()

    def test_codex_push_logs_outcome_without_payload_or_credentials(self):
        cfg = {"backend_url": "https://example.invalid", "backend_secret": "private-secret", "bundle_id": "test"}
        with patch.object(sb, "load_sessions", return_value={"local": {}}), \
                patch.object(sb, "mac_idle_seconds", return_value=999), \
                patch.object(sb, "host_label", return_value="test"), \
                patch.object(sb, "make_jwt", return_value="private-jwt"), \
                patch.object(sb, "resolve_device_tokens", return_value=["private-token"]), \
                patch.object(sb, "send_push", return_value=(400, "private-response")), \
                patch.object(sb, "log") as log:
            sb.codex_alert(cfg, "test", "stop", "private-reply")
        output = " ".join(str(c.args) for c in log.call_args_list)
        self.assertIn("HTTP 400", output)
        self.assertNotIn("private-", output)

    def test_missing_session_id_cannot_create_unknown_task(self):
        self.event("prompt", {"cwd": "/tmp/project", "prompt": "text"})
        self.assertEqual(sb.load_sessions()["local"], {})

    def test_interrupt_updates_local_state_without_network(self):
        hook = {"session_id": "codex-1", "cwd": "/tmp/project"}
        self.event("prompt", hook)
        push = self.event("interrupt", hook)
        push.assert_not_called()
        self.assertEqual(sb.load_sessions()["local"]["codex-1"]["status"], "waiting")

    def test_lock_screen_preserves_engine(self):
        state = {"local": {"c": {"project": "project", "status": "running",
                                   "since": 100, "engine": "codex"}}, "peers": {}}
        self.assertEqual(sb.merged_tasks(state, "mac", 101)[0]["engine"], "codex")

    def test_phone_permission_round_trip_retains_session_identity(self):
        for decision in ("allow", "deny"):
            with self.subTest(decision=decision), contextlib.ExitStack() as stack:
                state = sb.load_sessions()
                state["local"]["c"] = {"engine": "codex", "cwd": "/tmp/project",
                                        "pid": 42, "detail": "task", "status": "running"}
                sb.save_sessions(state)
                cfg = {"backend_url": "https://example.invalid", "backend_secret": "test",
                       "bundle_id": "test", "environment": "sandbox"}
                for name, value in {"host_label": "mac", "relays_list": [], "make_jwt": "",
                                    "resolve_device_tokens": ["test-device"],
                                    "send_push": (200, ""), "backend_poll": {"decision": decision}}.items():
                    stack.enter_context(patch.object(sb, name, return_value=value))
                stack.enter_context(patch.object(sb, "sync_peers"))
                stack.enter_context(patch.object(sb, "push_dashboard"))
                stack.enter_context(patch.dict(os.environ, {"SESSIONBELL_FORCE": "1"}))
                out = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                sb.handle_permission(cfg, {"session_id": "c", "cwd": "/tmp/project",
                                           "tool_name": "Bash", "tool_input": {"command": "pwd"}})
                self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["decision"],
                                 {"behavior": decision})
                entry = sb.load_sessions()["local"]["c"]
                self.assertEqual(entry["engine"], "codex")
                self.assertEqual(entry["pid"], 42)
                self.assertEqual(entry["detail"], "task")
                self.assertNotIn("approval_id", entry)

    def test_phone_timeout_does_not_approve(self):
        with contextlib.ExitStack() as stack:
            cfg = {"backend_url": "https://example.invalid", "backend_secret": "test",
                   "bundle_id": "test", "environment": "sandbox", "approval_timeout_seconds": 0}
            for name, value in {"host_label": "mac", "relays_list": [], "make_jwt": "",
                                "resolve_device_tokens": ["device"], "send_push": (200, "")}.items():
                stack.enter_context(patch.object(sb, name, return_value=value))
            stack.enter_context(patch.object(sb, "sync_peers"))
            stack.enter_context(patch.object(sb, "push_dashboard"))
            stack.enter_context(patch.dict(os.environ, {"SESSIONBELL_FORCE": "1"}))
            out = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            sb.handle_permission(cfg, {"session_id": "c", "tool_name": "Bash"})
            self.assertEqual(out.getvalue(), "")

    def bridge(self):
        with patch.object(sb, "host_label", return_value="test-mac"):
            bridge = sb.CodexBridge({"backend_url": "https://example.invalid", "backend_secret": "test"})
        bridge.rpc = Mock()
        return bridge

    def test_idle_bridge_refreshes_phone_status_without_push(self):
        bridge = self.bridge()
        bridge.rpc.poll.return_value = []
        bridge.wake.clear()
        bridge.last_discover = bridge.last_decisions = 100
        bridge.last_publish = 74
        with patch.object(sb.time, "monotonic", return_value=100), \
             patch.object(sb, "sync_peers") as sync, \
             patch.object(sb, "host_label", return_value="test-mac"), \
             patch.object(sb, "push_dashboard") as push:
            bridge.tick()
            self.assertEqual(bridge.last_publish, 100)
            sync.assert_called_once()
            push.assert_not_called()
            bridge.tick()
            sync.assert_called_once()

    def test_busy_session_keeps_command_queued(self):
        bridge = self.bridge()
        bridge.attached.add("c")
        state = sb.load_sessions()
        state["codex_sessions"] = {"c": {"managed": True}}
        sb.save_sessions(state)
        bridge.rpc.call.return_value = {"thread": {"status": {"type": "active"}}}
        command = {"command_id": "one", "action": "send", "session_id": "c", "text": "follow up"}
        with patch.object(bridge, "ack") as ack:
            self.assertFalse(bridge.execute(command))
            ack.assert_not_called()
        self.assertEqual(bridge.rpc.call.call_args.args[0], "thread/read")

    def test_rejected_send_preflight_gets_terminal_receipt_without_reconnect(self):
        for stage in ("attach", "read"):
            with self.subTest(stage=stage):
                bridge = self.bridge()
                state = sb.load_sessions()
                state["codex_sessions"] = {"c": {"managed": True}}
                sb.save_sessions(state)
                command = {"command_id": stage, "action": "send", "session_id": "c", "text": "continue"}
                with patch.object(bridge, "ack"), patch.object(bridge, "attach") as attach:
                    if stage == "attach":
                        attach.side_effect = sb.CodexRejected("Session unavailable")
                    else:
                        bridge.rpc.call.side_effect = sb.CodexRejected("Session unavailable")
                    self.assertTrue(bridge.execute(command))
                    self.assertTrue(bridge.execute(command))
                receipt = sb.load_sessions()["codex_commands"][stage]
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(attach.call_count, 1)
                self.assertFalse(any(c.args[0] == "turn/start" for c in bridge.rpc.call.call_args_list))

    def test_preflight_connection_failure_still_requests_reconnect(self):
        bridge = self.bridge()
        state = sb.load_sessions()
        state["codex_sessions"] = {"c": {"managed": True}}
        sb.save_sessions(state)
        with patch.object(bridge, "attach", side_effect=ConnectionError()), patch.object(bridge, "receipt") as receipt:
            with self.assertRaises(ConnectionError):
                bridge.execute({"command_id": "connection", "action": "send", "session_id": "c", "text": "continue"})
            receipt.assert_not_called()

    def test_session_fifo_survives_turn_finishing_between_queue_checks(self):
        bridge = self.bridge()
        bridge.attached.update(("c", "d"))
        bridge.wake.clear()
        bridge.last_discover = bridge.last_decisions = bridge.last_publish = 100
        bridge.rpc.poll.return_value = []
        state = sb.load_sessions()
        state["codex_sessions"] = {sid: {"managed": True} for sid in ("c", "d")}
        sb.save_sessions(state)
        commands = [{"command_id": name, "action": "send", "session_id": sid, "text": name}
                    for name, sid in (("first", "c"), ("second", "c"), ("other", "d"))]
        answer = {"command_id": "answer", "action": "answer", "session_id": "c"}
        bridge.commands = commands[:2] + [answer, commands[2]]
        reads, sends, answers = [], [], []
        def rpc(method, params):
            if method == "thread/read":
                reads.append(params["threadId"])
                return {"thread": {"status": {"type": "active" if len(reads) == 1 else "idle"}}}
            if method == "turn/start":
                sends.append(params["input"][0]["text"])
                return {"turn": {"id": "turn"}}
            self.fail("Unexpected RPC: " + method)
        bridge.rpc.call.side_effect = rpc
        execute = bridge.execute
        def dispatch(command):
            if command["action"] == "answer":
                answers.append(command["command_id"])
                return True
            return execute(command)
        with patch.object(sb.time, "monotonic", return_value=100), patch.object(bridge, "ack", return_value={"ok": True}), \
             patch.object(bridge, "execute", side_effect=dispatch):
            bridge.tick()
            self.assertEqual(reads, ["c", "d"])
            self.assertEqual(sends, ["other"])
            self.assertEqual(answers, ["answer"])
            self.assertEqual([c["command_id"] for c in bridge.commands], ["first", "second"])
            bridge.tick()
            self.assertEqual(sends, ["other", "first", "second"])
            self.assertFalse(bridge.commands)

    def test_reconnect_clears_persisted_requests_before_discovery_replay(self):
        class StopBridge(BaseException):
            pass
        bridge = self.bridge()
        bridge.pending["stale"] = {"id": 7}
        state = sb.load_sessions()
        state["local"] = {
            "c": {"engine": "codex", "managed": True, "pending_requests": [{"id": "stale"}]},
            "desktop": {"engine": "codex", "managed": False, "pending_requests": [{"id": "native"}]},
            "claude": {"engine": "claude", "pending_requests": [{"id": "claude"}]}}
        sb.save_sessions(state)
        def discover():
            self.assertFalse(bridge.pending)
            self.assertEqual(sb.load_sessions()["local"]["c"]["pending_requests"], [])
            bridge.attached.add("c")
            bridge.request({"id": 8, "method": "item/commandExecution/requestApproval",
                            "params": {"threadId": "c", "command": "test"}})
        with patch.object(sb, "CodexRPC") as rpc, patch.object(bridge, "discover", side_effect=discover), \
             patch.object(bridge, "tick", side_effect=StopBridge()), patch.object(bridge, "status"), \
             patch.object(sb, "codex_alert"):
            rpc.return_value.__enter__.return_value = bridge.rpc
            with self.assertRaises(StopBridge):
                bridge.run()
        local = sb.load_sessions()["local"]
        self.assertEqual(len(local["c"]["pending_requests"]), 1)
        self.assertNotEqual(local["c"]["pending_requests"][0]["id"], "stale")
        self.assertEqual(local["desktop"]["pending_requests"], [{"id": "native"}])
        self.assertEqual(local["claude"]["pending_requests"], [{"id": "claude"}])

    def test_error_unloaded_and_unknown_thread_statuses_are_not_done(self):
        bridge = self.bridge()
        bridge.attached.add("c")
        cases = [("systemError", "waiting"), ("notLoaded", "unknown"), ("futureStatus", "unknown"),
                 (None, "unknown"), ("idle", "done"), ("active", "running")]
        for kind, expected in cases:
            status = {"type": kind} if kind else {}
            with self.subTest(kind=kind), patch.object(sb, "project_root", return_value="/tmp"), \
                 patch.object(sb, "codex_alert") as alert:
                bridge.remember({"id": "c", "cwd": "/tmp", "status": status})
                self.assertEqual(sb.load_sessions()["local"]["c"]["status"], expected)
                bridge.event({"method": "thread/status/changed", "params": {"threadId": "c", "status": status}})
                entry = sb.load_sessions()["local"]["c"]
                self.assertEqual(entry["status"], expected)
                self.assertEqual(bool(entry["delivery_error"]), kind not in ("idle", "active"))
                alert.assert_not_called()
        bridge.event({"method": "thread/status/changed", "params": {
            "threadId": "c", "status": {"type": "active", "activeFlags": ["waitingOnApproval"]}}})
        self.assertEqual(sb.load_sessions()["local"]["c"]["status"], "waiting")

    def test_unmanaged_desktop_session_is_not_resumed_elsewhere(self):
        bridge = self.bridge()
        with patch.object(bridge, "receipt") as receipt:
            bridge.execute({"command_id": "one", "action": "send", "session_id": "c", "text": "test"})
            self.assertEqual(receipt.call_args.args[1], "failed")
        bridge.rpc.call.assert_not_called()

    def test_crashed_dispatch_is_never_replayed(self):
        bridge = self.bridge()
        state = sb.load_sessions()
        state["codex_commands"] = {"one": {"status": "dispatching", "session_id": "c"}}
        sb.save_sessions(state)
        with patch.object(bridge, "ack"):
            bridge.execute({"command_id": "one", "action": "spawn", "text": "test"})
        bridge.rpc.call.assert_not_called()
        self.assertEqual(sb.load_sessions()["codex_commands"]["one"]["status"], "uncertain")

    def test_delivered_command_is_acknowledged_without_a_second_turn(self):
        bridge = self.bridge()
        state = sb.load_sessions()
        state["codex_commands"] = {"one": {"status": "delivered", "session_id": "c"}}
        sb.save_sessions(state)
        with patch.object(bridge, "ack") as ack:
            bridge.execute({"command_id": "one", "action": "spawn", "text": "test"})
            self.assertEqual(ack.call_args.args[1], "delivered")
        bridge.rpc.call.assert_not_called()

    def test_spawn_is_sandboxed_and_preserves_message(self):
        bridge = self.bridge()
        bridge.rpc.call.side_effect = [{"thread": {"id": "c", "cwd": self.temp.name}}, {"turn": {"id": "t"}}]
        with patch.object(bridge, "ack", return_value={"ok": True}), patch.object(sb, "project_root", side_effect=lambda x: x):
            bridge.execute({"command_id": "one", "action": "spawn", "cwd": self.temp.name, "text": "line 1\nline 2"})
        params = bridge.rpc.call.call_args_list[0].args[1]
        self.assertEqual(params["sandbox"], "workspace-write")
        self.assertEqual(params["approvalPolicy"], "on-request")
        self.assertEqual(params["approvalsReviewer"], "user")
        self.assertEqual(bridge.rpc.call.call_args.args[1]["input"][0]["text"], "line 1\nline 2")
        self.assertEqual(sb.load_sessions()["codex_commands"]["one"]["status"], "delivered")

    def test_ambiguous_turn_start_is_not_retried(self):
        bridge = self.bridge()
        bridge.rpc.call.side_effect = [{"thread": {"id": "c", "cwd": self.temp.name}}, TimeoutError()]
        command = {"command_id": "one", "action": "spawn", "cwd": self.temp.name, "text": "test"}
        with patch.object(bridge, "ack", return_value={"ok": True}), patch.object(sb, "project_root", side_effect=lambda x: x):
            with self.assertRaises(TimeoutError):
                bridge.execute(command)
            bridge.execute(command)
        self.assertEqual(bridge.rpc.call.call_count, 2)
        self.assertEqual(sb.load_sessions()["codex_commands"]["one"]["status"], "uncertain")

    def test_question_answer_maps_by_question_id(self):
        bridge = self.bridge()
        bridge.pending["request"] = {"id": 7, "method": "item/tool/requestUserInput", "params": {
            "threadId": "c", "questions": [{"id": "a"}, {"id": "b"}]}}
        bridge.answer("request", answers={"a": "one", "b": "two"})
        self.assertEqual(bridge.rpc.send.call_args.args[0], {"id": 7, "result": {
            "answers": {"a": {"answers": ["one"]}, "b": {"answers": ["two"]}}}})
        with self.assertRaises(ValueError):
            bridge.answer("request", answers={"a": "one"})

    def test_permission_grant_is_scoped_to_requested_turn(self):
        bridge = self.bridge()
        requested = {"network": {"enabled": True}}
        bridge.pending["request"] = {"id": 7, "method": "item/permissions/requestApproval", "params": {"permissions": requested}}
        bridge.answer("request", decision="allow")
        self.assertEqual(bridge.rpc.send.call_args.args[0]["result"], {"permissions": requested, "scope": "turn"})
        bridge.answer("request", decision="deny")
        self.assertEqual(bridge.rpc.send.call_args.args[0]["result"]["permissions"], {})

    def test_native_resolution_clears_only_matching_prompt(self):
        bridge = self.bridge()
        bridge.attached.add("c")
        bridge.pending = {"one": {"id": 1, "params": {"threadId": "c"}},
                          "two": {"id": 2, "params": {"threadId": "c"}}}
        with patch.object(bridge, "publish_requests"):
            bridge.event({"method": "serverRequest/resolved", "params": {"threadId": "c", "requestId": 1}})
        self.assertEqual(set(bridge.pending), {"two"})


if __name__ == "__main__":
    unittest.main()
