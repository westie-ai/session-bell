# SessionBell 🔔

**把 Agent 装进锁屏。**

本地 AI 编程 agent(Claude Code,Codex 支持中)的手机监控与遥控:
状态推送到 iPhone,锁屏 Live Activity 面板聚合所有 Mac 的任务,
授权请求在锁屏卡片上一键批准,还能从手机远程操作真实终端、查看官方用量。

[English → README.md](README.md)

```
┌─ Mac #1 ─┐
│ CC hooks ─┼──HTTPS──┐
└───────────┘         ▼
┌─ Mac #2 ─┐   Cloudflare Worker ──APNs──▶ iPhone
│ CC hooks ─┼──▶    + D1 (kv)             锁屏卡片 · 通知
└───────────┘         ▲                   批准/拒绝 · 远程指令
                      └──────HTTPS──────── iPhone App
```

Mac 上没有常驻服务进程:钩子是单文件零依赖 Python 脚本,随 Claude Code
生命周期事件触发;后端是约 350 行的 Cloudflare Worker + 一张 D1 表。

## 功能

- **推送通知** — 等待输入、任务完成、请求授权;空闲检测保证你在键盘前时不吵
- **锁屏面板** — Live Activity 卡片聚合所有配对 Mac 的等待/运行/完成任务,带走秒计时
- **手机批准** — `PermissionRequest` 钩子推送带按钮的卡片,锁屏上「允许/拒绝」
  直接回答 Mac 上的授权询问(超时回落终端)
- **远程控制** — 从手机往 session 的真实终端(iTerm / Terminal.app)注入文字、
  回读输出,已结束的 session 用 `claude -c` 复活
- **用量面板** — 官方口径的周限额/分模型用量,与 `/usage` 同源,无需手动校准
- **多引擎** — Codex 客户端 session 打独立徽标,`sessionbell codex-setup`
  写入 CC 兼容的 `~/.codex/hooks.json`

## 两种用法

### 托管模式(10 分钟,无需 Apple 账号)

有人替你跑好了后端并给你邀请码:

1. 打开 join 页,输邀请码 → 得到专属**配对码**
2. TestFlight 装 App → 后端配置里粘配对码
3. 下载已签名的 `SessionBell.pkg` → 运行 `sessionbell pair <配对码>`

数据隔离:命名空间 = `sha256(secret)`,租户间物理隔离,推送网关只允许
推本命名空间登记过的设备。不放心就自托管——这正是开源的意义。

### 自托管(约 1 小时,一次性)

前置:Apple 付费开发者账号、Xcode、iPhone(iOS 17.2+)、免费 Cloudflare 账号。

1. **Apple 侧** — developer.apple.com:新建 APNs Auth Key(下载 `.p8`,
   记 Key ID 和 Team ID);注册自己的 App ID,勾选 Push Notifications
2. **后端** — `backend-cf/`:
   ```bash
   cd backend-cf
   # 编辑 wrangler.jsonc:换成你的 account_id,删掉/替换 routes
   npx wrangler d1 create sessionbell            # id 填回 wrangler.jsonc
   npx wrangler d1 execute sessionbell --remote --file schema.sql
   npx wrangler secret put APNS_KEY < AuthKey_XXXX.p8
   npx wrangler secret put APNS_KEY_ID
   npx wrangler secret put APNS_TEAM_ID
   npx wrangler secret put INVITE_CODE           # 任意字符串,守 /api/signup
   npx wrangler deploy
   ```
3. **Mac**:
   ```bash
   mkdir -p ~/.sessionbell && cp mac/config.example.json ~/.sessionbell/config.json
   # 填 backend_url + backend_secret(生成:openssl rand -hex 24);
   # 想让 Mac 自签 APNs 直推的话再填 team_id / key_id / p8_path
   bash mac/setup.sh
   ```
4. **iOS App**:
   ```bash
   cd ios   # project.yml 里换成你的 bundle id 和 DEVELOPMENT_TEAM
   xcodegen generate   # Xcode 打开,⌘R 装真机,允许通知
   ```
   App 里展开「后端配置」,填 Worker URL 和密钥,点**测试连接**;
   然后 `python3 mac/sessionbell_hook.py test`,手机响铃即全通。

注意:Debug 自装签名 7 天过期;长期使用建议自己传 TestFlight。
多台 Mac:只需重复第 3 步。

## 目录结构

| 路径 | 说明 |
|---|---|
| `mac/sessionbell_hook.py` | 全部 Mac 侧逻辑,零 pip 依赖:生命周期钩子、session 状态、终端注册表、AppleScript 注入/回读、APNs、官方用量、Codex 接入 |
| `ios/` | SwiftUI App + Widget 扩展(xcodegen 工程):带实体按钮的锁屏卡、任务列表、终端页、用量条 |
| `backend-cf/` | Cloudflare Worker + D1:令牌注册簿、决定信箱、跨 Mac 状态同步、APNs 网关、注册/join 页 |
| `backend/` | **已废弃**的 Vercel 旧后端,仅存档 |

## 钩子配置

`mac/setup.sh` 会自动写入 `~/.claude/settings.json`;手动配置见英文版
README 的 Hooks reference 一节。要点:**PermissionRequest 和 Stop 不能
async**——前者靠 stdout 返回批准决定,后者靠 stdout 注入手机远程指令;
人在键盘前时秒过,不拖慢正常使用。

## 安全模型

- Mac 钩子只上报:项目名、机器名、session 状态、提示词/详情摘要,
  以及(仅当你主动用手机终端时)终端回读内容
- 后端鉴权是 `x-sb-secret` 头里的单一租户密钥;命名空间 =
  `sha256(secret)[:16]`,Worker 不存明文密钥
- APNs 密钥只存在于 Cloudflare secrets(托管)或 `~/.sessionbell`(自托管),
  仓库和 App 二进制里没有任何敏感信息
- 远程终端注入只作用于你自己钩子登记过的 session,按 tty 精确定位

## 已知坑

- Live Activity push-to-start 有系统频率预算,短时间反复测试会被静默丢弃
  (APNs 仍返 200);用 App 内「唤起面板」按钮手动拉起
- 通知栏 App 图标有系统缓存,换图标后需重启 iPhone
- 无线调试在 iPhone 休眠后会断,解锁亮屏即恢复;插线最稳

## 许可证

[MIT](LICENSE)
