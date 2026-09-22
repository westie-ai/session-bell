# App Store & ASO Notes

_Last updated: 2026-09-21_

Working notes for SessionBell's App Store listing and App Store Optimization
(ASO). This is not build config — the source of truth for version/build numbers
is `ios/project.yml`.

## Current status (App Store)

- **1.2 — approved (Ready for Distribution).** What shipped:
  - **Windows desktop support** reflected in the copy (SessionBell now pairs
    with Mac **and** Windows).
  - **English (U.S.) localization added.** The listing was Chinese-only before,
    so English markets saw Chinese text. English now has its own description,
    keywords, subtitle and promotional text.
  - Sharper positioning — the description now leads with
    "Leave your computer freely."
- **Availability:** 148 countries/regions — all except the EU-27. The EU is
  excluded to avoid the DSA trader-status requirement; re-add later with trader
  info if we want the EU.
- **China mainland** is in the availability list but has **no ICP filing** yet,
  so the mainland store may not fully activate until an ICP number is added.

## 1.3 — submitted 2026-09-07 (build 7, Waiting for Review)

Shipped with it: open signup (no invite code), "Try the demo first" entry,
tenant secret in iCloud Keychain, the English title/subtitle/keywords below,
refreshed iPhone screenshots in both locales, review notes pointing reviewers
at the demo button. Build 7 (replacing 6 before review started) adds
localized push titles: the Mac hook sends APNs loc-keys, the app resolves
them from the String Catalog. Requires the hook in backend-cf/public to be
deployed (`npx wrangler deploy`) so Macs/Windows pick it up.

## 1.6 — submitted 2026-09-21 (build 11); approved 2026-09-22

1.5 was approved (Ready for Distribution) before this went in. Version created,
What's New, description and review notes set with `asc_release.py prepare`
(run with `SKIP_SHOTS=1`: the 1.5 screenshots are reused, the UI they show is
unchanged). Title and subtitle stay free of Mac / Claude / Codex in both
locales (5.2.5 + 4.1(a) rejection on 1.5). What changed:

- **Codex on macOS.** Codex desktop tasks show on the Tasks tab and the Lock
  Screen panel next to Claude Code tasks, with per-session token counts;
  shared CLI sessions can be approved, answered, followed up and started
  from the phone, with official quota. New-task screen has a Claude / Codex
  picker; task rows and Lock Screen cards show the agent icon.
- **Mac hook.** Phone-started sessions find a working `claude` binary (native
  installer or npm; a stale cached path is re-resolved). Phone commands are
  claimed on the backend so each is delivered exactly once; a failed
  terminal injection restores the command. Wake-from-sleep resync.
- Description: "(Claude Code and more)" → "(Claude Code, Codex and more)" and
  the stale "Currently invite-only." line removed, both locales.
- Backend must be redeployed with a normal `npx wrangler deploy` so
  `/api/codex` and `/api/command/restore` exist and Macs self-update to the
  Codex-aware hook (the 2026-09-20 pilot deploy kept the old public assets).

## 1.5 — submitted 2026-09-14 (build 10); rejected for the zh-Hans subtitle, resubmitted 2026-09-15; approved

Version id 46c5ea79-eb32-4da5-a193-8224ad79c639. Build 9 was pulled before review
started and replaced by build 10 (idle Macs stay visible on the Tasks tab). Store screenshots replaced in both
locales (iPhone 6.9" x5 incl. the Terminal view, iPad 13" x2) via
`ios/scripts/asc_release.py prepare` / `submit`; captured by
`$CLAUDE_JOB_DIR/tmp`-style scripts against a local worker, timed to the demo's
"waiting" phase (see memory). What changed:

- **Welcome screen leads with the outcome.** Title "Leave your computer freely";
  three scenario rows (rings when a task finishes or needs you / approve from
  the Lock Screen / send the next instruction from the phone). Primary button
  "Connect my Mac" with the "one line in Terminal, about 30 seconds" line as a
  caption underneath; the demo button is "See it in action first" (no negative
  phrasing). Same labels on the demo banner and the empty state.
- **Task detail = one page, two views.** Segmented control at the top:
  *Progress* (status + Claude's latest reply, plus a 3-line terminal teaser
  while the task is live) and *Terminal* (raw screen). One shared input bar
  under both; one frame loop shared by both views (2 s cadence on Terminal,
  8 s on Progress). The old 14-line preview card and the separate full-screen
  terminal page are gone; notification history moved to the toolbar.
- **Multi-device.** Welcome screen gains "Already set up on another device?
  Join that space" (6-digit code entry, no new space). Settings gains "Add
  another phone or iPad": mints a 15-minute code, shows it as digits + QR
  (`/p/<code>` universal link), and flips to "joined" when redeemed. A paired
  Mac can do the same with `sessionbell code` (hook `pair-code`). Same Apple
  ID still joins silently via iCloud Keychain. Welcome is capped at 560 pt
  wide on iPad.
- Onboarding restyled to the app palette (warm background, white cards,
  bell-yellow primary button, amber tint) — no more system blue.
- Screenshot test `testDetail` now also captures the Terminal view
  (`5-terminal`). The 1.2-era store screenshots still show the old detail page
  and welcome copy — re-capture before submitting.

Why: signups after open signup were ~5x, but 23 of 29 phones that registered
never paired a Mac (welcome screen explained mechanism, not value), and users
reported the detail page and terminal felt like duplicates.

## 1.4 — submitted 2026-09-08 20:06 (build 8, Waiting for Review)

### What changed (for the reviewer and for What's New)

- **New onboarding.** First screen asks one question — are you at your Mac? —
  and pairing is one line pasted into Terminal (`curl … | bash -s <6 digits>`).
  No pkg download, no base64 pairing code, no Universal Clipboard dependency.
  A Mac-first path exists too (run the line with no code, scan the QR), but
  the app and site steer people phone-first.
- **Visual redesign.** Warm neutral palette, prompt excerpt as the task title,
  one accent rule (yellow only on the "Allow" button and the active tab),
  status colors only on status glyphs/words, dark mode done as its own set.
- **Lock Screen card** shows the 5-hour / weekly usage and one line per task
  with the prompt excerpt; readable on light wallpapers (no more white tint).
- **Settings**: Add another Mac waits for the new Mac and confirms; Send
  Feedback (stored server-side, optional contact); Reset and Start Over.
- Fixes: duplicate delivery of phone commands, `<task-notification>` shown
  as a task name, notification prompt no longer covers the first screen.

### What's New text

**zh-Hans**
```
全新接入:打开 App,告诉它你在不在 Mac 前,然后在 Mac 终端里粘一行命令,配对完成。
不再需要下载安装包、不再复制长串配对码。
界面重做:任务卡直接显示你给 Claude 的那句话;锁屏卡片新增 5 小时 / 每周用量;深色模式单独调过。
设置页新增「再加一台 Mac」「发送反馈」「重置并重新开始」。
修复:手机指令偶尔重复投递、任务名显示为系统消息、首屏被通知权限弹窗遮住。
```

**en-US**
```
New setup: open the app, say whether you're at your Mac, paste one line into
Terminal on the Mac — paired. No installer download, no long pairing code.
Redesigned UI: task cards show the prompt you gave Claude; the Lock Screen card
now shows 5-hour and weekly usage; dark mode tuned separately.
Settings: Add another Mac, Send Feedback, Reset and Start Over.
Fixes: occasional duplicate delivery of phone commands, system messages shown
as task names, the notification prompt no longer covers the first screen.
```

### Metadata to change in ASC with this version

- zh-Hans subtitle: `AI 编程 agent 锁屏提醒与遥控`. The first attempt, `把 Mac 上的 Claude Code 装进锁屏`,
  was **rejected on 2026-09-15** (Guideline 5.2.5 "Mac" is an Apple trademark used inappropriately in the subtitle;
  Guideline 4.1(a) "Claude" is third-party content in the subtitle). Rule going forward: **no Apple or third-party
  product names in the app name or subtitle** — description, keywords and review notes were not flagged.
  Fixed via API (appInfoLocalizations PATCH), old submission canceled, build 10 resubmitted as a new review submission.
- First screenshot in both locales carries the prerequisite as a caption line
  ("需要一台装了 Claude Code 的 Mac" / "Needs a Mac running Claude Code") —
  add it in the ASC caption field or bake it into the image.
- **App Privacy**: add *Contact Info → Email Address (optional, user-provided,
  app functionality)* for the feedback form, and *Usage Data → Product
  Interaction* for onboarding milestones. Both are "not linked to identity",
  not used for tracking.
- Review notes: unchanged demo button ("先看看演示" / "Not right now — show me
  the demo") works without a Mac; reviewers can pair nothing and still see the
  demo tasks.

### How this release was shipped (repeatable)

```
# archive + upload (ASC API key from ~/.sessionbell/asc.json)
xcodebuild archive -project ios/SessionBell.xcodeproj -scheme SessionBell -destination 'generic/platform=iOS' \
  -configuration Release -archivePath /tmp/SessionBell.xcarchive -allowProvisioningUpdates \
  -authenticationKeyPath ~/.sessionbell/AuthKey_<KEY>.p8 -authenticationKeyID <KEY> -authenticationKeyIssuerID <ISSUER>
xcodebuild -exportArchive -archivePath /tmp/SessionBell.xcarchive -exportOptionsPlist ios/scripts/ExportOptions.plist \
  -exportPath /tmp/export -allowProvisioningUpdates -authenticationKeyPath … -authenticationKeyID … -authenticationKeyIssuerID …
# version, What's New, screenshots, subtitle, review notes, then attach build + submit
python3 ios/scripts/asc_release.py prepare      # edit WHATS_NEW / SUBTITLE_ZH / REVIEW_NOTES at the top first
python3 ios/scripts/asc_release.py submit <versionId>
```
App Privacy has no API: done in the ASC web UI (Contact Info → Email Address + Other
User Contact Info, linked, app functionality; Usage Data → Product Interaction, not
linked, analytics; nothing used for tracking).

### Screenshots

Re-captured 2026-09-08 with the redesigned UI: `appstore/screenshots/{zh-Hans,en}`
(1-onboarding, 2-tab0, 3-tab1, 4-detail), iPhone 17 Pro Max simulator,
1320×2868. Upload via Media Manager per the pipeline notes; iPad set unchanged.

## Pending / next version (1.3)

Bundle all of the below into the **next** update. Do not spin a review cycle
just for metadata — one review covers the build, all localizations and all
screenshots together, at no extra time.

### ASO: title / subtitle / keywords (English, U.S.)

The App Store **title** and **subtitle** are the highest-weighted search fields.
"SessionBell" alone captures no search demand, so the spare characters are put
to work. (Changing the App Store title does **not** change the home-screen icon
name — that comes from the app bundle's display name, independently.)

| Field | Value | Length |
|---|---|---|
| Title | `SessionBell: AI Agent Alerts` | 28 / 30 |
| Subtitle | `Coding agent alerts & control` | 29 / 30 |
| Keywords | `Claude Code,notification,remote,terminal,push,developer,CLI,lock screen,monitor,session,dashboard` | 97 / 100 |

Rationale:
- Keep the **SessionBell** brand first.
- Title absorbs AI / Agent / Alerts; subtitle adds coding / control; keywords
  fill with non-overlapping terms so no word is wasted repeating the title or
  subtitle.
- `Claude Code` is the highest-intent search term and already passed review in
  1.1's keywords, so it is safe to keep.

The Chinese (Simplified) subtitle "把 Agent 装进锁屏" is a good brand line —
keep it unless we decide to optimize the CN listing separately.

### English screenshots

Done in 1.2: the English (U.S.) localization has its own English-UI set
(iPhone 6.9" ×4, iPad 13" ×2), captured by the `SessionBellScreenshots` UI-test
target against a local worker (see `ios/Screenshots/ScreenshotTests.swift`).
Sources live in `appstore/screenshots/{en,zh-Hans}` and `appstore/ipad/en`.
For 1.3 the onboarding screenshot must be re-captured: the welcome screen now
shows "Get Started / Try the demo first" instead of the invite-code button.

## Version / build facts

- Last shipped: **1.6**, build **11** (approved 2026-09-22). Worker deployed 2026-09-22 (version 145ab9dd) with the Codex-aware public hook.
- Next build number: **≥ 12**.
- Bundle ID: `dev.yuesun.SessionBell` · ASC Apple ID: `6801045681`.
- Export Compliance is declared in-project (`ITSAppUsesNonExemptEncryption =
  false`), so Apple asks no encryption question.
- The iOS project uses XcodeGen: `ios/project.yml` is the source of truth; run
  `xcodegen generate` after editing it.

> `ios/project.yml` on `main` is at 1.6 / build 11.

## Context (not App Store)

- The marketing site already surfaces Windows (third onboarding card on the
  landing page).
