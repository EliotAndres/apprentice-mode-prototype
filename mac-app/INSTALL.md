# Install Apprentice

Opening the app is the install: on launch it registers itself to open at login and writes the `apprentice` skill to `~/.claude/skills/apprentice/`, updating it whenever a newer build ships a different one. There's no MCP server. Claude Code drives it with the CLI, and Cowork through files (`status.json`, `command.json`). See the skill.

Until there's a public release, `<ZIP>` is a local build. `scripts/package.sh` prints its path (`dist/Apprentice.zip`). The build isn't notarized, so a copy downloaded in a browser is blocked by Gatekeeper. A copy unpacked by Cowork or Claude Code isn't.

Paste this into **Cowork** or **Claude Code**:

````text
Install Apprentice from <ZIP>. It's a macOS menu bar app that records how I work, locally, so you can find my repeated workflows and turn them into skills.

Unpack it into ~/Applications (move any existing Apprentice.app to ~/Applications/_to_delete/ first). Then tell me to open Apprentice from ~/Applications once, and to allow Screen Recording, Accessibility and Input Monitoring for it when macOS asks. If you can run commands, open it yourself with `open -g` instead (never run the binary bare). Then read ~/Library/Application Support/Apprentice/status.json until `state` is "recording", and help me with any permission that's still false.
````

## Uninstall

Paste this prompt into Claude Code:

```text
Uninstall Apprentice:
1. Run `~/Applications/Apprentice.app/Contents/MacOS/Apprentice stop`. If you registered the old MCP server, also run `claude mcp remove --scope user apprentice`.
2. Run `tccutil reset All blog.eliot.apprentice` (before deleting the app: tccutil needs to find it).
3. Delete ~/Applications/Apprentice.app and ~/.claude/skills/apprentice.
4. Ask me before deleting the recordings in ~/.apprentice-mode.
```
