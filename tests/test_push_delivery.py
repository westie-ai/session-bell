"""P0-1a: one dead device token must not close the stop reply window."""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest.mock import patch
import test_codex

sb = test_codex.sb
CFG = {"backend_url": "https://b.invalid", "backend_secret": "s" * 20,
       "bundle_id": "dev.test", "environment": "sandbox"}


class StopReplyWindowTests(unittest.TestCase):
    def setUp(self):
        fixture = test_codex.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        os.environ.pop("SESSIONBELL_ENGINE", None)   # a Claude Code session

    def run_stop(self, push_results):
        hook = {"session_id": "s1", "cwd": "/tmp/proj", "transcript_path": ""}
        with contextlib.ExitStack() as st:
            st.enter_context(patch.object(sys, "argv", ["hook", "stop"]))
            st.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(hook))))
            st.enter_context(patch.object(sb, "load_config", return_value=dict(CFG)))
            st.enter_context(patch.object(sb, "self_update_from_hook"))
            st.enter_context(patch.object(sb, "mac_idle_seconds", return_value=9999))
            st.enter_context(patch.object(sb, "host_label", return_value="mac"))
            st.enter_context(patch.object(sb, "make_jwt", return_value="jwt"))
            for name in ("sync_peers", "push_dashboard"):
                st.enter_context(patch.object(sb, name))
            st.enter_context(patch.object(sb, "last_assistant_text", return_value="done it"))
            st.enter_context(patch.object(sb, "resolve_device_tokens",
                                          return_value=[f"t{i}" for i in range(len(push_results))]))
            st.enter_context(patch.object(sb, "send_push", side_effect=list(push_results)))
            inject = st.enter_context(patch.object(sb, "try_inject_command"))
            sb.main()
        return inject

    def test_dead_token_beside_live_one_keeps_reply_window(self):
        inject = self.run_stop([(410, '{"reason":"Unregistered"}'), (200, "")])
        inject.assert_called_once()

    def test_apns_429_on_one_token_keeps_reply_window(self):
        inject = self.run_stop([(200, ""), (429, '{"reason":"TooManyProviderTokenUpdates"}')])
        inject.assert_called_once()

    def test_nothing_delivered_skips_reply_window(self):
        inject = self.run_stop([(410, "")])
        inject.assert_not_called()


if __name__ == "__main__":
    unittest.main()
