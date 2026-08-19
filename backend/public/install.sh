#!/bin/bash
# SessionBell Mac 一键接入:
#   curl -fsSL https://<你的后端>/install.sh | bash -s -- <配对码>
# 无需 Apple 账号、无需 git、无需 .p8——推送由后端网关代签。
set -e

CODE="$1"
[ -n "$CODE" ] || { echo "用法: curl -fsSL .../install.sh | bash -s -- <配对码>"; exit 1; }

DIR="$HOME/.sessionbell"
mkdir -p "$DIR"

# 解码配对码 → 后端地址 + 密钥
eval "$(python3 - "$CODE" <<'EOF'
import base64, json, sys
d = json.loads(base64.b64decode(sys.argv[1]))
print(f'BACKEND_URL="{d["u"]}"'); print(f'BACKEND_SECRET="{d["s"]}"')
EOF
)"

# 拉 hook 脚本(与后端同源分发)
curl -fsSL "$BACKEND_URL/sessionbell_hook.py" -o "$DIR/sessionbell_hook.py"
chmod +x "$DIR/sessionbell_hook.py"

# 写配置(网关模式:无 p8、无 device_tokens,全部走后端)
[ -f "$DIR/config.json" ] || cat > "$DIR/config.json" <<EOF
{
  "bundle_id": "dev.yuesun.SessionBell",
  "environment": "production",
  "min_idle_seconds": 120,
  "backend_url": "$BACKEND_URL",
  "backend_secret": "$BACKEND_SECRET"
}
EOF

# 挂 hooks
HOOK="$DIR/sessionbell_hook.py"
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
print("✓ hooks 已接入 Claude Code")
EOF

# relay + watcher 常驻
PLIST="$HOME/Library/LaunchAgents/dev.piper.sessionbell.relay.plist"
mkdir -p "$HOME/Library/LaunchAgents"
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
    <key>StandardOutPath</key><string>$DIR/relay.log</string>
    <key>StandardErrorPath</key><string>$DIR/relay.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/dev.piper.sessionbell.relay" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# Otty 即时注入(可选,非 Otty 终端自动跳过)
OTTY_CFG="$HOME/.config/otty/config.toml"
if [ -f "$OTTY_CFG" ] && ! grep -q "ipc-allow-send-keys" "$OTTY_CFG"; then
  printf '\n# SessionBell 远程控制\nipc-allow-send-keys = true\n' >> "$OTTY_CFG"
  "/Applications/Otty.app/Contents/MacOS/otty-cli" config reload 2>/dev/null || true
fi

python3 "$HOOK" test && echo "🎉 接入完成!确认手机收到了测试推送。"
