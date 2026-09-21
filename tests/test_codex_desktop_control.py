"""Native control regressions, isolated from model calls, private data and APNs."""
import contextlib
import json
import os
import socket
import struct
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import Mock, patch
from test_codex_desktop import sb


class DesktopControlTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for key, value in {"CONFIG_DIR": str(self.root), "SESSIONS_PATH": str(self.root / "state.json"),
                           "LOG_PATH": str(self.root / "log")}.items():
            self.stack.enter_context(patch.object(sb, key, value))
        self.stack.enter_context(patch.object(sb, "host_label", return_value="testmac"))
        self.control = sb.CodexDesktopControl({"codex_desktop_followup": True})
        self.available = self.stack.enter_context(patch.object(self.control, "available", return_value=True))
        self.sid, self.cid = str(uuid.uuid4()), str(uuid.uuid4())
        self.command = {"command_id": self.cid, "session_id": self.sid, "action": "send",
                        "text": "test follow-up", "status": "queued", "created_at": time.time() * 1000}
        state = sb.load_sessions()
        state["local"][self.sid] = {"source": "desktop", "engine": "codex", "managed": False}
        sb.save_sessions(state)
        self.path = self.root / "transcript.jsonl"
        self.path.write_text("test\n")
        self.entry = {"status": "done", "desktop_turn": "turn1", "desktop_terminal": "turn1",
                      "desktop_started_at": time.time() - 30}
        self.snapshot = self.stack.enter_context(patch.object(self.control, "snapshot", side_effect=lambda sid:
            (self.entry, str(self.path), self.control.fingerprint(self.path))))
        self.backend = self.stack.enter_context(patch.object(sb, "backend_call", return_value={"ok": True}))
        self.ipc_type = self.stack.enter_context(patch.object(sb, "CodexDesktopIPC"))
        self.client = self.ipc_type.return_value
        self.client.request.side_effect = self.respond

    @staticmethod
    def respond(method, *args):
        if method == "thread-owner-discovery":
            return {"resultType": "success", "handledByClientId": "owner"}
        return {"resultType": "success", "result": {"result": {"turn": {"id": "next-turn"}}}}

    def receipt(self):
        return sb.load_sessions().get("codex_commands", {}).get(self.cid, {})

    def test_delivered_once_no_resume_or_managed_conversion(self):
        self.assertTrue(self.control.execute(self.command))
        self.assertEqual(self.receipt()["status"], "delivered")
        self.assertEqual([c.args[0] for c in self.client.request.call_args_list],
                         ["thread-owner-discovery", "thread-follower-start-turn"])
        params = self.client.request.call_args_list[-1].args[1]
        self.assertEqual(params, sb.codex_desktop_input(self.sid, self.cid, self.command["text"]))
        self.assertFalse(sb.load_sessions()["local"][self.sid]["managed"])
        self.assertNotIn(self.sid, sb.load_sessions().get("codex_sessions", {}))
        self.client.close.assert_called_once()
        self.control.execute(self.command)
        self.assertEqual(self.client.request.call_count, 2)

    def test_running_waits_then_sends_same_turn(self):
        self.entry["status"] = "running"
        self.assertFalse(self.control.execute(self.command))
        self.ipc_type.assert_not_called()
        self.assertFalse(self.receipt())
        self.entry["status"] = "done"
        self.assertTrue(self.control.execute(self.command))
        self.assertEqual(self.receipt()["status"], "delivered")

    def test_native_fifo_when_turn_finishes_between_snapshots(self):
        class StopControl(BaseException):
            pass
        second = dict(self.command, command_id=str(uuid.uuid4()), text="second")
        other_sid = str(uuid.uuid4())
        other = dict(self.command, command_id=str(uuid.uuid4()), session_id=other_sid, text="other")
        state = sb.load_sessions()
        state["local"][other_sid] = {"source": "desktop", "engine": "codex", "managed": False}
        sb.save_sessions(state)
        commands = [self.command, second, other]
        self.backend.side_effect = lambda cfg, method, *args: {"commands": commands} if method == "GET" else {"ok": True}
        reads, submitted = [], []
        original = dict(self.entry)
        def snapshot(sid):
            reads.append(sid)
            entry = dict(self.entry if sid == self.sid else original)
            if reads.count(self.sid) == 1 and sid == self.sid:
                entry["status"] = "running"
            return entry, str(self.path), self.control.fingerprint(self.path)
        self.snapshot.side_effect = snapshot
        def reply(method, *args):
            if method == "thread-follower-start-turn":
                cid = args[0]["turnStart"]["request"]["clientUserMessageId"]
                submitted.append(cid)
                if args[0]["conversationId"] == self.sid:
                    # A delivered phone follow-up is a new native turn too;
                    # later queued input still requires context review.
                    self.entry["desktop_started_at"] = time.time() + 1
            return self.respond(method, *args)
        self.client.request.side_effect = reply
        cycles = []
        def sleep(_):
            cycles.append(True)
            if len(cycles) == 1:
                self.assertEqual(reads, [self.sid, other_sid])
                self.assertEqual(submitted, [other["command_id"]])
                self.assertEqual(sb.load_sessions()["local"][self.sid]["desktop_queued"], 2)
                self.assertFalse(self.receipt())
            else:
                raise StopControl()
        with patch.object(sb.time, "sleep", side_effect=sleep), patch.object(sb.time, "monotonic", return_value=100), \
             patch.object(sb, "sync_peers"):
            with self.assertRaises(StopControl):
                self.control.run()
        self.assertEqual(submitted, [other["command_id"], self.cid])
        receipts = sb.load_sessions()["codex_commands"]
        self.assertEqual(receipts[self.cid]["status"], "delivered")
        self.assertEqual(receipts[second["command_id"]]["status"], "failed")
        self.assertIn("newer desktop turn", receipts[second["command_id"]]["message"])
        self.assertEqual(sb.load_sessions()["local"][self.sid]["desktop_queued"], 0)

    def test_new_desktop_turn_invalidates_queue(self):
        self.entry["desktop_started_at"] = time.time() + 1
        self.control.execute(self.command)
        self.assertEqual(self.receipt()["status"], "failed")
        self.ipc_type.assert_not_called()

    def test_expired_or_future_commands_fail_closed(self):
        for created in (time.time() - 901, time.time() + 60):
            with self.subTest(created=created):
                self.cid = self.command["command_id"] = str(uuid.uuid4())
                self.command["created_at"] = created * 1000
                self.control.execute(self.command)
                self.assertEqual(self.receipt()["status"], "failed")
        self.ipc_type.assert_not_called()

    def test_missing_or_unknown_lifecycle_does_not_send(self):
        for status in ("waiting", "unknown"):
            self.cid = self.command["command_id"] = str(uuid.uuid4())
            self.entry["status"] = status
            self.control.execute(self.command)
            self.assertEqual(self.receipt()["status"], "failed")
        self.ipc_type.assert_not_called()

    def test_unavailable_version_fails_without_connecting(self):
        self.available.return_value = False
        self.control.execute(self.command)
        self.ipc_type.assert_not_called()
        self.assertEqual(self.receipt()["status"], "failed")

    def test_owner_missing_does_not_submit(self):
        self.client.request.side_effect = lambda *args: {"resultType": "error"}
        self.control.execute(self.command)
        self.assertEqual(self.client.request.call_count, 1)
        self.assertEqual(self.receipt()["status"], "failed")

    def test_changed_transcript_after_discovery_not_sent(self):
        def reply(*args):
            self.path.write_text("changed\n")
            return self.respond(*args)
        self.client.request.side_effect = reply
        self.control.execute(self.command)
        self.assertEqual(self.client.request.call_count, 1)
        self.assertEqual(self.receipt()["status"], "failed")

    def test_backend_ack_required(self):
        self.backend.return_value = {"ok": False}
        self.assertFalse(self.control.execute(self.command))
        self.assertEqual(self.client.request.call_count, 1)
        self.assertFalse(self.receipt())

    def test_ambiguous_submission_never_retried(self):
        def reply(method, *args):
            if method == "thread-follower-start-turn":
                raise TimeoutError("private text must not be logged")
            return self.respond(method, *args)
        self.client.request.side_effect = reply
        self.control.execute(self.command)
        self.assertEqual(self.receipt()["status"], "uncertain")
        self.control.execute(self.command)
        self.assertEqual(self.client.request.call_count, 2)
        self.assertNotIn("private text", json.dumps(self.receipt()))

    def test_success_without_turn_confirmation_is_uncertain(self):
        self.client.request.side_effect = lambda *a: {"resultType": "success", "handledByClientId": "owner"}
        self.control.execute(self.command)
        self.assertEqual(self.receipt()["status"], "uncertain")

    def test_crash_ledger_prevents_retry_if_state_lost(self):
        self.control.execute(self.command)
        state = sb.load_sessions()
        state.pop("codex_commands")
        sb.save_sessions(state)
        self.control.execute(self.command)
        self.assertEqual(self.receipt()["status"], "uncertain")
        self.assertEqual(self.client.request.call_count, 2)

    def test_backend_dispatching_after_restart_is_not_replayed(self):
        self.command["status"] = "dispatching"
        self.control.execute(self.command)
        self.assertEqual(self.receipt()["status"], "uncertain")
        self.ipc_type.assert_not_called()

    def test_failed_local_persistence_never_submits(self):
        with patch.object(sb, "save_sessions"):
            with self.assertRaises(OSError):
                self.control.execute(self.command)
        self.assertEqual(self.client.request.call_count, 1)

    def test_cli_bridge_does_not_claim_desktop_commands(self):
        bridge = sb.CodexBridge({})
        bridge.rpc = None  # CLI is offline, native control remains usable.
        with patch.object(bridge, "receipt") as receipt, patch.object(bridge, "attach") as attach:
            self.assertTrue(bridge.execute(self.command))
            receipt.assert_not_called()
            attach.assert_not_called()
        self.control.execute(self.command)
        self.assertEqual(self.receipt()["status"], "delivered")

    def test_no_cli_or_claude_commands_claimed(self):
        self.command["session_id"] = str(uuid.uuid4())
        self.assertTrue(self.control.execute(self.command))
        self.backend.assert_not_called()
        self.ipc_type.assert_not_called()

    def test_payload_strict_shape_and_limits(self):
        result = sb.codex_desktop_input(self.sid, self.cid, "hello")
        self.assertEqual(result["turnStart"]["request"]["input"],
                         [{"type": "text", "text": "hello", "text_elements": []}])
        self.assertEqual(set(result["turnStart"]["request"]), {"threadId", "clientUserMessageId", "input"})
        for text in ("", " " * 2, "x" * 4001, None):
            with self.assertRaises(ValueError):
                sb.codex_desktop_input(self.sid, self.cid, text)

    def test_snapshot_parses_real_lifecycle_and_rejects_partial_record(self):
        self.control.observer.index = Mock(return_value={self.sid: {"rollout_path": str(self.path)}})
        self.control.observer.metadata = Mock(return_value={"id": self.sid})
        import datetime
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        records = [{"timestamp": timestamp, "type": "event_msg", "payload": {"type": kind, "turn_id": "t1"}}
                   for kind in ("task_started", "task_complete")]
        self.path.write_text("".join(json.dumps(r) + "\n" for r in records))
        entry, _, _ = sb.CodexDesktopControl.snapshot(self.control, self.sid)
        self.assertEqual(entry["status"], "done")
        self.assertGreater(entry["desktop_started_at"], 0)
        with self.path.open("a") as f:
            f.write('{"type":')
        with self.assertRaises(ValueError):
            sb.CodexDesktopControl.snapshot(self.control, self.sid)

    def test_build_pin_and_safe_socket_are_required(self):
        self.control.ARCHIVE = str(self.root / "fixture.asar")
        body = b"fixture"
        index = json.dumps({"files": {"code.js": {"size": len(body), "offset": "0"}}}).encode()
        header = struct.pack("<IIII", 4, len(index) + 8, len(index) + 4, len(index))
        Path(self.control.ARCHIVE).write_bytes(header + index + body)
        import hashlib
        self.control.ASSETS = {"code.js": hashlib.sha256(body).hexdigest()}
        self.assertTrue(sb.CodexDesktopControl.available(self.control))
        Path(self.control.ARCHIVE).write_bytes(header + index + b"changed")
        self.assertFalse(sb.CodexDesktopControl.available(self.control))
        self.ipc_type.path.side_effect = PermissionError()
        self.assertFalse(sb.CodexDesktopControl.available(self.control))


class DesktopIPCTests(unittest.TestCase):
    def setUp(self):
        self.client = sb.CodexDesktopIPC.__new__(sb.CodexDesktopIPC)
        self.client.socket, self.server = socket.socketpair()
        self.client.client_id = "our-client"
        self.addCleanup(self.client.close)
        self.addCleanup(self.server.close)
        self.cid = str(uuid.uuid4())

    def feed(self, obj):
        data = json.dumps(obj).encode()
        self.server.sendall(struct.pack("<I", len(data)) + data)

    def test_successful_owner_routing_and_unrelated_broadcast_discard(self):
        self.feed({"type": "broadcast", "private": "ignored"})
        self.feed({"type": "client-discovery-request", "requestId": "discovery"})
        self.feed({"type": "response", "requestId": self.cid, "method": "thread-follower-start-turn",
                   "handledByClientId": "owner", "resultType": "success"})
        result = self.client.request("thread-follower-start-turn", {}, 2, "owner", self.cid)
        self.assertEqual(result["resultType"], "success")
        wire = self.server.recv(10000)
        first_length = struct.unpack("<I", wire[:4])[0]
        sent = json.loads(wire[4:4 + first_length])
        self.assertEqual(sent["sourceClientId"], "our-client")
        self.assertEqual(sent["targetClientId"], "owner")
        self.assertIn(b'"canHandle": false', wire)

    def test_wrong_owner_rejected(self):
        self.feed({"type": "response", "requestId": self.cid, "method": "thread-follower-start-turn",
                   "handledByClientId": "wrong-owner"})
        with self.assertRaises(ValueError):
            self.client.request("thread-follower-start-turn", {}, 2, "owner", self.cid)

    def test_wrong_method_rejected(self):
        self.feed({"type": "response", "requestId": self.cid, "method": "different",
                   "handledByClientId": "owner"})
        with self.assertRaises(ValueError):
            self.client.request("thread-follower-start-turn", {}, 2, "owner", self.cid)

    def test_oversized_frame_rejected_before_allocation(self):
        self.server.sendall(struct.pack("<I", 9 * 1024 * 1024))
        with self.assertRaises(ValueError):
            self.client.request("thread-owner-discovery", {}, 1)

    def test_unapproved_method_never_written(self):
        with self.assertRaises(ValueError):
            self.client.request("thread/resume", {}, 1)


if __name__ == "__main__":
    unittest.main()
