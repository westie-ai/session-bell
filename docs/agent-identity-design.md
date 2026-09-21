# Agent identity, approved 2026-09-20

Show each task's agent as its leading brand icon, with an explicit Codex/Claude
name in the app. Show state separately as a colored dot and status text, not by
recoloring the brand. Apply consistently to live tasks, history, detail and
lock-screen/expanded-island task rows. Keep aggregate island counts/status unchanged
because the activity may contain both agents. Preserve sorting and controls.

Missing engine means legacy Claude; unknown future engine values use a neutral
terminal icon rather than incorrectly claiming either brand. Bundled assets are
shared by app and widget, with original colors and aspect ratios. VoiceOver must
announce agent identity and status, including when the compact widget omits the name.

## Claude corners, approved 2026-09-21

Clip the Claude image in the shared `SBAgentIcon` view with a continuous rounded
rectangle (radius 18% of the component size). Apply the clip to the image's actual
frame, before the outer alignment frame, so home and Live Activity task rows use
the same visible corners. Keep the bundled image, Codex mark, colors and row layout.

## Asset provenance

- Codex: unchanged `icon-codex-light.png` from the installed official macOS client,
  `/Applications/ChatGPT.app/Contents/Resources/`.
- Claude: unchanged `claude_app_icon.png` from the installed official macOS client,
  `/Applications/Claude.app/Contents/Resources/ion-dist/images/`.
- These marks identify their respective services, not SessionBell branding or
  sponsorship. Marks remain owned by OpenAI and Anthropic.
- References: https://openai.com/brand/ and https://brandfolder.com/anthropic/.

## Verification

Build app and widget, check assets are bundled in both, render mixed-agent rows
in light/dark appearances, check long titles/status visibility, then overlay the
development build on the paired phone. No public release or backend change.
