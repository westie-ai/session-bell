# r/ClaudeAI post — SessionBell 1.4

Flair: **Built with Claude**. Text post; App Store link once after "What I built", GitHub at the bottom (not a link post).
Rule 7 checklist: built by me with Claude Code ✓ · what it is + how Claude helped in detail + what it does ✓ · free to try, stated ✓.

## Title candidates (pick one)

1. The best part of running ten Claude Code sessions is leaving the computer. I built an iPhone app so my Lock Screen tells me as each one finishes.
2. I run ~10 Claude Code sessions across two Macs, all in auto mode. I built an iPhone app so my Lock Screen tells me which one just finished and what it wants next.
3. SessionBell: your Claude Code sessions on your iPhone Lock Screen — running / done / waiting for your next instruction, send the next step from your phone. Free, open source.

(1 or 2 read as a story and fit the sub better; 3 is the "product name first" version the rules discourage.)

## Body (English — post this)

**The problem.** I usually have ten or so Claude Code sessions going at once — a handful of projects on my MacBook, the rest on a Mac Studio in the studio. Everything runs in auto mode, so Claude almost never stops to ask for permission. It stops when the task is done and waits for the next instruction — and nothing tells me. I'd go get coffee, come back, and find three sessions had finished twenty minutes ago and were just sitting there. Ten sessions, two machines, and a good part of my day was "go check the terminals".

**What I built.** SessionBell is a small iPhone app plus a hook for Claude Code. On the Lock Screen (and in the Dynamic Island) you get one Live Activity card that lists every session on every Mac you own: the prompt you gave it, whether it's running / done / waiting for your next instruction, and how long it's been sitting. The moment a session finishes, your phone rings — but only if you're actually away from the Mac (the hook checks idle time, so it stays quiet while you're at the keyboard). Then you type the next instruction on the phone and it lands in that session. If you're not on auto mode, permission requests show up as a card with Allow / Deny buttons too.

Honestly the best part is the feeling. I go for a walk, and the phone lights up one by one — reply done, garden-log done, backend done — and I send each one its next step without sitting down. It's not a terminal on your phone. It's the "which of my ten sessions just finished, and what does it need" layer.

Free on the App Store: apps.apple.com/app/id6801045681 (needs a Mac running Claude Code; pairing is one line in Terminal).

**How it's different from Claude's Remote Control.** People will ask, so: Remote Control (the official feature) lets you continue one session from the Claude app or browser — you open the session and keep chatting with it. It's great for that. SessionBell is the layer before you open anything: it watches all your sessions on all your machines, tells you which one finished, and lets you fire off the next instruction (or an approval) in one tap from the Lock Screen without opening a session at all. I use both: SessionBell to know when, Remote Control (or just walking back) when the next step needs a real conversation. SessionBell also doesn't depend on your plan tier — it's a hook plus push notifications.

**How Claude helped build it.** Almost all of it was built in Claude Code, and the app was its own test subject — every time a build finished while I was away from the desk, that was a notification on my phone. Concretely:
- The Mac side is a single Python hook script wired into Claude Code's hook events (UserPromptSubmit, Stop, Notification, PermissionRequest, SubagentStop). Claude wrote the first version of the state machine that turns those events into "waiting / running / done" per session, and the idle-time check that suppresses pushes while you're at the keyboard.
- The iOS side is SwiftUI + ActivityKit. I hadn't shipped a Live Activity before; Claude walked me through push-to-start tokens, the 4 KB APNs payload cap (which forced the "one card for all sessions" design), and the interactive buttons via App Intents.
- The backend is a Cloudflare Worker with D1. Claude wrote it, and later found and fixed a query pattern that was burning through D1's free tier (a `LIKE` that couldn't use the index).
- Yesterday I had Claude redesign the whole onboarding after watching real users drop off: it's now "paste one line into Terminal on your Mac" instead of "download a pkg, copy a 150-character code through Universal Clipboard".

**What it costs.** Free. The app is on the App Store, the Mac hook and the backend are MIT on GitHub, and you can self-host the backend if you don't want your session titles going through mine. There's a demo mode in the app if you want to see the Lock Screen card before pairing a Mac.

- GitHub: https://github.com/westie-ai/session-bell
- Needs a Mac running Claude Code (Windows is in beta). Pairing is one line in Terminal.

Happy to answer anything about the hook events or Live Activities — that part took the most trial and error.

## 中文对照（自己看，不发）

**问题。** 我通常同时开十来个 Claude Code 会话，几个在 MacBook 上，其余在工作室的 Mac Studio 上。全部 auto mode，Claude 几乎不会停下来要权限，它停下来是因为任务做完了、等下一步指令，而没有任何东西通知我。去倒杯咖啡回来，三个会话二十分钟前就跑完了在那干等。十个会话两台机器，大半天花在"去看一眼终端"上。

**做了什么。** SessionBell 是一个 iPhone App 加一个 Claude Code hook。锁屏和灵动岛上一张卡片列出你所有 Mac 上的所有会话：你给它的那句话、运行中 / 已完成 / 等你下一步、放了多久。会话一跑完手机就响，但只在你不在 Mac 前时响。然后在手机上敲下一步指令，直接进那个会话。非 auto mode 的话权限请求也会变成带允许 / 拒绝按钮的卡片。

最爽的是那个感觉：出去散步，手机一个接一个亮起来，reply 完成、garden-log 完成、backend 完成，每个都不用坐下就把下一步派出去。它不是手机上的终端，它是"我十个会话里哪个刚跑完、要什么"这一层。

App Store 免费：apps.apple.com/app/id6801045681（需要一台跑 Claude Code 的 Mac，配对是终端里一行命令）。

**和 Remote Control 的区别。** Remote Control 让你从 Claude App 或浏览器**接着聊某一个会话**，这件事它做得很好。SessionBell 是你打开任何东西之前的那一层：盯着**所有机器上的所有会话**，告诉你哪个跑完了，锁屏一键把下一步指令（或者批准）发过去，不用打开会话。两个都用：SessionBell 负责"什么时候"，下一步需要真正对话时再用 Remote Control 或者走回去。SessionBell 也不依赖订阅档位。

**Claude 怎么帮的。** 几乎全部在 Claude Code 里做的，而且这个 App 是它自己的测试对象。具体：hook 状态机和空闲判断是 Claude 写的第一版；Live Activity 的 push-to-start、4KB 载荷上限（逼出了"一张卡放所有会话"的设计）、App Intents 的按钮是 Claude 带着做的；Cloudflare Worker + D1 后端是 Claude 写的，后来还找到并修了一个烧免费额度的 LIKE 查询；昨天看到真实用户流失后让 Claude 重做了整个引导。

**价格。** 免费。App 在 App Store，Mac hook 和后端 MIT 开源，可以自托管。App 里有演示模式，不配 Mac 也能先看锁屏卡片。

## Posting notes

- Post as a **text** post with the Built with Claude flair; App Store link once in the body, GitHub at the bottom, none in the title.
- Best window: US morning (北京时间 21:00–24:00), weekday.
- Reply to every comment in the first two hours; that's what keeps it on the front page.
- If AutoMod holds it (29 karma is on the low side), don't repost — message the mods once with "Built with Claude post held, rule 7 checklist met" and wait.
- Same body works for r/ClaudeCode and r/SideProject the next day; Show HN wants a shorter, flatter version (no "how Claude helped" section, lead with the problem).
