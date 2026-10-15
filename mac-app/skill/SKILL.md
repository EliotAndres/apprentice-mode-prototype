---
name: apprentice
description: Record how the user works with the Apprentice menu bar app and analyze the recordings to find repeated workflows worth automating. Use to start, pause, resume or stop recording, check status or storage, and for "what did I do today?", "analyze my session", "what could you automate for me?".
---

# Apprentice

Apprentice is a macOS menu bar app (`~/Applications/Apprentice.app`). It records the user's actions (clicks, typing, shortcuts, the accessibility element each one hits), plus one screenshot per action, into session folders. Everything stays local. The app opens at login, so it's normally already running.

## Controlling it

Data lives in `~/.apprentice-mode/` (call it `$D`; `data_root` in the status says where if it was moved). In Cowork, `$D` is the folder you were given. There are two ways to control the app; use the one your tools allow.

**If you can run commands** (Claude Code): `A=~/Applications/Apprentice.app/Contents/MacOS/Apprentice`, then `$A status | pause [--minutes N] | resume | stop | new-session`. If it isn't running, start it with `open -g ~/Applications/Apprentice.app`. Never run the binary with no arguments: it would lose its own permissions.

**If you can only read and write files** (Cowork):
- **Status:** read `$D/status.json`. The app rewrites it every 10 seconds.
- **Commands:** write `$D/command.json` containing one of `{"command": "status"}`, `{"command": "pause"}`, `{"command": "pause", "minutes": 30}`, `{"command": "resume"}`, `{"command": "new-session"}` or `{"command": "stop"}`. Within 2 seconds the app runs it, deletes `command.json` and writes the response to `$D/command-result.json`.
- **If `command.json` isn't picked up,** the app isn't running. Ask the user to open Apprentice from `~/Applications` once.

**Reporting:**
- Give storage in human units: `today_bytes`, `total_bytes`, `actions_today`, `free_disk_bytes`.
- The user can turn recording off or on with ⌃⌥⌘P or from the menu bar icon.
- To exclude an app, add its bundle id to `excluded_apps` in `$D/config.json`. The app reloads it within 10 seconds.

**Permissions:**
- If any entry in `permissions` is false, tell the user to allow **Apprentice** in System Settings → Privacy & Security. The three entries are Screen & System Audio Recording, Accessibility and Input Monitoring. Apprentice uses its own grants, so it's listed under its own name. Never try to grant permissions yourself.
- **Screen Recording stays false after granting:** stop the app, then start it again. macOS often applies that grant only after a relaunch.
- **"Bypass the private window picker" prompt:** if the user asks about it, the answer is Allow.

## Analyzing a recording

1. Sessions are folders in `$D/sessions/`, named by start time; each has a `manifest.json` (`started_at`, `duration_s`, `counts.actions`). Pick the one the user means, or the longest recent one.
2. Each session folder contains:
   - `traces/dom.jsonl`: one action per line. Fields: `t` (seconds), `op` (CLICK, TYPE, ENTER, SELECT, KEY), `app` (bundle id), `site`, `url`, `window`, `element.role`, `element.text`, `value` (typed text or shortcut) and `screenshot`.
   - `traces/context.jsonl`: app switches, window changes, URL changes and idle periods.
   - `screenshots/NNNNN.jpg`: the screen just before each action.

   Read the traces compactly. For example:
   ```sh
   jq -r '[(.t|floor), .op, .app, .site, .window, .element.role, .element.text, .value] | @tsv' traces/dom.jsonl
   ```
   Open a screenshot only when an action is ambiguous: canvas apps like Google Slides give clicks an empty `text`, and the screenshot shows what was clicked.
3. Write the timeline: what the user did, in a handful of plain steps with times.
4. Find **repeated workflows**: the same app or site and a similar sequence of steps done at least twice, where only some values change (search terms, dates, names, recipients). For each one, give:
   - a name;
   - when it happened;
   - the steps;
   - the parameters that vary;
   - the time spent per run;
   - how you would automate it. Use your judgment and pick whatever fits best: a script, an API, a CLI tool, a shortcut, browser automation, an app setting that removes the step altogether, or something else.

   One-off tasks and browsing aren't workflows.
5. Show them in the feed. Write `$D/feed/data.js` (overwrite it), then open `$D/feed/index.html` (`open` it in Claude Code; in Cowork, give the user the path). The page is already installed; don't edit it. `data.js` is one assignment:
   ```js
   window.FEED = {
     name: "Eliot",                        // the user's first name if you know it, else omit
     data_root: "/Users/…/.apprentice-mode", // absolute path of $D
     period: "the last 3 days",
     workflows: [{
       title: "You look up a GitHub profile and write a lead email, by hand", // plain words, what the user keeps doing
       category: "Browser",                // Browser, Mail, Docs, Spreadsheet, Terminal, Chat, Files, Calendar or Other
       insight: "Same 9 steps each time; only the username and role change.",
       runs: [{ at: "2026-10-14T14:12", minutes: 4.5 }],   // local time, one per run
       steps: ["Search GitHub for the username", "…"],
       parameters: ["username", "role"],
       automation: "How you would automate it, in a sentence or two.",
       saved_minutes_per_run: 4,
       automate_prompt: "A self-contained prompt for a new Claude Code session that builds this automation: the workflow, its steps and parameters, and your approach."
     }]
   };
   ```
   Each card's **Automate this** button opens a new Claude Code session with its `automate_prompt`, so make that prompt complete on its own.
6. Offer to build the automation for the best candidate.

Excluded apps and private browser windows appear only as `excluded_app_active` events. Secure fields are `[secure]` and card numbers are `[redacted]`. Don't try to recover them.
