# App Store & ASO Notes

_Last updated: 2026-09-08_

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

## 1.4 — ready to submit (build 8)

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

- zh-Hans subtitle: `把 Mac 上的 Claude Code 装进锁屏` (was 把 Agent 装进锁屏).
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

- Last shipped: **1.3**, build **7** (approved 2026-09-07). Ready: **1.4**, build **8**.
- Next build number: **≥ 9**.
- Bundle ID: `dev.yuesun.SessionBell` · ASC Apple ID: `6801045681`.
- Export Compliance is declared in-project (`ITSAppUsesNonExemptEncryption =
  false`), so Apple asks no encryption question.
- The iOS project uses XcodeGen: `ios/project.yml` is the source of truth; run
  `xcodegen generate` after editing it.

> `ios/project.yml` on `main` is at 1.4 / build 8.

## Context (not App Store)

- The marketing site already surfaces Windows (third onboarding card on the
  landing page).
