#!/bin/bash
# SessionBell Mac 一键接入(三种叫法,结果一样):
#   curl -fsSL https://sessionbell.westie.ai/i | bash               # Mac 先来:自动开空间,弹出二维码给手机扫
#   curl -fsSL https://sessionbell.westie.ai/i | bash -s 483920     # 手机先来:App 里显示的 6 位数字
#   curl -fsSL https://<你的后端>/install.sh | bash -s -- <配对码>    # 老式 base64 配对码 / 自托管
# 无需 Apple 账号、无需 git、无需 .p8——推送由后端网关代签。
set -e

BASE="${SB_BACKEND:-https://sessionbell.westie.ai}"
ARG="$1"
SHORT=""

command -v python3 >/dev/null 2>&1 || {
  echo "需要 python3(macOS 自带,首次使用会弹窗安装命令行工具)。装好后再跑一遍这行命令。"; exit 1; }

json_field() { python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get(sys.argv[1]) or "")' "$1"; }

if [ -z "$ARG" ]; then
  # Mac 先来:开一个新空间,拿到配对码 + 给手机用的 6 位数字
  RESP=$(curl -fsS -m 15 -X POST "$BASE/api/signup" -H 'Content-Type: application/json' -d '{}') \
    || { echo "❌ 连不上 $BASE,检查网络后再试。"; exit 1; }
  CODE=$(echo "$RESP" | json_field pairing_code)
  SHORT=$(echo "$RESP" | json_field short_code)
  [ -n "$CODE" ] || { echo "❌ 服务端没有返回配对信息:$RESP"; exit 1; }
elif echo "$ARG" | grep -Eq '^[0-9]{6}$'; then
  # 手机先来:用 App 上显示的 6 位数字换回配对码
  RESP=$(curl -fsS -m 15 "$BASE/api/pair/$ARG" 2>/dev/null) \
    || { echo "❌ 这个数字已过期或用过了。回到手机 App,它会给你一个新的。"; exit 1; }
  CODE=$(echo "$RESP" | json_field pairing_code)
  [ -n "$CODE" ] || { echo "❌ 这个数字已过期或用过了。回到手机 App,它会给你一个新的。"; exit 1; }
else
  CODE="$ARG"
fi

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
os.makedirs(os.path.dirname(p), exist_ok=True)
s = json.load(open(p)) if os.path.exists(p) else {}
hooks = s.setdefault("hooks", {})
want = {
    "UserPromptSubmit": (f"{hook} prompt", 30, True, None),
    "Notification": (f"{hook} notification", 30, True, None),
    "Stop": (f"{hook} stop", 960, False, None),
    "SessionEnd": (f"{hook} session-end", 30, True, None),
    "PermissionRequest": (f"{hook} permission", 900, False, None),
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

if [ -n "$SHORT" ]; then
  PAGE="$BASE/p/$SHORT"
  echo ""
  echo "✅ Mac 这边好了。现在拿起 iPhone:"
  echo ""
  echo "   相机扫浏览器里弹出的二维码,或在 App 里输入  ${SHORT:0:3} ${SHORT:3}"
  echo "   (没装 App 就先去 App Store 搜 SessionBell;这个数字 15 分钟内有效)"
  echo ""
  echo "   二维码页面:$PAGE"
  command -v open >/dev/null 2>&1 && open "$PAGE" >/dev/null 2>&1 || true
else
  python3 "$HOOK" test && echo "🎉 接入完成!确认手机收到了测试推送。"
fi
