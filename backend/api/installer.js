// Personalized double-clickable installer:
//   https://<instance>/api/installer?code=<配对码>
// Downloads "SessionBell安装器.command" — the user double-clicks it in Finder
// (first run: right-click → Open, per Gatekeeper) and never types a command.
export default function handler(req, res) {
  const code = req.query.code || '';
  if (!/^[A-Za-z0-9+/=_-]{16,512}$/.test(code)) {
    return res.status(400).send('bad code');
  }
  const origin = `https://${req.headers.host}`;
  const script = `#!/bin/bash
# SessionBell Mac 安装器(双击运行)
clear
echo "🔔 SessionBell 接入中……"
curl -fsSL "${origin}/install.sh" | bash -s -- "${code}" \\
  && echo "" && echo "✅ 完成!可以关闭这个窗口了。" \\
  || { echo ""; echo "❌ 出错了,把上面的输出截图发给管理员。"; }
read -n 1 -s -r -p "按任意键关闭…"
`;
  res.setHeader('Content-Type', 'application/x-shellscript; charset=utf-8');
  res.setHeader('Content-Disposition',
    "attachment; filename*=UTF-8''SessionBell%E5%AE%89%E8%A3%85%E5%99%A8.command");
  res.send(script);
}
