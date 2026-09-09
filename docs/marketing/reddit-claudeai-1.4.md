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

**What I built.** SessionBell is a small iPhone app plus a hook for Claude Code. On the Lock Screen (and in the Dynamic Island) you get one Live Activity card that lists every session on every Mac you own: the prompt you gave it, whether it's running / done / waiting for your next instruction, and how long it's been sitting. The moment a session finishes, your phone buzzes. Then you type the next instruction on the phone and it lands in that session. If you're not on auto mode, permission requests show up as a card with Allow / Deny buttons too.

Honestly the best part is the feeling. I go for a walk, and the phone lights up one by one — one project done, then another, then a third — and I send each one its next step without sitting down. It's not a terminal on your phone. It's the "which of my ten sessions just finished, and what does it need" layer.

Free on the App Store: apps.apple.com/app/id6801045681 (needs a Mac running Claude Code; pairing is one line in Terminal).

**How it's different from Claude's Remote Control.** People will ask, so: Remote Control (the official feature) lets you continue one session from the Claude app or browser — you open the session and keep chatting with it. It's great for that. SessionBell is the layer before you open anything: it watches all your sessions on all your machines, tells you which one finished, and lets you fire off the next instruction (or an approval) in one tap from the Lock Screen without opening a session at all. I use both: SessionBell to know when, Remote Control (or just walking back) when the next step needs a real conversation. SessionBell also doesn't depend on your plan tier — it's a hook plus push notifications.

**How Claude helped build it.** Honestly, it's most of the story: I'm not an iOS developer. Almost all of this was written in Claude Code, and the app was its own test rig — every build that finished while I was on the sofa showed up on my phone, which is how most of the bugs got found. The Mac side is a single Python hook on Claude Code's own events (Stop, Notification, PermissionRequest…), and Claude wrote the state machine that turns those into "running / done / waiting" per session. I'd never shipped a Live Activity; Claude got me through push-to-start tokens and the 4 KB APNs payload limit, which is the reason it's one card for all your sessions instead of one per session. The backend is a Cloudflare Worker with D1 that Claude wrote and later debugged when a `LIKE` query was quietly eating the free tier. And last week, after I showed it where real users were dropping off, it redesigned the whole onboarding: pairing is now one line pasted into Terminal instead of a pkg download and a 150-character code.
