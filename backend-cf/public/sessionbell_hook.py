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


def claude_bin():
    cache = os.path.join(CONFIG_DIR, "claude-bin")
    try:
        with open(cache) as f:
            p = f.read().strip()
        if p and os.path.exists(p):
            return p
    except OSError:
        pass
    import glob as _glob
    import shutil
    p = shutil.which("claude") or ""
    if not p:
        candidates = [os.path.expanduser("~/.local/bin/claude"),
                      "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
        candidates += sorted(_glob.glob(os.path.expanduser(
            "~/.nvm/versions/node/*/bin/claude")), reverse=True)
        p = next((c for c in candidates if os.path.exists(c)), "")
    if not p and not IS_WIN:
        try:
            r = subprocess.run(["/bin/zsh", "-ilc", "command -v claude"],
                               capture_output=True, text=True, timeout=15)
            p = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        except Exception:
            p = ""
    if p and os.path.exists(p):
        try:
            with open(cache, "w") as f:
                f.write(p)
        except OSError:
            pass
        return p
    return None


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
    if ok and sid in state["local"]:
        # Raw typing supersedes any queued sid-keyed command, like local typing.
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


def backend_call(cfg: dict, method: str, path: str, body=None):
    """GET/POST against the Vercel backend; returns parsed JSON or None."""
    url = cfg["backend_url"].rstrip("/") + path
    cmd = ["curl", "-sS", "-m", "8", "-H", f"x-sb-secret: {cfg['backend_secret']}"]
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
    try:
        with open(SESSIONS_PATH) as f:
            s = json.load(f)
        s.setdefault("local", {})
        s.setdefault("peers", {})
        return s
    except (OSError, ValueError):
        return {"local": {}, "peers": {}}


def save_sessions(state: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SESSIONS_PATH, "w") as f:
            json.dump(state, f, indent=2)
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


def prune_sessions(state: dict, now: int) -> None:
    local = state["local"]
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
            parent = pid2sid.get(e.get("parent_pid"))
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
    # Keep approval buttons on the card across unrelated dashboard updates.
    try:
        with open(PENDING_APPROVAL_PATH) as f:
            approval = json.load(f)
        if now - approval.get("ts", 0) < APPROVAL_FRESH_SECONDS:
            content_state["approvalId"] = approval["id"]
            content_state["approvalSummary"] = approval["summary"]
    except (OSError, ValueError, KeyError):
        pass
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
        title = f"🖐 {len(waiting)} 个任务在等你"
    else:
        title = f"⏳ {len(active)} 个任务运行中"
    aps = {
        "timestamp": now,
        "event": "start",
        "attributes-type": "SessionActivityAttributes",
        "attributes": attributes,
        "content-state": content_state,
        "stale-date": now + LA_STALE_SECONDS,
        # Apple requires an alert dict on push-to-start payloads.
        "alert": {
            "title": title,
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


def self_update(cfg: dict) -> None:
    """Hosted installs (script lives in ~/.sessionbell/) auto-update from the
    backend; git-checkout installs are the dev's business and are left alone."""
    import hashlib
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
    while True:
        # Adaptive: poll fast while a session likely awaits a phone reply
        # (waiting / freshly done) or the phone is actively driving us
        # (terminal view, recent machine commands); lazily when quiet.
        try:
            now0 = time.time()
            hot = (now0 - last_cmd < 180) or any(
                e.get("status") == "waiting"
                or (e.get("status") == "done" and now0 - e.get("since", 0) < 900)
                for e in load_sessions()["local"].values())
        except Exception:
            hot = False
        poll = cfg.get("watcher_poll_seconds") or (3 if hot else 15)
        time.sleep(poll)
        try:
            # Refresh usage stats every 10 min and push them to the backend.
            if time.time() - last_usage > 600:
                last_usage = time.time()
                self_update(cfg)
                usage_summary(cfg)
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
            resp = backend_call(cfg, "GET", "/api/command") or {}
            commands = resp.get("commands") or {}
            if not commands:
                continue
            state = load_sessions()
            changed = False
            # Machine-level commands addressed to THIS Mac.
            my_canon = canonical_label(host_label(cfg))
            cursors = state.setdefault("_cursors", {})
            for key, cmd in commands.items():
                action = ("sys" if key == f"_sys-{my_canon}"
                          else "spawn" if key == f"_spawn-{my_canon}"
                          else "tail" if key == f"_tail-{my_canon}"
                          else "type" if key == f"_type-{my_canon}" else None)
                if not action or not cmd.get("text"):
                    continue
                if cmd.get("ts", 0) <= max(cursors.get(key, 0),
                                           (time.time() - 4 * 3600) * 1000):
                    continue
                cursors[key] = cmd["ts"]
                changed = True
                last_cmd = time.time()
                if action == "sys":
                    handle_sys_command(cfg, cmd["text"])
                elif action == "spawn":
                    handle_spawn(cfg, cmd["text"])
                elif action == "type":
                    handle_type(cfg, state, cmd["text"])
                else:
                    handle_tail(cfg, cmd["text"].strip())
            for sid, cmd in commands.items():
                entry = state["local"].get(sid)
                if not entry or not cmd.get("text"):
                    continue
                cursor = entry.get("cmd_ts", 0)
                if cmd.get("ts", 0) <= max(cursor, (time.time() - 4 * 3600) * 1000):
                    continue
                if not (entry.get("term_type") or entry.get("pane")):
                    continue  # no injection route; stop-window fallback covers it
                if not entry.get("pid") or not pid_alive(entry["pid"]):
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
                    log(f"watcher: inject failed for {sid[:8]}: {err}")
            if changed:
                save_sessions(state)
        except Exception as exc:
            log(f"watcher error: {exc}")


def run_relay(cfg: dict) -> None:
    """Tiny LAN listener; the iPhone posts Live Activity push tokens here."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if use_backend(cfg):
        import threading
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
    context = clip_bytes(
        strip_markdown(last_assistant_text(hook.get("transcript_path", ""))), 600)

    now = int(time.time())
    relays = relays_list(cfg)
    jwt = make_jwt(cfg)
    env = cfg.get("environment", "sandbox")

    payload = {
        "aps": {
            "alert": {
                "title": f"🔐 {project} · 请求授权",
                "subtitle": host,
                "body": (f"{summary}"
                         + (f"\n\n⤷ 正在进行：{context}" if context else "")
                         + "\n（锁屏卡片可直接批准，或长按这条通知）"),
            },
            "sound": "default",
            "thread-id": session_id,
            "interruption-level": "time-sensitive",
            "category": "SB_DECIDE",
        },
        "sb": {
            "event": "permission",
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
    state["local"][session_id] = {"project": project, "status": "waiting", "since": now}
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
            resp = backend_call(cfg, "GET", f"/api/decision?id={request_id}")
            if resp and resp.get("decision") in ("allow", "deny"):
                decision = resp["decision"]
                break
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
        os.unlink(PENDING_APPROVAL_PATH)
    except OSError:
        pass
    if decision in ("allow", "deny"):
        state = load_sessions()
        state["local"][session_id] = {"project": project, "status": "running", "since": now}
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
    while time.time() < deadline:
        if watch_return and not os.environ.get("SESSIONBELL_FORCE"):
            idle = mac_idle_seconds()
            if idle is not None and idle < 5:
                log("stop wait: user is back at the Mac, releasing")
                return False
        # Re-read every round: the pane watcher may have claimed the command.
        cursor = (load_sessions()["local"].get(session_id) or {}).get("cmd_ts", 0)
        resp = backend_call(cfg, "GET", f"/api/command?id={session_id}")
        cmd = (resp or {}).get("command")
        fresh = cmd and cmd.get("ts", 0) > max(cursor, (time.time() - 4 * 3600) * 1000)
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
        time.sleep(2)
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
    """Register SessionBell in ~/.codex/hooks.json — Codex CLI, desktop app
    and the VS Code extension all fire these. Merges with existing entries
    (e.g. other tools' hooks); safe to re-run."""
    me = os.path.abspath(__file__)
    py = sys.executable or "/usr/bin/python3"

    def group(kind, timeout):
        return {"hooks": [{"type": "command", "timeout": timeout,
                           "command": f"SESSIONBELL_ENGINE=codex {py} {me} {kind}"}]}

    # PermissionRequest rides the notification kind: waiting + phone push,
    # NO stdout — codex falls through to its own approval UI. Blocking
    # phone approval waits until the decision schema is verified live.
    wanted = {"UserPromptSubmit": group("prompt", 10),
              "Stop": group("stop", 15),
              "SessionEnd": group("session-end", 10),
              "PermissionRequest": group("notification", 10)}
    path = os.path.expanduser("~/.codex/hooks.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    hooks = data.setdefault("hooks", {})
    for ev, g in wanted.items():
        arr = hooks.setdefault(ev, [])
        arr[:] = [x for x in arr if "sessionbell" not in json.dumps(x).lower()]
        arr.append(g)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✓ 已注册 SessionBell → {path}")
    print("  事件: UserPromptSubmit / Stop / SessionEnd / PermissionRequest(仅提醒)")
    print("  Codex session 会带 CODEX 角标出现在 App;批准联动待实测后开启。")


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
    cfg = load_config(kind)
    _GATEWAY_CFG.update(cfg)

    if kind == "relay":
        run_relay(cfg)
        return

    hook = {}
    if kind != "test" and not sys.stdin.isatty():
        try:
            hook = json.load(sys.stdin)
        except ValueError:
            hook = {}

    if kind == "permission":
        handle_permission(cfg, hook)
        return

    cwd = hook.get("cwd") or hook.get("workspace") or os.getcwd()
    project = os.path.basename(cwd.rstrip("/")) or cwd
    # Codex hooks share the schema but field names are unverified per
    # version — accept the likely aliases.
    session_id = (hook.get("session_id") or hook.get("thread_id")
                  or hook.get("conversation_id") or "unknown")
    engine = os.environ.get("SESSIONBELL_ENGINE")
    host = host_label(cfg)
    env = cfg.get("environment", "sandbox")
    now = int(time.time())

    def excerpt(text, limit=80):
        text = " ".join((text or "").split())
        return text[: limit - 1] + "…" if len(text) > limit else text

    # Dashboard bookkeeping runs for every event, idle or not — it's a status
    # board, not a ring. Alert pushes below stay idle-gated.
    dashboard_kinds = ("prompt", "session-end", "notification", "stop",
                       "subagent-start", "subagent-stop")
    if kind in dashboard_kinds:
        state = load_sessions()
        prev = state["local"].get(session_id) or {}
        # Last-prompt registry: outlives the session record (like `terms`),
        # so stop/notification after a prune/resurrect still name the task.
        last_prompt = (state.get("prompts", {}).get(session_id) or {}).get("text", "")
        if kind == "prompt":
            own_pid, parent_pid = claude_pids()
            ptext = excerpt(hook.get("prompt"))
            if ptext:
                state.setdefault("prompts", {})[session_id] = {
                    "text": ptext, "ts": int(now)}
            # Typing locally supersedes anything queued from the phone.
            state["local"][session_id] = {
                "project": project, "status": "running", "since": now,
                # Slash commands / spawned first beats carry no prompt text —
                # never blank out a task that already has a name.
                "detail": ptext or prev.get("detail") or last_prompt, "agents": 0,
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
        elif kind == "session-end":
            state["local"].pop(session_id, None)
        elif kind == "notification":
            # Keep the prompt excerpt — it names the task; the generic
            # "waiting for your input" message does not.
            state["local"][session_id] = {
                "project": project, "status": "waiting", "since": now,
                "detail": prev.get("detail") or last_prompt or excerpt(
                    hook.get("message") or hook.get("tool_name")),
                "agents": prev.get("agents", 0),
                "cmd_ts": prev.get("cmd_ts", 0),
                "pid": prev.get("pid") or find_claude_pid(),
                "parent_pid": prev.get("parent_pid") or claude_pids()[1],
                "pane": os.environ.get("OTTY_PANE_ID") or prev.get("pane"),
                "mode": hook.get("permission_mode") or prev.get("mode"),
                "effort": hook.get("effort") or prev.get("effort"),
                "engine": engine or prev.get("engine"),
            }
        elif kind == "stop":
            state["local"][session_id] = {
                "project": project, "status": "done", "since": now,
                "detail": prev.get("detail") or last_prompt, "agents": 0,
                "cmd_ts": prev.get("cmd_ts", 0),
                "pid": prev.get("pid") or find_claude_pid(),
                "parent_pid": prev.get("parent_pid") or claude_pids()[1],
                "pane": os.environ.get("OTTY_PANE_ID") or prev.get("pane"),
                "mode": hook.get("permission_mode") or prev.get("mode"),
                "effort": hook.get("effort") or prev.get("effort"),
                "engine": engine or prev.get("engine"),
            }
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
        if kind in ("subagent-start", "subagent-stop"):
            return
        sync_peers(cfg, state, host)
        push_dashboard(cfg, make_jwt(cfg), HOSTS[env], state, host)
        if kind in ("prompt", "session-end"):
            return

    # Only ring the phone when the user actually stepped away from the Mac.
    # Phone-spawned sessions are exempt: their owner IS the phone.
    if (kind != "test" and not os.environ.get("SESSIONBELL_FORCE")
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
            if kind == "stop" and use_backend(cfg):
                try_inject_command(cfg, env, session_id, project, host,
                                   10, watch_return=False)
            return

    task_detail = (load_sessions()["local"].get(session_id) or {}).get("detail", "")

    raw_md = ""
    if kind == "stop":
        title = f"✅ {project} · 任务完成"
        raw_md = last_assistant_text(hook.get("transcript_path", ""))
        body = strip_markdown(raw_md) or "Claude 已完成本轮任务"
        if task_detail:
            title = f"✅ {project} · 完成「{task_detail[:24]}」"
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
        title = f"🖐 {project} · 需要你"
        # What is Claude actually asking? The last assistant message says.
        raw_md = last_assistant_text(hook.get("transcript_path", ""))
        if raw_md:
            body = strip_markdown(raw_md)
        else:
            body = hook.get("message") or "Claude 在等待你的输入或授权"
            if task_detail:
                body = f"「{task_detail}」{body}"
    else:
        title = "🔔 SessionBell 测试"
        body = "推送链路打通了！"
        session_id = "test"

    # Full detail readable on the phone: long-press the banner, or open the
    # event in the app (which renders sb.md as real markdown). Budgets keep
    # the whole payload inside APNs' 4KB cap.
    body = clip_bytes(body, 1200)
    raw_md = clip_bytes(raw_md, 2000) if raw_md else ""

    payload = {
        "aps": {
            "alert": {"title": title, "subtitle": host, "body": body},
            "sound": "default",
            "thread-id": session_id,
            "interruption-level": "time-sensitive",
            # 长按可直接打字回复下一步指令(stop 时注入,见下)
            "category": "SB_REPLY",
        },
        "sb": {
            "event": kind,
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
    if kind == "stop" and ok and use_backend(cfg):
        try_inject_command(cfg, env, session_id, project, host,
                           cfg.get("reply_wait_seconds", 900), watch_return=True)


if __name__ == "__main__":
    main()
