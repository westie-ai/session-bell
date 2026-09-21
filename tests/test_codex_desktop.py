"""Native desktop observation, isolated from accounts, model calls and APNs."""
import datetime
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "desktop_sessionbell", Path(__file__).resolve().parents[1] / "mac/sessionbell_hook.py")
sb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sb)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "codex"
        (self.home / "sessions").mkdir(parents=True)
        for key, value in {"CONFIG_DIR": str(self.root),
                           "SESSIONS_PATH": str(self.root / "state.json"),
                           "LOG_PATH": str(self.root / "log")}.items():
            p = patch.object(sb, key, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(sb, "codex_alert")
        self.alert = p.start()
        self.addCleanup(p.stop)
        self.db = self.home / "state_5.sqlite"
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, cwd TEXT, title TEXT, updated_at INTEGER, archived INTEGER)")
        self.observer = sb.CodexDesktopObserver({}, str(self.home))

    def create(self, sid="desktop", originator="Codex Desktop", path=None):
        path = path or self.home / "sessions" / (sid + ".jsonl")
        path.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": sid, "originator": originator, "source": "vscode"}}) + "\n")
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("INSERT INTO threads VALUES (?,?,?,?,?,0)",
                               (sid, str(path), "/tmp/test-project", "Test task", int(time.time())))
        return path

    def append(self, kind, path=None, turn="turn-1", timestamp=None, **fields):
        stamp = datetime.datetime.fromtimestamp(timestamp or time.time(), datetime.timezone.utc).isoformat()
        event = {"timestamp": stamp, "type": "event_msg",
                 "payload": {"type": kind, "turn_id": turn, **fields}}
        with (path or self.home / "sessions/desktop.jsonl").open("a") as f:
            f.write(json.dumps(event) + "\n")

    def entry(self, sid="desktop"):
        return sb.load_sessions()["local"][sid]

    def test_silent_baseline_and_incremental_completion_once(self):
        self.create()
        self.append("task_started")
        self.append("task_complete", last_agent_message="old reply")
        self.observer.scan()
        self.assertEqual(self.entry()["status"], "done")
        self.alert.assert_not_called()
        self.append("task_started", turn="turn-2")
        self.observer.scan()
        self.assertEqual(self.entry()["status"], "running")
        self.append("task_complete", turn="turn-2", last_agent_message="new reply")
        self.observer.scan()
        self.assertEqual(self.entry()["latest_reply"], "new reply")
        self.assertEqual(self.entry()["source"], "desktop")
        self.assertFalse(self.entry()["managed"])
        self.observer.scan()
        sb.CodexDesktopObserver({}, str(self.home)).scan()
        self.alert.assert_called_once()

    def test_new_fast_turn_after_bootstrap(self):
        self.observer.scan()
        self.create()
        self.append("task_started")
        self.append("task_complete")
        self.observer.scan()
        self.alert.assert_called_once()

    def test_partial_line_is_retried(self):
        path = self.create()
        self.observer.scan()
        offset = self.observer.cursors["desktop"]["offset"]
        event = json.dumps({"type": "event_msg", "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "payload": {"type": "task_started", "turn_id": "t"}})
        with path.open("a") as f:
            f.write(event[:30])
        self.observer.scan()
        self.assertEqual(self.observer.cursors["desktop"]["offset"], offset)
        with path.open("a") as f:
            f.write(event[30:] + "\n")
        self.observer.scan()
        self.assertEqual(self.entry()["status"], "running")

    def test_malformed_and_missing_lifecycle_id_are_unknown(self):
        path = self.create()
        self.append("task_started")
        self.observer.scan()
        with path.open("a") as f:
            f.write("broken json\n")
        self.observer.scan()
        self.assertEqual(self.entry()["status"], "unknown")
        self.append("task_started")
        self.append("task_complete", turn=None)
        self.observer.scan()
        self.assertEqual(self.entry()["status"], "unknown")
        self.alert.assert_not_called()

    def test_replacement_is_silent(self):
        path = self.create()
        self.append("task_started")
        self.observer.scan()
        original_meta = path.read_text().splitlines()[0]
        replacement = path.with_suffix(".tmp")
        replacement.write_text(original_meta + "\n")
        replacement.replace(path)
        self.append("task_complete")
        self.observer.scan()
        self.assertEqual(self.entry()["status"], "done")
        self.alert.assert_not_called()

    def test_truncation_rebuilds_silently(self):
        path = self.create()
        self.append("task_started")
        self.append("task_complete", last_agent_message="x" * 1000)
        self.observer.scan()
        meta = path.read_text().splitlines()[0]
        path.write_text(meta + "\n")
        self.append("task_started", turn="new")
        self.observer.scan()
        self.assertEqual(self.entry()["status"], "running")
        self.alert.assert_not_called()

    def test_rejects_non_desktop_and_outside_paths(self):
        self.create("cli", "codex_cli_rs")
        self.create("extension", "VS Code")
        self.create("outside", path=self.root / "outside.jsonl")
        path = self.create("wrong-id")
        path.write_text(path.read_text().replace("wrong-id", "different-id"))
        self.observer.scan()
        self.assertEqual(sb.load_sessions()["local"], {})

    def test_cumulative_tokens_are_not_summed(self):
        self.create()
        self.append("task_started")
        for total in (100, 150, 150):
            self.append("token_count", info={"total_token_usage": {
                "total_tokens": total, "input_tokens": 120, "cached_input_tokens": 50,
                "output_tokens": 30, "secret": 99, "reasoning_output_tokens": -1,
                "cache_write_input_tokens": True}})
        self.observer.scan()
        self.assertEqual(self.entry()["token_usage"], {
            "total_tokens": 150, "input_tokens": 120, "cached_input_tokens": 50, "output_tokens": 30})

    def test_old_turn_completion_cannot_finish_new_turn(self):
        self.create()
        self.append("task_started", turn="new")
        self.append("task_complete", turn="old")
        self.observer.scan()
        self.assertEqual(self.entry()["status"], "running")

    def test_failed_turn_waits_instead_of_success(self):
        self.create()
        self.observer.scan()
        self.append("task_started")
        self.append("task_complete", error="failed")
        self.observer.scan()
        self.assertEqual(self.entry()["status"], "waiting")
        self.assertEqual(self.alert.call_args.args[2], "notification")

    def test_never_overwrites_managed_session(self):
        self.create()
        state = sb.load_sessions()
        state["local"]["desktop"] = {"managed": True, "status": "running"}
        sb.save_sessions(state)
        self.observer.scan()
        self.assertEqual(self.entry(), {"managed": True, "status": "running"})

    def test_unavailable_or_incompatible_index_is_not_deletion(self):
        self.create()
        self.observer.scan()
        self.db.rename(self.db.with_suffix(".backup"))
        self.assertFalse(self.observer.scan())
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("CREATE TABLE different (id TEXT)")
        self.assertFalse(self.observer.scan())
        self.assertEqual(self.entry()["source"], "desktop")

    def test_capability_timestamp_renews_without_dashboard_change(self):
        self.create()
        control = Mock(heartbeat=time.monotonic())
        control.available.return_value = True
        now = time.time()
        with patch.object(sb, "_CODEX_DESKTOP_CONTROL", control), patch.object(sb.time, "time", return_value=now):
            self.assertTrue(self.observer.scan())
            self.assertEqual(self.entry()["desktop_verified_at"], now)
        with patch.object(sb, "_CODEX_DESKTOP_CONTROL", control), patch.object(sb.time, "time", return_value=now + 20):
            self.assertFalse(self.observer.scan(), "Capability heartbeat alone must not push the dashboard")
            self.assertEqual(self.entry()["desktop_verified_at"], now + 20)
        self.alert.assert_not_called()

    def test_unavailable_index_cannot_renew_desktop_capability(self):
        self.create()
        control = Mock(heartbeat=time.monotonic())
        control.available.return_value = True
        now = time.time()
        with patch.object(sb, "_CODEX_DESKTOP_CONTROL", control), patch.object(sb.time, "time", return_value=now):
            self.observer.scan()
        with patch.object(self.observer, "index", return_value=None), patch.object(sb.time, "time", return_value=now + 60):
            self.assertFalse(self.observer.scan())
            self.assertEqual(self.entry()["desktop_verified_at"], now)
            self.assertGreaterEqual(sb.time.time() - self.entry()["desktop_verified_at"], 45)

    def test_failed_transcript_read_cannot_renew_desktop_capability(self):
        path = self.create()
        control = Mock(heartbeat=time.monotonic())
        control.available.return_value = True
        now = time.time()
        with patch.object(sb, "_CODEX_DESKTOP_CONTROL", control), patch.object(sb.time, "time", return_value=now):
            self.observer.scan()
        path.unlink()
        with patch.object(sb, "_CODEX_DESKTOP_CONTROL", control), patch.object(sb.time, "time", return_value=now + 60):
            self.assertFalse(self.observer.scan())
            self.assertEqual(self.entry()["desktop_verified_at"], now)

    def test_archive_removes_only_observed_record(self):
        self.create()
        self.observer.scan()
        state = sb.load_sessions()
        state["local"]["claude"] = {"engine": "claude", "status": "running"}
        sb.save_sessions(state)
        with contextlib.closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("UPDATE threads SET archived=1")
        self.observer.scan()
        self.assertNotIn("desktop", sb.load_sessions()["local"])
        self.assertIn("claude", sb.load_sessions()["local"])

    def test_permission_adapter_only_handles_validated_desktop(self):
        desktop = self.create()
        cli = self.create("cli", "codex_cli_rs")
        with patch.dict(os.environ, {"SESSIONBELL_ENGINE": "codex", "SESSIONBELL_DESKTOP_PERMISSIONS": "1", "SESSIONBELL_CODEX_SHARED": "", "CODEX_HOME": str(self.home)}), \
                patch.object(sb, "load_config", return_value={}), \
                patch.object(sb.sys, "argv", ["hook.py", "permission"]), \
                patch.object(sb, "handle_permission") as permission:
            for path, sid in ((cli, "cli"), (self.root / "missing", "desktop")):
                with patch.object(sb.sys, "stdin", io.StringIO(json.dumps({"session_id": sid, "transcript_path": str(path)}))):
                    sb.main()
            permission.assert_not_called()
            with patch.object(sb.sys, "stdin", io.StringIO(json.dumps({"session_id": "desktop", "transcript_path": str(desktop)}))):
                sb.main()
            permission.assert_called_once()

    def test_enable_never_changes_desktop_transport(self):
        config = self.root / "config.json"
        config.write_text("{}")
        socket = self.home / "app-server-control/app-server-control.sock"
        socket.parent.mkdir()
        socket.touch()
        with patch.object(sb, "IS_WIN", False), patch.object(sb.sys, "platform", "darwin"), \
                patch.object(sb, "codex_bin", return_value="/tmp/codex"), \
                patch.object(sb, "load_config", return_value={}), \
                patch.object(sb, "CONFIG_PATH", str(config)), \
                patch.object(sb.os.path, "expanduser", return_value=str(self.root / "LaunchAgents")), \
                patch.object(sb, "codex_home", return_value=str(self.home)), \
                patch.object(sb, "CodexRPC"), patch.object(sb.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()):
            run.return_value.stdout = ""
            sb.cmd_codex_enable()
        self.assertTrue(all(call.args[0][:2] == ["launchctl", "getenv"] for call in run.call_args_list))
        self.assertEqual(list((self.root / "LaunchAgents").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
