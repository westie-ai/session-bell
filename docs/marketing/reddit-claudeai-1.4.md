# r/ClaudeAI post — SessionBell 1.4

Flair: **Built with Claude**. Text post; App Store / GitHub links at the bottom of the body (not a link post).
Rule 7 checklist: built by me with Claude Code ✓ · what it is + how Claude helped in detail + what it does ✓ · free to try, stated ✓.

## Title candidates (pick one)

1. I run 4–5 Claude Code sessions across two Macs. I built an iPhone app so my Lock Screen tells me which one is waiting for me.
2. I kept walking away from Claude Code and coming back to find it had been waiting on me for 20 minutes. So I built a Lock Screen for it.
3. SessionBell: your Claude Code sessions on your iPhone Lock Screen — waiting / running / done, with approve-from-phone. Free, open source.

(1 or 2 read as a story and fit the sub better; 3 is the "product name first" version the rules discourage.)

## Body (English — post this)

**The problem.** I usually have several Claude Code sessions going at once — two or three projects on my MacBook, a couple more on a Mac Studio in the studio. Claude does its thing for anywhere from 30 seconds to 20 minutes, and the moment it stops to ask me something, nothing tells me. I'd get coffee, come back, and find one session had been sitting on a permission prompt the whole time while another had finished ten minutes ago. Multiply that by five sessions and two machines and half my day was "go check the terminals".

**What I built.** SessionBell is a small iPhone app plus a hook for Claude Code. On the Lock Screen (and in the Dynamic Island) you get one Live Activity card that lists every session on every Mac you own: the prompt you gave it, whether it's running / waiting for you / done, and how long it's been waiting. When a session stops and needs you, your phone rings — but only if you're actually away from the Mac (the hook checks idle time, so it stays quiet while you're at the keyboard). Permission requests show up as a card with Allow / Deny buttons, so `git push` doesn't sit there for an hour because you went to lunch. You can also type a quick reply from the phone and it lands in the session.

It's not a terminal on your phone. It's the "which of my sessions needs me right now" layer.

**How it's different from Claude's Remote Control.** People will ask, so: Remote Control (the official feature) lets you *continue one session* from the Claude app or browser — you open the session and keep chatting with it. It's great for that. SessionBell is the layer before you open anything: it watches *all* your sessions on *all* your machines and tells you which one stopped, why, and lets you approve or answer in one tap from the Lock Screen without opening a session at all. I use both: SessionBell to know when, Remote Control (or just walking back) to actually continue. SessionBell also doesn't depend on your plan tier — it's a hook plus push notifications.

**How Claude helped build it.** Almost all of it was built in Claude Code, and the app was its own test subject — every time Claude stopped to ask me something during development, that was a notification I could check on my phone. Concretely:
- The Mac side is a single Python hook script wired into Claude Code's hook events (UserPromptSubmit, Stop, Notification, PermissionRequest, SubagentStop). Claude wrote the first version of the state machine that turns those events into "waiting / running / done" per session, and the idle-time check that suppresses pushes while you're at the keyboard.
- The iOS side is SwiftUI + ActivityKit. I hadn't shipped a Live Activity before; Claude walked me through push-to-start tokens, the 4 KB APNs payload cap (which forced the "one card for all sessions" design), and the interactive Allow / Deny buttons via App Intents.
- The backend is a Cloudflare Worker with D1. Claude wrote it, and later found and fixed a query pattern that was burning through D1's free tier (a `LIKE` that couldn't use the index).
- Yesterday I had Claude redesign the whole onboarding after watching real users drop off: it's now "paste one line into Terminal on your Mac" instead of "download a pkg, copy a 150-character code through Universal Clipboard".

**What it costs.** Free. The app is on the App Store, the Mac hook and the backend are MIT on GitHub, and you can self-host the backend if you don't want your session titles going through mine. There's a demo mode in the app if you want to see the Lock Screen card before pairing a Mac.

- App Store: https://apps.apple.com/app/id6801045681
- GitHub: https://github.com/westie-ai/session-bell
- Needs a Mac running Claude Code (Windows is in beta). Pairing is one line in Terminal.

Happy to answer anything about the hook events or Live Activities — that part took the most trial and error.

## 中文对照（自己看，不发）

**问题。** 我通常同时开四五个 Claude Code 会话，两三个在 MacBook 上，另外几个在工作室的 Mac Studio 上。Claude 一跑就是 30 秒到 20 分钟，它停下来问我的时候没有任何东西通知我。去倒杯咖啡回来，发现一个会话卡在权限询问上等了半天，另一个十分钟前就跑完了。乘以五个会话两台机器，半天时间花在"去看一眼终端"上。

**做了什么。** SessionBell 是一个 iPhone App 加一个 Claude Code hook。锁屏和灵动岛上一张 Live Activity 卡片列出你所有 Mac 上的所有会话：你给它的那句话、运行中 / 等你 / 已完成、等了多久。会话停下来需要你时手机会响，但只在你不在 Mac 前时响（hook 看空闲时间，在键盘前就不打扰）。权限请求变成带"允许 / 拒绝"按钮的卡片，`git push` 不会因为你去吃饭卡一小时。也能从手机敲一句话回去。

它不是手机上的终端，它是"我哪个会话现在需要我"这一层。

**和 Remote Control 的区别。** Remote Control 是官方功能，让你从 Claude App 或浏览器**接着聊某一个会话**，打开会话继续对话，这件事它做得很好。SessionBell 是你打开任何东西之前的那一层：盯着**所有机器上的所有会话**，告诉你哪个停了、为什么，让你在锁屏上一键批准或回复，不用打开会话。我两个都用：SessionBell 负责"什么时候"，Remote Control 或者走回去负责"接着干"。SessionBell 也不依赖订阅档位，就是 hook 加推送。

**Claude 怎么帮的。** 几乎全部在 Claude Code 里做的，而且这个 App 是它自己的测试对象。具体：hook 状态机和空闲判断是 Claude 写的第一版；Live Activity 的 push-to-start、4KB 载荷上限（逼出了"一张卡放所有会话"的设计）、App Intents 的按钮是 Claude 带着做的；Cloudflare Worker + D1 后端是 Claude 写的，后来还找到并修了一个烧免费额度的 LIKE 查询；昨天看到真实用户流失后让 Claude 重做了整个引导。

**价格。** 免费。App 在 App Store，Mac hook 和后端 MIT 开源，可以自托管。App 里有演示模式，不配 Mac 也能先看锁屏卡片。

## Posting notes

- Post as a **text** post with the Built with Claude flair; put links at the bottom of the body, none in the title.
- Best window: US morning (北京时间 21:00–24:00), weekday.
- Reply to every comment in the first two hours; that's what keeps it on the front page.
- If AutoMod holds it (29 karma is on the low side), don't repost — message the mods once with "Built with Claude post held, rule 7 checklist met" and wait.
- Same body works for r/ClaudeCode and r/SideProject the next day; Show HN wants a shorter, flatter version (no "how Claude helped" section, lead with the problem).
