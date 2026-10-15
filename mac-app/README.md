# Apprentice

macOS menu bar app that records how I work, so a separate pipeline can find repeated workflows and automate them. It records actions (clicks, typing, selections, shortcuts), the accessibility element each one hits, and one screenshot per action, as session bundles in a fixed format (see `schemas/`).

```sh
scripts/build.sh     # swift build -c release → ~/Applications/Apprentice.app (signed, LSUIElement)
scripts/package.sh   # dist/Apprentice.zip for the paste-a-prompt installer (INSTALL.md)
scripts/start.sh     # launch (via `open`, see NOTES.md) and print the status JSON
swift test           # unit tests, including schema validation of a generated bundle
```

Control from a shell or from Claude (the `apprentice` skill in `~/.claude/skills/` wraps these):

```sh
A=~/Applications/Apprentice.app/Contents/MacOS/Apprentice
$A status | pause [--minutes N] | resume | stop | new-session | prune --keep-gb N
```

## Menu bar

The icon is the Claude spark. A small dot bottom right means it's recording right now (the dot hides while an excluded app is frontmost). Dimmed means off. Orange means a missing permission or a storage problem.

The menu has **Turn off / Turn on Apprentice Mode** (⌃⌥⌘P does the same), **Trigger Workflow Harvesting** and **Quit Claude**, which quits Apprentice. Trigger Workflow Harvesting opens a new Claude Code session in the data root (`claude://code/new?folder=…&q=…`) with `AUTOMATE_TASKS.md` as the prompt, asking for the last 3 days. Pausing for a set time is CLI-only (`pause --minutes N`), and excluded apps are edited in `config.json`.

## Permissions

Apprentice has its own Screen Recording, Accessibility and Input Monitoring grants, listed as **Apprentice** in System Settings → Privacy & Security. Inheriting the terminal's grants doesn't work under Superset. NOTES.md has the Milestone 0 results.

macOS 15+ periodically asks whether Apprentice may keep recording the screen, worded "bypass the system private window picker". Answer **Allow**. If a grant is missing or revoked, recording pauses, the menu bar icon turns orange, and recording resumes within 30 s of the grant coming back.

## Data

`~/.apprentice-mode/` (move it with `defaults write blog.eliot.apprentice dataRoot <path>`, or `APPRENTICE_DATA_ROOT` for one run), excluded from Time Machine:

```
config.json          cap_gb (20), retention_days (30), excluded_apps, redaction_patterns
status.json          rewritten every 10 s
command.json         file-based control (see the skill), answered in command-result.json
AUTOMATE_TASKS.md    the Trigger Workflow Harvesting prompt, seeded from the skill only when missing; yours to edit
feed/index.html      the workflow feed page, kept in sync with the app; Claude writes feed/data.js
sessions/<YYYYMMDD-HHMMSS>/
  manifest.json  traces/dom.jsonl  traces/context.jsonl  screenshots/<seq>.jpg
```

A new session starts after 10 min idle, after a screen lock, at midnight, and at each launch. When storage passes the cap, the oldest whole sessions are pruned (never the open one). If pruning can't get back under the cap, or free disk drops below 10 GB, recording pauses with a notification. The icon turns orange at 80% of the cap.

## Privacy

- Secure fields: the value is recorded as `[secure]`, with no screenshot.
- Excluded apps: the bundle ids in `excluded_apps` in `config.json`, which starts as 1Password, Keychain Access, System Settings, Messages and FaceTime. Edit the list there; it's reloaded within 10 s. Nothing from them is recorded except an anonymous `excluded_app_active` context event. Their windows are also cut out of every screenshot.
- Private and incognito browser windows are treated the same way.
- Card numbers, IBANs and SSNs in typed values are replaced with `[redacted]`.
- The app makes no network calls.
