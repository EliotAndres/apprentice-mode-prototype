# NOTES: macOS behaviour observed on this machine

Recorded 2026-10-12 on macOS 26.6.2 (25G83), Apple Silicon, Swift 6.4 / Xcode. Claude Code runs inside Superset.app (Electron).

## TCC inheritance (Milestone 0): doesn't work here, so Apprentice has its own grants

Spike: a status-item binary that takes one SCK screenshot, reads the AX element under the mouse, and opens a listen-only event tap.

| launched as | responsible process | screenshot | AX at mouse | event tap |
|---|---|---|---|---|
| `nohup …/Contents/MacOS/Spike &` from Claude's Bash | the spike itself | ✗ "user declined TCCs" | ✗ -25211 | created, no events |
| `open Spike.app` | the spike itself | ✗ | ✗ | created, no events |
| bare binary (no bundle) | itself | ✗ | ✗ | created, no events |

`responsibility_get_pid_responsible_for_pid` for each process up the chain: Superset → its pty zsh → `claude` → Claude's Bash zsh → child. Every process was **its own** responsible process. Superset's own helper processes did report Superset as responsible, so the call works. Electron terminals disclaim responsibility for the shells they spawn, and something below that disclaims again. So nothing a terminal-hosted Claude starts can inherit the host app's grants here.

**Claude.app (desktop) doesn't lend its grants either** (read from its `app.asar`, Claude 2.31226). Every child is spawned through `Claude.app/Contents/Helpers/disclaimer`, which disclaims TCC responsibility. That covers Claude Code sessions in the Code tab, so a skill's Bash commands don't inherit either. Responsibility is kept only when the call site opts in (`keepTccResponsibility`, used for `ssh`) *and* the binary is "system-owned": root-owned and not group/other-writable along the whole path, on the boot volume. Otherwise it logs `[untrustedLaunch] not lending Claude's TCC responsibility … launching it disclaimed instead`. Cowork runs in a VM. Inheriting would need a root-owned install at best, so it's ruled out.

**Decision (Eliot):** the app is called **Apprentice** and holds its own Screen Recording, Accessibility and Input Monitoring grants. It appears under its own name in Privacy & Security.
- It calls `CGRequestScreenCaptureAccess` / `AXIsProcessTrustedWithOptions(prompt)` / `CGRequestListenEventAccess` at first start. That is what lists it in each pane.
- `start.sh` launches it with `open`, so it is always its own responsible process. Exec'd from Terminal.app it would be checked against Terminal's grants instead.
- It is signed with the `Apple Development: Eliot Andres` certificate instead of ad-hoc. The designated requirement is `identifier "blog.eliot.apprentice" and anchor apple generic and certificate leaf[subject.CN] = "Apple Development: Eliot Andres (6J84AXK2HG)" …`, so grants survive rebuilds. **Observed:** after several rebuild + reinstall cycles all three permissions stayed green with no new prompt. Ad-hoc signing would change the cdhash on every build and invalidate the grants.
- `CGEventTap.tapCreate` succeeds even without Input Monitoring / Accessibility. It just never delivers events. Use the preflight calls for status, never "tap created".

## ScreenCaptureKit

- **The "private window picker" prompt.** On the first `SCStream` after the grant, macOS 15+ shows *"Apprentice is requesting to bypass the system private window picker and directly access your screen and audio"*. It is drawn by `UserNotificationCenter`, which shows up in context.jsonl as an `app_activated` at that moment. Answer **Allow**. macOS asks again periodically (roughly monthly).
- If the prompt is denied or the grant lapses: `CGPreflightScreenCaptureAccess()` turns false, the 30 s poll pauses capture and shows the warning triangle, and capture resumes when it's back (`restartIfStopped`).
- Frames: 1280×720 for a 1512×982 pt display at quality 0.6 came to about 116 KB on a busy Hacker News page.
- `SCStream` only delivers frames when the screen changes. At rest the 1 fps stream costs nothing visible.
- `excludingApplications: [self]` hides our windows but **not our status item**: the "676 KB" menu-bar title still shows in frames. On macOS 26 the status item seems to be composited outside our app's windows. Harmless, left as is.
- Resource use while recording, on a live session with Chrome / Firefox / Superset in use: `top` sampled every 5 s for a minute showed CPU 0.0 to 1.5% (mean ≈ 0.5%) and memory 16 to 18 MB.

## Accessibility quirks

- **Chrome URL:** `AXWebArea`'s `AXURL` works once `AXManualAccessibility = true` is set on the app element. We set it on each app's first activation. The omnibox is `AXTextField` "Address and search bar", but we don't need it.
- **Chrome incognito:** the window title ends in `(Incognito)`, e.g. "Wikipedia - Google Chrome (Incognito)", and the toolbar has an `AXButton` "Incognito". Both are checked.
- **Firefox private:** the title ends in "— Private Browsing", and there's an `AXImage` "Private browsing". Detected by title.
- **Safari private:** not tested, because a private window can't be opened from the CLI without UI scripting. The marker list includes "Private Browsing" / "Private Window" as a guess. **Check during acceptance.**
- **Leak, minor:** a brand-new incognito window can produce one `app_activated` event, before its title is set, with app = Chrome, an empty window title and no URL. The next poll marks it private.
- **Firefox address bar** is an `AXComboBox` with placeholder "Search with Google or enter address". `AXValue` holds the typed text, so TYPE values come from AX, not keystroke reconstruction.
- **Clicks land on labels.** In web content the hit element is usually `AXStaticText` inside an `AXLink`, and the link itself has no title. We promote pass-through roles (static text, image, group) to the nearest actionable ancestor within 5 levels, and take the text from the hit label's `AXValue` (HN "39 comments" → `role: link, text: "39 comments"`). The spec's text list (title/description/placeholder/help) doesn't mention a static text's value; we added it because otherwise every web link would have empty text.
- **SELECT** = a click whose target role is `AXMenuItem` (popup buttons, context menus, `<select>` menus in Chrome, menu-bar menus). A press-drag-release selection in a popup menu has no mouse-down on the item and is missed.
- **Secure input:** with `IsSecureEventInputEnabled()` true, keyDown events don't reach the tap at all. So `[secure]` TYPE comes from two sources. One is the coalescer, when keys do arrive in an `AXSecureTextField`. The other is a 1 s focus poll: focus entered a secure field and left with `AXNumberOfCharacters > 0` → TYPE `[secure]` with no screenshot. **Check during acceptance (step 3).**
- Large fields: when `AXValue` is longer than 500 characters (an editor or a document), TYPE uses the keystroke-reconstructed text instead of the whole field.

## Other

- `tmutil addexclusion` (sticky, no root) works on the data root.
- `UserDefaults(suiteName:)` with our own bundle id logs a warning and doesn't work. The CLI binary lives in the same bundle, so `UserDefaults.standard` already shares the domain.
- `launchctl`/`open`: a recorder started with `open` keeps running after the Claude session or terminal tab closes, since launchd is its parent. SIGHUP is ignored anyway.
