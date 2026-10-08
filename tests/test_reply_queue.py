"""P1: reply queue on the Mac — claim, inject once, ack; ledger dedupe; superseding."""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest.mock import patch, call
import test_codex

sb = test_codex.sb
CFG = {"backend_url": "https://b.invalid", "backend_secret": "s" * 20,
       "bundle_id": "dev.test", "environment": "sandbox"}


def reply(n, sid="s1", text=None, status="queued"):
    return {"id": f"00000000-0000-4000-8000-{n:012d}", "session_id": sid,
            "text": text or f"reply {n}", "status": status, "created_at": n}


class Backend:
    """Records calls; claim answers from `claimable`."""
    def __init__(self, claimable=()):
        self.calls, self.claimable = [], set(claimable)

    def __call__(self, cfg, method, path, body=None, timeout=8):
        self.calls.append((path, body))
        if path == "/api/reply/claim":
            return {"claimed": body["id"] in self.claimable}
        return {"ok": True}

    def acks(self):
        return [(b["id"][-2:], b["status"]) for p, b in self.calls if p == "/api/reply/ack"]

    def paths(self, path):
        return [b for p, b in self.calls if p == path]


class ReplyQueueTests(unittest.TestCase):
    def setUp(self):
        fixture = test_codex.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        os.environ.pop("SESSIONBELL_ENGINE", None)
        state = sb.load_sessions()
        state["local"]["s1"] = {"project": "p", "status": "running", "since": 1,
                                "pid": 42, "term_type": "otty", "pane": "p_1"}
        state["local"]["nopane"] = {"project": "q", "status": "running", "since": 1, "pid": 43}
        sb.save_sessions(state)

    def test_watcher_types_each_reply_once_in_order_and_acks(self):
        backend = Backend(claimable={reply(1)["id"], reply(2)["id"]})
        typed = []

        def type_(entry, text):
            # The prompt hook our typing triggers must already see it as ours.
            self.assertTrue(sb.is_injected_reply("s1", text))
            typed.append(text)
            return True, ""
        with patch.object(sb, "backend_call", backend), \
             patch.object(sb, "pid_alive", return_value=True), \
             patch.object(sb, "type_into_terminal", side_effect=type_):
            sb.watcher_deliver_replies(CFG, [reply(1), reply(2)])
        self.assertEqual(typed, ["reply 1", "reply 2"])
        self.assertEqual(backend.acks(), [("01", "delivered"), ("02", "delivered")])

    def test_failed_typing_hands_reply_back_and_forgets_it(self):
        backend = Backend(claimable={reply(1)["id"]})
        with patch.object(sb, "backend_call", backend), \
             patch.object(sb, "pid_alive", return_value=True), \
             patch.object(sb, "type_into_terminal", return_value=(False, "pane gone")):
            sb.watcher_deliver_replies(CFG, [reply(1)])
        self.assertEqual(backend.acks(), [("01", "queued")])
        self.assertNotIn(reply(1)["id"], sb.load_reply_ledger())

    def test_lapsed_claim_of_an_already_typed_reply_is_acked_not_retyped(self):
        sb.record_reply_delivered(reply(1))
        backend = Backend(claimable={reply(1)["id"]})
        with patch.object(sb, "backend_call", backend), \
             patch.object(sb, "pid_alive", return_value=True), \
             patch.object(sb, "type_into_terminal") as type_:
            sb.watcher_deliver_replies(CFG, [reply(1)])
        type_.assert_not_called()
        self.assertEqual(backend.paths("/api/reply/claim"), [])
        self.assertEqual(backend.acks(), [("01", "delivered")])

    def test_lost_claim_race_means_no_typing(self):
        backend = Backend(claimable=())
        with patch.object(sb, "backend_call", backend), \
             patch.object(sb, "pid_alive", return_value=True), \
             patch.object(sb, "type_into_terminal") as type_:
            sb.watcher_deliver_replies(CFG, [reply(1)])
        type_.assert_not_called()

    def test_session_without_terminal_route_waits_for_stop_and_is_remembered(self):
        backend = Backend(claimable={reply(1, "nopane")["id"]})
        with patch.object(sb, "backend_call", backend), \
             patch.object(sb, "pid_alive", return_value=True):
            sb.watcher_deliver_replies(CFG, [reply(1, "nopane")])
        self.assertEqual(backend.paths("/api/reply/claim"), [])
        self.assertEqual(sb.load_sessions()["local"]["nopane"]["pending_replies"], [reply(1, "nopane")["id"]])

    def test_stop_hook_continues_with_all_queued_replies(self):
        backend = Backend(claimable={reply(1)["id"], reply(2)["id"]})
        resp = {"command": None, "reply_rev": 7, "replies": [reply(2), reply(1), reply(3, "other")]}
        out = io.StringIO()
        with patch.object(sb, "backend_call", backend), \
             patch.object(sb, "backend_poll", return_value=resp), \
             patch.object(sb, "sync_peers"), patch.object(sb, "push_dashboard"), \
             patch.object(sb, "make_jwt"), patch.object(sb, "mac_idle_seconds", return_value=999), \
             patch.object(sys, "stdout", out):
            injected = sb.try_inject_command(CFG, "sandbox", "s1", "p", "mac", 30, watch_return=True)
        self.assertTrue(injected)
        decision = json.loads(out.getvalue())
        self.assertEqual(decision["decision"], "block")
        self.assertEqual(decision["followup_message"], "reply 1\n\nreply 2")
        self.assertEqual(backend.acks(), [("01", "delivered"), ("02", "delivered")])

    def test_human_typing_supersedes_pending_replies_but_our_own_typing_does_not(self):
        backend = Backend()
        state = sb.load_sessions()
        state["local"]["s1"]["pending_replies"] = [reply(2)["id"]]
        sb.save_sessions(state)
        sb.record_reply_delivered(reply(1, text="please continue"))

        def prompt(text):
            hook = {"session_id": "s1", "cwd": "/tmp/p", "prompt": text}
            with patch.object(sys, "argv", ["hook", "prompt"]), \
                 patch.object(sys, "stdin", io.StringIO(json.dumps(hook))), \
                 patch.object(sb, "load_config", return_value=dict(CFG)), \
                 patch.object(sb, "backend_call", backend), \
                 patch.object(sb, "engine_pids", return_value=(None, None)), \
                 patch.object(sb, "pid_alive", return_value=True), \
                 patch.object(sb, "terminal_handle", return_value=(None, None)), \
                 patch.object(sb, "sync_peers"), patch.object(sb, "push_dashboard"), \
                 patch.object(sb, "make_jwt"):
                sb.main()
        prompt("please   continue")            # our injected reply echoing back
        self.assertEqual(backend.paths("/api/reply/cancel"), [])
        prompt("actually, do something else")  # the human is back
        self.assertEqual([b["id"] for b in backend.paths("/api/reply/cancel")], [reply(2)["id"]])
        self.assertNotIn("pending_replies", sb.load_sessions()["local"]["s1"])

    def test_state_advertises_reply_queue_capability(self):
        backend = Backend()
        with patch.object(sb, "backend_call", backend), \
             patch.object(sb, "cached_usage", return_value={}), \
             patch.object(sb, "cached_codex_usage", return_value={}), \
             patch.object(sb, "recent_projects", return_value=[]), \
             patch.object(sb, "caffeinate_active", return_value=False):
            sb.sync_peers(CFG, sb.load_sessions(), "mac")
        self.assertEqual(backend.paths("/api/state")[0]["caps"], ["reply-queue"])


if __name__ == "__main__":
    unittest.main()
