#!/usr/bin/env python3
"""SessionBell — Claude Code hook -> APNs push, no server required.

Usage:
  sessionbell_hook.py stop          # wired to the Stop hook
  sessionbell_hook.py notification  # wired to the Notification hook
  sessionbell_hook.py prompt        # wired to UserPromptSubmit — marks session running
  sessionbell_hook.py session-end   # wired to SessionEnd — drops session from the dashboard
  sessionbell_hook.py subagent-start  # wired to PreToolUse (Task|Agent) — bumps ⚙︎ badge
  sessionbell_hook.py subagent-stop   # wired to SubagentStop — drops ⚙︎ badge
  sessionbell_hook.py permission    # wired to PermissionRequest — approve/deny from the phone
  sessionbell_hook.py relay         # LAN HTTP listener: LA push tokens + peer-Mac state (launchd)
  sessionbell_hook.py test          # manual end-to-end test

Config lives at ~/.sessionbell/config.json, see config.example.json.
Signs an ES256 JWT with the Apple .p8 key via openssl (no pip deps) and
POSTs to APNs over HTTP/2 via curl.
"""
import base64
import json
import os
import subprocess
import sys
import time

CODEX_USAGE_MAX_AGE = 2 * 3600

CONFIG_DIR = os.path.expanduser("~/.sessionbell")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
JWT_CACHE_PATH = os.path.join(CONFIG_DIR, "jwt-cache.json")
LOG_PATH = os.path.join(CONFIG_DIR, "sessionbell.log")
TOKENS_PATH = os.path.join(CONFIG_DIR, "activity-tokens.json")
SESSIONS_PATH = os.path.join(CONFIG_DIR, "sessions.json")
DECISIONS_DIR = os.path.join(CONFIG_DIR, "decisions")
PENDING_APPROVAL_PATH = os.path.join(CONFIG_DIR, "pending-approval.json")
APPROVAL_FRESH_SECONDS = 600  # buttons vanish from the card after this

HOSTS = {
    "production": "api.push.apple.com",
    "sandbox": "api.sandbox.push.apple.com",
}
JWT_MAX_AGE = 45 * 60  # APNs rejects tokens older than 60 min; refresh at 45

IS_WIN = os.name == "nt"
# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — Windows stand-in for
# start_new_session=True (which is POSIX-only).
WIN_DETACHED = 0x00000008 | 0x00000200


def self_cmd(mode: str) -> list:
    """How to re-invoke this hook as a child process. Under the Windows exe
    the runner sets SESSIONBELL_RUNNER to its own path; plain python installs
    use the interpreter that's running us."""
    runner = os.environ.get("SESSIONBELL_RUNNER")
    if runner:
        return [runner, mode]
    return [sys.executable, os.path.abspath(__file__), mode]


def log(msg: str) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def load_config(kind: str) -> dict:
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        if kind == "test":
            sys.stderr.write(
                f"SessionBell: 缺少配置 {CONFIG_PATH}\n"
                "复制 config.example.json 过去并填入 team_id/key_id/p8_path/device_tokens。\n"
            )
            sys.exit(1)
        sys.exit(0)  # hooks silently no-op until configured

    has_backend = bool(cfg.get("backend_url") and cfg.get("backend_secret"))
    if has_backend:
        # Gateway mode: backend signs pushes and knows the device tokens —
        # only the bundle id is mandatory locally.
        required = ("bundle_id",)
    else:
        required = ("team_id", "key_id", "p8_path", "bundle_id")
    missing = [k for k in required if not cfg.get(k)]
    if missing or (not has_backend and not cfg.get("device_tokens")):
        if kind == "test":
            sys.stderr.write(f"SessionBell: 配置缺字段: {missing or 'device_tokens'}\n")
            sys.exit(1)
        sys.exit(0)
    return cfg


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def der_to_raw_sig(der: bytes) -> bytes:
    """Convert an openssl DER ECDSA signature to the raw r||s JWT form."""
    assert der[0] == 0x30
    idx = 2
    if der[1] & 0x80:
        idx = 2 + (der[1] & 0x7F)
    assert der[idx] == 0x02
    rlen = der[idx + 1]
    r = der[idx + 2 : idx + 2 + rlen]
    idx = idx + 2 + rlen
    assert der[idx] == 0x02
    slen = der[idx + 1]
    s = der[idx + 2 : idx + 2 + slen]
    return r.lstrip(b"\x00").rjust(32, b"\x00") + s.lstrip(b"\x00").rjust(32, b"\x00")


CAFFEINATE_PID = os.path.join(CONFIG_DIR, "caffeinate.pid")


def project_root(cwd: str) -> str:
    """Resolve a git worktree back to its main repo (/yusen/reply, not the
    worktree dir). The .git FILE of a linked worktree points at
    <main>/.git/worktrees/<name>."""
    import re
    gitfile = os.path.join(cwd, ".git")
    if os.path.isfile(gitfile):
        try:
            with open(gitfile) as f:
                m = re.search(r"gitdir:\s*(.+)", f.read())
            if m:
                main = re.sub(r"/\.git/worktrees/.*$", "", m.group(1).strip())
                if main != m.group(1).strip() and os.path.isdir(main):
                    return main
        except OSError:
            pass
    return cwd


PROJECTS_CACHE = os.path.join(CONFIG_DIR, "projects-cache.json")


def recent_projects(limit: int = 8) -> list:
    """Claude Code 自己的项目历史(~/.claude.json)按最近使用排序 —
    手机新建 session 时的目录候选,不依赖 SessionBell 的事件积累。"""
    try:
        with open(PROJECTS_CACHE) as f:
            c = json.load(f)
        if time.time() - c.get("ts", 0) < 600:
            return c.get("projects", [])
    except (OSError, ValueError):
        pass
    import re
    out = []
    try:
        with open(os.path.expanduser("~/.claude.json")) as f:
            paths = (json.load(f).get("projects") or {}).keys()
        scored = []
        for p in paths:
            if not os.path.isdir(p):
                continue
            enc = re.sub(r"[/.]", "-", p)
            try:
                mt = os.path.getmtime(os.path.expanduser(f"~/.claude/projects/{enc}"))
            except OSError:
                mt = 0
            scored.append((mt, project_root(p)))
        seen = set()
        for _, root in sorted(scored, reverse=True):
            if root not in seen:
                seen.add(root)
                out.append(root)
            if len(out) >= limit:
                break
    except (OSError, ValueError):
        pass
    try:
        with open(PROJECTS_CACHE, "w") as f:
            json.dump({"ts": int(time.time()), "projects": out}, f)
    except OSError:
        pass
    return out


def canonical_label(label: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", label.lower())


def caffeinate_active() -> bool:
    try:
        with open(CAFFEINATE_PID) as f:
            return pid_alive(int(f.read().strip()))
    except (OSError, ValueError):
        return False


def handle_sys_command(cfg: dict, text: str) -> None:
    if text == "caffeinate:on" and not caffeinate_active():
        if IS_WIN:
            # caffeinate(1) has no Windows twin — hold SetThreadExecutionState
            # in a child of our own; killing it releases the assertion.
            p = subprocess.Popen(self_cmd("stayawake"), creationflags=WIN_DETACHED)
        else:
            p = subprocess.Popen(["caffeinate", "-is"], start_new_session=True)
        with open(CAFFEINATE_PID, "w") as f:
            f.write(str(p.pid))
        log("caffeinate ON — Mac will stay awake")
    elif text == "caffeinate:off":
        try:
            with open(CAFFEINATE_PID) as f:
                os.kill(int(f.read().strip()), 15)
        except (OSError, ValueError):
            pass
        try:
            os.unlink(CAFFEINATE_PID)
        except OSError:
            pass
        log("caffeinate OFF")
    sync_peers(cfg, load_sessions(), host_label(cfg))  # reflect state to app


def _claude_works(p: str) -> bool:
    """Executable AND actually runs: an npm install that died halfway leaves a
    non-executable stub at ~/.nvm/.../bin/claude (or an executable one that
    just prints 'native package missing'), so existence alone proves nothing."""
    if not p or not os.path.isfile(p) or not os.access(p, os.X_OK):
        return False
    try:
        r = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=20)
        return r.returncode == 0 and "claude" in r.stdout.lower()
    except Exception:
        return False


def claude_bin():
    """Find a working `claude`, whichever way it was installed: the native
    installer (~/.local/bin), Homebrew, or npm under nvm / a global prefix.
    The result is cached, but a cached path that stopped working (user switched
    installers, npm reinstall broke) is re-resolved, not trusted."""
    cache = os.path.join(CONFIG_DIR, "claude-bin")
    try:
        with open(cache) as f:
            cached = f.read().strip()
        if _claude_works(cached):
            return cached
    except OSError:
        pass
    import glob as _glob
    import shutil
    candidates = []
    w = shutil.which("claude")
    if w:
        candidates.append(w)
    candidates += [os.path.expanduser("~/.local/bin/claude"),
                   "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    candidates += sorted(_glob.glob(os.path.expanduser(
        "~/.nvm/versions/node/*/bin/claude")), reverse=True)
    if not IS_WIN:
        # The relay runs under launchd with a bare PATH; the login shell knows
        # where the user's own install lives (npm prefix, volta, asdf...).
        try:
            r = subprocess.run(["/bin/zsh", "-ilc", "command -v claude"],
                               capture_output=True, text=True, timeout=15)
            if r.stdout.strip():
                candidates.append(r.stdout.strip().splitlines()[-1])
        except Exception:
            pass
    seen = set()
    for c in candidates:
        c = os.path.realpath(c) if os.path.islink(c) else c
        if c in seen:
            continue
        seen.add(c)
        if _claude_works(c):
            try:
                with open(cache, "w") as f:
                    f.write(c)
            except OSError:
                pass
            return c
    log("claude_bin: no working claude found; tried " + ", ".join(candidates))
    return None


def codex_home():
    return os.path.abspath(os.path.expanduser(os.environ.get("CODEX_HOME", "~/.codex")))


def codex_bin():
    """Also works under launchd, whose PATH does not include nvm or Homebrew."""
    import glob
    import shutil
    candidates = [shutil.which("codex"),
                  "/Applications/Codex.app/Contents/Resources/codex",
                  "/Applications/ChatGPT.app/Contents/Resources/codex",
                  os.path.expanduser("~/.local/bin/codex"),
                  "/opt/homebrew/bin/codex", "/usr/local/bin/codex"]
    candidates += sorted(glob.glob(os.path.expanduser(
        "~/.nvm/versions/node/*/bin/codex")), reverse=True)
    return next((p for p in candidates if p and os.access(p, os.X_OK)), None)


class CodexDesktopObserver:
    """Read-only desktop event observer; never resumes or controls a thread."""
    MAX_LINE = 4 * 1024 * 1024

    def __init__(self, cfg, home=None):
        self.cfg = cfg
        self.home = os.path.realpath(home or codex_home())
        self.started_at = time.time()
        self.bootstrapped = False
        self.cursor_path = os.path.join(CONFIG_DIR, "codex-desktop-cursors.json")
        try:
            with open(self.cursor_path) as f:
                self.cursors = json.load(f)
            if not isinstance(self.cursors, dict):
                self.cursors = {}
        except (OSError, ValueError):
            self.cursors = {}

    @staticmethod
    def timestamp(value):
        import datetime
        try:
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (AttributeError, TypeError, ValueError):
            return None

    def index(self):
        import glob
        import sqlite3
        from pathlib import Path
        rows = {}
        available = False
        databases = glob.glob(os.path.join(self.home, "state_*.sqlite"))
        databases += glob.glob(os.path.join(self.home, "sqlite", "state_*.sqlite"))
        for database in databases:
            connection = None
            try:
                connection = sqlite3.connect(Path(database).as_uri() + "?mode=ro", timeout=0.5)
                connection.row_factory = sqlite3.Row
                result = connection.execute(
                        "SELECT id,rollout_path,cwd,title,updated_at FROM threads "
                        "WHERE archived=0 AND updated_at>? ORDER BY updated_at DESC LIMIT 200",
                        (int(time.time()) - 2 * 86400,)).fetchall()
                available = True
                for row in result:
                    item = dict(row)
                    if item["updated_at"] >= rows.get(item["id"], {}).get("updated_at", 0):
                        rows[item["id"]] = item
            except (OSError, sqlite3.Error):
                continue
            finally:
                if connection:
                    connection.close()
        return rows if available else None

    def metadata(self, path, sid):
        path = os.path.realpath(path)
        if not any(os.path.commonpath([path, os.path.join(self.home, folder)]) == os.path.join(self.home, folder)
                   for folder in ("sessions", "archived_sessions")):
            return None
        try:
            with open(path, "rb") as f:
                line = f.readline(self.MAX_LINE + 1)
            if len(line) > self.MAX_LINE or not line.endswith(b"\n"):
                return None
            event = json.loads(line)
            data = event.get("payload") or {}
            if event.get("type") != "session_meta" or data.get("id") != sid:
                return None
            if str(data.get("originator", "")).lower() != "codex desktop":
                return None
            return data
        except (OSError, ValueError, AttributeError):
            return None

    def consume(self, entry, event):
        """Return completed turn ID only for an explicit terminal event."""
        kind = event.get("type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None
        ts = self.timestamp(event.get("timestamp"))
        if ts is None:
            return None
        if kind == "event_msg":
            event_type = payload.get("type")
            turn = payload.get("turn_id")
            if event_type in ("task_started", "task_complete", "turn_aborted") and not isinstance(turn, str):
                entry.update(status="unknown", delivery_error="Unrecognized desktop lifecycle event.")
                return None
            if event_type == "task_started" and isinstance(turn, str):
                entry.update(status="running", since=ts, desktop_started_at=ts,
                             desktop_turn=turn, latest_reply="", delivery_error="")
                entry.pop("desktop_terminal", None)
            elif event_type in ("task_complete", "turn_aborted"):
                if not isinstance(turn, str) or (entry.get("desktop_turn") and entry["desktop_turn"] != turn):
                    return None
                failed = event_type == "turn_aborted" or bool(payload.get("error"))
                entry.update(status="waiting" if failed else "done", since=ts,
                             desktop_turn=turn, desktop_terminal=turn,
                             delivery_error="Codex turn interrupted or failed." if failed else "")
                if isinstance(payload.get("last_agent_message"), str):
                    entry["latest_reply"] = clip_bytes(payload["last_agent_message"], 8000)
                return turn
            elif event_type == "token_count":
                info = payload.get("info") or {}
                totals = info.get("total_token_usage") if isinstance(info, dict) else None
                if isinstance(totals, dict):
                    # This is a cumulative snapshot, not a delta to be summed.
                    usage = {key: value for key, value in totals.items()
                             if key in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                                        "output_tokens", "reasoning_output_tokens", "total_tokens")
                             and type(value) is int and value >= 0}
                    if usage:
                        entry["token_usage"] = usage
        elif kind == "response_item" and payload.get("type") == "message":
            if payload.get("role") == "assistant":
                text = "\n".join(part.get("text", "") for part in payload.get("content", [])
                                 if isinstance(part, dict) and isinstance(part.get("text"), str))
                if text and payload.get("phase") == "final_answer":
                    entry["latest_reply"] = clip_bytes(text, 8000)
        return None

    def scan(self):
        import tempfile
        state = load_sessions()
        changed = False
        refreshed = False
        alerts = []
        now = time.time()
        rows = self.index()
        if rows is None:
            return False  # A locked/unavailable index is not a session deletion.
        for sid, row in rows.items():
            try:
                path = os.path.realpath(row["rollout_path"])
                stat = os.stat(path)
                fingerprint = [stat.st_dev, stat.st_ino]
                cursor = self.cursors.get(sid, {})
                old = state["local"].get(sid, {})
                if old.get("managed") or state.get("codex_sessions", {}).get(sid, {}).get("managed"):
                    continue
                reset = cursor.get("file") != fingerprint or stat.st_size < cursor.get("offset", 0)
                if reset or not cursor.get("desktop"):
                    if not self.metadata(path, sid):
                        continue
                    cursor = {"file": fingerprint, "offset": 0, "desktop": True}
                entry = dict(old or cursor.get("entry", {})) if not reset else {}
                entry.update(engine="codex", source="desktop", managed=False,
                             project=os.path.basename(row["cwd"].rstrip("/")) or "Codex",
                             cwd=row["cwd"], detail=str(row.get("title") or "")[:160],
                             agents=0)
                entry.setdefault("status", "unknown")
                entry.setdefault("since", row["updated_at"])
                control = _CODEX_DESKTOP_CONTROL
                entry["desktop_can_send"] = bool(control and control.available()
                                                  and time.monotonic() - control.heartbeat < 45)
                with open(path, "rb") as f:
                    f.seek(cursor["offset"])
                    for _ in range(20000):
                        position = f.tell()
                        line = f.readline(self.MAX_LINE + 1)
                        if not line:
                            break
                        if len(line) > self.MAX_LINE:
                            # Skip a complete oversized record without loading it.
                            while line and not line.endswith(b"\n"):
                                line = f.readline(self.MAX_LINE + 1)
                            if not line.endswith(b"\n"):
                                f.seek(position)
                                break
                            cursor["offset"] = f.tell()
                            entry.update(status="unknown", delivery_error="Unsupported desktop event size.")
                            continue
                        if not line.endswith(b"\n"):
                            break  # writer is still appending this record
                        cursor["offset"] = f.tell()
                        try:
                            event = json.loads(line)
                            if not isinstance(event, dict):
                                continue
                            turn = self.consume(entry, event)
                        except (ValueError, TypeError, AttributeError):
                            entry.update(status="unknown", delivery_error="Unrecognized desktop event.")
                            continue
                        if turn:
                            fresh = (self.timestamp(event.get("timestamp")) or 0) >= self.started_at
                            if (self.bootstrapped and not reset and fresh
                                    and entry.get("desktop_notified_turn") != turn):
                                alerts.append((sid, turn))
                            entry["desktop_notified_turn"] = turn
                # New files after startup may already contain a whole fast turn.
                if (reset and self.bootstrapped and entry.get("desktop_terminal")
                        and entry.get("since", 0) >= self.started_at
                        and sid not in self.cursors):
                    alerts.append((sid, entry["desktop_terminal"]))
                if entry.get("status") == "running" and now - entry.get("since", now) > RUNNING_MAX_AGE:
                    entry.update(status="unknown", delivery_error="No recent desktop activity; status is unknown.")
                # Host publications can outlive this observer. Only successful
                # reads renew the capability; timestamp-only renewals must not
                # generate dashboard/APNs updates every scan.
                if not entry["desktop_can_send"]:
                    entry["desktop_verified_at"] = 0
                elif now - old.get("desktop_verified_at", 0) >= 15:
                    entry["desktop_verified_at"] = now
                limit = {"done": DONE_LINGER_SECONDS, "waiting": WAITING_LINGER_SECONDS,
                         "running": RUNNING_MAX_AGE}.get(entry.get("status"), SESSION_MAX_AGE)
                if now - entry.get("since", 0) <= limit:
                    if entry != old:
                        state["local"][sid] = entry
                        if ({k: v for k, v in entry.items() if k != "desktop_verified_at"}
                                != {k: v for k, v in old.items() if k != "desktop_verified_at"}):
                            changed = True
                        else:
                            refreshed = True
                elif old.get("source") == "desktop":
                    state["local"].pop(sid, None)
                    changed = True
                cursor["entry"] = entry
                self.cursors[sid] = cursor
            except (OSError, ValueError, TypeError):
                continue
        # Remove only observed desktop records no longer in the active index.
        for sid, entry in list(state["local"].items()):
            if entry.get("source") == "desktop" and sid not in rows:
                state["local"].pop(sid)
                changed = True
        if changed or refreshed:
            save_sessions(state)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="desktop-cursors-", dir=CONFIG_DIR)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({sid: c for sid, c in self.cursors.items()
                           if sid in rows or now - c.get("entry", {}).get("since", 0) < 2 * 86400}, f)
            os.replace(tmp, self.cursor_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        # Persist before notifying: a relay restart cannot replay old completions.
        for sid, turn in set(alerts):
            entry = state["local"].get(sid, {})
            if entry.get("desktop_terminal") == turn:
                codex_alert(self.cfg, sid, "stop" if entry["status"] == "done" else "notification",
                            entry.get("latest_reply") or entry.get("delivery_error") or "Codex completed this turn.")
        self.bootstrapped = True
        return changed

    def run(self):
        while True:
            try:
                if self.scan():
                    state = load_sessions()
                    label = host_label(self.cfg)
                    sync_peers(self.cfg, state, label)
                    push_dashboard(self.cfg, make_jwt(self.cfg), HOSTS[self.cfg.get("environment", "sandbox")], state, label)
            except Exception as exc:
                log("Codex desktop observer: " + type(exc).__name__)
            time.sleep(2)


class CodexLocalSocket:
    """RFC 6455 over Codex's local Unix socket, using only the standard library."""
    def __init__(self, path):
        import hashlib
        import socket
        import stat
        import threading
        info = os.stat(path)
        parent = os.stat(os.path.dirname(path))
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
                or parent.st_uid != os.getuid() or parent.st_mode & 0o022):
            raise RuntimeError("Codex socket must be owned and protected by the current user")
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.write_lock = threading.Lock()
        self.socket.settimeout(10)
        try:
            self.socket.connect(path)
            key = base64.b64encode(os.urandom(16)).decode()
            self.socket.sendall(("GET /rpc HTTP/1.1\r\nHost: localhost\r\n"
                                 "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                                 "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: " + key +
                                 "\r\n\r\n").encode())
            header = b""
            while not header.endswith(b"\r\n\r\n") and len(header) < 16384:
                header += self.read_exact(1)
            lines = header.decode("ascii").split("\r\n")
            fields = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
            fields = {k.lower(): v.strip() for k, v in fields.items()}
            expected = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if " 101 " not in lines[0] or fields.get("sec-websocket-accept") != expected:
                raise RuntimeError("Codex websocket handshake failed")
            self.socket.settimeout(None)
        except Exception:
            self.socket.close()
            raise

    def read_exact(self, size):
        data = bytearray()
        while len(data) < size:
            part = self.socket.recv(size - len(data))
            if not part:
                raise EOFError("Codex socket closed")
            data.extend(part)
        return bytes(data)

    def send_frame(self, payload, opcode=1):
        import struct
        size = len(payload)
        head = bytes([0x80 | opcode])
        head += (bytes([0x80 | size]) if size < 126 else
                 b"\xfe" + struct.pack("!H", size) if size < 65536 else
                 b"\xff" + struct.pack("!Q", size))
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.write_lock:
            self.socket.sendall(head + mask + masked)

    def send(self, message):
        self.send_frame(json.dumps(message, ensure_ascii=False).encode())

    def read(self):
        import struct
        fragments = bytearray()
        while True:
            first, second = self.read_exact(2)
            size = second & 127
            if size == 126:
                size = struct.unpack("!H", self.read_exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read_exact(8))[0]
            if size + len(fragments) > 16 * 1024 * 1024 or second & 128:
                raise RuntimeError("Invalid Codex websocket frame")
            payload = self.read_exact(size)
            opcode = first & 15
            if opcode == 8:
                raise EOFError("Codex websocket closed")
            if opcode == 9:
                self.send_frame(payload, 10)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1):
                raise RuntimeError("Unsupported Codex websocket frame")
            fragments.extend(payload)
            if first & 128:
                return json.loads(fragments.decode())

    def close(self):
        import socket
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.socket.close()


class CodexRejected(RuntimeError):
    """An explicit server rejection, distinct from an ambiguous disconnect."""


class CodexRPC:
    """Bounded JSONL client. Never prints protocol payloads or credentials.

    A fresh stdio server is only used for account reads. Session control must
    connect to the owning shared server; resuming on a second server can fork
    the live state of a desktop conversation.
    """
    def __init__(self, shared=False, executable=None):
        import queue
        import threading
        binary = executable or codex_bin()
        if not binary:
            raise RuntimeError("Codex is not installed")
        self.process = None
        self.socket = None
        if shared:
            sock = os.path.join(codex_home(), "app-server-control", "app-server-control.sock")
            if not os.path.exists(sock):
                raise RuntimeError("Codex shared server is not running")
            self.socket = CodexLocalSocket(sock)
        else:
            args = [binary, "app-server", "--stdio"]
            env = dict(os.environ)
            env["PATH"] = os.path.dirname(binary) + os.pathsep + env.get("PATH", "")
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        self.messages = queue.Queue()
        self.sequence = 0
        self.notifications = []
        self.on_request = None

        def read():
            try:
                if self.socket:
                    while True:
                        self.messages.put(self.socket.read())
                else:
                    for line in self.process.stdout:
                        try:
                            self.messages.put(json.loads(line))
                        except ValueError:
                            continue
            except (OSError, EOFError, ValueError, RuntimeError):
                pass
            finally:
                self.messages.put(None)

        threading.Thread(target=read, daemon=True).start()
        try:
            self.call("initialize", {"clientInfo": {
                "name": "sessionbell", "title": "SessionBell", "version": "1.0"},
                "capabilities": {"experimentalApi": True}}, timeout=15)
            self.send({"method": "initialized"})
        except Exception:
            self.close()
            raise

    def send(self, message):
        if self.socket:
            self.socket.send(message)
            return
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def call(self, method, params=None, timeout=20):
        import queue
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError("Codex request timed out: " + method)
            if message is None:
                raise RuntimeError("Codex connection closed")
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    # Server errors may contain prompts/paths; keep them off logs.
                    error = CodexRejected("Codex rejected " + method + " (" +
                                          str(message["error"].get("code", "unknown")) + ")")
                    error.details = message["error"]
                    raise error
                return message.get("result") or {}
            if "method" in message and "id" in message:
                if self.on_request:
                    self.on_request(message)
                else:
                    self.send({"id": message["id"], "error": {
                        "code": -32601, "message": "SessionBell read-only client"}})
            else:
                self.notifications.append(message)

    def poll(self, timeout=0.2):
        import queue
        events, self.notifications = self.notifications, []
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty:
            return events
        if message is None:
            raise EOFError("Codex connection closed")
        if "method" in message and "id" in message:
            if self.on_request:
                self.on_request(message)
        elif "method" in message:
            events.append(message)
        return events

    def close(self):
        if self.socket:
            self.socket.close()
            return
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


_CODEX_BRIDGE = None
_CODEX_DESKTOP_CONTROL = None


def codex_is_desktop(sid, state=None):
    state = state if state is not None else load_sessions()
    if state.get("local", {}).get(sid, {}).get("source") == "desktop":
        return True
    # A completed conversation may have aged out of the dashboard.
    try:
        with open(os.path.join(CONFIG_DIR, "codex-desktop-cursors.json")) as f:
            return json.load(f).get(sid, {}).get("desktop") is True
    except (OSError, ValueError, AttributeError):
        return False


def codex_desktop_input(sid, cid, text):
    import uuid
    uuid.UUID(sid)
    uuid.UUID(cid)
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("Follow-up must contain between 1 and 4000 characters.")
    return {"conversationId": sid, "turnStart": {
        "request": {"threadId": sid, "clientUserMessageId": cid,
                    "input": [{"type": "text", "text": text, "text_elements": []}]},
        "context": {"inheritThreadSettings": True, "attachments": [], "commentAttachments": []}}}


class CodexDesktopIPC:
    """Pinned native IPC, not the CLI app-server; no resume/configuration calls."""
    @staticmethod
    def path():
        import stat
        path = os.path.join(codex_home(), "ipc", "ipc.sock")
        info, parent = os.lstat(path), os.lstat(os.path.dirname(path))
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
                or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or info.st_mode & 0o077 or parent.st_mode & 0o077):
            raise ValueError("Desktop socket permissions are unsafe.")
        return path

    def __init__(self):
        import socket
        path = self.path()
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(8)
        self.client_id = "initializing-client"
        try:
            self.socket.connect(path)
            initial = self.request("initialize", {"clientType": "sessionbell-relay"}, 0)
            self.client_id = initial["result"]["clientId"]
            if initial.get("resultType") != "success" or not isinstance(self.client_id, str):
                raise ValueError("Desktop initialization rejected.")
        except Exception:
            self.close()
            raise

    def close(self):
        self.socket.close()

    def send(self, message):
        import struct
        data = json.dumps(message).encode()
        self.socket.settimeout(8)
        self.socket.sendall(struct.pack("<I", len(data)) + data)

    def exact(self, count, deadline):
        data = bytearray()
        while len(data) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Desktop response timed out.")
            self.socket.settimeout(remaining)
            part = self.socket.recv(count - len(data))
            if not part:
                raise EOFError("Desktop disconnected.")
            data.extend(part)
        return bytes(data)

    def request(self, method, params, version, target=None, request_id=None):
        import struct
        import uuid
        if (method, version) not in (("initialize", 0), ("thread-owner-discovery", 1),
                                     ("thread-follower-start-turn", 2)):
            raise ValueError("Unsupported desktop method.")
        rid = request_id or str(uuid.uuid4())
        message = {"type": "request", "method": method, "version": version,
                   "requestId": rid, "sourceClientId": self.client_id,
                   "params": params, "timeoutMs": 15000}
        if target:
            message["targetClientId"] = target
        self.send(message)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            size = struct.unpack("<I", self.exact(4, deadline))[0]
            if not 0 < size <= 8 * 1024 * 1024:
                raise ValueError("Unsupported desktop frame size.")
            event = json.loads(self.exact(size, deadline))
            if not isinstance(event, dict):
                raise ValueError("Invalid desktop response.")
            if event.get("type") == "client-discovery-request":
                self.send({"type": "client-discovery-response", "requestId": event["requestId"],
                           "response": {"canHandle": False}})
            elif event.get("type") == "response" and event.get("requestId") == rid:
                if event.get("method") != method or (target and event.get("handledByClientId") != target):
                    raise ValueError("Unexpected desktop response routing.")
                return event
            # Never log or persist unrelated conversation broadcasts.
        raise TimeoutError("Desktop response timed out.")


class CodexDesktopControl:
    """Opt-in pilot. Independent of CLI connectivity; ambiguous writes never retry."""
    ARCHIVE = "/Applications/ChatGPT.app/Contents/Resources/app.asar"
    ASSETS = {
        "webview/assets/app-initial-1b87ae739476.js": "c87b94027faefdc31cc165975dc0f14b28e3f6d922f6a5188756c8f570f2b3d7",
        ".vite/build/src-J2PvP4xj.js": "4cc980cd737b02f999b9fe8d9757c37d2ce86c928043f19f46d56cc52bce8f66",
    }

    def __init__(self, cfg):
        import threading
        self.cfg = cfg
        self.host = canonical_label(host_label(cfg))
        self.wake = threading.Event()
        self.wake.set()
        self.heartbeat = 0
        self.build_key = None
        self.build_ok = False
        self.observer = CodexDesktopObserver(cfg)

    @staticmethod
    def fingerprint(path):
        info = os.stat(path)
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    def available(self):
        import hashlib
        import struct
        if not self.cfg.get("codex_desktop_followup"):
            return False
        try:
            CodexDesktopIPC.path()
            key = self.fingerprint(self.ARCHIVE)
            if self.build_key != key:
                self.build_key = key
                self.build_ok = False
                with open(self.ARCHIVE, "rb") as f:
                    header = f.read(16)
                    size = struct.unpack_from("<I", header, 12)[0]
                    if not 0 < size <= 16 * 1024 * 1024:
                        return False
                    index = json.loads(f.read(size))
                    base = 8 + struct.unpack_from("<I", header, 4)[0]
                    for path, expected in self.ASSETS.items():
                        entry = index
                        for part in path.split("/"):
                            entry = entry["files"][part]
                        if not 0 < entry["size"] <= 32 * 1024 * 1024:
                            return False
                        f.seek(base + int(entry["offset"]))
                        if hashlib.sha256(f.read(entry["size"])).hexdigest() != expected:
                            return False
                self.build_ok = self.fingerprint(self.ARCHIVE) == key
                self.build_key = key
            return self.build_ok
        except (OSError, ValueError, KeyError, TypeError, struct.error):
            return False

    def snapshot(self, sid):
        row = (self.observer.index() or {}).get(sid)
        if not row or not self.observer.metadata(row["rollout_path"], sid):
            raise ValueError("Open the original conversation in Codex Desktop first.")
        path = os.path.realpath(row["rollout_path"])
        before = self.fingerprint(path)
        if before[2] > 64 * 1024 * 1024:
            raise ValueError("This conversation is too large for the desktop pilot.")
        entry = {}
        with open(path, "rb") as f:
            for _ in range(100000):
                line = f.readline(self.observer.MAX_LINE + 1)
                if not line:
                    break
                if not line.endswith(b"\n") or len(line) > self.observer.MAX_LINE:
                    raise ValueError("Desktop history is updating or unsupported. Not sent.")
                self.observer.consume(entry, json.loads(line))
            else:
                raise ValueError("Desktop history exceeds the pilot limit.")
        if self.fingerprint(path) != before:
            raise ValueError("Desktop conversation changed during validation. Not sent.")
        return entry, path, before

    def ack(self, command, status, message=""):
        response = backend_call(self.cfg, "POST", "/api/codex", {
            "host": self.host, "command_id": command["command_id"], "action": "ack",
            "status": status, "message": message, "session_id": command.get("session_id", "")})
        return isinstance(response, dict) and response.get("ok") is True

    def receipt(self, command, status, message=""):
        state = load_sessions()
        record = {"status": status, "message": message, "session_id": command["session_id"], "ts": int(time.time())}
        state.setdefault("codex_commands", {})[command["command_id"]] = record
        entry = state["local"].get(command["session_id"])
        if entry and entry.get("source") == "desktop":
            entry["desktop_delivery"] = message or ("Sent to the original desktop conversation." if status == "delivered" else status)
        save_sessions(state)
        if load_sessions().get("codex_commands", {}).get(command["command_id"]) != record:
            raise OSError("Could not persist desktop delivery receipt")
        self.ack(command, status, message)

    def execute(self, command):
        import fcntl
        import uuid
        sid, cid = command.get("session_id", ""), command.get("command_id", "")
        if command.get("action") != "send" or not codex_is_desktop(sid):
            return True  # The CLI bridge owns other commands.
        uuid.UUID(cid)
        directory = os.path.join(CONFIG_DIR, "desktop-dispatch")
        os.makedirs(directory, mode=0o700, exist_ok=True)
        # A durable, content-free ledger survives state loss; flock prevents two
        # relays dispatching a command concurrently. Never delete/recycle it.
        with open(os.path.join(directory, cid), "a+b") as ledger:
            os.fchmod(ledger.fileno(), 0o600)
            try:
                fcntl.flock(ledger, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            previous = load_sessions().get("codex_commands", {}).get(cid)
            if previous:
                status = "uncertain" if previous["status"] == "dispatching" else previous["status"]
                message = previous.get("message", "")
                if status == "uncertain" and not message:
                    message = "Check the original desktop conversation before sending again."
                self.receipt(command, status, message)
                return True
            if ledger.tell() or command.get("status") == "dispatching":
                self.receipt(command, "uncertain", "Delivery could not be confirmed. Check Codex Desktop; no automatic retry.")
                return True
            client, submitted = None, False
            try:
                params = codex_desktop_input(sid, cid, command.get("text"))
                if not self.available():
                    raise ValueError("Desktop follow-up is unavailable for this version. Continue on your Mac.")
                created = command.get("created_at", 0) / 1000
                if not 0 <= time.time() - created <= 900:
                    raise ValueError("Follow-up expired or its timestamp is invalid. Not sent.")
                entry, path, fingerprint = self.snapshot(sid)
                if not entry.get("desktop_started_at") or entry["desktop_started_at"] > created:
                    raise ValueError("A newer desktop turn started after this message was queued. Review it before sending again.")
                if entry.get("status") == "running":
                    return False
                if entry.get("status") != "done" or entry.get("desktop_terminal") != entry.get("desktop_turn"):
                    raise ValueError("Finish or resolve the current turn in Codex Desktop first.")
                client = CodexDesktopIPC()
                owner = client.request("thread-owner-discovery", {"hostId": "local", "conversationId": sid}, 1)
                target = owner.get("handledByClientId")
                if owner.get("resultType") != "success" or not isinstance(target, str) or not target:
                    raise ValueError("Original conversation owner is unavailable. Open it in Codex Desktop.")
                if not self.ack(command, "dispatching"):
                    return False
                # Persist before any potentially ambiguous write to the owner.
                ledger.write(b"attempted\n")
                ledger.flush()
                os.fsync(ledger.fileno())
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                self.receipt(command, "dispatching")
                if self.fingerprint(path) != fingerprint or not self.available():
                    raise ValueError("Desktop conversation or version changed. Not sent.")
                submitted = True
                response = client.request("thread-follower-start-turn", params, 2, target, cid)
                result = response.get("result") or {}
                turn = (result.get("result") or {}).get("turn") or {}
                if response.get("resultType") != "success" or not isinstance(turn.get("id"), str):
                    raise RuntimeError("Desktop did not confirm the turn.")
                self.receipt(command, "delivered")
            except Exception as exc:
                status = "uncertain" if submitted else "failed"
                message = ("Delivery could not be confirmed. Check Codex Desktop; no automatic retry." if submitted
                           else str(exc) if isinstance(exc, ValueError)
                           else "Desktop connection or local storage unavailable. Not sent.")
                self.receipt(command, status, message[:300])
            finally:
                if client:
                    client.close()
            return True

    def run(self):
        commands, last_poll, last_publish = [], 0, 0
        while True:
            try:
                self.heartbeat = time.monotonic()
                now = self.heartbeat
                if (self.wake.is_set() and now - last_poll > 2) or now - last_poll > 15:
                    self.wake.clear()
                    last_poll = now
                    response = backend_call(self.cfg, "GET", "/api/codex?host=" + self.host)
                    if response is not None:
                        commands = [c for c in response.get("commands", [])
                                    if c.get("status") in ("queued", "dispatching")
                                    and c.get("action") == "send" and codex_is_desktop(c.get("session_id"))]
                remaining, blocked_sessions = [], set()
                for command in commands:
                    sid = command.get("session_id")
                    if sid in blocked_sessions:
                        remaining.append(command)
                        continue
                    if not self.execute(command):
                        remaining.append(command)
                        blocked_sessions.add(sid)
                commands = remaining
                state = load_sessions()
                changed = False
                for sid, entry in state["local"].items():
                    if entry.get("source") == "desktop":
                        count = sum(c.get("session_id") == sid for c in commands)
                        if entry.get("desktop_queued", 0) != count:
                            entry["desktop_queued"] = count
                            changed = True
                if changed:
                    save_sessions(state)
                if changed or now - last_publish > 20:
                    sync_peers(self.cfg, load_sessions(), host_label(self.cfg))
                    last_publish = now
            except Exception as exc:
                log("Codex desktop control: " + type(exc).__name__)
            time.sleep(2)


def codex_bridge_status():
    try:
        with open(os.path.join(CONFIG_DIR, "codex-bridge.json")) as f:
            status = json.load(f)
        if time.time() - status.get("ts", 0) > 45:
            return {"connected": False, "message": "Codex connection is offline"}
        return status
    except (OSError, ValueError):
        return {"connected": False}


def codex_alert(cfg, sid, kind, text, request_id=None):
    state = load_sessions()
    entry = state["local"].get(sid, {})
    project = entry.get("project") or "Codex"
    # Completion always rings, even while the user is working on the Mac.
    # Keep the existing idle policy for approvals and attention notifications.
    idle = mac_idle_seconds() if kind != "stop" else None
    phone_owned = state.get("codex_sessions", {}).get(sid, {}).get("phone_owned")
    threshold = cfg.get("permission_min_idle_seconds", 30) if kind == "permission" else cfg.get("min_idle_seconds", 120)
    if not phone_owned and idle is not None and idle < threshold:
        log(f"codex alert {kind}: skipped at keyboard (idle {idle:.0f}s < {threshold}s)")
        return
    title = "✅ Codex · " + project if kind == "stop" else "Codex · " + project
    sb = {"engine": "codex", "event": kind, "session_id": sid,
          "source": entry.get("source", ""),
          "project": project, "cwd": entry.get("cwd", ""), "host": host_label(cfg),
          "ts": int(time.time()), "md": clip_bytes(text, 1500),
          "backend": {"url": cfg["backend_url"], "secret": cfg["backend_secret"]}}
    if request_id:
        sb["request_id"] = request_id
    payload = {"aps": {"alert": {"title": title, "body": clip_bytes(strip_markdown(text), 700)},
                       "sound": "default", "thread-id": sid,
                       "category": "SB_DECIDE" if request_id else "SB_REPLY",
                       "interruption-level": "time-sensitive"}, "sb": sb}
    jwt = make_jwt(cfg)
    tokens = resolve_device_tokens(cfg)
    if not tokens:
        log(f"codex alert {kind}: no registered notification devices")
    for token in tokens:
        code, _ = send_push(jwt, HOSTS[cfg.get("environment", "sandbox")], token, payload, cfg["bundle_id"])
        # Never log device tokens, credentials, conversation text or response bodies.
        log(f"codex alert {kind}: HTTP {code}")


class CodexBridge:
    """One long-lived client of the SAME app-server used by desktop and CLI.

    Only the bridge thread touches RPC. The legacy watcher wakes its mailbox
    reader when a new UUID command arrives; idle Macs don't add a polling loop.
    """
    def __init__(self, cfg):
        import threading
        self.cfg = cfg
        self.rpc = None
        self.attached = set()
        self.pending = {}
        self.wake = threading.Event()
        self.wake.set()
        self.commands = []
        self.dirty = False
        self.last_publish = self.last_discover = self.last_decisions = 0
        self.last_mailbox = 0
        self.host = canonical_label(host_label(cfg))
        self.started_at = int(time.time())

    def status(self, connected, message=""):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "codex-bridge.json"), "w") as f:
            json.dump({"connected": connected, "message": message, "ts": int(time.time())}, f)

    def update(self, sid, **fields):
        state = load_sessions()
        entry = state["local"].setdefault(sid, {"project": "Codex", "since": int(time.time())})
        entry.update(fields, engine="codex", managed=True)
        if "status" in fields:
            entry["since"] = int(time.time())
        save_sessions(state)
        self.dirty = True

    @staticmethod
    def thread_status_fields(status):
        kind = status.get("type")
        if kind == "active":
            waiting = any(x in ("waitingOnApproval", "waitingOnUserInput")
                          for x in status.get("activeFlags", []))
            return {"status": "waiting" if waiting else "running", "delivery_error": ""}
        if kind == "idle":
            return {"status": "done", "delivery_error": ""}
        if kind == "systemError":
            return {"status": "waiting", "delivery_error": "Codex encountered a system error. Check the session on your Mac."}
        if kind == "notLoaded":
            return {"status": "unknown", "delivery_error": "This Codex session is not loaded."}
        return {"status": "unknown", "delivery_error": "Codex session status is unavailable."}

    def reset_requests(self):
        # RPC request IDs belong to one connection. A resolved request may not
        # be replayed after reconnect, so persisted phone controls must expire.
        self.pending.clear()
        state = load_sessions()
        changed = False
        for entry in state["local"].values():
            if entry.get("engine") == "codex" and entry.get("managed") and entry.get("pending_requests"):
                entry["pending_requests"] = []
                changed = True
        if changed:
            save_sessions(state)
            self.dirty = True

    def remember(self, thread, phone_owned=False):
        sid = thread["id"]
        state = load_sessions()
        record = state.setdefault("codex_sessions", {}).setdefault(sid, {})
        record.update(cwd=thread.get("cwd", ""), project=os.path.basename(thread.get("cwd", "")) or "Codex",
                      managed=True, engine="codex", ts=int(time.time()))
        if phone_owned:
            record["phone_owned"] = True
        save_sessions(state)
        self.update(sid, cwd=record["cwd"], project=record["project"],
                    root=project_root(record["cwd"]), pid=None, parent_pid=None,
                    parent_sid=thread.get("parentThreadId"),
                    detail=(thread.get("name") or thread.get("preview") or "")[:160],
                    **self.thread_status_fields(thread.get("status") or {}))

    def attach(self, sid):
        if sid not in self.attached:
            self.attached.add(sid)
            try:
                result = self.rpc.call("thread/resume", {"threadId": sid, "excludeTurns": True})
                self.remember(result["thread"])
                # A short task may finish before discovery attaches. Hydrate only
                # its latest turn, never the full conversation history.
                page = self.rpc.call("thread/turns/list", {
                    "threadId": sid, "limit": 1, "sortDirection": "desc", "itemsView": "full"})
                for turn in page.get("data", []):
                    for item in turn.get("items", []):
                        self.event({"method": "item/completed", "params": {"threadId": sid, "item": item}})
                    if turn.get("completedAt", 0) and turn["completedAt"] >= self.started_at:
                        self.finish(sid, turn)
            except Exception:
                self.attached.discard(sid)
                raise

    def discover(self):
        cursor = None
        while True:
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            result = self.rpc.call("thread/loaded/list", params)
            for sid in result.get("data", []):
                try:
                    self.attach(sid)
                except CodexRejected:
                    # A newly created thread has no rollout until its first turn.
                    # Retry on the next discovery, without dropping other sessions.
                    continue
            cursor = result.get("nextCursor")
            if not cursor:
                break

    def event(self, message):
        method, p = message.get("method"), message.get("params") or {}
        sid = p.get("threadId")
        if method == "thread/started":
            thread = p.get("thread") or {}
            if thread.get("id"):
                self.remember(thread)
                try:
                    self.attach(thread["id"])
                except CodexRejected:
                    pass
            return
        if method == "account/rateLimits/updated":
            with open(os.path.join(CONFIG_DIR, "codex-usage.json"), "w") as f:
                json.dump(normalize_codex_usage(p), f)
            self.dirty = True
            return
        if not sid or sid not in self.attached:
            return
        if method == "serverRequest/resolved":
            for key, request in list(self.pending.items()):
                if request["id"] == p.get("requestId") and request["params"].get("threadId") == sid:
                    self.pending.pop(key)
            self.publish_requests(sid)
        elif method == "turn/started":
            self.update(sid, status="running", turn_id=(p.get("turn") or {}).get("id"),
                        delivery_error="", latest_reply="")
        elif method == "thread/status/changed":
            self.update(sid, **self.thread_status_fields(p.get("status") or {}))
        elif method == "item/completed":
            item = p.get("item") or {}
            if item.get("type") == "agentMessage":
                self.update(sid, latest_reply=clip_bytes(item.get("text", ""), 8000))
            elif item.get("type") == "userMessage":
                text = " ".join(x.get("text", "") for x in item.get("content", []) if x.get("type") == "text")
                if text:
                    self.update(sid, detail=text[:160])
        elif method == "turn/completed":
            self.finish(sid, p.get("turn") or {})
        elif method in ("thread/archived", "thread/closed"):
            state = load_sessions()
            state["local"].pop(sid, None)
            save_sessions(state)
            self.attached.discard(sid)
            self.dirty = True

    def finish(self, sid, turn):
        failed = turn.get("status") in ("failed", "interrupted")
        entry = load_sessions()["local"].get(sid, {})
        already = entry.get("notified_turn") == turn.get("id")
        self.update(sid, status="waiting" if failed else "done", notified_turn=turn.get("id"))
        if not already:
            codex_alert(self.cfg, sid, "notification" if failed else "stop",
                        entry.get("latest_reply") or ("Codex stopped before completing this turn." if failed else "Codex completed this turn."))

    def request(self, message):
        import uuid
        method, p = message["method"], message.get("params") or {}
        sid = p.get("threadId")
        if not sid or sid not in self.attached:
            # Other clients may own specialized requests. Never reject them here.
            return
        if method not in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                          "item/permissions/requestApproval", "item/tool/requestUserInput"):
            self.update(sid, delivery_error="This request needs attention in Codex on your Mac.")
            return
        if any(r["id"] == message["id"] for r in self.pending.values()):
            return
        key = "codex-" + uuid.uuid4().hex
        self.pending[key] = dict(message, received_at=time.time())
        self.publish_requests(sid)
        public = next(x for x in self.public_requests(sid) if x["id"] == key)
        codex_alert(self.cfg, sid, "notification" if public["kind"] == "question" else "permission",
                    public["summary"], None if public["kind"] == "question" else key)

    def public_requests(self, sid):
        result = []
        for key, message in self.pending.items():
            p = message["params"]
            if p.get("threadId") != sid:
                continue
            questions = p.get("questions", [])
            summary = p.get("reason") or "Codex requests permission"
            if p.get("command"):
                summary += "\n" + p["command"]
            if p.get("permissions"):
                summary += "\n" + json.dumps(p["permissions"], ensure_ascii=False)
            if questions:
                summary = "\n".join(q.get("question", "") for q in questions)
            result.append({"id": key, "kind": "question" if questions else "approval",
                           "summary": summary[:4000], "questions": questions,
                           "ts": int(message["received_at"])})
        return result

    def publish_requests(self, sid):
        self.update(sid, pending_requests=self.public_requests(sid))

    def answer(self, key, decision=None, answers=None):
        request = self.pending.get(key)
        if not request:
            raise ValueError("This request was already answered or expired. Refresh the task.")
        p, method = request["params"], request["method"]
        if method == "item/tool/requestUserInput":
            if not isinstance(answers, dict) or set(answers) != {q["id"] for q in p["questions"]}:
                raise ValueError("Answer every question before sending.")
            if any(not isinstance(v, str) or not v.strip() for v in answers.values()):
                raise ValueError("Answers must not be empty.")
            result = {"answers": {k: {"answers": [v]} for k, v in answers.items()}}
        elif decision not in ("allow", "deny"):
            raise ValueError("Choose allow or deny.")
        elif method == "item/permissions/requestApproval":
            result = {"permissions": p.get("permissions", {}) if decision == "allow" else {}, "scope": "turn"}
        else:
            choices = p.get("availableDecisions")
            value = "accept" if decision == "allow" else "decline"
            if choices and value not in choices:
                raise ValueError("This approval needs a decision in Codex on your Mac.")
            result = {"decision": value}
        self.rpc.send({"id": request["id"], "result": result})
        # Wait for serverRequest/resolved before removing the visible prompt.

    def ack(self, command, status, message="", sid=""):
        return backend_call(self.cfg, "POST", "/api/codex", {
            "host": self.host, "command_id": command["command_id"], "action": "ack",
            "status": status, "message": message, "session_id": sid})

    def receipt(self, command, status, message="", sid=""):
        state = load_sessions()
        state.setdefault("codex_commands", {})[command["command_id"]] = {
            "status": status, "message": message, "session_id": sid, "ts": int(time.time())}
        save_sessions(state)
        self.ack(command, status, message, sid)

    def execute(self, command):
        cid, action, sid = command["command_id"], command["action"], command.get("session_id", "")
        state = load_sessions()
        if action == "send" and codex_is_desktop(sid, state):
            return True  # Native desktop control owns this, even if CLI is offline.
        previous = state.get("codex_commands", {}).get(cid)
        if previous:
            status = "uncertain" if previous["status"] == "dispatching" else previous["status"]
            self.receipt(command, status, previous.get("message", "") or "Check Codex before retrying.", previous.get("session_id", sid))
            return True
        if command.get("status") == "dispatching":
            self.receipt(command, "uncertain", "Connection was interrupted. Check Codex before retrying.", sid)
            return True
        if action == "send":
            record = state.get("codex_sessions", {}).get(sid, {})
            if not record.get("managed"):
                self.receipt(command, "failed", "Open this session in the shared Codex service first.", sid)
                return True
            try:
                self.attach(sid)
                thread = self.rpc.call("thread/read", {"threadId": sid})["thread"]
            except CodexRejected as exc:
                self.receipt(command, "failed", str(exc)[:300], sid)
                return True
            if thread.get("canAcceptDirectInput") is False:
                self.receipt(command, "failed", "This child session does not accept direct input.", sid)
                return True
            if (thread.get("status") or {}).get("type") == "active":
                return False  # queued until idle; never inject around an approval
        if not self.ack(command, "dispatching", sid=sid):
            return False
        self.receipt(command, "dispatching", sid=sid)
        try:
            if action == "spawn":
                cwd = os.path.expanduser(command.get("cwd", ""))
                if not os.path.isabs(cwd) or not os.path.isdir(cwd):
                    raise ValueError("The project folder does not exist on this Mac.")
                # Explicit user approvals; no bypass or account-setting changes.
                result = self.rpc.call("thread/start", {"cwd": cwd, "sandbox": "workspace-write",
                                                       "approvalPolicy": "on-request", "approvalsReviewer": "user"})
                sid = result["thread"]["id"]
                self.remember(result["thread"], phone_owned=True)
                self.attached.add(sid)
            if action in ("spawn", "send"):
                self.rpc.call("turn/start", {"threadId": sid, "clientUserMessageId": cid,
                                             "input": [{"type": "text", "text": command["text"]}]})
                self.update(sid, status="running", detail=command["text"][:160])
            elif action == "answer":
                key = command.get("request_id")
                request = self.pending.get(key)
                if not request or request["params"].get("threadId") != sid:
                    raise ValueError("This request is no longer pending in this session.")
                self.answer(key, answers=json.loads(command["answers"]))
                deadline = time.monotonic() + 8
                while key in self.pending and time.monotonic() < deadline:
                    for event in self.rpc.poll():
                        self.event(event)
                if key in self.pending:
                    raise TimeoutError("Codex did not confirm the answer")
            else:
                raise ValueError("Unknown Codex action.")
            self.receipt(command, "delivered", sid=sid)
        except (ValueError, CodexRejected) as exc:
            self.receipt(command, "failed", str(exc)[:300], sid)
        except Exception:
            self.receipt(command, "uncertain", "Connection interrupted. Check Codex before sending again.", sid)
            raise
        return True

    def tick(self):
        for event in self.rpc.poll():
            self.event(event)
        now = time.monotonic()
        if now - self.last_discover > 10:
            self.discover()
            self.last_discover = now
            self.status(True)
        if self.wake.is_set() and now - self.last_mailbox > 2:
            self.last_mailbox = now
            self.wake.clear()
            response = backend_call(self.cfg, "GET", "/api/codex?host=" + self.host)
            if response is not None:
                self.commands = [c for c in response.get("commands", []) if c.get("status") in ("queued", "dispatching")]
            else:
                self.wake.set()
        remaining = []
        blocked_sessions = set()
        for command in self.commands:
            is_send = command.get("action") == "send"
            sid = command.get("session_id")
            if is_send and sid in blocked_sessions:
                remaining.append(command)
                continue
            if not self.execute(command):
                remaining.append(command)
                if is_send:
                    blocked_sessions.add(sid)
        self.commands = remaining
        if now - self.last_decisions > 2:
            for key, request in list(self.pending.items()):
                if request["method"] == "item/tool/requestUserInput":
                    continue
                response = backend_call(self.cfg, "GET", "/api/decision?id=" + key)
                if response and response.get("decision") in ("allow", "deny") and not request.get("answered"):
                    try:
                        self.answer(key, decision=response["decision"])
                        request["answered"] = True
                    except ValueError as exc:
                        self.update(request["params"]["threadId"], delivery_error=str(exc))
            self.last_decisions = now
        # iOS considers a bridge heartbeat older than 45 seconds offline.
        # Keep idle connections fresh without sending extra APNs updates.
        if (self.dirty and now - self.last_publish > 3) or now - self.last_publish > 25:
            changed = self.dirty
            self.dirty = False
            state = load_sessions()
            label = host_label(self.cfg)
            sync_peers(self.cfg, state, label)
            if changed:
                push_dashboard(self.cfg, make_jwt(self.cfg), HOSTS[self.cfg.get("environment", "sandbox")], state, label)
            self.last_publish = now

    def run(self):
        while True:
            try:
                with CodexRPC(shared=True) as rpc:
                    self.rpc = rpc
                    rpc.on_request = self.request
                    self.attached.clear()
                    self.reset_requests()
                    self.wake.set()
                    self.discover()
                    self.status(True)
                    self.dirty = True
                    while True:
                        self.tick()
            except Exception as exc:
                self.status(False, "Codex shared service is unavailable")
                log("Codex bridge disconnected: " + type(exc).__name__)
                time.sleep(5)


def normalize_codex_usage(result, now=None):
    """Only transport quota data, never account identifiers or auth tokens."""
    buckets = result.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        one = result.get("rateLimits") or {}
        buckets = {one.get("limitId") or "codex": one} if one else {}
    limits = []
    for key, bucket in sorted(buckets.items()):
        if not isinstance(bucket, dict):
            continue
        windows = []
        for name in ("primary", "secondary"):
            window = bucket.get(name)
            if not isinstance(window, dict):
                continue
            pct = window.get("usedPercent")
            if not isinstance(pct, (int, float)) or isinstance(pct, bool):
                continue
            windows.append({"id": name, "used_pct": pct,
                            "window_minutes": window.get("windowDurationMins"),
                            "resets_at": window.get("resetsAt")})
        if windows:
            limits.append({"id": key, "name": bucket.get("limitName") or key,
                           "plan": bucket.get("planType"), "windows": windows})
    return {"updated_at": int(now if now is not None else time.time()),
            "available": bool(limits), "limits": limits,
            "ordinary_usage_allowed": result.get("ordinaryUsageAllowed")}


def cached_codex_usage():
    try:
        with open(os.path.join(CONFIG_DIR, "codex-usage.json")) as f:
            cached = json.load(f)
        age = time.time() - cached.get("updated_at", 0)
        if age < CODEX_USAGE_MAX_AGE:
            return dict(cached, stale=age > 900)
    except (OSError, ValueError, TypeError):
        pass
    return {}


def refresh_codex_usage():
    if not codex_bin():
        return {}
    cached = cached_codex_usage()
    if cached and time.time() - cached.get("updated_at", 0) < 600:
        return cached
    try:
        with CodexRPC() as rpc:
            account = (rpc.call("account/read", {"refreshToken": False}).get("account") or {})
            if account.get("type") != "chatgpt":
                result = {"updated_at": int(time.time()), "available": False,
                          "limits": [], "reason": "subscription_required"}
            else:
                result = normalize_codex_usage(rpc.call("account/rateLimits/read"))
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "codex-usage.json"), "w") as f:
            json.dump(result, f)
        return result
    except Exception as exc:
        log("Codex usage unavailable: " + type(exc).__name__)
        return dict(cached, stale=True) if cached else {
            "available": False, "limits": [], "reason": "unavailable"}


def handle_spawn(cfg: dict, text: str) -> None:
    """Phone-initiated session: headless `claude -p` — shows up on the
    dashboard via its own hooks, pushes its result, resumable at the desk."""
    try:
        req = json.loads(text)
    except ValueError:
        return
    cwd = os.path.expanduser(req.get("cwd") or "~")
    prompt = (req.get("prompt") or "").strip()
    if not prompt or not os.path.isdir(cwd):
        log(f"spawn: bad request (cwd={cwd})")
        return
    claude = claude_bin()
    if not claude:
        log("spawn: claude binary not found")
        return
    import shlex
    mode = req.get("mode") or "default"
    mode_flag = {"auto": " --permission-mode auto",
                 "bypass": " --permission-mode bypassPermissions",
                 "acceptEdits": " --permission-mode acceptEdits"}.get(mode, "")

    # 首选:开一个可见的 Otty 窗口跑交互式 session — 回到桌面即实况,
    # watcher 也能继续向它注入后续指令。
    inner = f"SESSIONBELL_SPAWNED=1 {shlex.quote(claude)}{mode_flag} {shlex.quote(prompt)}"
    title = "🔔 " + " ".join(prompt.split())[:24]
    try:
        p = subprocess.run(
            [OTTY_CLI, "open", cwd, "--command", inner, "--title", title, "-q"],
            capture_output=True, text=True, timeout=15)
        if p.returncode == 0:
            log(f"spawn: otty window @ {cwd}: {prompt[:50]}")
            return
        log(f"spawn: otty open failed ({(p.stderr or p.stdout).strip()[:80]}), "
            "falling back to headless")
    except Exception as exc:
        log(f"spawn: otty unavailable ({exc}), falling back to headless")

    args = [claude, "-p", prompt]
    if mode_flag:
        args += mode_flag.split()
    logf = open(os.path.join(CONFIG_DIR, f"spawn-{int(time.time())}.log"), "w")
    env = dict(os.environ, SESSIONBELL_SPAWNED="1")
    detach = ({"creationflags": WIN_DETACHED} if IS_WIN
              else {"start_new_session": True})
    subprocess.Popen(args, cwd=cwd, stdout=logf, stderr=logf, env=env, **detach)
    log(f"spawn: headless claude -p @ {cwd}: {prompt[:50]}")


def terminal_handle():
    """(type, handle) describing how to type into this session's terminal."""
    if IS_WIN:
        # Windows console APIs address the CONSOLE by pid (recorded on the
        # session already) — no per-terminal handle, no window-focus games.
        return "winconsole", ""
    if os.environ.get("OTTY_PANE_ID"):
        return "otty", os.environ["OTTY_PANE_ID"]
    if os.environ.get("TMUX_PANE") and os.environ.get("TMUX"):
        sock = os.environ["TMUX"].split(",")[0]
        return "tmux", f"{sock}|{os.environ['TMUX_PANE']}"
    tp = os.environ.get("TERM_PROGRAM", "")
    # Resolve the tty NOW (inherited from claude) — the handle then outlives
    # the claude process, same as an otty pane id.
    if tp == "iTerm.app":
        return "iterm", session_tty(os.getpid()) or ""
    if tp == "Apple_Terminal":
        return "terminal", session_tty(os.getpid()) or ""
    return None, None


def session_tty(pid):
    try:
        out = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return f"/dev/{out}" if out and out != "??" else None
    except Exception:
        return None


APPLESCRIPT_BY_TTY = {
    "iterm": '''
on run argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if tty of s is (item 1 of argv) then
            tell s to write text (item 2 of argv)
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "notfound"
end run''',
    "terminal": '''
on run argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if tty of t is (item 1 of argv) then
          do script (item 2 of argv) in t
          return "ok"
        end if
      end repeat
    end repeat
  end tell
  return "notfound"
end run''',
}


CAPTURE_BY_TTY = {
    "iterm": '''
on run argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if tty of s is (item 1 of argv) then
            return text of s
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return ""
end run''',
    "terminal": '''
on run argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if tty of t is (item 1 of argv) then
          return history of t
        end if
      end repeat
    end repeat
  end tell
  return ""
end run''',
}


def capture_pane(entry: dict):
    """Grab recent terminal text for the session — remote progress view."""
    ttype = entry.get("term_type") or ("otty" if entry.get("pane") else None)
    if ttype == "otty":
        pane = entry.get("term_handle") or entry.get("pane") or ""
        pane_id = pane if pane.startswith("p_") else f"p_{pane}"
        p = subprocess.run([OTTY_CLI, "pane", "capture", "--pane", pane_id],
                           capture_output=True, text=True, timeout=10)
        if p.returncode == 0:
            return p.stdout
        log(f"capture: otty failed: {(p.stderr or p.stdout).strip()[:80]}")
    elif ttype == "tmux":
        sock, _, pane = (entry.get("term_handle") or "").partition("|")
        tmux = next((t for t in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux",
                                 "/usr/bin/tmux") if os.path.exists(t)), "tmux")
        p = subprocess.run([tmux, "-S", sock, "capture-pane", "-p", "-t", pane],
                           capture_output=True, text=True, timeout=10)
        if p.returncode == 0:
            return p.stdout
    elif ttype in ("iterm", "terminal"):
        tty = entry.get("term_handle") or session_tty(entry.get("pid"))
        if tty:
            p = subprocess.run(["osascript", "-", tty],
                               input=CAPTURE_BY_TTY[ttype],
                               capture_output=True, text=True, timeout=15)
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout
    elif ttype == "winconsole" and IS_WIN:
        pid = entry.get("pid")
        if pid and pid_alive(pid):
            p = subprocess.run(self_cmd("winconsole") + ["read", str(pid)],
                               capture_output=True, text=True, timeout=15,
                               encoding="utf-8", errors="replace")
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout
            log(f"capture: winconsole failed: {(p.stderr or p.stdout).strip()[:80]}")
    return None


def handle_tail(cfg: dict, target_sid: str) -> None:
    state = load_sessions()
    # The pane outlives the session: fall back to the terminal registry so
    # the phone terminal stays connected after the session record is gone.
    entry = state["local"].get(target_sid) or state.get("terms", {}).get(target_sid)
    if not entry:
        text = "(没有该 session 的终端记录 — 可能来自旧版本 hook)"
    else:
        text = capture_pane(entry)
        if text is None:
            text = "(终端已关闭,或该终端不支持远程捕获)"
    lines = text.rstrip().splitlines()[-100:]
    backend_call(cfg, "POST", "/api/capture",
                 {"session_id": target_sid,
                  "text": clip_bytes("\n".join(lines), 12000)})
    log(f"tail: captured {target_sid[:8]} ({len(lines)} lines)")


def tool_line(c: dict) -> str:
    """一行概括一次工具调用:名字 + 描述(Bash)或目标文件/命令。"""
    name = c.get("name") or "tool"
    inp = c.get("input") or {}
    if not isinstance(inp, dict):
        inp = {}
    what = inp.get("description") or ""
    if not what:
        for k in ("file_path", "path", "notebook_path", "pattern", "command", "query", "prompt", "url"):
            v = inp.get(k)
            if isinstance(v, str) and v.strip():
                what = os.path.basename(v.rstrip("/")) if k in ("file_path", "path", "notebook_path") else v
                break
    return clip_bytes(" ".join(f"{name} {what}".split()), 120)


def session_markdown(session_id: str, max_bytes: int = 24000) -> str:
    """整理版进度:把 Claude Code 的本地会话记录压成 markdown —— 你的每条提示、
    Claude 的每段回复、工具调用一行一个。给手机的「进展」视图用,内容和终端
    画面对应,只是排好了版。只保留末尾 max_bytes,按块截断。"""
    import glob
    paths = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl"))
    if not paths:
        return ""
    blocks: list = []
    tools: list = []

    def flush_tools():
        if tools:
            blocks.append("\n".join(f"- 🔧 {t}" for t in tools))
            del tools[:]

    try:
        with open(paths[0]) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("isSidechain"):
                    continue
                kind = obj.get("type")
                content = (obj.get("message") or {}).get("content")
                if kind == "user":
                    if isinstance(content, str):
                        texts = [content]
                    elif isinstance(content, list):
                        texts = [c.get("text", "") for c in content
                                 if isinstance(c, dict) and c.get("type") == "text"]
                    else:
                        texts = []
                    prompt = "\n".join(t for t in texts if t).strip()
                    # hook / 系统注入的内容都是 <xml> 开头,不是人打的
                    if not prompt or prompt.startswith("<"):
                        continue
                    flush_tools()
                    blocks.append("### ❯ " + clip_bytes(" ".join(prompt.split()), 300))
                elif kind == "assistant" and isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "text" and (c.get("text") or "").strip():
                            flush_tools()
                            blocks.append(c["text"].strip())
                        elif c.get("type") == "tool_use":
                            tools.append(tool_line(c))
    except OSError:
        return ""
    flush_tools()
    out: list = []
    size = 0
    for b in reversed(blocks):
        n = len(b.encode()) + 2
        if size + n > max_bytes:
            if out:
                out.append("…")
            break
        out.append(b)
        size += n
    return "\n\n".join(reversed(out))


def handle_md(cfg: dict, target_sid: str) -> None:
    """手机「进展」视图:回传整理版会话记录(见 session_markdown)。"""
    text = session_markdown(target_sid)
    if not text:
        text = "(没有这个 session 的本地会话记录 — 可能在另一台电脑上,或已被清理)"
    backend_call(cfg, "POST", "/api/capture",
                 {"session_id": target_sid, "kind": "md", "text": clip_bytes(text, 24000)})
    log(f"md: sent {target_sid[:8]} ({len(text)} chars)")


def handle_type(cfg: dict, state: dict, payload: str) -> None:
    """Raw terminal typing from the phone's terminal view. Unlike sid-keyed
    commands this targets the PANE, not the claude process — it works after
    the session ended (e.g. type `claude -c` to resume from the phone)."""
    try:
        data = json.loads(payload)
        sid, text = data["sid"], " ".join(str(data["text"]).split())
    except (ValueError, KeyError, TypeError):
        return
    entry = state["local"].get(sid) or state.get("terms", {}).get(sid)
    if not entry:
        log(f"type: no terminal record for {sid[:8]}")
        return
    ok, err = type_into_terminal(entry, text)
    if ok:
        # Preserve the installed relay's server-side queue cancellation.
        claim_command(cfg, sid)
        if sid in state["local"]:
            state["local"][sid]["cmd_ts"] = int(time.time() * 1000)
    log(f"type: {sid[:8]} {'ok: ' + text[:40] if ok else 'failed: ' + err}")


def type_into_terminal(entry: dict, text: str):
    """Best-effort injection into whichever terminal hosts the session."""
    ttype = entry.get("term_type") or ("otty" if entry.get("pane") else None)
    if ttype == "otty":
        pane = entry.get("term_handle") or entry.get("pane") or ""
        pane_id = pane if pane.startswith("p_") else f"p_{pane}"
        p = subprocess.run(
            [OTTY_CLI, "pane", "send-text", "--pane", pane_id, text + "\r"],
            capture_output=True, text=True, timeout=10)
        return p.returncode == 0, (p.stderr or p.stdout).strip()[:120]
    if ttype == "tmux":
        sock, _, pane = (entry.get("term_handle") or "").partition("|")
        tmux = next((p for p in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux",
                                 "/usr/bin/tmux") if os.path.exists(p)), "tmux")
        p = subprocess.run([tmux, "-S", sock, "send-keys", "-t", pane, text, "Enter"],
                           capture_output=True, text=True, timeout=10)
        return p.returncode == 0, (p.stderr or p.stdout).strip()[:120]
    if ttype in ("iterm", "terminal"):
        tty = entry.get("term_handle") or session_tty(entry.get("pid"))
        if not tty:
            return False, "no tty"
        p = subprocess.run(["osascript", "-", tty, text],
                           input=APPLESCRIPT_BY_TTY[ttype],
                           capture_output=True, text=True, timeout=15)
        ok = p.returncode == 0 and "ok" in p.stdout
        return ok, (p.stderr or p.stdout).strip()[:120]
    if ttype == "winconsole":
        if not IS_WIN:
            return False, "winconsole entry on non-Windows host"
        pid = entry.get("pid")
        if not pid or not pid_alive(pid):
            return False, "process gone"
        b64 = base64.b64encode((text + "\r").encode("utf-8")).decode()
        p = subprocess.run(self_cmd("winconsole") + ["write", str(pid), b64],
                           capture_output=True, text=True, timeout=15,
                           encoding="utf-8", errors="replace")
        ok = p.returncode == 0 and "ok" in (p.stdout or "")
        return ok, (p.stderr or p.stdout or "").strip()[:120]
    return False, f"unsupported terminal ({ttype})"


def cmd_winconsole(action: str, pid_s: str, payload_b64: str = "") -> None:
    """Windows terminal mode worker: attach to the target session's console
    by pid and read its screen buffer / write its input queue. Focus-free —
    the console is addressed directly, never the foreground window.

    Runs as a short-lived child (spawned via self_cmd) because AttachConsole
    is exclusive: a process has one console, so the long-lived watcher can't
    hop between sessions itself."""
    if not IS_WIN:
        print("ERR not-windows")
        sys.exit(1)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    import ctypes
    from ctypes import wintypes

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                    ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

    class CSBI(ctypes.Structure):
        _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD),
                    ("wAttributes", ctypes.c_ushort), ("srWindow", SMALL_RECT),
                    ("dwMaximumWindowSize", COORD)]

    k32 = ctypes.windll.kernel32
    k32.FreeConsole()
    try:
        pid = int(pid_s)
    except ValueError:
        print("ERR bad-pid")
        sys.exit(1)
    if not k32.AttachConsole(pid):
        print(f"ERR attach:{k32.GetLastError()}")
        sys.exit(1)

    GENERIC_RW = 0x80000000 | 0x40000000
    SHARE_RW = 0x00000001 | 0x00000002
    OPEN_EXISTING = 3
    INVALID_HANDLE = wintypes.HANDLE(-1).value

    if action == "read":
        h = k32.CreateFileW("CONOUT$", GENERIC_RW, SHARE_RW, None,
                            OPEN_EXISTING, 0, None)
        if h == INVALID_HANDLE:
            print(f"ERR conout:{k32.GetLastError()}")
            sys.exit(1)
        info = CSBI()
        if not k32.GetConsoleScreenBufferInfo(h, ctypes.byref(info)):
            print(f"ERR csbi:{k32.GetLastError()}")
            sys.exit(1)
        width = max(1, info.dwSize.X)
        last = max(info.dwCursorPosition.Y, info.srWindow.Bottom)
        first = max(0, last - 200)
        lines = []
        for y in range(first, last + 1):
            buf = ctypes.create_unicode_buffer(width + 1)
            n = wintypes.DWORD(0)
            if k32.ReadConsoleOutputCharacterW(h, buf, width, COORD(0, y),
                                               ctypes.byref(n)):
                lines.append(buf.value[:n.value].rstrip())
        sys.stdout.write("\n".join(lines))
    elif action == "write":
        class CHAR_UNION(ctypes.Union):
            _fields_ = [("UnicodeChar", ctypes.c_wchar),
                        ("AsciiChar", ctypes.c_char)]

        class KEY_EVENT(ctypes.Structure):
            _fields_ = [("bKeyDown", wintypes.BOOL),
                        ("wRepeatCount", ctypes.c_ushort),
                        ("wVirtualKeyCode", ctypes.c_ushort),
                        ("wVirtualScanCode", ctypes.c_ushort),
                        ("uChar", CHAR_UNION),
                        ("dwControlKeyState", wintypes.DWORD)]

        class EVENT_UNION(ctypes.Union):
            _fields_ = [("KeyEvent", KEY_EVENT)]

        class INPUT_RECORD(ctypes.Structure):
            _fields_ = [("EventType", ctypes.c_ushort), ("Event", EVENT_UNION)]

        try:
            text = base64.b64decode(payload_b64).decode("utf-8")
        except Exception:
            text = ""
        if not text:
            print("ERR empty")
            sys.exit(1)
        h = k32.CreateFileW("CONIN$", GENERIC_RW, SHARE_RW, None,
                            OPEN_EXISTING, 0, None)
        if h == INVALID_HANDLE:
            print(f"ERR conin:{k32.GetLastError()}")
            sys.exit(1)
        KEY_EVENT_TYPE = 0x0001
        VK_RETURN = 0x0D
        records = (INPUT_RECORD * (len(text) * 2))()
        for i, ch in enumerate(text):
            vk = VK_RETURN if ch == "\r" else 0
            for j, down in ((0, 1), (1, 0)):
                r = records[i * 2 + j]
                r.EventType = KEY_EVENT_TYPE
                r.Event.KeyEvent.bKeyDown = down
                r.Event.KeyEvent.wRepeatCount = 1
                r.Event.KeyEvent.wVirtualKeyCode = vk
                r.Event.KeyEvent.uChar.UnicodeChar = ch
        n = wintypes.DWORD(0)
        if not k32.WriteConsoleInputW(h, records, len(text) * 2,
                                      ctypes.byref(n)):
            print(f"ERR write:{k32.GetLastError()}")
            sys.exit(1)
        print("ok")
    else:
        print("ERR bad-action")
        sys.exit(1)


def resolve_device_tokens(cfg: dict) -> list:
    """Explicit config wins; otherwise the backend knows (app auto-registers)."""
    tokens = cfg.get("device_tokens") or []
    if not tokens and cfg.get("backend_url") and cfg.get("backend_secret"):
        remote = backend_call(cfg, "GET", "/api/token") or {}
        tokens = remote.get("devices") or []
    return tokens


def use_push_gateway(cfg: dict) -> bool:
    """No local .p8 → the backend signs and forwards APNs pushes."""
    if os.environ.get("SESSIONBELL_FORCE_GATEWAY"):
        return True
    p8 = os.path.expanduser(cfg.get("p8_path") or "")
    return bool(cfg.get("backend_url") and cfg.get("backend_secret")
                and (not p8 or not os.path.exists(p8)))


def make_jwt(cfg: dict) -> str:
    if use_push_gateway(cfg):
        return ""
    # Reuse a cached token: APNs asks that we refresh no more often than 20 min.
    try:
        with open(JWT_CACHE_PATH) as f:
            cache = json.load(f)
        if time.time() - cache["iat"] < JWT_MAX_AGE and cache.get("kid") == cfg["key_id"]:
            return cache["token"]
    except (OSError, ValueError, KeyError):
        pass

    header = b64url(json.dumps({"alg": "ES256", "kid": cfg["key_id"]}).encode())
    iat = int(time.time())
    claims = b64url(json.dumps({"iss": cfg["team_id"], "iat": iat}).encode())
    signing_input = f"{header}.{claims}".encode()

    p = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", os.path.expanduser(cfg["p8_path"])],
        input=signing_input,
        capture_output=True,
    )
    if p.returncode != 0:
        log(f"openssl sign failed: {p.stderr.decode(errors='replace').strip()}")
        sys.stderr.write(f"SessionBell: openssl 签名失败: {p.stderr.decode(errors='replace')}\n")
        sys.exit(1)

    token = f"{header}.{claims}.{b64url(der_to_raw_sig(p.stdout))}"
    try:
        with open(JWT_CACHE_PATH, "w") as f:
            json.dump({"token": token, "iat": iat, "kid": cfg["key_id"]}, f)
    except OSError:
        pass
    return token


def host_label(cfg: dict) -> str:
    """Which machine is ringing — config override, else a CACHED computer name.

    Cached because the two fallback paths (scutil vs hostname) return
    different spellings; flip-flopping labels made every session appear
    twice in the merged dashboard.
    """
    if cfg.get("host_label"):
        return cfg["host_label"]
    cache_path = os.path.join(CONFIG_DIR, "host-label")
    try:
        with open(cache_path) as f:
            cached = f.read().strip()
        if cached:
            return cached
    except OSError:
        pass
    name = ""
    try:
        name = subprocess.run(
            ["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        pass
    if not name:
        import platform
        name = platform.node().removesuffix(".local") or "Mac"
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            f.write(name)
    except OSError:
        pass
    return name


def mac_idle_seconds():
    """Seconds since last local keyboard/mouse input, or None if unknown.
    (Named for its origin; answers for Windows too.)"""
    if IS_WIN:
        try:
            import ctypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                # GetTickCount wraps at 49.7 days; the c_uint math below
                # stays correct across the wrap.
                ticks = ctypes.windll.kernel32.GetTickCount()
                return ctypes.c_uint(ticks - lii.dwTime).value / 1000.0
        except Exception:
            pass
        return None
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=5
        ).stdout
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                return int(line.split("=")[-1].strip()) / 1e9
    except Exception:
        pass
    return None


def last_assistant_text(transcript_path: str) -> str:
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "assistant":
            content = (obj.get("message") or {}).get("content") or []
            texts = [
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            text = "\n".join(t for t in texts if t).strip()
            if text:
                return text
    return ""


# ---------------- Live Activity ----------------

LA_STALE_SECONDS = 30 * 60       # waiting card considered stale after 30 min
LA_DONE_DISMISS_SECONDS = 10 * 60  # "done" card auto-dismisses after 10 min


def load_activity_tokens() -> dict:
    try:
        with open(TOKENS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_activity_tokens(tokens: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(TOKENS_PATH, "w") as f:
            json.dump(tokens, f, indent=2)
    except OSError:
        pass


def relay_address(cfg: dict) -> str:
    port = cfg.get("relay_port", 48765)
    try:
        name = subprocess.run(
            ["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if name:
            return f"{name}.local:{port}"
    except Exception:
        pass
    return ""


def use_backend(cfg: dict) -> bool:
    return bool(cfg.get("backend_url") and cfg.get("backend_secret"))


def backend_call(cfg: dict, method: str, path: str, body=None, timeout: int = 8):
    """GET/POST against the backend; returns parsed JSON or None.
    `timeout` must exceed any `wait=` long-poll budget in `path`."""
    url = cfg["backend_url"].rstrip("/") + path
    cmd = ["curl", "-sS", "-m", str(timeout), "-H", f"x-sb-secret: {cfg['backend_secret']}"]
    if method == "POST":
        cmd += ["-X", "POST", "-H", "Content-Type: application/json",
                "-d", json.dumps(body or {})]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        log(f"backend {method} {path} failed: {p.stderr.strip()}")
        return None
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


# Long-poll: the backend holds GET /api/command|/api/decision open for up to
# `wait` seconds and returns the moment a row newer than `since` (ms) lands.
# One held connection replaces a 3 s hammer (which was 86% of all requests).
LP_WAIT = 25          # backend caps at 25; curl timeout adds headroom
LP_HTTP_TIMEOUT = LP_WAIT + 10


def backend_poll(cfg: dict, path: str, wait: int, since: int = 0):
    """GET with long-poll params; wait<=0 degrades to a plain GET."""
    wait = max(0, min(LP_WAIT, int(wait)))
    sep = "&" if "?" in path else "?"
    return backend_call(cfg, "GET", f"{path}{sep}wait={wait}&since={int(since)}",
                        timeout=(wait + 10) if wait else 8)


def claim_command(cfg: dict, key: str, ts=None) -> bool:
    """Only the winner of a server-side claim may deliver a legacy command.

    The watcher and Stop hook can see the same row concurrently. Timestamp-
    guarded deletion retains newer messages; missing confirmation fails closed.
    ts=None cancels a queued message after explicit raw terminal input.
    """
    body = {"key": key}
    if ts is not None:
        body["ts"] = ts
    resp = backend_call(cfg, "POST", "/api/command/claim", body)
    if not isinstance(resp, dict) or "claimed" not in resp:
        log(f"claim {key[:12]}: backend didn't answer, leaving it queued")
        return False
    return bool(resp["claimed"])


_GATEWAY_CFG = {}  # set once in main(); lets send_* route without signature churn


def gateway_push(device_token: str, topic: str, push_type: str, payload: dict):
    cfg = _GATEWAY_CFG
    r = backend_call(cfg, "POST", "/api/push", {
        "device_token": device_token, "topic": topic, "push_type": push_type,
        "priority": 10, "payload": payload,
        "environment": cfg.get("environment", "production"),
    })
    if not r:
        return 0, "gateway unreachable"
    return r.get("status", 0), json.dumps(r.get("body") or "")[:200]


def loc_alert(key: str, args: list) -> dict:
    """APNs 标题本地化:key 是中文原文(带 %@ 占位),App 的 String Catalog 里有英文译文。
    手机按自己的语言查表;老版本 App 查不到 key 就原样显示中文,所以完全向后兼容。
    同时带上渲染好的 title,给不认 loc-key 的消费者(relay 日志等)看。"""
    title = key
    for a in args:
        title = title.replace("%@", str(a), 1)
    return {"title": title, "title-loc-key": key, "title-loc-args": [str(a) for a in args]}


def send_la_push(jwt: str, apns_host: str, la_token: str, bundle_id: str, aps: dict):
    """Live Activity pushes use a dedicated topic suffix and push type."""
    if _GATEWAY_CFG and use_push_gateway(_GATEWAY_CFG):
        return gateway_push(la_token, f"{bundle_id}.push-type.liveactivity",
                            "liveactivity", {"aps": aps})
    cmd = [
        "curl", "-sS", "--http2", "-m", "10",
        "-o", "-", "-w", "\n%{http_code}",
        "-H", f"authorization: bearer {jwt}",
        "-H", f"apns-topic: {bundle_id}.push-type.liveactivity",
        "-H", "apns-push-type: liveactivity",
        "-H", "apns-priority: 10",
        "-d", json.dumps({"aps": aps}, ensure_ascii=False),
        f"https://{apns_host}/3/device/{la_token}",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return 0, p.stderr.strip()
    body, _, code = p.stdout.rpartition("\n")
    return int(code or 0), body.strip()


DONE_LINGER_SECONDS = 10 * 60   # finished tasks stay on the card this long
SESSION_MAX_AGE = 24 * 3600     # drop sessions that never got a terminal event
PEER_FRESH_SECONDS = 3600       # ignore peer-Mac state older than this


def load_sessions() -> dict:
    import copy
    try:
        with open(SESSIONS_PATH) as f:
            s = json.load(f)
        s.setdefault("local", {})
        s.setdefault("peers", {})
        s["_baseline"] = copy.deepcopy(s)
        return s
    except (OSError, ValueError):
        return {"local": {}, "peers": {}, "_baseline": {"local": {}, "peers": {}}}


def save_sessions(state: dict) -> None:
    """Merge only this caller's changes under a lock; hooks run concurrently."""
    import copy
    import tempfile
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SESSIONS_PATH + ".lock", "a+b") as lock:
            if IS_WIN:
                import msvcrt
                if os.path.getsize(SESSIONS_PATH + ".lock") == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            current = load_sessions()
            current.pop("_baseline", None)
            baseline = state.get("_baseline", {})

            def merge(old, new, live):
                if isinstance(old, dict) and isinstance(new, dict) and isinstance(live, dict):
                    for key in old.keys() - new.keys():
                        if live.get(key) == old[key]:
                            live.pop(key, None)
                    for key, value in new.items():
                        if key not in old:
                            live[key] = copy.deepcopy(value)
                        elif value != old[key]:
                            live[key] = merge(old[key], value, live.get(key))
                    return live
                return copy.deepcopy(new)

            desired = {k: v for k, v in state.items() if k != "_baseline"}
            current = merge(baseline, desired, current)
            fd, tmp = tempfile.mkstemp(prefix="sessions-", suffix=".tmp", dir=CONFIG_DIR)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(current, f, indent=2)
                os.replace(tmp, SESSIONS_PATH)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            state.clear()
            state.update(current)
            state["_baseline"] = copy.deepcopy(current)
    except OSError:
        pass


WAITING_LINGER_SECONDS = 30 * 60   # already notified; stop occupying the card
RUNNING_MAX_AGE = 6 * 3600         # a "running" turn this old is a zombie


def _win_process_table() -> dict:
    """pid -> (ppid, exe name) via a Toolhelp32 snapshot — one syscall, no
    subprocess (wmic is gone on Win11 and PowerShell costs ~1s per spawn)."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    TH32CS_SNAPPROCESS = 0x2
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    table = {}
    if snap in (0, -1):
        return table
    try:
        e = PROCESSENTRY32()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = k32.Process32First(snap, ctypes.byref(e))
        while ok:
            table[int(e.th32ProcessID)] = (
                int(e.th32ParentProcessID),
                e.szExeFile.decode(errors="replace").lower())
            ok = k32.Process32Next(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return table


def claude_pid_chain() -> list:
    """All claude processes above this hook, nearest first. A second entry
    means this session was spawned BY another claude — it's a sub-agent."""
    pids = []
    if IS_WIN:
        try:
            table = _win_process_table()
        except Exception:
            return pids
        pid = os.getppid()
        for _ in range(12):
            ent = table.get(pid)
            if not ent:
                break
            ppid, name = ent
            if "claude" in name:
                pids.append(pid)
            if not ppid or ppid == pid:
                break
            pid = ppid
        return pids
    pid = os.getppid()
    for _ in range(12):
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=,command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            break
        if not out:
            break
        parts = out.split(None, 1)
        if len(parts) == 2 and "claude" in parts[1].lower():
            pids.append(pid)
        try:
            nxt = int(parts[0])
        except (ValueError, IndexError):
            break
        if nxt <= 1:
            break
        pid = nxt
    return pids


def claude_pids():
    chain = claude_pid_chain()
    return (chain[0] if chain else None,
            chain[1] if len(chain) > 1 else None)


def engine_pids(engine=None):
    if engine != "codex":
        return claude_pids()
    # A desktop app-server owns many threads: it is never their parent agent.
    pid = os.getppid()
    for _ in range(12):
        try:
            line = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                  capture_output=True, text=True, timeout=2).stdout.strip()
            parent, executable = line.split(None, 1)
            if os.path.basename(executable).lower() in ("codex", "codex.exe"):
                return pid, None
            pid = int(parent)
            if pid <= 1:
                break
        except (OSError, ValueError, subprocess.TimeoutExpired):
            break
    return None, None


def find_claude_pid():
    return claude_pids()[0]


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (ValueError, TypeError):
        return False
    if IS_WIN:
        # os.kill(pid, 0) on Windows calls TerminateProcess — it KILLS the
        # session instead of probing it. Query the process handle instead.
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
                return bool(ok) and code.value == STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


TERM_REGISTRY_MAX_AGE = 7 * 24 * 3600   # pane handles outlive sessions a week


INJECTED_PREFIXES = ("<task-notification", "<system-reminder", "<command-name",
                     "<local-command", "<user-prompt-submit-hook", "[Request interrupted")


def injected_text(text) -> bool:
    """Claude Code 注入的伪 prompt(后台任务完成通知、系统提醒、斜杠命令回显…),
    不是用户敲的,不能当任务名。"""
    return bool(text) and str(text).lstrip().startswith(INJECTED_PREFIXES)


def clean_detail(text) -> str:
    return "" if injected_text(text) else (text or "")


def prune_sessions(state: dict, now: int) -> None:
    local = state["local"]
    # 一次性洗掉修复前已经被污染的任务名 / prompt 登记,否则 stop 推送会一直沿用旧值。
    for entry in local.values():
        if injected_text(entry.get("detail")):
            entry["detail"] = ""
    prompts = state.get("prompts", {})
    for sid in [k for k, v in prompts.items() if injected_text(v.get("text"))]:
        del prompts[sid]
    limits = {"done": DONE_LINGER_SECONDS,
              "waiting": WAITING_LINGER_SECONDS,
              "running": RUNNING_MAX_AGE}
    for sid in list(local):
        entry = local[sid]
        age = now - entry.get("since", 0)
        if age > limits.get(entry.get("status"), SESSION_MAX_AGE):
            del local[sid]
        elif (entry.get("status") in ("running", "waiting")
              and entry.get("pid") and not pid_alive(entry["pid"])):
            # The claude process is gone — the session ended without a
            # stop/session-end event (closed terminal, crash, kill).
            del local[sid]
    terms = state.get("terms", {})
    for sid in list(terms):
        if now - terms[sid].get("ts", 0) > TERM_REGISTRY_MAX_AGE:
            del terms[sid]
    prompts = state.get("prompts", {})
    for sid in list(prompts):
        if now - prompts[sid].get("ts", 0) > TERM_REGISTRY_MAX_AGE:
            del prompts[sid]


def merged_tasks(state: dict, my_label: str, now: int) -> list:
    """Merged, deduped, HIERARCHICAL: sub-agent sessions (spawned by another
    claude process) ride directly under their parent, flagged sub=true."""
    limits = {"done": DONE_LINGER_SECONDS,
              "waiting": WAITING_LINGER_SECONDS,
              "running": RUNNING_MAX_AGE}
    collected = {}  # sid -> (task, parent_sid or None)

    def add_group(sessions: dict, host: str):
        pid2sid = {e.get("pid"): s for s, e in sessions.items() if e.get("pid")}
        for sid, e in sessions.items():
            if now - e.get("since", 0) > limits.get(e.get("status"), SESSION_MAX_AGE):
                continue
            if sid in collected and collected[sid][0]["since"] >= e.get("since", 0):
                continue
            t = {"project": e["project"], "host": host,
                 "status": e["status"], "since": e["since"]}
            if e.get("detail"):
                t["detail"] = e["detail"]
            if e.get("agents"):
                t["agents"] = e["agents"]
            if e.get("mode") and e["mode"] != "default":
                t["mode"] = e["mode"]
            if e.get("engine"):
                t["engine"] = e["engine"]
            parent = e.get("parent_sid") or pid2sid.get(e.get("parent_pid"))
            collected[sid] = (t, parent if parent != sid else None)

    add_group(state["local"], my_label)
    for peer_host, blob in state["peers"].items():
        if now - blob.get("ts", 0) > PEER_FRESH_SECONDS:
            continue
        add_group(blob.get("sessions", {}), peer_host)

    roots, children = [], {}
    for sid, (t, parent) in collected.items():
        if parent and parent in collected:
            t["sub"] = True
            children.setdefault(parent, []).append((sid, t))
        else:
            roots.append((sid, t))
    order = {"waiting": 0, "running": 1, "done": 2}
    roots.sort(key=lambda st: (order.get(st[1]["status"], 3), st[1]["since"]))
    out = []
    for sid, t in roots:
        out.append(t)
        for _, child in sorted(children.get(sid, []), key=lambda st: st[1]["since"]):
            out.append(child)
    return out[:6]  # content-state has a 4KB budget; details take room


def hook_version() -> str:
    import hashlib
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:8]
    except OSError:
        return "unknown"


def sync_peers(cfg: dict, state: dict, my_label: str) -> None:
    """Publish our session table and pull the other Macs' (best effort)."""
    if use_backend(cfg):
        backend_call(cfg, "POST", "/api/state",
                     {"host": my_label, "ts": int(time.time()),
                      "sessions": state["local"], "usage": cached_usage() or None,
                      "codex_usage": cached_codex_usage() or None,
                      "codex": codex_bridge_status(),
                      "awake": caffeinate_active(),
                      "projects": recent_projects(),
                      "hook_v": hook_version()})
        remote = backend_call(cfg, "GET", "/api/state") or {}
        state["peers"] = {h: v for h, v in remote.items() if h != my_label}
        save_sessions(state)
        return
    peers = cfg.get("peers") or []
    if not peers:
        return
    body = json.dumps({"host": my_label, "ts": int(time.time()), "sessions": state["local"]})
    for peer in peers:
        p = subprocess.run(
            ["curl", "-sS", "-m", "3", "-X", "POST", "-d", body, f"http://{peer}/state"],
            capture_output=True, text=True,
        )
        if p.returncode != 0:
            log(f"peer {peer} unreachable: {p.stderr.strip()}")


def relays_list(cfg: dict) -> list:
    own = relay_address(cfg)
    out = [own] if own else []
    for peer in cfg.get("peers") or []:
        if peer not in out:
            out.append(peer)
    return out


def push_dashboard(cfg, jwt, apns_host, state, my_label):
    """Send the merged cross-Mac task list as one Live Activity dashboard."""
    now = int(time.time())
    tasks = merged_tasks(state, my_label, now)
    active = [t for t in tasks if t["status"] in ("waiting", "running")]
    content_state = {"tasks": tasks, "updatedAt": now}
    # 5h 窗口 / 周用量:锁屏卡标题右侧那个小数字。没有缓存就不带,卡片自动不显示。
    u = cached_usage()
    if u.get("official_session_pct") is not None:
        content_state["usage5h"] = int(u["official_session_pct"])
        if u.get("session_reset_ts"):
            content_state["usage5hResets"] = int(u["session_reset_ts"])
    if u.get("official_pct") is not None:
        content_state["usageWeek"] = int(u["official_pct"])
    # Keep approval buttons on the card across unrelated dashboard updates.
    try:
        with open(PENDING_APPROVAL_PATH) as f:
            approval = json.load(f)
        if now - approval.get("ts", 0) < APPROVAL_FRESH_SECONDS:
            content_state["approvalId"] = approval["id"]
            content_state["approvalSummary"] = approval["summary"]
    except (OSError, ValueError, KeyError):
        pass
    if not content_state.get("approvalId"):
        approvals = [r for entry in state["local"].values()
                     for r in entry.get("pending_requests", []) if r.get("kind") == "approval"]
        if approvals:
            approval = min(approvals, key=lambda r: r.get("ts", 0))
            content_state["approvalId"] = approval["id"]
            content_state["approvalSummary"] = clip_bytes(approval["summary"], 300)
    tokens = load_activity_tokens()

    entry = tokens.get("_dashboard")
    if use_backend(cfg):
        remote = backend_call(cfg, "GET", "/api/token") or {}
        dash = remote.get("dashboard") or {}
        # Activities live at most ~8h; an older token is a corpse — APNs still
        # answers 200 for it, so age is the only reliable liveness signal.
        if dash.get("token") and now * 1000 - dash.get("ts", 0) < 8 * 3600 * 1000:
            entry = {"token": dash["token"]}
        else:
            entry = None
        remote_pts = remote.get("pts") or []
    else:
        remote_pts = tokens.get("_pts", [])

    if entry and entry.get("token"):
        if active:
            aps = {
                "timestamp": now,
                "event": "update",
                "content-state": content_state,
                "stale-date": now + LA_STALE_SECONDS,
            }
        else:
            aps = {
                "timestamp": now,
                "event": "end",
                "content-state": content_state,
                "dismissal-date": now + LA_DONE_DISMISS_SECONDS,
            }
        code, resp = send_la_push(jwt, apns_host, entry["token"], cfg["bundle_id"], aps)
        log(f"la-{aps['event']} dashboard HTTP {code} {resp}")
        if aps["event"] == "end" or code == 200:
            if aps["event"] == "end":
                tokens.pop("_dashboard", None)
                tokens.pop("_dashboard_started", None)
                save_activity_tokens(tokens)
            return
        # update failed — token stale, fall through to a fresh start
        tokens.pop("_dashboard", None)
        save_activity_tokens(tokens)

    if not active:
        return

    # No update token yet (app hasn't phoned home). Give it a short grace
    # window before re-starting, so cards don't pile up — but don't stay
    # silent forever if the token never arrives.
    if now - tokens.get("_dashboard_started", 0) < 120:
        log("la-start skipped: waiting for update token from app")
        return

    start_tokens = list(cfg.get("live_activity_start_tokens") or [])
    for t in remote_pts:
        if t not in start_tokens:
            start_tokens.append(t)
    if not start_tokens:
        return  # Live Activity not set up yet; alert pushes still work

    if use_backend(cfg):
        attributes = {"backend": cfg["backend_url"].rstrip("/"),
                      "secret": cfg["backend_secret"]}
    else:
        attributes = {"backend": "", "secret": "", "relays": relays_list(cfg)}

    waiting = [t for t in active if t["status"] == "waiting"]
    if waiting:
        title_key, title_args = "🖐 %@ 个任务在等你", [str(len(waiting))]
    else:
        title_key, title_args = "⏳ %@ 个任务运行中", [str(len(active))]
    aps = {
        "timestamp": now,
        "event": "start",
        "attributes-type": "SessionActivityAttributes",
        "attributes": attributes,
        "content-state": content_state,
        "stale-date": now + LA_STALE_SECONDS,
        # Apple requires an alert dict on push-to-start payloads.
        "alert": {
            **loc_alert(title_key, title_args),
            "body": " · ".join(t["project"] for t in active[:3]),
        },
    }
    started = False
    for t in start_tokens:
        code, resp = send_la_push(jwt, apns_host, t, cfg["bundle_id"], aps)
        log(f"la-start dashboard -> {t[:8]}… HTTP {code} {resp}")
        started = started or code == 200
    if started:
        tokens["_dashboard_started"] = now
        save_activity_tokens(tokens)


OTTY_CLI = "/Applications/Otty.app/Contents/MacOS/otty-cli"


UPDATE_STAMP = os.path.join(CONFIG_DIR, "last-update-check")
HOOK_UPDATE_INTERVAL = 6 * 3600   # hook-event driven check when no relay is alive


def restart_relay() -> None:
    """Bring the relay onto the code now on disk. Works whether the job is
    running (kickstart -k), loaded-but-dead, or never bootstrapped."""
    if IS_WIN:
        subprocess.Popen(self_cmd("relay"), creationflags=WIN_DETACHED)
        return
    uid = os.getuid()
    label = "dev.piper.sessionbell.relay"
    r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
                       capture_output=True)
    if r.returncode != 0:
        plist = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        if os.path.exists(plist):
            subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", plist],
                           capture_output=True)


def self_update_from_hook(cfg: dict) -> None:
    """Hook events (stop / session-end) also drive updates, throttled to once
    per HOOK_UPDATE_INTERVAL, so a Mac whose relay died still converges on the
    hosted version. Runs DETACHED: the hook itself must not spend up to 20 s on
    a download before it has even read its event (session-end has a 30 s budget)."""
    try:
        if time.time() - os.path.getmtime(UPDATE_STAMP) < HOOK_UPDATE_INTERVAL:
            return
    except OSError:
        pass
    try:
        kwargs = {"creationflags": WIN_DETACHED} if IS_WIN else {"start_new_session": True}
        subprocess.Popen(self_cmd("self-update"), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    except Exception as exc:
        log(f"self-update spawn failed: {exc}")


def cmd_self_update(cfg: dict) -> None:
    """Detached worker: check for a newer hosted script, then make sure the relay
    is alive either way. The throttle stamp is written only after a completed
    check, so an offline attempt does not silence updates for 6 h."""
    self_update(cfg, exit_after=False)
    if not IS_WIN:
        # kickstart WITHOUT -k: starts the job only if it is not running.
        subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/dev.piper.sessionbell.relay"],
                       capture_output=True)
    try:
        with open(UPDATE_STAMP, "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def self_update(cfg: dict, exit_after: bool = True) -> None:
    """Hosted installs (script lives in ~/.sessionbell/) auto-update from the
    backend; git-checkout installs are the dev's business and are left alone.
    exit_after=True is the relay's mode (launchd KeepAlive restarts it on the
    new code); hook processes pass False and restart the relay explicitly."""
    import hashlib
    if cfg.get("auto_update") is False:
        return
    me = os.path.abspath(__file__)
    # normcase/normpath: on Windows expanduser mixes / and \ — a raw
    # startswith would silently disable self-update there.
    if not os.path.normcase(me).startswith(
            os.path.normcase(os.path.abspath(CONFIG_DIR))):
        return
    try:
        p = subprocess.run(
            ["curl", "-fsSL", "-m", "20",
             cfg["backend_url"].rstrip("/") + "/sessionbell_hook.py"],
            capture_output=True, timeout=25)
        if p.returncode != 0 or len(p.stdout) < 10000:
            return
        new = p.stdout
        with open(me, "rb") as f:
            if hashlib.sha256(f.read()).digest() == hashlib.sha256(new).digest():
                return
        tmp = me + ".new"
        with open(tmp, "wb") as f:
            f.write(new)
        if subprocess.run([sys.executable, "-m", "py_compile", tmp],
                          capture_output=True).returncode != 0:
            os.unlink(tmp)
            log("self-update: downloaded script failed to compile, skipped")
            return
        os.replace(tmp, me)
        os.chmod(me, 0o755)
        log("self-update: new version installed, restarting daemon")
        if not exit_after:
            restart_relay()
            return
        if IS_WIN:
            # No launchd KeepAlive here — hand off to a fresh copy ourselves.
            subprocess.Popen(self_cmd("relay"), creationflags=WIN_DETACHED)
        os._exit(0)  # launchd KeepAlive brings us back on the new code
    except Exception as exc:
        log(f"self-update error: {exc}")


def run_watcher(cfg: dict) -> None:
    """Instant remote control: poll the mailbox and TYPE fresh commands into
    the session's Otty pane — equivalent to the user typing at the keyboard,
    so it works mid-turn (steering) and on idle sessions (starts a turn)."""
    last_usage = 0.0
    last_cmd = 0.0
    last_reap = 0.0
    # Newest command ts the backend has shown us; the long-poll returns early
    # only for rows newer than this. 0 → the first call answers immediately.
    seen_ts = 0
    backend_down = False
    # `watcher_poll_seconds` keeps the legacy fixed-interval mode (no hold).
    legacy_poll = cfg.get("watcher_poll_seconds")
    first = True
    last_pass = time.time()
    while True:
        # The server holds the request when quiet; only back off locally if
        # the backend is unreachable or a legacy fixed interval is configured.
        poll = legacy_poll or (10 if backend_down else 1)
        # First pass runs straight away: a freshly (re)started relay should
        # publish state now, not after an idle 15 s.
        if first:
            first = False
        else:
            time.sleep(poll)
        # Include the held network request in sleep/wake detection: the Mac
        # may suspend during the long-poll, not just during time.sleep().
        # Re-sync right away instead of waiting for the next
        # 10-min tick, so the phone stops showing pre-sleep state. The network
        # (and any VPN) may lag the wake by a while — wait for the backend to
        # answer before forcing the tick, so the sync itself doesn't time out.
        asleep = time.time() - last_pass - poll - (0 if legacy_poll else LP_WAIT)
        last_pass = time.time()
        if asleep > 90:
            log(f"watcher: woke after {int(asleep)}s asleep, resyncing")
            for _ in range(24):
                if backend_call(cfg, "GET", "/api/state") is not None:
                    break
                time.sleep(5)
            last_usage = 0.0
            last_reap = 0.0
        try:
            # Refresh usage stats every 10 min and push them to the backend.
            if time.time() - last_usage > 600:
                last_usage = time.time()
                self_update(cfg)
                usage_summary(cfg)
                refresh_codex_usage()
                state = load_sessions()
                sync_peers(cfg, state, host_label(cfg))
            # Reap dead/expired sessions even when no hook events fire —
            # without this, finished tasks linger on the lock screen until
            # the next keystroke anywhere on this Mac.
            if time.time() - last_reap > 60:
                last_reap = time.time()
                state = load_sessions()
                before = json.dumps(state["local"], sort_keys=True)
                prune_sessions(state, int(time.time()))
                if json.dumps(state["local"], sort_keys=True) != before:
                    save_sessions(state)
                    lbl = host_label(cfg)
                    sync_peers(cfg, state, lbl)
                    push_dashboard(cfg, make_jwt(cfg),
                                   HOSTS[cfg.get("environment", "sandbox")],
                                   state, lbl)
            resp = backend_poll(cfg, "/api/command",
                                0 if legacy_poll else LP_WAIT, seen_ts)
            backend_down = resp is None
            if backend_down:
                continue
            commands = resp.get("commands") or {}
            if not commands:
                continue
            seen_ts = max([seen_ts] + [int(c.get("ts", 0)) for c in commands.values()])
            state = load_sessions()
            changed = False
            # Machine-level commands addressed to THIS Mac.
            my_canon = canonical_label(host_label(cfg))
            cursors = state.setdefault("_cursors", {})
            for key, cmd in commands.items():
                action = ("sys" if key == f"_sys-{my_canon}"
                          else "codex" if key == f"_codex-{my_canon}"
                          else "spawn" if key == f"_spawn-{my_canon}"
                          else "tail" if key == f"_tail-{my_canon}"
                          else "md" if key == f"_md-{my_canon}"
                          else "type" if key == f"_type-{my_canon}" else None)
                if not action or not cmd.get("text"):
                    continue
                if cmd.get("ts", 0) <= max(cursors.get(key, 0),
                                           (time.time() - 4 * 3600) * 1000):
                    continue
                if not claim_command(cfg, key, cmd.get("ts")):
                    continue
                cursors[key] = cmd["ts"]
                changed = True
                last_cmd = time.time()
                if action == "sys":
                    handle_sys_command(cfg, cmd["text"])
                elif action == "codex":
                    if _CODEX_BRIDGE:
                        _CODEX_BRIDGE.wake.set()
                    if _CODEX_DESKTOP_CONTROL:
                        _CODEX_DESKTOP_CONTROL.wake.set()
                elif action == "spawn":
                    handle_spawn(cfg, cmd["text"])
                elif action == "type":
                    handle_type(cfg, state, cmd["text"])
                elif action == "md":
                    handle_md(cfg, cmd["text"].strip())
                else:
                    handle_tail(cfg, cmd["text"].strip())
            for sid, cmd in commands.items():
                entry = state["local"].get(sid) or state.get("codex_sessions", {}).get(sid)
                if not entry or not cmd.get("text"):
                    continue
                cursor = entry.get("cmd_ts", 0)
                if cmd.get("ts", 0) <= max(cursor, (time.time() - 4 * 3600) * 1000):
                    continue
                if entry.get("engine") == "codex":
                    if codex_is_desktop(sid, state):
                        # Conversion to /api/codex would reset this legacy
                        # reply's acceptance time and bypass context/age guards.
                        if claim_command(cfg, sid, cmd.get("ts")):
                            entry["cmd_ts"] = cmd["ts"]
                            entry["desktop_delivery"] = (
                                "Legacy notification reply was not sent. Review the conversation "
                                "and resend from the updated SessionBell app.")
                            changed = True
                        continue
                    import uuid
                    command_id = str(uuid.uuid5(uuid.NAMESPACE_URL, sid + ":" + str(cmd["ts"])))
                    result = backend_call(cfg, "POST", "/api/codex", {
                        "host": my_canon, "command_id": command_id, "action": "send",
                        "session_id": sid, "text": cmd["text"]})
                    if result and result.get("ok"):
                        claim_command(cfg, sid, cmd.get("ts"))
                        entry["cmd_ts"] = cmd["ts"]
                        changed = True
                        if _CODEX_BRIDGE:
                            _CODEX_BRIDGE.wake.set()
                    continue
                if not (entry.get("term_type") or entry.get("pane")):
                    continue  # no injection route; stop-window fallback covers it
                if not entry.get("pid") or not pid_alive(entry["pid"]):
                    continue
                if not claim_command(cfg, sid, cmd.get("ts")):
                    continue
                text = " ".join(cmd["text"].split())
                ok_inject, err = type_into_terminal(entry, text)
                if ok_inject:
                    entry["cmd_ts"] = cmd["ts"]
                    changed = True
                    last_cmd = time.time()
                    log(f"watcher: typed into {sid[:8]} "
                        f"({entry.get('term_type') or 'otty'}): {text[:40]}")
                else:
                    restored = backend_call(cfg, "POST", "/api/command/restore",
                                            {"session_id": sid, "text": cmd["text"], "ts": cmd["ts"]})
                    outcome = "restored" if isinstance(restored, dict) and restored.get("restored") else "not restored"
                    log(f"watcher: inject failed for {sid[:8]}, {outcome}: {err}")
            if changed:
                save_sessions(state)
        except Exception as exc:
            log(f"watcher error: {exc}")


def run_relay(cfg: dict) -> None:
    """Tiny LAN listener; the iPhone posts Live Activity push tokens here."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if use_backend(cfg):
        import threading
        global _CODEX_BRIDGE, _CODEX_DESKTOP_CONTROL
        if cfg.get("codex_enabled") and not IS_WIN:
            _CODEX_DESKTOP_CONTROL = CodexDesktopControl(cfg)
            threading.Thread(target=_CODEX_DESKTOP_CONTROL.run, daemon=True).start()
            _CODEX_BRIDGE = CodexBridge(cfg)
            threading.Thread(target=_CODEX_BRIDGE.run, daemon=True).start()
            threading.Thread(target=CodexDesktopObserver(cfg).run, daemon=True).start()
        threading.Thread(target=run_watcher, args=(cfg,), daemon=True).start()
        log("watcher: started (instant command injection via Otty panes)")

    port = cfg.get("relay_port", 48765)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _respond(self, code: int, body: str = "ok"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._respond(200 if self.path == "/health" else 404)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self._respond(400, "bad json")
                return

            if self.path == "/token":
                tokens = load_activity_tokens()
                if body.get("update_token"):
                    tokens["_dashboard"] = {
                        "token": body["update_token"],
                        "ts": int(time.time()),
                    }
                    tokens.pop("_dashboard_started", None)
                    log("relay: dashboard update token received")
                if body.get("pts_token"):
                    pts = tokens.setdefault("_pts", [])
                    if body["pts_token"] not in pts:
                        pts.append(body["pts_token"])
                        log("relay: new push-to-start token")
                save_activity_tokens(tokens)
                self._respond(200)
            elif self.path == "/state":
                if body.get("host") and isinstance(body.get("sessions"), dict):
                    state = load_sessions()
                    state["peers"][body["host"]] = {
                        "ts": body.get("ts", int(time.time())),
                        "sessions": body["sessions"],
                    }
                    save_sessions(state)
                    log(f"relay: state from {body['host']} ({len(body['sessions'])} sessions)")
                self._respond(200)
            elif self.path == "/decision":
                import re
                rid = re.sub(r"[^A-Za-z0-9._-]", "", str(body.get("request_id") or ""))[:128]
                decision = body.get("decision")
                if rid and decision in ("allow", "deny"):
                    os.makedirs(DECISIONS_DIR, exist_ok=True)
                    with open(os.path.join(DECISIONS_DIR, rid), "w") as f:
                        f.write(decision)
                    log(f"relay: decision {decision} for {rid[:12]}")
                    self._respond(200)
                else:
                    self._respond(400, "bad decision")
            else:
                self._respond(404, "not found")

    # Backend mode needs no LAN exposure — the phone talks HTTPS to the
    # backend, not to this relay. Only legacy LAN mode binds wide.
    bind = "127.0.0.1" if use_backend(cfg) else "0.0.0.0"
    log(f"relay: listening on {bind}:{port}")
    ThreadingHTTPServer((bind, port), Handler).serve_forever()


# ---------------- Phone approval ----------------

def tool_summary(tool_name: str, tool_input) -> str:
    if not isinstance(tool_input, dict):
        tool_input = {}
    for key in ("command", "file_path", "url", "prompt"):
        v = tool_input.get(key)
        if isinstance(v, str) and v.strip():
            return f"{tool_name}: {v.strip()}"
    if tool_input:
        return f"{tool_name} {json.dumps(tool_input, ensure_ascii=False)[:120]}"
    return tool_name


def handle_permission(cfg: dict, hook: dict) -> None:
    """Push an actionable notification, wait for the phone's verdict.

    Prints the PermissionRequest decision JSON on stdout when the user answers;
    prints nothing on timeout so the normal terminal prompt takes over.
    """
    phone_owned = bool(os.environ.get("SESSIONBELL_FORCE")
                       or os.environ.get("SESSIONBELL_SPAWNED"))
    if not phone_owned:
        idle = mac_idle_seconds()
        # 授权请求多半出现在"刚派完活就走开"的头一两分钟——门槛必须比普通
        # 提醒低得多,否则大多数请求都被"人还在"吞掉,手机上一次都见不到。
        min_idle = cfg.get("permission_min_idle_seconds", 30)
        if idle is not None and idle < min_idle:
            log(f"skip permission: user at keyboard (idle {idle:.0f}s < {min_idle}s)")
            return  # user is at the Mac — let the terminal ask

    import re
    import uuid

    request_id = hook.get("tool_use_id") or uuid.uuid4().hex
    request_id = re.sub(r"[^A-Za-z0-9._-]", "", request_id)[:128] or uuid.uuid4().hex
    cwd = hook.get("cwd") or os.getcwd()
    project = os.path.basename(cwd.rstrip("/")) or cwd
    session_id = hook.get("session_id") or "unknown"
    host = host_label(cfg)
    summary = clip_bytes(tool_summary(hook.get("tool_name") or "工具",
                                      hook.get("tool_input")), 800)
    tool_input = hook.get("tool_input") or {}
    if isinstance(tool_input, dict) and tool_input.get("description"):
        summary = f"{tool_input['description']}\n{summary}"
    # What was Claude doing when it hit this prompt?
    engine = os.environ.get("SESSIONBELL_ENGINE")
    context = clip_bytes(strip_markdown(
        hook.get("last_assistant_message") or
        ("" if engine == "codex" else last_assistant_text(hook.get("transcript_path", "")))), 600)

    now = int(time.time())
    relays = relays_list(cfg)
    jwt = make_jwt(cfg)
    env = cfg.get("environment", "sandbox")

    if context:
        body_key, body_args = "%@\n\n⤷ 正在进行：%@\n（锁屏卡片可直接批准，或长按这条通知）", [summary, context]
    else:
        body_key, body_args = "%@\n（锁屏卡片可直接批准，或长按这条通知）", [summary]
    payload = {
        "aps": {
            "alert": {
                **loc_alert("🔐 %@ · 请求授权", [project]),
                "subtitle": host,
                # body 只放 summary(老 App 的批准卡读它);带提示语的完整文案走 loc-key。
                "body": summary,
                "loc-key": body_key,
                "loc-args": body_args,
            },
            "sound": "default",
            "thread-id": session_id,
            "interruption-level": "time-sensitive",
            "category": "SB_DECIDE",
        },
        "sb": {
            "event": "permission",
            "engine": engine,
            "source": "desktop" if os.environ.get("SESSIONBELL_DESKTOP_PERMISSIONS") == "1" else "",
            "session_id": session_id,
            "cwd": cwd,
            "project": project,
            "host": host,
            "ts": now,
            "request_id": request_id,
            "relays": relays,
            "backend": ({"url": cfg["backend_url"].rstrip("/"),
                         "secret": cfg["backend_secret"]}
                        if use_backend(cfg) else None),
        },
    }

    sent = False
    for device_token in resolve_device_tokens(cfg):
        code, resp = send_push(jwt, HOSTS[env], device_token, payload, cfg["bundle_id"])
        log(f"permission {request_id[:12]} -> {device_token[:8]}… HTTP {code} {resp}")
        sent = sent or code == 200
    if not sent:
        return

    # Put the approval buttons on the Live Activity card.
    try:
        with open(PENDING_APPROVAL_PATH, "w") as f:
            # The card only shows two lines — keep its copy compact.
            json.dump({"id": request_id, "summary": clip_bytes(summary, 300),
                       "ts": now}, f)
    except OSError:
        pass
    state = load_sessions()
    state["local"][session_id] = {**state["local"].get(session_id, {}),
                                 "project": project, "status": "waiting", "since": now,
                                 "engine": engine, "cwd": cwd,
                                 "approval_id": request_id}
    save_sessions(state)
    sync_peers(cfg, state, host)
    push_dashboard(cfg, jwt, HOSTS[env], state, host)

    decision = None
    decision_path = os.path.join(DECISIONS_DIR, request_id)
    deadline = time.time() + cfg.get("approval_timeout_seconds", 600)
    while time.time() < deadline:
        # 人回到电脑前就立刻让位给终端提示,别让键盘前的人等手机。
        if not phone_owned:
            idle = mac_idle_seconds()
            if idle is not None and idle < 3:
                log(f"permission {request_id[:12]}: user returned, terminal takes over")
                break
        if use_backend(cfg):
            # Long-poll; stay short unless the phone owns this prompt, so the
            # "user is back at the Mac" check above keeps its ~1 s reaction.
            hold = LP_WAIT if phone_owned else 1
            hold = int(min(hold, max(0, deadline - time.time())))
            resp = backend_poll(cfg, f"/api/decision?id={request_id}", hold)
            if resp and resp.get("decision") in ("allow", "deny"):
                decision = resp["decision"]
                break
            if resp is None:
                time.sleep(1)
            continue
        try:
            with open(decision_path) as f:
                decision = f.read().strip()
            os.unlink(decision_path)
            break
        except OSError:
            time.sleep(0.5)

    # Clear the buttons; reflect the outcome on the dashboard.
    try:
        with open(PENDING_APPROVAL_PATH) as f:
            pending = json.load(f)
        if pending.get("id") == request_id:
            os.unlink(PENDING_APPROVAL_PATH)
    except (OSError, ValueError):
        pass
    state = load_sessions()
    entry = state["local"].get(session_id)
    if entry and entry.get("approval_id") == request_id:
        entry.pop("approval_id", None)
    if decision in ("allow", "deny"):
        state["local"][session_id] = {**(entry or {}), "project": project,
                                     "status": "running", "since": int(time.time())}
    save_sessions(state)
    push_dashboard(cfg, make_jwt(cfg), HOSTS[env], load_sessions(), host)

    if decision in ("allow", "deny"):
        log(f"permission {request_id[:12]}: {decision} (from phone)")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": decision},
            }
        }))
    else:
        log(f"permission {request_id[:12]}: timeout, falling back to terminal")


def strip_markdown(text: str) -> str:
    """Plain-text rendition for notification banners (which can't render md)."""
    import re
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*\n]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s*[-*]\s+", "· ", text, flags=re.M)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text


def clip_bytes(text: str, max_bytes: int) -> str:
    """Trim to a UTF-8 byte budget (APNs caps the whole payload at 4KB)."""
    b = text.encode("utf-8")
    if len(b) <= max_bytes:
        return text
    return b[:max_bytes].decode("utf-8", errors="ignore") + "…"


def try_inject_command(cfg, env, session_id, project, host,
                       wait_seconds, watch_return) -> bool:
    """Poll the command mailbox; on a fresh command, block the stop and feed
    it to Claude. Returns True if a command was injected."""
    deadline = time.time() + wait_seconds
    # The cursor only ever moves forward. It is re-read every round because
    # the pane watcher may claim the command, but the session record can also
    # vanish mid-wait (prune_sessions drops "done" entries after
    # DONE_LINGER_SECONDS, well inside the 960 s stop window) — reading 0 from
    # a pruned entry must not resurrect a command that was already delivered.
    cursor = (load_sessions()["local"].get(session_id) or {}).get("cmd_ts", 0)
    while time.time() < deadline:
        if watch_return and not os.environ.get("SESSIONBELL_FORCE"):
            idle = mac_idle_seconds()
            if idle is not None and idle < 5:
                log("stop wait: user is back at the Mac, releasing")
                return False
        cursor = max(cursor, (load_sessions()["local"].get(session_id) or {}).get("cmd_ts", 0))
        # Hold the request open; keep it short while we also watch for the
        # user coming back to the keyboard (that check runs between polls).
        hold = 5 if watch_return else LP_WAIT
        hold = int(min(hold, max(0, deadline - time.time())))
        resp = backend_poll(cfg, f"/api/command?id={session_id}", hold, cursor)
        cmd = (resp or {}).get("command")
        fresh = cmd and cmd.get("ts", 0) > max(cursor, (time.time() - 4 * 3600) * 1000)
        if fresh and cmd.get("text") and not claim_command(cfg, session_id, cmd.get("ts")):
            cursor = max(cursor, cmd.get("ts", 0))
            log("stop: command already claimed by the watcher")
            fresh = False
        if fresh and cmd.get("text"):
            text = cmd["text"]
            state = load_sessions()
            entry = state["local"].get(session_id) or {"project": project}
            entry.update({
                "status": "running", "since": int(time.time()),
                "detail": " ".join(text.split())[:80], "cmd_ts": cmd["ts"],
            })
            state["local"][session_id] = entry
            save_sessions(state)
            sync_peers(cfg, state, host)
            push_dashboard(cfg, make_jwt(cfg), HOSTS[env], state, host)
            log(f"stop: remote command -> continue ({text[:40]})")
            print(json.dumps({
                "decision": "block",
                "reason": f"📱 手机远程指令: {text[:60]}",
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "additionalContext":
                        f"用户通过 SessionBell 手机端远程发来新指令，请继续执行：\n\n{text}",
                },
            }, ensure_ascii=False))
            return True
        if resp is None or hold < 2:
            time.sleep(2)  # backend unreachable / budget nearly spent
    return False


def send_push(jwt: str, host: str, device_token: str, payload: dict, bundle_id: str):
    if _GATEWAY_CFG and use_push_gateway(_GATEWAY_CFG):
        return gateway_push(device_token, bundle_id, "alert", payload)
    cmd = [
        "curl", "-sS", "--http2", "-m", "10",
        "-o", "-", "-w", "\n%{http_code}",
        "-H", f"authorization: bearer {jwt}",
        "-H", f"apns-topic: {bundle_id}",
        "-H", "apns-push-type: alert",
        "-H", "apns-priority: 10",
        "-d", json.dumps(payload, ensure_ascii=False),
        f"https://{host}/3/device/{device_token}",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return 0, p.stderr.strip()
    body, _, code = p.stdout.rpartition("\n")
    return int(code or 0), body.strip()


USAGE_CACHE_PATH = os.path.join(CONFIG_DIR, "usage-cache.json")


def week_window(cfg: dict):
    """(window_start_ts, next_reset_ts). Anchored to the account's weekly
    reset when configured (week_reset_day/hour/tz), else rolling 7 days."""
    day = (cfg or {}).get("week_reset_day")
    if day is None:
        return time.time() - 7 * 86400, None
    import datetime
    from zoneinfo import ZoneInfo
    names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    wd = day if isinstance(day, int) else names.get(str(day).lower()[:3], 0)
    tz = ZoneInfo(cfg.get("week_reset_tz", "Asia/Shanghai"))
    hour = cfg.get("week_reset_hour", 0)
    now = datetime.datetime.now(tz)
    anchor = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    anchor -= datetime.timedelta(days=(now.weekday() - wd) % 7)
    if anchor > now:
        anchor -= datetime.timedelta(days=7)
    return anchor.timestamp(), (anchor + datetime.timedelta(days=7)).timestamp()


def compute_usage_by_day(window_start: float = None) -> dict:
    import datetime
    import glob

    now = time.time()
    week_ago = window_start or (now - 7 * 86400)
    by_day = {}
    files = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    for path in files:
        try:
            if os.path.getmtime(path) < week_ago:
                continue
            with open(path) as f:
                for line in f:
                    if '"usage"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("type") != "assistant":
                        continue
                    ts = obj.get("timestamp", "")
                    usage = (obj.get("message") or {}).get("usage") or {}
                    if not ts or not usage:
                        continue
                    try:
                        day_ts = datetime.datetime.fromisoformat(
                            ts.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        continue
                    if day_ts < week_ago:
                        continue
                    day = datetime.datetime.fromtimestamp(day_ts).strftime("%m-%d")
                    d = by_day.setdefault(
                        day, {"in": 0, "out": 0, "cache": 0, "calls": 0,
                              "fable_out": 0, "opus_out": 0})
                    d["in"] += usage.get("input_tokens", 0)
                    d["out"] += usage.get("output_tokens", 0)
                    d["cache"] += usage.get("cache_read_input_tokens", 0)
                    d["calls"] += 1
                    model = (obj.get("message") or {}).get("model") or ""
                    if "fable" in model:
                        d["fable_out"] += usage.get("output_tokens", 0)
                    elif "opus" in model:
                        d["opus_out"] += usage.get("output_tokens", 0)
        except OSError:
            continue
    return by_day


OFFICIAL_USAGE_CACHE = os.path.join(CONFIG_DIR, "official-usage.json")


def _iso_ts(ts):
    try:
        import datetime
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def fetch_official_api():
    """/api/oauth/usage — the /usage panel's own (undocumented) endpoint,
    OAuth token read-only from Claude Code's keychain entry (CC refreshes it).
    Cached 15 min: the endpoint 429s readily and numbers move slowly."""
    try:
        with open(OFFICIAL_USAGE_CACHE) as f:
            cached = json.load(f)
    except (OSError, ValueError):
        cached = None
    if cached and time.time() - cached.get("updated_at", 0) < 900:
        return cached
    try:
        import urllib.request
        if IS_WIN:
            # Claude Code keeps credentials in a file outside macOS.
            with open(os.path.expanduser("~/.claude/.credentials.json")) as f:
                tok = json.load(f)["claudeAiOauth"]["accessToken"]
        else:
            tok = json.loads(subprocess.run(
                ["security", "find-generic-password",
                 "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5).stdout.strip()
            )["claudeAiOauth"]["accessToken"]
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            # Without a claude-code UA the request lands in a strict 429 bucket.
            headers={"Authorization": "Bearer " + tok,
                     "anthropic-beta": "oauth-2025-04-20",
                     "User-Agent": "claude-code/2.1.227"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        out = {"updated_at": time.time()}
        for lim in d.get("limits") or []:
            kind = lim.get("kind") or ""
            model = ((lim.get("scope") or {}).get("model") or {})
            name = model.get("display_name")
            if lim.get("percent") is None:
                continue
            if kind == "weekly_scoped" and name and "pct" not in out:
                out.update(pct=lim["percent"], name=name,
                           resets_at=_iso_ts(lim.get("resets_at", "")))
            elif kind == "session" and "session_pct" not in out:
                out.update(session_pct=lim["percent"],
                           session_resets_at=_iso_ts(lim.get("resets_at", "")))
            elif not name and kind not in ("session",) and kind.startswith(
                    ("week", "seven")) and "total_pct" not in out:
                out.update(total_pct=lim["percent"],
                           total_resets_at=_iso_ts(lim.get("resets_at", "")))
        if len(out) > 1:
            try:
                with open(OFFICIAL_USAGE_CACHE, "w") as f:
                    json.dump(out, f)
            except OSError:
                pass
            return out
    except Exception as exc:
        log(f"official usage fetch failed: {exc}")
    # Stale-but-recent cache beats the token-proxy fallback.
    if cached and time.time() - cached.get("updated_at", 0) < 2 * 3600:
        return cached
    return None


def official_usage():
    """Ground truth for the usage bars. Primary: fetch the OAuth endpoint
    ourselves (zero setup — this is what makes new installs config-free).
    Fallback: ccwatch (wesdget)'s cache file, premium-only."""
    off = fetch_official_api()
    if off and (off.get("pct") is not None or off.get("total_pct") is not None):
        return off
    try:
        with open(os.path.expanduser("~/.claude/wesdget-model-usage.json")) as f:
            d = json.load(f)
        off = d.get("official") or {}
        if off.get("percent") is not None and time.time() - off.get("updated_at", 0) < 3600:
            return {"pct": off["percent"], "resets_at": off.get("resets_at"),
                    "name": off.get("display_name") or "Fable"}
    except (OSError, ValueError):
        pass
    return None


def usage_summary(cfg: dict = None) -> dict:
    """Compact rollup since the account's last weekly reset, cached for sync."""
    import datetime
    window_start, next_reset = week_window(cfg or {})
    by_day = compute_usage_by_day(window_start)
    today = datetime.datetime.now().strftime("%m-%d")
    t = by_day.get(today, {"out": 0, "calls": 0, "fable_out": 0})
    week_out = sum(d["out"] for d in by_day.values())
    week_calls = sum(d["calls"] for d in by_day.values())
    fable = sum(d.get("fable_out", 0) for d in by_day.values())
    opus = sum(d.get("opus_out", 0) for d in by_day.values())
    week_fable = fable + opus  # premium-tier bucket, name tells which
    summary = {"ts": int(time.time()),
               "today_out": t["out"], "today_calls": t["calls"],
               "week_out": week_out, "week_calls": week_calls,
               "week_fable": week_fable,
               "premium_name": "Fable" if fable >= opus else "Opus",
               "fable_budget": (cfg or {}).get("weekly_fable_budget_tokens"),
               "week_budget": (cfg or {}).get("weekly_budget_tokens", 10_000_000),
               "reset_ts": int(next_reset) if next_reset else None}
    official = official_usage()
    if official:
        if official.get("pct") is not None:
            summary["official_pct"] = official["pct"]
            summary["premium_name"] = official.get("name") or summary["premium_name"]
        if official.get("total_pct") is not None:
            summary["official_total_pct"] = official["total_pct"]
        if official.get("session_pct") is not None:
            summary["official_session_pct"] = official["session_pct"]
            if official.get("session_resets_at"):
                summary["session_reset_ts"] = int(official["session_resets_at"])
        resets = official.get("total_resets_at") or official.get("resets_at")
        if resets:
            summary["reset_ts"] = int(resets)
    try:
        with open(USAGE_CACHE_PATH, "w") as f:
            json.dump(summary, f)
    except OSError:
        pass
    return summary


def cached_usage() -> dict:
    try:
        with open(USAGE_CACHE_PATH) as f:
            u = json.load(f)
        if time.time() - u.get("ts", 0) < 3600:
            return u
    except (OSError, ValueError):
        pass
    return {}


def cmd_usage() -> None:
    """Local token usage from ~/.claude transcripts — per Mac = per account."""
    by_day = compute_usage_by_day()

    def fmt(n):
        return f"{n/1e6:.1f}M" if n >= 1e6 else f"{n/1e3:.0f}k" if n >= 1000 else str(n)

    label = host_label(load_config("usage") if os.path.exists(CONFIG_PATH) else {})
    print(f"📊 {label} 本周期用量(本机账号,自 transcript 统计)")
    print(f"{'日期':6} {'输出':>8} {'Fable出':>8} {'缓存读':>8} {'调用':>6}")
    tot = {"out": 0, "fable_out": 0, "cache": 0, "calls": 0}
    for day in sorted(by_day):
        d = by_day[day]
        print(f"{day:6} {fmt(d['out']):>8} {fmt(d.get('fable_out', 0)):>8}"
              f" {fmt(d['cache']):>8} {d['calls']:>6}")
        for k in tot:
            tot[k] += d.get(k, 0)
    print(f"{'合计':6} {fmt(tot['out']):>8} {fmt(tot['fable_out']):>8}"
          f" {fmt(tot['cache']):>8} {tot['calls']:>6}")
    print("\n官方套餐余量与重置时间请在 Claude Code 里输 /usage 查看(本工具无法读取)。")


def cmd_calibrate(pct_str: str) -> None:
    """`calibrate 24` — official /usage says 24%; back-solve the real budget."""
    try:
        pct = float(pct_str)
        assert 0 < pct <= 100
    except (ValueError, AssertionError):
        sys.stderr.write("用法: sessionbell_hook.py calibrate <官方百分比,如 24>\n")
        sys.exit(1)
    cfg = load_config("usage")
    summary = usage_summary(cfg)
    if not summary.get("reset_ts"):
        sys.stderr.write("⚠ 未配置 week_reset_day/hour/tz——请先在 config.json 里配好重置锚点,"
                         "否则窗口不对,校准无意义。\n")
        sys.exit(1)
    budget = int(summary["week_out"] / (pct / 100))
    with open(CONFIG_PATH) as f:
        raw = json.load(f)
    raw["weekly_budget_tokens"] = budget
    msg = (f"✓ 本周期已用 {summary['week_out']:,} tokens = 官方 {pct}% "
           f"→ 周预算校准为 {budget:,}")
    # 可选第二个参数:官方 Fable/高级模型分项百分比
    if len(sys.argv) > 3:
        try:
            fpct = float(sys.argv[3])
            if 0 < fpct <= 100:
                if summary.get("week_fable"):
                    raw["weekly_fable_budget_tokens"] = int(
                        summary["week_fable"] / (fpct / 100))
                    msg += f";高级模型预算校准为 {raw['weekly_fable_budget_tokens']:,}"
                else:
                    msg += ";⚠ 高级模型桶为 0,第二个参数被跳过(先 git pull + 重启 relay 再试)"
        except ValueError:
            pass
    with open(CONFIG_PATH, "w") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)
    usage_summary(raw)  # refresh cache with the new budgets
    print(msg + ",已写入 config")


def cmd_codex_setup() -> None:
    """Register the optional desktop permission hook, preserving other tools.

    Lifecycle monitoring is read-only; shared CLI approvals use app-server.
    Codex's normal explicit hook trust review is still required.
    """
    me = os.path.abspath(__file__)
    py = sys.executable or "/usr/bin/python3"

    import shlex
    import shutil

    def group(kind, timeout, matcher=None):
        entry = {"hooks": [{"type": "command", "timeout": timeout,
                            "command": "SESSIONBELL_ENGINE=codex SESSIONBELL_DESKTOP_PERMISSIONS=1 " +
                            " ".join(shlex.quote(x) for x in (py, me, kind))}]}
        if matcher:
            entry["matcher"] = matcher
        return entry

    wanted = {"PermissionRequest": group("permission", 900)}
    path = os.path.join(codex_home(), "hooks.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        raise SystemExit("Cannot read Codex hooks.json; no changes made.")
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise SystemExit("Invalid Codex hooks.json; no changes made.")
    hooks = data.setdefault("hooks", {})
    for ev in set(hooks) | set(wanted):
        arr = hooks.setdefault(ev, [])
        if not isinstance(arr, list):
            raise SystemExit("Invalid hook group; no changes made: " + ev)
        # Preserve unrelated handlers even when they share a matcher group.
        keep = []
        for entry in arr:
            handlers = [h for h in entry.get("hooks", [])
                        if "sessionbell_hook.py" not in h.get("command", "")]
            if handlers:
                keep.append(dict(entry, hooks=handlers))
        arr[:] = keep
        if ev in wanted:
            arr.append(wanted[ev])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        backup = path + ".sessionbell-backup-" + str(time.time_ns())
        shutil.copy2(path, backup)
        print("Backup: " + backup)
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix="hooks-", suffix=".json", dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    print(f"✓ 已注册 SessionBell → {path}")
    print("  已配置可选的桌面手机审批；状态与完成通知由只读监测器提供。")
    print("  请在 Codex 的 /hooks 或 Hooks 设置中审核并信任 SessionBell hooks。")
    print("  信任完成后新开会话生效；桌面会话不支持手机追问。")


def cmd_codex_enable():
    """Enable shared CLI control and read-only native desktop observation."""
    import plistlib
    import shutil
    if IS_WIN or sys.platform != "darwin":
        raise SystemExit("Shared Codex desktop integration currently requires macOS.")
    binary = codex_bin()
    if not binary:
        raise SystemExit("Install Codex first.")
    cfg = load_config("test")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    launch_dir = os.path.expanduser("~/Library/LaunchAgents")
    os.makedirs(launch_dir, exist_ok=True)
    path = os.path.join(launch_dir, "dev.piper.sessionbell.codex.plist")
    stamp = str(time.time_ns())
    backup_dir = os.path.join(CONFIG_DIR, "codex-backup-" + stamp)
    os.makedirs(backup_dir, mode=0o700)
    shutil.copy2(CONFIG_PATH, os.path.join(backup_dir, "config.json"))
    if os.path.exists(path):
        shutil.copy2(path, os.path.join(backup_dir, "codex.plist"))
    prior = {key: subprocess.run(["launchctl", "getenv", key], capture_output=True, text=True).stdout.strip()
             for key in ("CODEX_APP_SERVER_USE_LOCAL_DAEMON", "CODEX_APP_SERVER_WS_URL")}
    with open(os.path.join(backup_dir, "desktop-env.json"), "w") as f:
        json.dump(prior, f)
    service = {"Label": "dev.piper.sessionbell.codex",
               "ProgramArguments": [binary, "-c", "features.code_mode_host=true",
                                    "app-server", "--listen", "unix://"],
               "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
               "EnvironmentVariables": {
                   "PATH": os.path.dirname(binary) + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
                   "CODEX_HOME": codex_home(), "SESSIONBELL_CODEX_SHARED": "1"},
               "StandardOutPath": os.path.join(CONFIG_DIR, "codex-service.log"),
               "StandardErrorPath": os.path.join(CONFIG_DIR, "codex-service.log")}
    # Never replace/restart an existing daemon; it may already own active turns.
    sock = os.path.join(codex_home(), "app-server-control", "app-server-control.sock")
    if os.path.exists(sock):
        with CodexRPC(shared=True) as rpc:
            rpc.call("thread/loaded/list")
    else:
        with open(path, "wb") as f:
            plistlib.dump(service, f)
        domain = "gui/" + str(os.getuid())
        result = subprocess.run(["launchctl", "bootstrap", domain, path], capture_output=True)
        if result.returncode != 0:
            subprocess.run(["launchctl", "kickstart", domain + "/dev.piper.sessionbell.codex"], check=True)
        deadline = time.monotonic() + 15
        while not os.path.exists(sock) and time.monotonic() < deadline:
            time.sleep(0.2)
        with CodexRPC(shared=True) as rpc:
            rpc.call("thread/loaded/list")
    # Never redirect the desktop: its native MCP transports require its own server.
    cfg["codex_enabled"] = True
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    with open(os.path.join(CONFIG_DIR, "codex-install.json"), "w") as f:
        json.dump({"backup": backup_dir, "service": path, "desktop": "read-only observer"}, f)
    print("Codex shared service ready. Backup: " + backup_dir)
    print("CLI: codex --remote unix://")
    print("Desktop sessions are observed read-only; no desktop restart or transport change.")
    print("Restart SessionBell relay after installing this hook version.")


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "test"
    if kind == "stayawake":
        # Windows caffeinate: hold the machine awake until this process dies.
        if IS_WIN:
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            while True:
                time.sleep(3600)
        return
    if kind == "winconsole":
        cmd_winconsole(sys.argv[2] if len(sys.argv) > 2 else "",
                       sys.argv[3] if len(sys.argv) > 3 else "",
                       sys.argv[4] if len(sys.argv) > 4 else "")
        return
    if kind == "usage":
        cmd_usage()
        return
    if kind == "codex-setup":
        cmd_codex_setup()
        return
    if kind == "codex-enable":
        cmd_codex_enable()
        return
    if kind == "codex":
        binary = codex_bin()
        if not binary:
            raise SystemExit("Install Codex first.")
        os.execv(binary, [binary, "--remote", "unix://"] + sys.argv[2:])
    if kind == "codex-usage":
        print(json.dumps(refresh_codex_usage(), ensure_ascii=False, indent=2))
        return
    if kind == "calibrate":
        cmd_calibrate(sys.argv[2] if len(sys.argv) > 2 else "")
        return
    if kind == "pairing-code":
        cfg = load_config("usage")
        code = base64.b64encode(json.dumps(
            {"u": cfg["backend_url"].rstrip("/"),
             "s": cfg["backend_secret"]}).encode()).decode()
        print(code)
        return
    if kind == "pair-code":
        # 给同一空间再加一台手机/iPad:向后端要一个 15 分钟有效的 6 位数字,
        # 另一台设备在引导页「加入那个空间」里输入,或用相机扫打开的那一页。
        cfg = load_config("usage")
        pc = base64.b64encode(json.dumps(
            {"u": cfg["backend_url"].rstrip("/"),
             "s": cfg["backend_secret"]}).encode()).decode()
        r = backend_call(cfg, "POST", "/api/pair-code", {"pairing_code": pc}) or {}
        code = r.get("short_code")
        if not code:
            sys.stderr.write(f"SessionBell: 没拿到配对数字 {r}\n")
            sys.exit(1)
        page = cfg["backend_url"].rstrip("/") + "/p/" + code
        print(f"📱 在另一台设备的 SessionBell 里选「加入那个空间」,输入 {code[:3]} {code[3:]}(15 分钟内有效)")
        print(f"   或者用相机扫这一页上的二维码:{page}")
        if not IS_WIN:
            subprocess.run(["open", page], capture_output=True)
        return
    cfg = load_config(kind)
    _GATEWAY_CFG.update(cfg)

    if kind == "relay":
        run_relay(cfg)
        return
    if kind == "self-update":
        cmd_self_update(cfg)
        return
    if kind in ("stop", "session-end"):
        self_update_from_hook(cfg)

    hook = {}
    if kind != "test" and not sys.stdin.isatty():
        try:
            hook = json.load(sys.stdin)
        except ValueError:
            hook = {}

    if os.environ.get("SESSIONBELL_ENGINE") == "codex":
        # Shared sessions are observed directly by the bridge, including native
        # approval requests. Do not also run blocking hooks for the same turn.
        known = load_sessions().get("codex_sessions", {}).get(hook.get("session_id"), {})
        if os.environ.get("SESSIONBELL_CODEX_SHARED") == "1" or (
                known.get("managed") and codex_bridge_status().get("connected")):
            return

    if kind == "permission":
        if os.environ.get("SESSIONBELL_DESKTOP_PERMISSIONS") == "1":
            path = hook.get("transcript_path")
            if not isinstance(path, str) or not CodexDesktopObserver(cfg).metadata(path, hook.get("session_id")):
                return  # Never intercept CLI/extension approvals or an unknown origin.
        handle_permission(cfg, hook)
        return

    cwd = hook.get("cwd") or hook.get("workspace") or os.getcwd()
    project = os.path.basename(cwd.rstrip("/")) or cwd
    # Codex hooks share the schema but field names are unverified per
    # version — accept the likely aliases.
    session_id = (hook.get("session_id") or hook.get("thread_id")
                  or hook.get("conversation_id") or "unknown")
    engine = os.environ.get("SESSIONBELL_ENGINE")
    if engine == "codex" and session_id == "unknown":
        return  # Never merge unrelated conversations into an invented session.
    host = host_label(cfg)
    env = cfg.get("environment", "sandbox")
    now = int(time.time())

    def excerpt(text, limit=80):
        text = " ".join((text or "").split())
        return text[: limit - 1] + "…" if len(text) > limit else text

    # Dashboard bookkeeping runs for every event, idle or not — it's a status
    # board, not a ring. Alert pushes below stay idle-gated.
    dashboard_kinds = ("prompt", "session-end", "notification", "stop", "interrupt",
                       "subagent-start", "subagent-stop")
    if kind in dashboard_kinds:
        state = load_sessions()
        prev = state["local"].get(session_id) or {}
        # Last-prompt registry: outlives the session record (like `terms`),
        # so stop/notification after a prune/resurrect still name the task.
        last_prompt = clean_detail((state.get("prompts", {}).get(session_id) or {}).get("text", ""))
        if kind == "prompt":
            own_pid, parent_pid = engine_pids(engine)
            # 注入的伪 prompt 不能当任务名,否则锁屏上全是 <task-notification> <task-id>…;沿用上一条真实 prompt。
            ptext = "" if injected_text(hook.get("prompt")) else excerpt(hook.get("prompt"))
            if ptext:
                state.setdefault("prompts", {})[session_id] = {
                    "text": ptext, "ts": int(now)}
            # Typing locally supersedes anything queued from the phone.
            state["local"][session_id] = {
                **prev,
                "project": project, "status": "running", "since": now,
                # Slash commands / spawned first beats carry no prompt text —
                # never blank out a task that already has a name.
                "detail": ptext or clean_detail(prev.get("detail")) or last_prompt, "agents": 0,
                "cwd": cwd,
                "root": project_root(cwd),
                "pid": own_pid or prev.get("pid"),
                "parent_pid": parent_pid or prev.get("parent_pid"),
                "pane": os.environ.get("OTTY_PANE_ID") or prev.get("pane"),
                "term_type": terminal_handle()[0] or prev.get("term_type"),
                "term_handle": terminal_handle()[1] or prev.get("term_handle"),
                "cmd_ts": int(now * 1000),
                "mode": hook.get("permission_mode") or prev.get("mode"),
                "effort": hook.get("effort") or prev.get("effort"),
                "engine": engine or prev.get("engine"),
            }
            # Pane handles outlive the session record so the phone terminal
            # can reattach after session-end / prune (registry, 7-day TTL).
            ent = state["local"][session_id]
            if ent.get("term_type") or ent.get("pane"):
                state.setdefault("terms", {})[session_id] = {
                    "project": project, "ts": int(now),
                    "term_type": ent.get("term_type"),
                    "term_handle": ent.get("term_handle"),
                    "pane": ent.get("pane"), "pid": ent.get("pid"),
                }
            if engine == "codex":
                state.setdefault("codex_sessions", {})[session_id] = {
                    "cwd": cwd, "project": project, "engine": engine, "ts": now}
        elif kind == "session-end":
            state["local"].pop(session_id, None)
        elif kind == "notification":
            # Keep the prompt excerpt — it names the task; the generic
            # "waiting for your input" message does not.
            state["local"][session_id] = {
                **prev,
                "project": project, "status": "waiting", "since": now,
                "detail": clean_detail(prev.get("detail")) or last_prompt or excerpt(
                    hook.get("message") or hook.get("tool_name")),
                "agents": prev.get("agents", 0),
                "cmd_ts": prev.get("cmd_ts", 0),
                "pid": prev.get("pid") or engine_pids(engine)[0],
                "parent_pid": prev.get("parent_pid") or engine_pids(engine)[1],
                "pane": os.environ.get("OTTY_PANE_ID") or prev.get("pane"),
                "mode": hook.get("permission_mode") or prev.get("mode"),
                "effort": hook.get("effort") or prev.get("effort"),
                "engine": engine or prev.get("engine"),
                "cwd": cwd,
            }
        elif kind in ("stop", "interrupt"):
            state["local"][session_id] = {
                **prev,
                "project": project, "status": "waiting" if kind == "interrupt" else "done", "since": now,
                "detail": clean_detail(prev.get("detail")) or last_prompt, "agents": 0,
                "cmd_ts": prev.get("cmd_ts", 0),
                "pid": prev.get("pid") or engine_pids(engine)[0],
                "parent_pid": prev.get("parent_pid") or engine_pids(engine)[1],
                "pane": os.environ.get("OTTY_PANE_ID") or prev.get("pane"),
                "mode": hook.get("permission_mode") or prev.get("mode"),
                "effort": hook.get("effort") or prev.get("effort"),
                "engine": engine or prev.get("engine"),
                "cwd": cwd,
            }
            if engine == "codex" and hook.get("last_assistant_message"):
                state["local"][session_id]["latest_reply"] = clip_bytes(hook["last_assistant_message"], 8000)
        elif kind == "subagent-start":
            if not prev:
                return  # unseen session; don't invent a row
            prev["agents"] = prev.get("agents", 0) + 1
            state["local"][session_id] = prev
        elif kind == "subagent-stop":
            if not prev:
                return
            prev["agents"] = max(0, prev.get("agents", 0) - 1)
            state["local"][session_id] = prev
        prune_sessions(state, now)
        save_sessions(state)
        # Subagent churn is bookkeeping only — a badge isn't worth a network
        # round-trip and an LA push per spawn; it rides the next real event.
        if kind in ("subagent-start", "subagent-stop", "interrupt"):
            return
        sync_peers(cfg, state, host)
        push_dashboard(cfg, make_jwt(cfg), HOSTS[env], state, host)
        if kind in ("prompt", "session-end"):
            return

    # Only ring the phone when the user actually stepped away from the Mac.
    # Phone-spawned sessions are exempt: their owner IS the phone.
    if (kind != "test" and not (engine == "codex" and kind == "stop")
            and not os.environ.get("SESSIONBELL_FORCE")
            and not os.environ.get("SESSIONBELL_SPAWNED")):
        idle = mac_idle_seconds()
        min_idle = cfg.get("min_idle_seconds", 120)
        if kind == "notification" and hook.get("notification_type") == "permission_prompt":
            # 授权卡着整个任务,等不起两分钟——与 PermissionRequest 同一道低门槛。
            min_idle = cfg.get("permission_min_idle_seconds", 30)
        if idle is not None and idle < min_idle:
            log(f"skip {kind}: user at keyboard (idle {idle:.0f}s < {min_idle}s)")
            # Still honor a phone command sent moments ago — a quick mailbox
            # check so remote control works even with the user at the desk.
            if kind == "stop" and use_backend(cfg) and engine != "codex":
                try_inject_command(cfg, env, session_id, project, host,
                                   10, watch_return=False)
            return

    task_detail = clean_detail((load_sessions()["local"].get(session_id) or {}).get("detail", ""))

    raw_md = ""
    body_key = body_args = None   # 固定短语走 loc-key;动态正文(Claude 的回复)原样发
    if kind == "stop":
        title_key, title_args = "✅ %@ · 任务完成", [project]
        raw_md = (hook.get("last_assistant_message") or "") if engine == "codex" else last_assistant_text(hook.get("transcript_path", ""))
        body = strip_markdown(raw_md)
        if not body:
            body = "Codex 已完成本轮任务" if engine == "codex" else "Claude 已完成本轮任务"
            body_key, body_args = body, []
        if task_detail:
            title_key, title_args = "✅ %@ · 完成「%@」", [project, task_detail[:24]]
    elif kind == "notification":
        # Permission prompts get their own actionable push from the
        # PermissionRequest hook — while that card is still live, don't
        # double-ring. But if it was idle-skipped or already timed out,
        # this notification is the only chance to ring — let it through.
        if hook.get("notification_type") == "permission_prompt" and use_backend(cfg):
            try:
                with open(PENDING_APPROVAL_PATH) as f:
                    if now - json.load(f).get("ts", 0) < APPROVAL_FRESH_SECONDS:
                        return
            except (OSError, ValueError):
                pass
        title_key, title_args = "🖐 %@ · 需要你", [project]
        # What is Claude actually asking? The last assistant message says.
        raw_md = (hook.get("last_assistant_message") or "") if engine == "codex" else last_assistant_text(hook.get("transcript_path", ""))
        if raw_md:
            body = strip_markdown(raw_md)
        else:
            body = hook.get("message") or ("Codex 在等待你的输入或授权" if engine == "codex" else "Claude 在等待你的输入或授权")
            if not hook.get("message"):
                body_key, body_args = body, []
            if task_detail:
                body_key, body_args = "「%@」%@", [task_detail, body]
                body = f"「{task_detail}」{body}"
    else:
        title_key, title_args = "🔔 SessionBell 测试", []
        body, body_key, body_args = "推送链路打通了！", "推送链路打通了！", []
        session_id = "test"

    # Full detail readable on the phone: long-press the banner, or open the
    # event in the app (which renders sb.md as real markdown). Budgets keep
    # the whole payload inside APNs' 4KB cap.
    body = clip_bytes(body, 1200)
    raw_md = clip_bytes(raw_md, 2000) if raw_md else ""

    alert = {**loc_alert(title_key, title_args), "subtitle": host, "body": body}
    if body_key is not None:
        alert["loc-key"], alert["loc-args"] = body_key, [clip_bytes(a, 1200) for a in body_args]
    payload = {
        "aps": {
            "alert": alert,
            "sound": "default",
            "thread-id": session_id,
            "interruption-level": "time-sensitive",
            # 长按可直接打字回复下一步指令(stop 时注入,见下)
            "category": "SB_REPLY",
        },
        "sb": {
            "event": kind,
            "engine": engine,
            "session_id": session_id,
            "cwd": cwd,
            "project": project,
            "host": host,
            "ts": int(time.time()),
            "backend": ({"url": cfg["backend_url"].rstrip("/"),
                         "secret": cfg["backend_secret"]}
                        if use_backend(cfg) else None),
            "md": raw_md or None,
        },
    }

    jwt = make_jwt(cfg)
    other_env = "production" if env == "sandbox" else "sandbox"

    # The installer ends with `hook test`: publish our first heartbeat right
    # here so the phone's "waiting for the Mac" page flips within one poll,
    # instead of waiting for the relay's first watcher tick.
    if kind == "test" and use_backend(cfg):
        sync_peers(cfg, load_sessions(), host)

    ok = True
    for device_token in resolve_device_tokens(cfg):
        code, resp = send_push(jwt, HOSTS[env], device_token, payload, cfg["bundle_id"])
        if code == 400 and "BadDeviceToken" in resp:
            # Debug builds talk to sandbox, TestFlight/App Store to production —
            # fall back to the other environment before giving up.
            code, resp = send_push(jwt, HOSTS[other_env], device_token, payload, cfg["bundle_id"])
            if code == 200:
                log(f"hint: token belongs to {other_env}; set \"environment\": \"{other_env}\"")
        log(f"{kind} -> {device_token[:8]}… HTTP {code} {resp}")
        if code != 200:
            ok = False
            if kind == "test":
                sys.stderr.write(f"SessionBell: 推送失败 HTTP {code} {resp}\n")

    if kind == "test":
        if ok:
            print("✅ 推送已发出，看看手机。")
        else:
            sys.exit(1)
        return

    # Remote control: while the user is away, keep the turn open after the
    # done-push so a phone reply can be injected. Releases the moment the
    # user touches the Mac again, or after reply_wait_seconds.
    if kind == "stop" and ok and use_backend(cfg) and engine != "codex":
        try_inject_command(cfg, env, session_id, project, host,
                           cfg.get("reply_wait_seconds", 900), watch_return=True)


if __name__ == "__main__":
    main()
