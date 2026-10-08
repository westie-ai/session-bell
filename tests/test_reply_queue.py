"""P1: reply queue on the Mac — claim, inject once, ack; ledger dedupe; superseding; backoff."""
import contextlib
import io
import json
import os
import sys
import threading
import unittest
from unittest.mock import patch
import test_codex

sb = test_codex.sb
CFG = {"backend_url": "https://b.invalid", "backend_secret": "s" * 20,
       "bundle_id": "dev.test", "environment": "sandbox"}


def reply(n, sid="s1", text=None, status="queued", created_at=None):
    return {"id": f"00000000-0000-4000-8000-{n:012d}", "session_id": sid,
            "text": text or f"reply {n}", "status": status,
            "created_at": created_at if created_at is not None else n}


class Backend:
    """Records calls; claim succeeds for ids in `claimable` (with claim ids c-<n>)."""
    def __init__(self, claimable=(), events=None):
        self.calls, self.claimable, self.events = [], set(claimable), events

    def __call__(self, cfg, method, path, body=None, timeout=8):
        self.calls.append((path, body))
        if self.events is not None:
            self.events.append(path)
        if path == "/api/reply/claim":
            ok = body["id"] in self.claimable
            return {"claimed": ok, "claim_id": "c-" + body["id"][-2:]} if ok else {"claimed": False}
        return {"ok": True}

    def acks(self):
        return [(b["id"][-2:], b["status"], b["claim_id"]) for p, b in self.calls if p == "/api/reply/ack"]

    def paths(self, path):
        return [b for p, b in self.calls if p == path]


class ReplyQueueTests(unittest.TestCase):
    def setUp(self):
        fixture = test_codex.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        os.environ.pop("SESSIONBELL_ENGINE", None)
        retry = getattr(sb, "_REPLY_RETRY", {})
        retry.clear()
        self.addCleanup(retry.clear)
        state = sb.load_sessions()
        state["local"]["s1"] = {"project": "p", "status": "running", "since": 1,
                                "pid": 42, "term_type": "otty", "pane": "p_1"}
        state["local"]["nopane"] = {"project": "q", "status": "running", "since": 1, "pid": 43}
        sb.save_sessions(state)

    def deliver(self, backend, replies, typing=(True, ""), now=1000.0):
        with patch.object(sb, "backend_call", backend), \
             patch.object(sb, "pid_alive", return_value=True), \
             patch.object(sb.time, "time", return_value=now), \
             patch.object(sb, "type_into_terminal",
                          side_effect=typing if callable(typing) else (lambda e, t: typing)) as type_:
            sb.watcher_deliver_replies(CFG, replies)
        return type_

    def test_watcher_types_each_reply_once_in_order_and_acks_with_its_claim(self):
        backend = Backend(claimable={reply(1)["id"], reply(2)["id"]})
        typed = []

        def type_(entry, text):
            # The prompt hook our typing triggers must already see it as ours.
            self.assertTrue(sb.is_injected_reply("s1", text))
            typed.append(text)
            return True, ""
        self.deliver(backend, [reply(2), reply(1)], typing=type_)
        self.assertEqual(typed, ["reply 1", "reply 2"])
        self.assertEqual(backend.acks(), [("01", "delivered", "c-01"), ("02", "delivered", "c-02")])

    def test_repeated_typing_failure_backs_off_then_leaves_it_to_the_stop_hook(self):
        r = reply(1)
        backend = Backend(claimable={r["id"]})
        fail = (False, "pane gone")
        self.deliver(backend, [r], typing=fail, now=1000)
        self.deliver(backend, [r], typing=fail, now=1001)        # the bumped rev wakes us at once
        self.assertEqual(len(backend.paths("/api/reply/claim")), 1, "no immediate retry")
        self.assertNotIn(r["id"], sb.load_reply_ledger())
        self.deliver(backend, [], typing=fail, now=1000 + 31)     # due retry from the local list
        self.deliver(backend, [], typing=fail, now=1000 + 31 + 121)
        self.deliver(backend, [r], typing=fail, now=100_000)     # gave up: Stop hook's job now
        self.assertEqual(len(backend.paths("/api/reply/claim")), 3)
        self.assertEqual([a[1] for a in backend.acks()], ["queued"] * 3)

    def test_lapsed_claim_of_an_already_typed_reply_is_acked_not_retyped(self):
        sb.record_reply_delivered(reply(1))
        backend = Backend(claimable={reply(1)["id"]})
        type_ = self.deliver(backend, [reply(1)])
        type_.assert_not_called()
        self.assertEqual(backend.acks(), [("01", "delivered", "c-01")])

    def retry_acks(self, backend, now):
        with patch.object(sb, "backend_call", backend), patch.object(sb.time, "time", return_value=now):
            sb.retry_unacked_replies(CFG)

    def type_with_lost_ack(self, r):
        class AckLost(Backend):
            def __call__(self, cfg, method, path, body=None, timeout=8):
                resp = super().__call__(cfg, method, path, body, timeout)
                return None if path == "/api/reply/ack" else resp
        sb._ACK_TRIED.clear()
        self.addCleanup(sb._ACK_TRIED.clear)
        self.deliver(AckLost(claimable={r["id"]}), [r], now=1000)
        self.assertTrue(sb.load_reply_ledger()[r["id"]].get("unacked"))

    def test_lost_delivered_ack_is_resent_by_the_watcher_until_it_lands(self):
        r = reply(1)
        self.type_with_lost_ack(r)
        backend = Backend()
        self.retry_acks(backend, now=1005)
        self.assertEqual(backend.acks(), [], "the typing process still gets its own go first")
        self.retry_acks(backend, now=1030)
        self.assertEqual(backend.acks(), [("01", "delivered", "c-01")], "same claim it was typed under")
        self.assertNotIn("unacked", sb.load_reply_ledger()[r["id"]])
        self.retry_acks(backend, now=1100)
        self.assertEqual(len(backend.acks()), 1, "nothing left to resend")

    def test_late_ack_after_the_claim_lapsed_reclaims_and_acks_without_typing(self):
        r = reply(1)
        self.type_with_lost_ack(r)

        class Lapsed(Backend):
            def __call__(self, cfg, method, path, body=None, timeout=8):
                resp = super().__call__(cfg, method, path, body, timeout)
                if path == "/api/reply/ack" and body["claim_id"] == "c-01":
                    return {"ok": False, "error": "not the claim holder"}
                if path == "/api/reply/claim":
                    return {"claimed": True, "claim_id": "c-02"}
                return resp
        backend = Lapsed()
        with patch.object(sb, "type_into_terminal") as type_:
            self.retry_acks(backend, now=1030)
            type_.assert_not_called()
        self.assertEqual(backend.acks(), [("01", "delivered", "c-01"), ("01", "delivered", "c-02")])
        self.assertNotIn("unacked", sb.load_reply_ledger()[r["id"]])

    def test_lost_claim_race_means_no_typing(self):
        type_ = self.deliver(Backend(claimable=()), [reply(1)])
        type_.assert_not_called()

    def test_session_without_terminal_route_waits_for_stop(self):
        backend = Backend(claimable={reply(1, "nopane")["id"]})
        self.deliver(backend, [reply(1, "nopane")])
        self.assertEqual(backend.paths("/api/reply/claim"), [])

    def stop(self, backend, resp):
        out = io.StringIO()
        with patch.object(sb, "backend_call", backend), \
             patch.object(sb, "backend_poll", return_value=resp), \
             patch.object(sb, "sync_peers"), patch.object(sb, "push_dashboard"), \
             patch.object(sb, "make_jwt"), patch.object(sb, "mac_idle_seconds", return_value=999), \
             patch.object(sys, "stdout", out):
            injected = sb.try_inject_command(CFG, "sandbox", "s1", "p", "mac", 30, watch_return=True)
        return injected, out.getvalue()

    def test_stop_hook_continues_with_all_queued_replies_then_acks(self):
        events = []
        backend = Backend(claimable={reply(1)["id"], reply(2)["id"]}, events=events)
        real_print = sb.print_stop_continue
        with patch.object(sb, "print_stop_continue",
                          side_effect=lambda t: (events.append("print"), real_print(t))):
            injected, out = self.stop(backend, {"command": None, "reply_rev": 7,
                                                "replies": [reply(2), reply(1), reply(3, "other")]})
        self.assertTrue(injected)
        self.assertEqual(json.loads(out)["followup_message"], "reply 1\n\nreply 2")
        self.assertEqual(backend.acks(), [("01", "delivered", "c-01"), ("02", "delivered", "c-02")])
        self.assertLess(events.index("print"), events.index("/api/reply/ack"), "hand over before ack")
        self.assertTrue(sb.is_injected_reply("s1", "reply 1\n\nreply 2"), "joined text counts as ours")

    def test_typing_at_the_mac_supersedes_older_replies_wherever_they_are_delivered(self):
        state = sb.load_sessions()
        sb.note_human_prompt(state, "s1", 2)   # typed at t=2 s
        sb.save_sessions(state)
        old, new = reply(1, created_at=1000), reply(2, created_at=3000)   # ms
        backend = Backend(claimable={old["id"], new["id"]})
        injected, out = self.stop(backend, {"command": None, "reply_rev": 1, "replies": [old, new]})
        self.assertEqual([b["id"] for b in backend.paths("/api/reply/cancel")], [old["id"]])
        self.assertEqual(json.loads(out)["followup_message"], "reply 2")

        backend = Backend(claimable={old["id"]})
        type_ = self.deliver(backend, [old])
        type_.assert_not_called()
        self.assertEqual([b["id"] for b in backend.paths("/api/reply/cancel")], [old["id"]])

    def test_prompt_hook_records_human_typing_locally_and_ignores_our_own(self):
        sb.record_reply_delivered(reply(1, text="please continue"))
        backend = Backend()

        def prompt(text, now):
            hook = {"session_id": "s1", "cwd": "/tmp/p", "prompt": text}
            with patch.object(sys, "argv", ["hook", "prompt"]), \
                 patch.object(sys, "stdin", io.StringIO(json.dumps(hook))), \
                 patch.object(sb, "load_config", return_value=dict(CFG)), \
                 patch.object(sb, "backend_call", backend), \
                 patch.object(sb, "engine_pids", return_value=(None, None)), \
                 patch.object(sb, "pid_alive", return_value=True), \
                 patch.object(sb, "terminal_handle", return_value=(None, None)), \
                 patch.object(sb, "sync_peers"), patch.object(sb, "push_dashboard"), \
                 patch.object(sb, "make_jwt"), \
                 patch.object(sb, "is_injected_reply", wraps=sb.is_injected_reply):
                sb.main()
        prompt("please   continue", 0)
        self.assertNotIn("s1", sb.load_sessions().get("human_prompts", {}))
        prompt("actually, do something else", 0)
        self.assertIn("s1", sb.load_sessions()["human_prompts"])
        self.assertFalse([p for p, _ in backend.calls if p.startswith("/api/reply")],
                         "no network in the prompt hook")

    def test_concurrent_ledger_writers_lose_nothing(self):
        threads = [threading.Thread(target=sb.record_reply_delivered, args=(reply(i),)) for i in range(1, 31)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(sb.load_reply_ledger()), 30)

    def test_state_advertises_reply_queue_capability(self):
        backend = Backend()
        with patch.object(sb, "backend_call", backend), \
             patch.object(sb, "cached_usage", return_value={}), \
             patch.object(sb, "cached_codex_usage", return_value={}), \
             patch.object(sb, "recent_projects", return_value=[]), \
             patch.object(sb, "caffeinate_active", return_value=False):
            sb.sync_peers(CFG, sb.load_sessions(), "mac")
        self.assertEqual(backend.paths("/api/state")[0]["caps"], ["reply-queue"])

    def test_alert_payload_tells_the_app_about_the_queue(self):
        sent = []
        hook = {"session_id": "s1", "cwd": "/tmp/p", "transcript_path": ""}
        with patch.object(sys, "argv", ["hook", "notification"]), \
             patch.object(sys, "stdin", io.StringIO(json.dumps(hook))), \
             patch.object(sb, "load_config", return_value=dict(CFG)), \
             patch.object(sb, "mac_idle_seconds", return_value=9999), \
             patch.object(sb, "pid_alive", return_value=True), \
             patch.object(sb, "engine_pids", return_value=(None, None)), \
             patch.object(sb, "host_label", return_value="mac"), \
             patch.object(sb, "sync_peers"), patch.object(sb, "push_dashboard"), \
             patch.object(sb, "make_jwt"), \
             patch.object(sb, "resolve_device_tokens", return_value=["t"]), \
             patch.object(sb, "send_push", side_effect=lambda *a: sent.append(a[3]) or (200, "")):
            sb.main()
        self.assertEqual(sent[0]["sb"]["caps"], ["reply-queue"])


if __name__ == "__main__":
    unittest.main()
