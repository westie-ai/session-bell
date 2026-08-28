"""SessionBell for Windows — thin launcher around the shared hook script.

Built into sessionbell.exe by PyInstaller (see .github/workflows/windows.yml).
The exe embeds a Python runtime but NO business logic: `pair` downloads
~/.sessionbell/sessionbell_hook.py from the backend (the same file every Mac
runs), and every other invocation just executes that script. The watcher's
self-update therefore keeps Windows machines on the newest hook automatically
— shipping a new exe is only ever needed for launcher changes.

Usage:
  sessionbell pair [配对码]   接入(不带参数时自动读剪贴板)
  sessionbell status          查看守护日志
  sessionbell <anything>      透传给 hook(prompt/stop/relay/test/...)
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

CONFIG_DIR = os.path.expanduser("~/.sessionbell")
HOOK = os.path.join(CONFIG_DIR, "sessionbell_hook.py")
LOG = os.path.join(CONFIG_DIR, "sessionbell.log")

# Windows consoles default to cp1252/GBK — printing the 中文/emoji UI
# crashes with UnicodeEncodeError before anything happens.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def exe_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(__file__)


def clipboard_text() -> str:
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=10)
        return p.stdout or ""
    except Exception:
        return ""


def die(msg: str) -> None:
    print(msg)
    sys.exit(1)


def install_hooks(exe: str) -> None:
    """Wire this exe into ~/.claude/settings.json — same event set as the
    Mac install.sh; keep both lists in sync when events change."""
    path = os.path.expanduser("~/.claude/settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, ValueError):
        settings = {}
    hooks = settings.setdefault("hooks", {})
    want = {
        "UserPromptSubmit": ("prompt", 30, True, None),
        "Notification": ("notification", 30, True, None),
        "Stop": ("stop", 960, False, None),
        "SessionEnd": ("session-end", 30, True, None),
        "PermissionRequest": ("permission", 120, False, None),
        "PreToolUse": ("subagent-start", 30, True, "Task|Agent"),
        "SubagentStop": ("subagent-stop", 30, True, None),
    }
    for event, (mode, timeout, is_async, matcher) in want.items():
        entries = hooks.setdefault(event, [])
        entries[:] = [e for e in entries
                      if not any("sessionbell" in h.get("command", "").lower()
                                 for h in e.get("hooks", []))]
        h = {"type": "command", "command": f'"{exe}" {mode}', "timeout": timeout}
        if is_async:
            h["async"] = True
        entry = {"hooks": [h]}
        if matcher:
            entry["matcher"] = matcher
        entries.append(entry)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
    print("✓ hooks 已接入 Claude Code")


def install_autostart(exe: str) -> None:
    """Relay/watcher at logon. A .vbs shim hides the console window —
    Task Scheduler has no way to start a console app invisibly."""
    appdir = os.path.join(os.environ.get("LOCALAPPDATA", CONFIG_DIR), "SessionBell")
    os.makedirs(appdir, exist_ok=True)
    vbs = os.path.join(appdir, "relay-hidden.vbs")
    with open(vbs, "w", encoding="utf-8") as f:
        f.write('CreateObject("Wscript.Shell").Run """{}"" relay", 0, False\n'
                .format(exe))
    subprocess.run(
        ["schtasks", "/Create", "/F", "/TN", "SessionBell Relay",
         "/TR", f'wscript.exe "{vbs}"', "/SC", "ONLOGON"],
        capture_output=True)
    # Start it now as well — pairing shouldn't require a re-login.
    DETACHED = 0x00000008 | 0x00000200
    subprocess.Popen(["wscript.exe", vbs], creationflags=DETACHED)
    print("✓ 守护进程已注册开机自启")


def cmd_pair(code: str) -> None:
    if not code:
        # iPhone 上拷贝的配对命令经剪贴板直达这里;取最后一个 token,
        # 整行命令和裸配对码都兼容。
        toks = clipboard_text().split()
        code = toks[-1] if toks else ""
        if code:
            print("📋 使用剪贴板里的配对码")
    if not code:
        die("用法: sessionbell pair <配对码>\n"
            "提示: 在 iPhone 上拷贝配对命令后,直接运行 sessionbell pair 也可以")
    try:
        d = json.loads(base64.b64decode(code))
        url, secret = d["u"].rstrip("/"), d["s"]
    except Exception:
        die("❌ 配对码不合法(剪贴板里不是配对码?试试 sessionbell pair <配对码>)")

    print(f"🔔 正在接入 {url} …")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with urllib.request.urlopen(url + "/sessionbell_hook.py", timeout=30) as r:
        blob = r.read()
    if len(blob) < 10000:
        die("❌ 下载 hook 失败(返回内容异常)")
    with open(HOOK, "wb") as f:
        f.write(blob)

    cfg_path = os.path.join(CONFIG_DIR, "config.json")
    if not os.path.exists(cfg_path):
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({
                "bundle_id": "dev.yuesun.SessionBell",
                "environment": "production",
                "min_idle_seconds": 120,
                "backend_url": url,
                "backend_secret": secret,
            }, f, indent=2)

    exe = exe_path()
    install_hooks(exe)
    install_autostart(exe)
    if run_hook(["test"]) == 0:
        print("🎉 接入完成!确认手机收到了测试推送。")


def cmd_doctor() -> None:
    """One-shot self-diagnosis — designed to be screenshot in one message."""
    def row(ok, name, detail=""):
        mark = "✅" if ok else ("⚠️" if ok is None else "❌")
        print(f"{mark} {name}" + (f" — {detail}" if detail else ""))
        return bool(ok)

    print(f"SessionBell doctor @ {os.environ.get('COMPUTERNAME', '?')}\n")

    cfg = {}
    try:
        with open(os.path.join(CONFIG_DIR, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        row(True, "配置", cfg.get("backend_url", ""))
    except (OSError, ValueError):
        row(False, "配置", "没有 ~/.sessionbell/config.json — 先跑 sessionbell pair")

    row(os.path.exists(HOOK), "hook 脚本",
        HOOK if os.path.exists(HOOK) else "缺失 — 重跑 sessionbell pair")

    url, secret = cfg.get("backend_url", ""), cfg.get("backend_secret", "")
    if url and secret:
        try:
            req = urllib.request.Request(url.rstrip("/") + "/api/state",
                                         headers={"x-sb-secret": secret})
            with urllib.request.urlopen(req, timeout=10) as r:
                row(r.status == 200, "后端连通", f"HTTP {r.status}")
        except Exception as exc:
            row(False, "后端连通", str(exc)[:80])

    try:
        with open(os.path.expanduser("~/.claude/settings.json"),
                  encoding="utf-8") as f:
            s = json.load(f)
        wired = sum(1 for entries in s.get("hooks", {}).values()
                    for e in entries for h in e.get("hooks", [])
                    if "sessionbell" in h.get("command", "").lower())
        row(wired >= 7, "Claude Code hooks", f"{wired}/7 已挂")
    except (OSError, ValueError):
        row(False, "Claude Code hooks", "读不到 ~/.claude/settings.json")

    import shutil
    p = shutil.which("claude")
    row(bool(p), "claude 命令", p or "PATH 里找不到(装了 Claude Code 吗?)")

    try:
        with urllib.request.urlopen("http://127.0.0.1:48765/health", timeout=3) as r:
            row(r.status == 200, "守护进程(relay)", "运行中")
    except Exception:
        row(False, "守护进程(relay)",
            "没在跑 — 重新登录一次,或手动跑 sessionbell relay 看报错")

    q = subprocess.run(["schtasks", "/Query", "/TN", "SessionBell Relay"],
                       capture_output=True)
    row(q.returncode == 0, "开机自启", "已注册" if q.returncode == 0 else "未注册")

    print("\n—— 最近日志 ——")
    cmd_log(15)


def cmd_log(n: int) -> None:
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            print("".join(f.readlines()[-n:]), end="")
    except OSError:
        print("(还没有日志)")


def run_hook(args: list) -> int:
    if not os.path.exists(HOOK):
        # Hooks silently no-op until paired — mirrors the Mac behaviour.
        if args and args[0] in ("test", "usage"):
            die("尚未接入(先跑 sessionbell pair <配对码>)")
        return 0
    os.environ["SESSIONBELL_RUNNER"] = exe_path()
    sys.argv = [HOOK] + args
    import runpy
    try:
        runpy.run_path(HOOK, run_name="__main__")
    except SystemExit as e:
        return int(e.code or 0)
    return 0


def interactive() -> None:
    """Double-clicked from Explorer: no args, and the console dies with us —
    so guide, don't print usage, and always pause before exiting."""
    print("🔔 SessionBell for Windows\n")
    try:
        if os.path.exists(os.path.join(CONFIG_DIR, "config.json")):
            cmd_doctor()
        else:
            code = ""
            toks = clipboard_text().split()
            if toks:
                try:
                    json.loads(base64.b64decode(toks[-1]))
                    code = toks[-1]
                    print("📋 剪贴板里发现配对码")
                except Exception:
                    pass
            if not code:
                print("在 iPhone 的 SessionBell App 里:设置 → 重新打开接入引导 →")
                print("第三屏「连接 Mac」→ 拷贝配对命令,发到电脑再复制,或直接粘到下面。\n")
                toks = input("粘贴配对命令或配对码: ").split()
                code = toks[-1] if toks else ""
            cmd_pair(code)
    except SystemExit:
        pass
    except Exception as exc:
        print(f"❌ 出错了: {exc}")
    try:
        input("\n按回车退出…")
    except EOFError:
        pass


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "pair":
        cmd_pair(args[1] if len(args) > 1 else "")
    elif cmd == "doctor":
        cmd_doctor()
    elif cmd in ("log", "logs", "status"):
        try:
            n = int(args[1]) if len(args) > 1 else 50
        except ValueError:
            n = 50
        cmd_log(n)
    elif cmd == "help" or cmd == "--help":
        print("SessionBell — 本地 AI 编程助手的移动指挥台")
        print("用法:")
        print("  sessionbell             双击/裸跑 = 安装向导(已装则体检)")
        print("  sessionbell pair [配对码]   接入(不带参数时自动读剪贴板)")
        print("  sessionbell doctor          一键体检(报障时截图这个)")
        print("  sessionbell log [行数]      查看日志(默认最近 50 行)")
    elif cmd:
        sys.exit(run_hook(args))
    else:
        interactive()


if __name__ == "__main__":
    main()
