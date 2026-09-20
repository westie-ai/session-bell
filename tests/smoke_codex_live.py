"""Opt-in authenticated integration test, using only a new read-only test thread.

Requires the local shared daemon and an existing Codex login. Makes two short
model calls; never reads existing conversations. Archives its own thread.
"""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from unittest.mock import patch
from contextlib import ExitStack

spec = importlib.util.spec_from_file_location(
    "sessionbell", Path(__file__).resolve().parents[1] / "mac/sessionbell_hook.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


def wait_turn(rpc, sid, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for message in rpc.poll(0.5):
            p = message.get("params") or {}
            if message.get("method") == "turn/completed" and p.get("threadId") == sid:
                assert p["turn"]["status"] == "completed", "Test turn did not complete"
                return
    raise TimeoutError("Test turn did not finish")


def main():
    with tempfile.TemporaryDirectory(prefix="sb-live-", dir="/tmp") as project:
        with sb.CodexRPC(shared=True) as owner:
            result = owner.call("thread/start", {
                "cwd": project, "sandbox": "read-only", "approvalPolicy": "never",
                "serviceName": "sessionbell_integration_test"})
            sid = result["thread"]["id"]
            try:
                owner.call("turn/start", {"threadId": sid,
                    "input": [{"type": "text", "text": "This is a SessionBell integration test. Do not use tools. Reply with exactly OK."}]})
                wait_turn(owner, sid)
                with sb.CodexRPC(shared=True) as follower:
                    result = follower.call("thread/resume", {"threadId": sid, "excludeTurns": True})
                    assert result["thread"]["id"] == sid
                    # Exercise the actual CLI against the same server/session.
                    binary = sb.codex_bin()
                    process = subprocess.run([binary, "queue", "--remote", "unix://", "--thread", sid,
                                              "--message", "Do not use tools. Reply with exactly NEXT."],
                                             capture_output=True, text=True, timeout=30)
                    assert process.returncode == 0, "CLI queue failed (output withheld)"
                    wait_turn(follower, sid)
                    print("PASS: model completion, second client resumes same thread, CLI sends follow-up")
            finally:
                try:
                    owner.call("thread/archive", {"threadId": sid})
                except Exception:
                    print("Test thread could not be archived; it remains in Codex history.")
        # Exercise the real SessionBell adapter without APNs or a production
        # backend: only the backend acknowledgments are replaced in this test.
        with ExitStack() as stack:
            for key, value in {"CONFIG_DIR": project, "SESSIONS_PATH": project + "/sessions.json",
                               "LOG_PATH": project + "/sessionbell.log"}.items():
                stack.enter_context(patch.object(sb, key, value))
            stack.enter_context(patch.object(sb, "backend_call", return_value={"ok": True}))
            stack.enter_context(patch.object(sb, "codex_alert"))
            stack.enter_context(patch.object(sb, "host_label", return_value="test-mac"))
            bridge = sb.CodexBridge({"backend_url": "https://example.invalid", "backend_secret": "test"})
            with sb.CodexRPC(shared=True) as rpc:
                bridge.rpc = rpc
                rpc.on_request = bridge.request
                cid = str(uuid.uuid4())
                bridge.execute({"command_id": cid, "action": "spawn", "cwd": project,
                                "text": "Do not use tools. Reply with exactly BRIDGE_OK."})
                sid = sb.load_sessions()["codex_commands"][cid]["session_id"]
                try:
                    assert sb.load_sessions()["codex_commands"][cid]["status"] == "delivered"
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        for event in rpc.poll(0.5):
                            bridge.event(event)
                        state = sb.load_sessions()["local"].get(sid, {})
                        if state.get("notified_turn"):
                            assert "BRIDGE_OK" in state.get("latest_reply", "")
                            print("PASS: SessionBell spawn, delivery receipt, completion state and reply extraction")
                            break
                    else:
                        raise TimeoutError("Bridge did not observe completion")
                    with sb.CodexRPC(shared=True) as follower:
                        observer = sb.CodexBridge(bridge.cfg)
                        observer.rpc = follower
                        observer.attach(sid)
                        assert "BRIDGE_OK" in sb.load_sessions()["local"][sid]["latest_reply"]
                        print("PASS: late subscriber hydrates the latest completed turn")
                finally:
                    rpc.call("thread/archive", {"threadId": sid})


if __name__ == "__main__":
    main()
