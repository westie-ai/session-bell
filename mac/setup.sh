#!/bin/bash
# SessionBell 一键接入/升级脚本(幂等,可重复跑)
# 用法: bash <repo>/mac/setup.sh
set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$REPO/mac/sessionbell_hook.py"
PLIST=~/Library/LaunchAgents/dev.piper.sessionbell.relay.plist
OTTY_CFG=~/.config/otty/config.toml
OTTY_CLI="/Applications/Otty.app/Contents/MacOS/otty-cli"

echo "== SessionBell setup ($REPO) =="

# 0. 前置检查
[ -f ~/.sessionbell/config.json ] || {
  echo "❌ 缺 ~/.sessionbell/config.json — 从另一台 Mac 拷贝 config.json 和 .p8 过来再跑"; exit 1; }
python3 -m py_compile "$HOOK" && echo "✓ hook 语法 OK"

# 1. 拉最新代码
git -C "$REPO" pull --ff-only && echo "✓ 代码已最新"

# 2. hooks 挂进 ~/.claude/settings.json(已存在则更新路径/参数)
python3 - "$HOOK" <<'EOF'
import json, os, sys
hook = sys.argv[1]
p = os.path.expanduser("~/.claude/settings.json")
s = json.load(open(p)) if os.path.exists(p) else {}
hooks = s.setdefault("hooks", {})
want = {
    "UserPromptSubmit": (f"{hook} prompt", 30, True, None),
    "Notification": (f"{hook} notification", 30, True, None),
    "Stop": (f"{hook} stop", 960, False, None),
    "SessionEnd": (f"{hook} session-end", 30, True, None),
    "PermissionRequest": (f"{hook} permission", 120, False, None),
    "PreToolUse": (f"{hook} subagent-start", 30, True, "Task|Agent"),
    "SubagentStop": (f"{hook} subagent-stop", 30, True, None),
}
for event, (cmd, timeout, is_async, matcher) in want.items():
    entries = hooks.setdefault(event, [])
    entries[:] = [e for e in entries
                  if not any("sessionbell" in h.get("command", "")
                             for h in e.get("hooks", []))]
    h = {"type": "command", "command": cmd, "timeout": timeout}
    if is_async:
        h["async"] = True
    entry = {"hooks": [h]}
    if matcher:
        entry["matcher"] = matcher
    entries.append(entry)
json.dump(s, open(p, "w"), indent=2, ensure_ascii=False)
print("✓ 7 个 hooks 已对齐", p)
EOF

# 3. relay + watcher 常驻(launchd)
mkdir -p ~/Library/LaunchAgents
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>dev.piper.sessionbell.relay</string>
    <key>ProgramArguments</key>
    <array><string>/usr/bin/python3</string><string>$HOOK</string><string>relay</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$HOME/.sessionbell/relay.log</string>
    <key>StandardErrorPath</key><string>$HOME/.sessionbell/relay.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/dev.piper.sessionbell.relay" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "✓ relay+watcher 已启动"

# 4. Otty 即时注入开关
if [ -f "$OTTY_CFG" ] && ! grep -q "ipc-allow-send-keys" "$OTTY_CFG"; then
  printf '\n# SessionBell 远程控制:允许注入文本\nipc-allow-send-keys = true\n' >> "$OTTY_CFG"
  "$OTTY_CLI" config reload 2>/dev/null && echo "✓ Otty 注入已开启" \
    || echo "⚠ Otty 配置已写,请手动 reload(otty-cli config reload)"
else
  echo "✓ Otty 配置无需改动(或非 Otty 终端,即时注入不可用,Stop 窗口兜底仍有效)"
fi

# 5. 验证
python3 "$HOOK" test && echo "== 完成!手机应已收到测试推送 =="
