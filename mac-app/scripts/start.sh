#!/bin/sh
# Start the recorder and print its status JSON.
# Launched through LaunchServices (`open`) on purpose: Apprentice holds its own TCC grants, and `open`
# makes it its own responsible process from any terminal (a terminal-spawned child would be checked
# against the terminal's grants instead). It also outlives the shell / Claude session that ran this.
set -e
APP="$HOME/Applications/Apprentice.app"
BIN="$APP/Contents/MacOS/Apprentice"
LOG="$HOME/Library/Logs/Apprentice.log"
[ -x "$BIN" ] || "$(dirname "$0")/build.sh" >/dev/null
if "$BIN" status >/dev/null 2>&1; then "$BIN" status; exit 0; fi
open -g --stdout "$LOG" --stderr "$LOG" "$APP"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.5
  if "$BIN" status 2>/dev/null; then exit 0; fi
done
echo '{"ok": false, "error": "recorder did not answer, see '"$LOG"'"}'
exit 1
