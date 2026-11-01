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

    def run_stop(self, push_results, idle=9999):
        hook = {"session_id": "s1", "cwd": "/tmp/proj", "transcript_path": ""}
        with contextlib.ExitStack() as st:
            st.enter_context(patch.object(sys, "argv", ["hook", "stop"]))
            st.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(hook))))
            st.enter_context(patch.object(sb, "load_config", return_value=dict(CFG)))
            st.enter_context(patch.object(sb, "self_update_from_hook"))
            st.enter_context(patch.object(sb, "mac_idle_seconds", return_value=idle))
            st.enter_context(patch.object(sb, "host_label", return_value="mac"))
            st.enter_context(patch.object(sb, "make_jwt", return_value="jwt"))
            dash = {name: st.enter_context(patch.object(sb, name))
                    for name in ("sync_peers", "push_dashboard")}
            publish = st.enter_context(patch.object(sb, "publish_async"))
            st.enter_context(patch.object(sb, "last_assistant_text", return_value="done it"))
            st.enter_context(patch.object(sb, "resolve_device_tokens",
                                          return_value=[f"t{i}" for i in range(len(push_results))]))
            push = st.enter_context(patch.object(sb, "send_push", side_effect=list(push_results)))
            inject = st.enter_context(patch.object(sb, "try_inject_command"))
            sb.main()
        inject.push = push
        inject.publish = publish
        inject.sync_peers = dash["sync_peers"]
        inject.push_dashboard = dash["push_dashboard"]
        return inject

    def test_dead_token_beside_live_one_keeps_reply_window(self):
        inject = self.run_stop([(410, '{"reason":"Unregistered"}'), (200, "")])
        inject.assert_called_once()

    def test_apns_429_on_one_token_keeps_reply_window(self):
        inject = self.run_stop([(200, ""), (429, '{"reason":"TooManyProviderTokenUpdates"}')])
        inject.assert_called_once()

    def test_user_at_keyboard_neither_rings_nor_polls_mailbox(self):
        # The old "quick mailbox check" long-polled for 10 s on every Stop
        # while the user sat at the desk; the relay watcher covers that now.
        inject = self.run_stop([(200, "")], idle=0)
        inject.assert_not_called()
        inject.push.assert_not_called()

    def test_dashboard_sync_is_detached_not_inline(self):
        # sync_peers + push_dashboard are 6-13 s of network over a VPN; the
        # hook hands them to a detached child and must not run them itself.
        inject = self.run_stop([(200, "")], idle=0)
        inject.publish.assert_called_once()
        inject.sync_peers.assert_not_called()
        inject.push_dashboard.assert_not_called()

    def test_publish_worker_syncs_peers_then_pushes_dashboard(self):
        calls = []
        with patch.object(sb, "load_sessions", return_value={"local": {}}), \
             patch.object(sb, "host_label", return_value="mac"), \
             patch.object(sb, "make_jwt", return_value="jwt"), \
             patch.object(sb, "sync_peers", side_effect=lambda *a: calls.append("sync")), \
             patch.object(sb, "push_dashboard", side_effect=lambda *a, **k: calls.append("push")):
            sb.cmd_publish(dict(CFG))
        self.assertEqual(calls, ["sync", "push"])

    def test_publish_async_spawns_detached_publish_worker(self):
        with patch.object(sb.subprocess, "Popen") as popen:
            sb.publish_async(dict(CFG))
        self.assertEqual(popen.call_args.args[0][-1], "publish")
        self.assertTrue(popen.call_args.kwargs.get("start_new_session") or popen.call_args.kwargs.get("creationflags"))

    def test_nothing_delivered_skips_reply_window(self):
        inject = self.run_stop([(410, "")])
        inject.assert_not_called()


    def test_unattended_sdk_run_gets_push_but_no_reply_window(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "sdk-ts"}):
            os.environ.pop("SESSIONBELL_SPAWNED", None)
            inject = self.run_stop([(200, "")])
        inject.assert_not_called()

    def test_phone_spawned_headless_run_keeps_reply_window(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli", "SESSIONBELL_SPAWNED": "1"}):
            inject = self.run_stop([(200, "")])
        inject.assert_called_once()

    def test_interactive_cli_keeps_reply_window(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "cli"}):
            inject = self.run_stop([(200, "")])
        inject.assert_called_once()


if __name__ == "__main__":
    unittest.main()
