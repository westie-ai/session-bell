"""P0-2: Live Activity state machine — stale, never-inferred completion, dismissal, priority."""
import contextlib
import json
import os
import unittest
from unittest.mock import patch
import test_codex

sb = test_codex.sb
CFG = {"bundle_id": "dev.test", "environment": "sandbox"}


class LiveActivityTests(unittest.TestCase):
    def setUp(self):
        fixture = test_codex.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        p = patch.object(sb, "TOKENS_PATH", str(root / "tokens.json"))
        p.start(); self.addCleanup(p.stop)
        sb.save_activity_tokens({"_dashboard": {"token": "ab" * 32}})
        for name in ("cached_usage",):
            q = patch.object(sb, name, return_value={})
            q.start(); self.addCleanup(q.stop)

    def state(self, *statuses, now=10_000):
        return {"local": {f"s{i}": {"project": f"p{i}", "status": st, "since": now - 5}
                          for i, st in enumerate(statuses)}, "peers": {}}

    def push(self, state, now=10_000):
        sent = []
        with patch.object(sb.time, "time", return_value=now), \
             patch.object(sb, "send_la_push",
                          side_effect=lambda *a: sent.append(a) or (200, "")):
            sb.push_dashboard(CFG, "jwt", "host", state, "mac")
        self.assertEqual(len(sent), 1)
        jwt, host, token, bundle, aps, *rest = sent[0]
        return aps, (rest[0] if rest else 10)

    def test_dead_process_mid_turn_is_failed_not_removed(self):
        state = self.state("running", "waiting")
        for e in state["local"].values():
            e["pid"] = 4242
        with patch.object(sb, "pid_alive", return_value=False):
            sb.prune_sessions(state, 10_000)
        # running → failed; waiting (turn already over) → a normal exit, removed
        self.assertEqual({k: e["status"] for k, e in state["local"].items()}, {"s0": "failed"})
        tasks = sb.merged_tasks(state, "mac", 10_000)
        self.assertEqual([t["status"] for t in tasks], ["failed"])

    def test_keepalive_refresh_never_starts_a_card(self):
        sb.save_activity_tokens({})
        sent = []
        with patch.object(sb.time, "time", return_value=10_000), \
             patch.object(sb, "send_la_push", side_effect=lambda *a: sent.append(a) or (200, "")):
            sb.push_dashboard(dict(CFG, live_activity_start_tokens=["cd" * 32]), "jwt", "host",
                              self.state("running"), "mac", refresh_only=True)
        self.assertEqual(sent, [])

    def test_running_update_is_routine_and_goes_stale_in_20_minutes(self):
        aps, priority = self.push(self.state("running"))
        self.assertEqual(aps["event"], "update")
        self.assertEqual(aps["stale-date"], 10_000 + 20 * 60)
        self.assertEqual(priority, 5)

    def test_waiting_update_is_urgent(self):
        aps, priority = self.push(self.state("waiting", "running"))
        self.assertEqual(priority, 10)

    def test_end_dismisses_after_60_seconds(self):
        aps, priority = self.push(self.state("done"))
        self.assertEqual(aps["event"], "end")
        self.assertEqual(aps["dismissal-date"], 10_000 + 60)
        self.assertEqual(priority, 10)

    def test_failed_only_card_ends_with_failed_state(self):
        aps, _ = self.push(self.state("failed"))
        self.assertEqual(aps["event"], "end")
        self.assertEqual([t["status"] for t in aps["content-state"]["tasks"]], ["failed"])

    def test_watcher_refreshes_stale_date_while_tasks_are_active(self):
        class StopWatcher(BaseException):
            pass
        state = sb.load_sessions()
        state["local"]["s"] = {"project": "p", "status": "running", "since": 1_000}
        sb.save_sessions(state)
        clock = [1_000.0]
        polls = []

        def poll(*a):
            polls.append(a)
            if len(polls) == 2:
                raise StopWatcher()
            clock[0] += sb.LA_REFRESH_SECONDS + 5
            return {"commands": {}}

        with contextlib.ExitStack() as st:
            st.enter_context(patch.object(sb.time, "time", side_effect=lambda: clock[0]))
            st.enter_context(patch.object(sb.time, "sleep", side_effect=lambda s: clock.__setitem__(0, clock[0] + s)))
            st.enter_context(patch.object(sb, "host_label", return_value="mac"))
            for name in ("self_update", "usage_summary", "refresh_codex_usage", "sync_peers",
                         "ensure_cursor_hooks", "make_jwt"):
                st.enter_context(patch.object(sb, name))
            st.enter_context(patch.object(sb, "backend_call", return_value={}))
            st.enter_context(patch.object(sb, "backend_poll", side_effect=poll))
            dash = st.enter_context(patch.object(sb, "push_dashboard"))
            with self.assertRaises(StopWatcher):
                sb.run_watcher({})
        dash.assert_called()


if __name__ == "__main__":
    unittest.main()
