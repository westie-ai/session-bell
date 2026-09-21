#!/bin/bash
# SessionBell CLI — https://sessionbell.westie.ai
case "$1" in
  codex|codex-setup|codex-usage|codex-enable)
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    HOOK="$SCRIPT_DIR/sessionbell_hook.py"
    [ -f "$HOOK" ] || HOOK="$HOME/.sessionbell/sessionbell_hook.py"
    [ -f "$HOOK" ] || { echo "请先运行 sessionbell pair <配对码>"; exit 1; }
    exec python3 "$HOOK" "$@"
    ;;
  pair)
    CODE="$2"
    if [ -z "$CODE" ]; then
      # 无参数:读剪贴板 — iPhone 上拷贝的配对命令经通用剪贴板直达这里。
      # 取最后一个空白分隔的 token,整行命令和裸配对码都兼容。
      CODE=$(pbpaste 2>/dev/null | tr -s '[:space:]' ' ' | awk '{print $NF}')
      [ -n "$CODE" ] && echo "📋 使用剪贴板里的配对码"
    fi
    [ -n "$CODE" ] || { echo "用法: sessionbell pair <配对码>"; echo "提示: 在 iPhone 上拷贝配对命令后,直接运行 sessionbell pair 也可以"; exit 1; }
    URL=$(python3 -c "import base64,json,sys;print(json.loads(base64.b64decode(sys.argv[1]))['u'])" "$CODE" 2>/dev/null) \
      || { echo "❌ 配对码不合法(剪贴板里不是配对码?试试 sessionbell pair <配对码>)"; exit 1; }
    echo "🔔 正在接入 $URL …"
    curl -fsSL "$URL/install.sh" | bash -s -- "$CODE"
    ;;
  status)
    tail -5 "$HOME/.sessionbell/sessionbell.log" 2>/dev/null || echo "尚未接入(先跑 sessionbell pair <配对码>)"
    ;;
  code)
    # 再加一台手机/iPad:打印 6 位数字并打开二维码页
    python3 "$HOME/.sessionbell/sessionbell_hook.py" pair-code
    ;;
  *)
    echo "SessionBell — 本地 AI 编程助手的移动指挥台"
    echo "用法:"
    echo "  sessionbell pair [配对码]   接入(不带参数时自动读剪贴板)"
    echo "  sessionbell status          查看守护日志"
    echo "  sessionbell codex-setup     接入 Codex hooks(保留已有配置)"
    echo "  sessionbell codex-usage     查看 Codex 官方账号额度"
    echo "  sessionbell codex-enable    开启本机共享服务和桌面连接"
    echo "  sessionbell codex [参数]    在共享服务里使用 Codex CLI"
    echo "  sessionbell code            给另一台手机/iPad 出 6 位加入码"
    ;;
esac
