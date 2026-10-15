You're acting out a scripted work session to generate test data for a workflow-extraction eval. A recorder captures everything you do in the Chrome window that is already open on this desktop, with a tab for each site the script starts from, plus Notes and Mail (local pages). Your job is to perform the script below exactly, like a real person at a normal pace.

How to act:
- Drive that Chrome only through the `bu` command (browser-use, already attached to it), with Python helpers in a heredoc:
  ```bash
  bu <<'PY'
  print(list_tabs())
  PY
  ```
- Act like a person using mouse and keyboard. Allowed helpers: `list_tabs`, `switch_tab(target)` followed by `activate_tab(target)` (that is how you "switch to a tab"), `capture_screenshot(path)` (then look at the image), `page_info`, `click_at_xy`, `type_text`, `press_key`, `scroll`, `wait`, `wait_for_load`, `close_tab`. Use `js(...)` and `cdp("Accessibility.getFullAXTree")` / `cdp("DOM.getBoxModel", ...)` only to read the page or find where an element is, never to change it (no setting values, no `.click()`, no submitting forms in JS). Don't use `goto_url`, `fill_input` or `dispatch_key`, and don't navigate by URL: start from a site's page and use its own search boxes, menus and links. The only exception is a step that says to open a new tab and go to a site: use `new_tab("https://<that site>")`.
- Pace yourself like a person: at most one numbered step of the script per `bu` call, and `wait(1)` between consecutive actions (clicks, typing, key presses) inside a call.
- To type into a field: find its box, `click_at_xy` its center, then `type_text(...)`. Check the value took (screenshot, or read it with `js`) before moving on.
- After every step, check it worked (screenshot or a targeted `js` read). Dismiss cookie or consent dialogs ("Reject all" or close; accepting is fine if there's no reject), banners and pop-ups by clicking them, then redo the step. If a "verify you are human" check appears, click its checkbox once and wait about 10 seconds. Try a failing step up to 4 times before giving up on it.
- Never sign in, buy, send, post or submit anything. In Mail, never click Send.
- Do the chunks in order and nothing else. Don't skip, reorder or add work.

Log every chunk with Bash: run `echo "$(date -Iseconds) start N" >> ~/eval/log.txt` right before chunk N, and `echo "$(date -Iseconds) end N ok" >> ~/eval/log.txt` once its last step has visibly worked. If a chunk can't be done (site blocked by a bot check, element still failing after 4 tries), log `end N failed <short reason>` instead and move on. For a pause chunk, run `sleep` for that many seconds, and log it like any other chunk.

When you've finished, reply with one paragraph: which chunks failed and why, and anything that went differently from the script.

The script:
