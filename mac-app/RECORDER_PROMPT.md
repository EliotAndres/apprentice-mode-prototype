# Prompt: build "Replicate Recorder", a macOS menu bar app that records how I work

You are building a small native macOS app that runs on my everyday Mac while I work. It records what I do (actions, the UI element each one hits, and one screenshot per action) so that a separate pipeline can later find workflows I repeat and turn them into Claude skills. You are not building that pipeline, only the recorder. The output format below is fixed, because the pipeline already reads it.

Work in a new git repo called `replicate-recorder`. Use Swift 6, SwiftPM, AppKit/SwiftUI and ScreenCaptureKit. Don't use Xcode project files, Electron or Python at runtime. Target macOS 15 or later on Apple Silicon.

## 1. The constraint that shapes everything: inherit Claude's permissions

The app must not need its own Screen Recording / Accessibility / Input Monitoring grants. Claude Code launches it, and it runs under the TCC permissions of the app that hosts Claude: Terminal, iTerm2, Ghostty, VS Code, or the Claude desktop app. That's how I keep one set of grants.

How macOS decides this: TCC checks the *responsible process*. A binary that a terminal spawns directly has that terminal as its responsible process, so it gets the terminal's grants. An app started with `open` or LaunchServices is its own responsible process and needs its own grants. So:

- **Launch by exec'ing the binary inside the bundle, never with `open`.** Use `nohup "$APP/Contents/MacOS/ReplicateRecorder" >>"$LOG" 2>&1 & disown`. Ship this as `scripts/start.sh`.
- **Survive the Claude session that started it.** Ignore SIGHUP. Closing the Claude session or the terminal tab must not stop recording. Quitting the host app itself may stop it; detect that and say so in the menu.
- **Check permissions at startup and in the menu.** Use `CGPreflightScreenCaptureAccess()`, `AXIsProcessTrusted()` and `CGPreflightListenEventAccess()`. If any is missing, the menu bar icon shows a warning and the menu names the host app that needs the grant, e.g. "Grant Screen Recording to iTerm2 in System Settings → Privacy & Security". Find the host app by walking up the parent process chain to the first `.app`. Never call the `Request…` variants from the recorder: they would prompt on behalf of the host app in a confusing way.
- **Verify this before building anything else.** Milestone 0 is a spike: a tiny status-item binary, launched from Claude Code in my terminal, that takes one ScreenCaptureKit screenshot, reads the AX element under the mouse, and receives a listen-only CGEventTap click. Report which of the three worked, and what happened when it was launched with `open` instead. If inheritance doesn't work on this macOS version, stop and tell me rather than working around it.
- **Expect re-confirmation prompts.** macOS 15+ periodically asks whether the host app may "continue to record the screen". Document that, and handle a revoked grant mid-session: pause cleanly, show the warning, resume when the grant comes back (poll every 30 s).

Ad-hoc code signing is fine. Inheritance doesn't depend on the child's signature, and no notarization is needed.

## 2. Menu bar app

- **Status item only.** `LSUIElement = true`: no Dock icon, no main window.
- **Icon state:** recording (filled dot), paused (hollow), permission problem (warning triangle), storage near its cap (amber).
- **Title next to the icon:** today's stored size, e.g. `1.2 GB`. Make it toggleable.
- **Menu:**
  - Status line ("Recording · 412 actions today · session 3h12m").
  - Pause for 15 min / 1 h / until resumed, and Resume.
  - The Storage section (§4).
  - "Exclude current app".
  - Open data folder.
  - Permission status for each permission, naming the host app.
  - "Quit recorder".
- **Global hotkey** to pause or resume (⌃⌥⌘P), with a brief HUD confirming the new state.

## 3. What to capture

The useful signal is *actions*: which element was hit, what was typed. Pixels support it but aren't the main signal, so record one screenshot per action, not video.

**Actions** come from a listen-only `CGEventTap` (never modify events):
- **CLICK:** left mouse down. Record the AX element at the click point (`AXUIElementCopyElementAtPosition`).
- **TYPE:** coalesce keystrokes into one TYPE per field.
  - The run ends on a focus change, Enter, Tab, or 3 s idle.
  - `value` is the resulting field value, read from the element's `AXValue` when it's readable, else rebuilt from key events.
- **ENTER:** Return in a field, as its own action after the TYPE.
- **SELECT:** a choice in a popup button, menu or combo box, with `value` set to the chosen item's title.
- **Keyboard shortcuts** (⌘ + key): op `KEY`, value like `cmd+shift+t`.
- **Scroll:** skip.

**Element:** for each action, read from AX:
- `role`: AXRole without the "AX" prefix, lowercased (`button`, `textfield`, `link`, `menuitem`…).
- `text`: the first non-empty of AXTitle, AXDescription, AXPlaceholderValue, AXHelp, or the title of the nearest labelled ancestor. Truncate to 200 characters.
- `bbox`: frame converted to screenshot pixels.
- `tag`: AXSubrole if present.

AX reads must time out after 250 ms (`AXUIElementSetMessagingTimeout`). A slow app must never stall the event tap: do the AX work off the tap callback.

**Context** for every action:
- frontmost app name and bundle id;
- window title;
- in browsers (Safari, Chrome, Arc, Edge, Brave, Firefox), the current URL, read via AX (the address bar's AXValue, or AXDocument / AXURL on the web area). Don't use AppleScript: Automation permission is per-target and noisy.

`site` = the registrable domain for browser pages (`booking.com` → `booking`), else the app's bundle id.

**Context events** go in `traces/context.jsonl`, one line each, same clock: app activations, window title changes, URL changes, idle start/end (60 s with no input), and screen lock/unlock.

**Screenshots:**
- Keep a ScreenCaptureKit `SCStream` on the display under the cursor at 1 fps (bump it to 4 fps for 2 s after any input). Hold only the latest frame in memory.
- On each action, save the frame from *before* the input: a JPEG, at most 1280 px wide, quality 0.6, about 100 KB.
- Exclude the recorder's own UI with `SCContentFilter`'s excluding-applications.
- Write no frames while idle or locked, and none for excluded apps.

## 4. Storage monitor (a first-class feature, not an afterthought)

- **Location:** `~/Library/Application Support/ReplicateRecorder/` (configurable). Exclude it from Time Machine (`tmutil addexclusion`).
- **Byte counting:** count bytes as they're written so the menu updates live. Every 10 minutes and at startup, reconcile against a real directory scan.
- **Storage section of the menu:**
  - today's size and total size;
  - a per-day list for the last 7 days (size and action count);
  - free disk space;
  - the cap and retention settings;
  - "Delete today", "Delete all", "Reveal in Finder".
- **Limits and defaults:**
  - Size cap 20 GB, retention 30 days. Prune the oldest whole sessions first. Never delete the session being written.
  - Amber icon at 80% of the cap.
  - At 100%, prune; if pruning can't free enough, pause and notify.
  - Below 10 GB free on disk, pause and notify. Resume automatically above 15 GB.
- **status.json:** write `status.json` in the data root every 10 s (state, permissions, host app, today/total bytes, actions today, current session id, free disk, last error). The pipeline and Claude read it.

## 5. Output format (fixed; the pipeline reads it)

**Session bundles.** One directory per session under `sessions/<session_id>/`. `session_id` is `YYYYMMDD-HHMMSS`, lowercase, matching `^[a-z0-9_-]+$`.

**When to start a new session:**
- after 10 minutes idle,
- after screen lock,
- at local midnight,
- at recorder restart.

**Layout:**

```
sessions/20261012-091500/
  manifest.json
  traces/dom.jsonl        # one action per line (despite the name, not browser-only)
  traces/context.jsonl    # app/window/url/idle events
  screenshots/00000.jpg   # named by seq
```

**manifest.json:**

```json
{
  "schema_version": "0.2",
  "session_id": "20261012-091500",
  "source": {"kind": "macos_recorder", "recorder_version": "0.1.0", "host_app": "com.googlecode.iterm2"},
  "started_at": "2026-10-12T09:15:00-07:00",
  "duration_s": 3812.4,
  "timing": {"kind": "observed"},
  "streams": {
    "dom": {"path": "traces/dom.jsonl", "format": "jsonl", "count": 412},
    "screenshots": {"path": "screenshots/", "format": "jpeg max 1280w", "count": 409},
    "accessibility": {"path": "traces/context.jsonl", "format": "jsonl", "count": 97}
  },
  "counts": {"actions": 412, "sites": 11},
  "displays": [{"id": 1, "width_px": 3024, "height_px": 1964, "scale": 2.0}]
}
```

**Writing the manifest.** Write it at session start. Rewrite it atomically (temp file + rename) every minute and at session end, so a crash leaves a valid bundle.

**traces/dom.jsonl.** One line per action, with `seq` starting at 0 and contiguous, and `t` in seconds since `started_at`, monotonic non-decreasing:

```json
{"seq": 0, "t": 9.22, "type": "action", "op": "CLICK", "value": "", "site": "booking", "url": "https://www.booking.com/attractions", "app": "com.google.Chrome", "window": "Attractions – Booking.com", "element": {"tag": "", "role": "link", "text": "Attractions", "bbox": [577.6, 68.0, 128.1, 44.0]}, "screenshot": "screenshots/00000.jpg", "dom_snapshot": null}
```

Required fields are `seq, t, type, op, site, element{role, text}`; `bbox` is `[x, y, w, h]` in screenshot pixels. The op enum is `CLICK, TYPE, SELECT, HOVER, ENTER`, extended here with `KEY`. Put the full JSON Schemas (manifest and action) in `schemas/` and validate every finished session against them in the tests.

## 6. Privacy (non-negotiable)

- **Passwords.** Never record a value typed into a secure field (`AXSecureTextField` subrole, or `IsSecureEventInputEnabled()` true). Log the action with `value: "[secure]"` and skip its screenshot.
- **Excluded apps.** Default list: 1Password, Keychain Access, System Settings, Messages, FaceTime. Editable in a `config.json`, and also from the "Exclude current app" menu item. For an excluded app, record nothing at all, not even a context event beyond "excluded app active".
- **Private browsing.** Treat private and incognito windows like an excluded app. Detect them from the window title / AX.
- **Typed-value redaction.** Before writing, replace anything that looks like a card number, IBAN or SSN with `[redacted]`. The regexes live in config.
- **No network.** The app makes no network calls of any kind. Everything stays local.

## 7. Control from Claude

**Single binary, two modes.** With no arguments it runs the recorder (refusing to start if one is already running: a pid lock in the data root). With a subcommand it talks to the running instance over a Unix socket in the data root and prints JSON:
- `ReplicateRecorder status`
- `pause [--minutes N]`
- `resume`
- `stop`
- `new-session`
- `prune --keep-gb N`

**Scripts:**
- `scripts/build.sh`: `swift build -c release`, wraps the result in `ReplicateRecorder.app` with an Info.plist (LSUIElement), ad-hoc signs it, and installs to `~/Applications/`.
- `scripts/start.sh`: the exec-not-open launch from §1; it prints the status JSON once running.

**Claude Code skill.** Install a skill at `~/.claude/skills/replicate-recorder/SKILL.md` so that "start/stop/pause the recorder" and "how much has the recorder stored?" map to those scripts and subcommands. Its body explains the permission-inheritance rule, so a future Claude never launches it with `open`.

## 8. Tests and acceptance

**Unit tests** (`swift test`):
- keystroke coalescing into TYPE/ENTER;
- AX role/text extraction from fixtures;
- session splitting rules;
- storage accounting and pruning order (with a fake clock and filesystem);
- redaction regexes;
- secure-field suppression;
- schema validation of a generated bundle.

**Manual acceptance**, run with me at the end and written up in `ACCEPTANCE.md`:
1. Launched from Claude Code in my terminal, with no new permission prompts and all three permissions green in the menu.
2. 10 minutes of real work across Chrome and two native apps produces a valid bundle. Actions show sensible `role/text/value`, and screenshots match the actions.
3. Typing into a password field records `[secure]` and no screenshot.
4. Closing the Claude session leaves the recorder running; `ReplicateRecorder status` still answers.
5. Setting the cap to 200 MB triggers pruning and the amber/paused states correctly.
6. CPU averages under 3% while recording, and memory stays under 150 MB. Measure with `top`, and report it.

## 9. Order of work

1. The Milestone 0 permission spike from §1. Report back before continuing.
2. Event tap, AX extraction and `dom.jsonl`, without screenshots.
3. Screenshots.
4. Sessions and manifest.
5. Storage monitor.
6. Menu polish.
7. Control socket and skill.
8. Privacy hardening.
9. Acceptance.

Commit after each step with a clear message. Keep the code readable: long, explicit variable names (`screenshot_frame`, `focused_element`, `session_directory`), never one- or two-letter names.

When something about macOS behaviour is uncertain (TCC inheritance, AX quirks in Electron apps, ScreenCaptureKit prompts), test it on this machine and record what you observed in `NOTES.md`. Don't assume.
