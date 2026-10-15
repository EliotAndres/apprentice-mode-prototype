#!/usr/bin/env bash
# Record a scripted agent session on a boat.dev sandbox (Linux desktop + Chrome).
#   live/boat.sh start <sandbox> <scenario>   fresh Chrome with CDP + recorder, then prompt.md + the scenario script to Codex
#   live/boat.sh fetch <sandbox> <scenario>   stop the recorder, copy everything to data/live/<sandbox>/, build the ground truth
# Scenarios live in live/scenarios/; run several at once on forks (`boat fork`), one screen each.
# The executing agent runs on Codex (your Codex tokens, not Claude's); it only performs and logs the script.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PYTHONPATH=data-generation:evaluation:visualization
SANDBOX=${2:?sandbox id}
SCENARIO=${3:?scenario json}
PROVIDER=${PROVIDER:-codex}
MODEL=${MODEL:-gpt-6.1-sol}  # the default gpt-5.6-terra logged steps it never checked
# Optional residential proxy (http://user:pass@host:port) for the sites in live/proxy.pac; everything else goes direct.
PROXY=${PROXY:-}
# How the agent drives Chrome: "computer" (the sandbox's X11 computer-use tools) or "browser-use" (CDP, via the `bu` CLI).
DRIVER=${DRIVER:-computer}
LIVE=data-generation/live
on_sandbox() { boat exec "$SANDBOX" --no-update --timeout 120 "$1" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["stdout"], d["stderr"], end="")'; }

case $1 in
start)
  boat interrupt "$SANDBOX" --no-update || true  # errors when nothing ever ran; one screen: any agent still running would fight this one
  # Fresh start: no old recording, log or browser state. Patterns are bracketed and use the
  # expanded path so pkill can't match the shell running this very command.
  on_sandbox 'pkill -INT -f "[c]dp_recorder.py"; pkill -f "[u]ser-data-dir=/home/user/.config/chrome-eval"; pkill -f "[h]ttp.server 8000"
    sleep 2; rm -rf ~/eval ~/.config/chrome-eval; mkdir -p ~/eval ~/pages'
  # A fork's computer-use driver can come up hung (every agent action then times out): restart it if so.
  on_sandbox 'D=/opt/ascii/cua-driver/cua-driver; S=/run/ascii-cua/driver.sock
    timeout 20 $D call --socket $S list_windows "{}" >/dev/null 2>&1 || { pkill -f "[c]ua-driver serve"; sleep 8
      timeout 20 $D call --socket $S list_windows "{}" >/dev/null 2>&1 || { echo "computer driver still hung" >&2; exit 1; }; }'
  boat scp --no-update data-generation/replicate/cdp_recorder.py "$SANDBOX:/home/user/cdp_recorder.py"
  for PAGE in "$LIVE"/pages/*.html; do boat scp --no-update "$PAGE" "$SANDBOX:/home/user/pages/"; done
  boat exec "$SANDBOX" --no-update --detach 'python3 -m http.server 8000 --bind 127.0.0.1 --directory ~/pages'
  TABS=$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["tabs"]))' "$SCENARIO")
  CHROME_PROXY=""
  if [ -n "$PROXY" ]; then
    # Chrome can't send proxy credentials, so a local pproxy adds them.
    HOSTPORT=${PROXY##*@}; CREDS=${PROXY#*://}; CREDS=${CREDS%@*}
    on_sandbox "pkill -f '[p]proxy -l'; true"  # separate call: the pattern would match the command below
    boat exec "$SANDBOX" --no-update --detach "cd ~ && uv run -q --with pproxy pproxy -l http://127.0.0.1:8899 -r 'http://$HOSTPORT#$CREDS'" >/dev/null
    CHROME_PROXY="--proxy-pac-url='data:application/x-ns-proxy-autoconfig;base64,$(base64 < "$LIVE/proxy.pac" | tr -d '\n')'"
    on_sandbox 'for i in $(seq 60); do curl -s -m 10 -x http://127.0.0.1:8899 https://ipv4.icanhazip.com && exit 0; sleep 2; done; echo "proxy not up" >&2; exit 1'
  fi
  on_sandbox "(DISPLAY=:0 setsid google-chrome-stable --remote-debugging-port=9222 --user-data-dir=\$HOME/.config/chrome-eval \
      --no-first-run --no-default-browser-check --start-maximized $CHROME_PROXY $TABS >/tmp/chrome.log 2>&1 &); sleep 8"
  boat exec "$SANDBOX" --no-update --detach 'cd ~ && uv run -q --with websockets python cdp_recorder.py --out ~/eval/sessions'
  if [ "$DRIVER" = browser-use ]; then
    on_sandbox 'command -v browser-use >/dev/null || uv tool install -q "browser-use[cli]"
      printf "#!/bin/sh\nBU_CDP_URL=http://localhost:9222 BH_TAB_MARKER=0 exec ~/.local/bin/browser-use \"\$@\"\n" > ~/.local/bin/bu; chmod +x ~/.local/bin/bu
      ~/.local/bin/bu --reload >/dev/null 2>&1; echo "print(page_info())" | ~/.local/bin/bu'
  fi
  # Never let the agent act unrecorded: wait for the recorder's first manifest.
  on_sandbox 'for i in $(seq 90); do ls ~/eval/sessions/*/manifest.json >/dev/null 2>&1 && exit 0; sleep 1; done; echo "recorder did not start" >&2; exit 1'
  on_sandbox 'ls ~/eval/sessions/*/manifest.json' | grep -q manifest || { echo "recorder did not start; not prompting" >&2; exit 1; }
  PROMPT_FILE=$LIVE/prompt.md; [ "$DRIVER" = browser-use ] && PROMPT_FILE=$LIVE/prompt-browser-use.md
  PROMPT="$(cat "$PROMPT_FILE")
$(uv run -q python -m replicate.live_ground_truth prompt "$SCENARIO")"
  boat prompt "$SANDBOX" --no-update --provider "$PROVIDER" --model "$MODEL" --new "$PROMPT"
  ;;
fetch)
  # Optional 4th arg: the id the session gets under data/sessions/ (default: agent-<sandbox>).
  SESSION_ID=${4:-agent-$SANDBOX}
  on_sandbox 'pkill -INT -f "[c]dp_recorder.py"; sleep 3; ls ~/eval/sessions'
  OUT=data/live/$SANDBOX
  mkdir -p "$OUT"
  boat scp --no-update "$SANDBOX:/home/user/eval/log.txt" "$OUT/log.txt"
  on_sandbox 'cd ~/eval/sessions && tar czf /tmp/session.tgz */'
  boat scp --no-update "$SANDBOX:/tmp/session.tgz" "$OUT/session.tgz"
  rm -rf "data/sessions/$SESSION_ID"; mkdir -p "data/sessions/$SESSION_ID"
  tar xzf "$OUT/session.tgz" -C "data/sessions/$SESSION_ID" --strip-components 1 && rm "$OUT/session.tgz"
  python3 - "data/sessions/$SESSION_ID/manifest.json" "$SESSION_ID" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1])); manifest["session_id"] = sys.argv[2]
json.dump(manifest, open(sys.argv[1], "w"), indent=1)
PY
  uv run -q python -m replicate.live_ground_truth build "$SCENARIO" "$OUT/log.txt" --out "$OUT/ground_truth.json"
  uv run -q python -m replicate.live_ground_truth convert "$OUT/ground_truth.json" "data/sessions/$SESSION_ID" \
    --out "data/ground_truth/$SESSION_ID.json"
  uv run -q python -m replicate.check_bundle "data/sessions/$SESSION_ID" --gt-dir data/ground_truth
  ;;
esac
