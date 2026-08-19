#!/bin/bash
# SessionBell CLI — https://sessionbell.westie.ai
case "$1" in
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
  *)
    echo "SessionBell — 本地 AI 编程助手的移动指挥台"
    echo "用法:"
    echo "  sessionbell pair [配对码]   接入(不带参数时自动读剪贴板)"
    echo "  sessionbell status          查看守护日志"
    ;;
esac
