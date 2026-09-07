# App Store & ASO Notes

_Last updated: 2026-09-07_

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

- Last shipped: **1.2**, build **5** (build 4 was uploaded first, then replaced by build 5 with the English UI localization).
- Next: **1.3**, build **≥ 6**.
- Bundle ID: `dev.yuesun.SessionBell` · ASC Apple ID: `6801045681`.
- Export Compliance is declared in-project (`ITSAppUsesNonExemptEncryption =
  false`), so Apple asks no encryption question.
- The iOS project uses XcodeGen: `ios/project.yml` is the source of truth; run
  `xcodegen generate` after editing it.

> `ios/project.yml` on `main` is at 1.2 / build 5. Bump to 1.3 / 6 for the
> next build.

## Context (not App Store)

- The marketing site already surfaces Windows (third onboarding card on the
  landing page).
