"""P0-3: failures get their own alert — never a completion, never a bare "needs you"."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch
import test_codex

sb = test_codex.sb
ROOT = Path(__file__).resolve().parents[1]
CFG = {"backend_url": "https://b.invalid", "backend_secret": "s" * 20,
       "bundle_id": "dev.test", "environment": "sandbox"}


class ClaudeStopFailureTests(unittest.TestCase):
    def setUp(self):
        fixture = test_codex.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        os.environ.pop("SESSIONBELL_ENGINE", None)

    def run_hook(self, hook):
        hook = {"session_id": "s1", "cwd": "/tmp/proj", "transcript_path": "", **hook}
        sent = []
        with contextlib.ExitStack() as st:
            st.enter_context(patch.object(sys, "argv", ["hook", "stop-failure"]))
            st.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(hook))))
            st.enter_context(patch.object(sb, "load_config", return_value=dict(CFG)))
            st.enter_context(patch.object(sb, "mac_idle_seconds", return_value=9999))
            st.enter_context(patch.object(sb, "host_label", return_value="mac"))
            st.enter_context(patch.object(sb, "make_jwt", return_value="jwt"))
            for name in ("sync_peers", "push_dashboard"):
                st.enter_context(patch.object(sb, name))
            st.enter_context(patch.object(sb, "resolve_device_tokens", return_value=["t"]))
            st.enter_context(patch.object(sb, "send_push",
                                          side_effect=lambda *a: sent.append(a[3]) or (200, "")))
            inject = st.enter_context(patch.object(sb, "try_inject_command"))
            sb.main()
        self.assertEqual(len(sent), 1, "exactly one alert per failure")
        inject.assert_not_called()
        return sent[0]

    def test_rate_limit_failure_has_failure_copy(self):
        payload = self.run_hook({"error": "rate_limit", "error_details": "429 from API"})
        alert = payload["aps"]["alert"]
        self.assertEqual(alert["title-loc-key"], "❌ %@ · 任务失败")
        self.assertEqual(alert["title-loc-args"], ["proj"])
        self.assertEqual(alert["loc-key"], sb.FAILURE_REASONS["rate_limit"])
        self.assertEqual(payload["sb"]["event"], "failure")
        self.assertIn("429 from API", payload["sb"]["md"])
        entry = sb.load_sessions()["local"]["s1"]
        self.assertEqual((entry["status"], entry["error"]), ("failed", "rate_limit"))

    def test_unknown_error_still_says_failed(self):
        payload = self.run_hook({"error": "something_new"})
        alert = payload["aps"]["alert"]
        self.assertEqual(alert["loc-key"], sb.FAILURE_FALLBACK)
        self.assertEqual(alert["loc-args"], ["Claude"])
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("完成", text)
        self.assertNotIn("Done", text)

    def test_every_failure_phrase_is_translated_in_the_app(self):
        catalog = json.loads((ROOT / "ios/Shared/Localizable.xcstrings").read_text())["strings"]
        keys = list(sb.FAILURE_REASONS.values()) + [sb.FAILURE_FALLBACK, "❌ %@ · 任务失败", "❌ %@ · 失败「%@」"]
        for k in keys:
            self.assertIn("en", catalog.get(k, {}).get("localizations", {}), k)


class CodexFailureTests(unittest.TestCase):
    def setUp(self):
        fixture = test_codex.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)

    def finish(self, status, **turn):
        bridge = sb.CodexBridge.__new__(sb.CodexBridge)
        bridge.cfg = dict(CFG)
        updates = {}
        bridge.update = lambda sid, **f: updates.update(f)
        with patch.object(sb, "codex_alert") as alert:
            bridge.finish("c1", {"id": "turn-1", "status": status, **turn})
        return updates["status"], alert.call_args.args[2], alert.call_args.args[3]

    def test_failed_turn_is_a_failure(self):
        status, kind, text = self.finish("failed", error={"message": "model overloaded"})
        self.assertEqual((status, kind, text), ("failed", "failure", "model overloaded"))

    def test_interrupted_turn_waits_for_input(self):
        status, kind, _ = self.finish("interrupted")
        self.assertEqual((status, kind), ("waiting", "notification"))

    def test_failure_title(self):
        sent = []
        with patch.object(sb, "resolve_device_tokens", return_value=["t"]), \
             patch.object(sb, "make_jwt", return_value="j"), \
             patch.object(sb, "host_label", return_value="mac"), \
             patch.object(sb, "mac_idle_seconds", return_value=0), \
             patch.object(sb, "send_push", side_effect=lambda *a: sent.append(a[3]) or (200, "")):
            sb.codex_alert(dict(CFG), "c1", "failure", "boom")
        self.assertEqual(len(sent), 1, "failure rings even at the keyboard, like completion")
        self.assertTrue(sent[0]["aps"]["alert"]["title"].startswith("❌ Codex · "))


class FailureHookRegistrationTests(unittest.TestCase):
    def setUp(self):
        fixture = test_codex.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.home = fixture.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        p = patch.dict(os.environ, {"HOME": str(self.home)})
        p.start(); self.addCleanup(p.stop)
        self.settings = self.home / ".claude/settings.json"

    def write(self, hooks):
        self.settings.write_text(json.dumps({"hooks": hooks}))

    def test_adds_async_stop_failure_next_to_our_stop_hook_once(self):
        self.write({"Stop": [{"hooks": [{"type": "command", "command": "/x/sessionbell_hook.py stop",
                                         "timeout": 960}]}]})
        self.assertTrue(sb.ensure_claude_failure_hook(quiet=False))
        self.assertFalse(sb.ensure_claude_failure_hook(quiet=False))
        h = json.loads(self.settings.read_text())["hooks"]["StopFailure"]
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["hooks"][0], {"type": "command", "command": "/x/sessionbell_hook.py stop-failure",
                                            "timeout": 30, "async": True})

    def test_leaves_settings_alone_without_our_stop_hook(self):
        self.write({"Stop": [{"hooks": [{"type": "command", "command": "other stop"}]}]})
        self.assertFalse(sb.ensure_claude_failure_hook(quiet=False))
        self.assertNotIn("StopFailure", json.loads(self.settings.read_text())["hooks"])

    def test_installers_register_stop_failure_async(self):
        for rel in ("mac/setup.sh", "backend-cf/public/install.sh", "windows/sessionbell_runner.py"):
            text = (ROOT / rel).read_text()
            self.assertRegex(text, r'"StopFailure": \(f?"[^"]*stop-failure", 30, True, None\)', rel)


if __name__ == "__main__":
    unittest.main()
