# Acceptance (§8)

Note: the data root has since moved to `~/.apprentice-mode/`.

Run 2026-10-12 on macOS 26.6.2, Apprentice 0.1.0, data root `~/Library/Application Support/Apprentice`.
✅ = verified, ⏳ = needs Eliot at the machine.

| # | Check | Result |
|---|---|---|
| 1 | Launched from Claude Code, no new prompts, all three permissions green | ✅ `scripts/start.sh` from Claude's Bash → `status` shows screen_recording / accessibility / input_monitoring all `true`. No new prompt across 4 rebuild + relaunch cycles (stable signing). The one-time grants and the "bypass private window picker" Allow are Apprentice's own (see NOTES.md: inheritance can't work under Superset). |
| 2 | 10 min real work across Chrome + two native apps → valid bundle, sensible role/text/value, matching screenshots | ⏳ Partial: session `20261012-142856` (Firefox, Chrome, Superset) has CLICK/TYPE/ENTER/KEY and passes the schemas (`APPRENTICE_VALIDATE_SESSIONS=… swift test`). Spot check: CLICK `role: link, text: "39 comments", site: ycombinator`, bbox `[314, 298.7, 47.3, 8.7]` lands on the link in `screenshots/00007.jpg` (1280×720, 116 KB), cursor on it, i.e. the frame from before the click. TYPE value `https://news.ycombinator.com` read from the address bar's AXValue. **Still to do:** a deliberate 10 min across Chrome + two native apps, then review the bundle together. |
| 3 | Password field → `[secure]`, no screenshot | ⏳ Logic unit-tested (`SecureFieldSuppressionTests`). On a real login form, keystrokes don't reach the tap under secure input, so this relies on the focus-poll path (NOTES.md). **Type into a real password field and check the TYPE line.** |
| 4 | Closing the Claude session leaves it running; `status` answers | ✅ in principle: the recorder's parent is launchd (`ppid 1`), not this session's shell. **Confirm:** close this Claude session, then `~/Applications/Apprentice.app/Contents/MacOS/Apprentice status` from a new terminal. |
| 5 | Small cap triggers pruning and the amber / paused states | ✅ With the cap at 1 MB (data 1.9 MB): pruned the one prunable session (`pruned 1 sessions` in the log), kept the open session, still over the cap → `state: paused_storage_full`. With the cap at 2.3 MB (82%): `storage_level: near_cap`, still recording (amber icon). Restored to 20 GB → `normal`. Run with a tiny cap instead of 200 MB because only ~2 MB had been recorded. |
| 6 | CPU < 3% average, memory < 150 MB (`top`) | ✅ so far: `top -l 13 -s 5` during light real use gave CPU 0.0–1.5% (mean ≈ 0.5%) and memory 16–18 MB. Re-measure during step 2. |

Also to check during step 2: a Safari private window is treated as excluded (not testable from the CLI).
