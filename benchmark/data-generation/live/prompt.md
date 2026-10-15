You're acting out a scripted work session to generate test data for a workflow-extraction eval. A recorder captures everything you do in the Chrome window that is already open on this desktop, with a tab for each site the script starts from, plus Notes and Mail (local pages). Your job is to perform the script below exactly, like a real person at a normal pace.

How to act:
- Use only the `computer` tools (click, type_text, press_key, hotkey, scroll) on that Chrome window. Don't use the `browser_*` tools, don't launch another browser, and don't navigate by typing deep URLs: start from a site's page and use its own search boxes, menus and links. Switch tabs by clicking them. Always pass `delivery_mode: "foreground"`: background input can't type into Chrome on this machine.
- Clicks and typing can silently miss (a cookie or consent dialog in the way, focus elsewhere). After every step, look at a screenshot and check it worked. Dismiss cookie or consent dialogs ("Reject all" or close), banners and pop-ups (their X, or Escape), then re-click the field and redo the step. To type into a field, click it, look again to check it is focused (pages can lag or re-render the field), then call `type_text` with the same `x` and `y` as that click. After typing, check the text actually appears; if not, click the field again and retype. Try up to 4 times before giving up on a step. If a cookie dialog has no reject option, accepting cookies is fine (it isn't signing in). If a "verify you are human" check appears, click its checkbox once and wait about 10 seconds for the page; only if it keeps blocking, log the chunk as failed.
- Never sign in, buy, send, post or submit anything. In Mail, never click Send.
- Do the chunks in order and nothing else. Don't skip, reorder or add work.

Log every chunk, using Bash only for this: run `echo "$(date -Iseconds) start N" >> ~/eval/log.txt` right before chunk N, and `echo "$(date -Iseconds) end N ok" >> ~/eval/log.txt` once its last step has visibly worked. If a chunk can't be done (site blocked by a bot check, element or typing still failing after 4 tries), log `end N failed <short reason>` instead and move on. For a pause chunk, run `sleep` for that many seconds, and log it like any other chunk.

When you've finished, reply with one paragraph: which chunks failed and why, and anything that went differently from the script.

The script:
