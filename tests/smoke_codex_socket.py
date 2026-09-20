"""Opt-in real protocol smoke test. No login, model runs, or existing sessions.

Run: python3 -B tests/smoke_codex_socket.py
Creates an isolated temporary Codex home and server, then shuts both down.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "sessionbell", Path(__file__).resolve().parents[1] / "mac/sessionbell_hook.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


def main():
    # Darwin Unix socket paths are limited to 104 bytes; its default TMPDIR
    # plus app-server-control exceeds that before the socket can be created.
    with tempfile.TemporaryDirectory(prefix="sb-codex-", dir="/tmp") as temporary:
        directory = Path(temporary) / "app-server-control"
        directory.mkdir(mode=0o700)
        path = directory / "app-server-control.sock"
        binary = sb.codex_bin()
        if not binary:
            raise SystemExit("Install Codex before running this smoke test.")
        env = dict(os.environ, CODEX_HOME=temporary)
        env["PATH"] = str(Path(binary).parent) + os.pathsep + env.get("PATH", "")
        process = subprocess.Popen([binary, "app-server", "--listen", "unix://" + str(path)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not path.exists() and time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.05)
            with patch.object(sb, "codex_home", return_value=temporary):
                with sb.CodexRPC(shared=True) as rpc:
                    result = rpc.call("thread/loaded/list")
                    assert result.get("data") == [], "Expected an isolated server with no threads"
                    created = rpc.call("thread/start", {"cwd": temporary})["thread"]
                    with sb.CodexRPC(shared=True) as follower:
                        loaded = follower.call("thread/loaded/list")["data"]
                        assert created["id"] in loaded
                    print("PASS: Unix websocket handshake, initialize, two clients see the same loaded thread")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
