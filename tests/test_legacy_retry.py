"""A failed legacy delivery cannot replace a newer phone instruction."""
import contextlib
import unittest
from unittest.mock import patch
import test_codex


class LegacyRetryTests(unittest.TestCase):
    def test_failed_injection_uses_atomic_restore_with_original_timestamp(self):
        sb = test_codex.sb
        fixture = test_codex.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)

        class StopWatcher(BaseException):
            pass

        for restored in (True, False, None):
            with self.subTest(restored=restored), contextlib.ExitStack() as stack:
                state = sb.load_sessions()
                state["local"]["claude"] = {
                    "engine": "claude", "pid": 123, "term_type": "test", "cmd_ts": 0}
                sb.save_sessions(state)
                stack.enter_context(patch.object(sb.time, "time", return_value=2000))
                stack.enter_context(patch.object(sb.time, "sleep"))
                stack.enter_context(patch.object(sb, "host_label", return_value="test-mac"))
                for name in ("self_update", "usage_summary", "refresh_codex_usage", "sync_peers", "prune_sessions"):
                    stack.enter_context(patch.object(sb, name))
                stack.enter_context(patch.object(sb, "backend_poll", side_effect=[
                    {"commands": {"claude": {"ts": 1900000, "text": "old instruction"}}}, StopWatcher()]))
                stack.enter_context(patch.object(sb, "claim_command", return_value=True))
                stack.enter_context(patch.object(sb, "pid_alive", return_value=True))
                stack.enter_context(patch.object(sb, "type_into_terminal", return_value=(False, "offline")))
                backend = stack.enter_context(patch.object(sb, "backend_call",
                    return_value=None if restored is None else {"restored": restored}))
                with self.assertRaises(StopWatcher):
                    sb.run_watcher({})
                backend.assert_called_once_with({}, "POST", "/api/command/restore", {
                    "session_id": "claude", "text": "old instruction", "ts": 1900000})
                self.assertEqual(sb.load_sessions()["local"]["claude"]["cmd_ts"], 0)
