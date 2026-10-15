"""Record a Chrome session into a v0.2 bundle, the same layout Apprentice writes.

Apprentice is macOS-only, so on a Linux sandbox this stands in for it: it attaches to
Chrome over the DevTools protocol, injects capture-phase listeners into every tab and
writes actions, context events and one screenshot per action.

    DISPLAY=:0 google-chrome-stable --remote-debugging-port=9222 \
        --user-data-dir=$HOME/.config/chrome-eval &
    uv run --with websockets python -m replicate.cdp_recorder --out ~/eval/sessions

Stop it with SIGINT or SIGTERM; the manifest is rewritten every minute and on exit.
Only the browser is visible to it: the address bar and Chrome's own UI (tab strip,
shortcuts like ctrl+t) show up as context events (url changes, tabs opening), not actions.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import itertools
import json
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import websockets

RECORDER_VERSION = "0.1.0"

# Runs in every frame of every tab. Calls the __rec binding with one JSON payload per action.
PAGE_SCRIPT = r"""
(() => {
  if (window.__recInstalled || typeof window.__rec !== 'function') return;
  window.__recInstalled = true;
  const ROLES = {A: 'link', BUTTON: 'button', INPUT: 'textbox', TEXTAREA: 'textbox', SELECT: 'combobox',
                 IMG: 'img', OPTION: 'option', LI: 'listitem', LABEL: 'label', SUMMARY: 'button'};
  const INTERACTIVE = 'a,button,input,select,textarea,summary,label,[role],[onclick],[contenteditable=""],[contenteditable=true]';
  const clean = s => (s || '').trim().replace(/\s+/g, ' ').slice(0, 200);
  const isSecure = el => el && el.tagName === 'INPUT' && el.type === 'password';
  function describe(el) {
    if (!el || !el.getBoundingClientRect) return {tag: '', role: '', text: '', bbox: null};
    const box = el.getBoundingClientRect();
    // Screenshots are the viewport, downscaled to at most 1280 px wide.
    const scale = Math.min(1, 1280 / (innerWidth * devicePixelRatio)) * devicePixelRatio;
    let role = el.getAttribute('role') || ROLES[el.tagName] || el.tagName.toLowerCase();
    if (el.tagName === 'INPUT' && ['checkbox', 'radio', 'submit', 'button', 'search'].includes(el.type))
      role = {submit: 'button', search: 'searchbox'}[el.type] || el.type;
    if (el.isContentEditable && !el.getAttribute('role')) role = 'textbox';
    const text = clean(el.getAttribute('aria-label') || el.placeholder || el.title || el.alt ||
                       (el.tagName === 'INPUT' ? el.value : el.innerText) || el.getAttribute('name'));
    return {tag: el.tagName.toLowerCase(), role, text,
            bbox: [box.x * scale, box.y * scale, box.width * scale, box.height * scale]};
  }
  const send = (op, el, value) => {
    try { window.__rec(JSON.stringify({op, value: value || '', element: describe(el),
                                       url: location.href, title: top === self ? document.title : ''})); }
    catch (e) {}
  };
  const fieldValue = el => isSecure(el) ? '[secure]'
    : (el.isContentEditable ? el.innerText : String(el.value ?? '')).slice(0, 2000);

  // TYPE: one per field, ended by leaving the field, Enter, or 3 s idle.
  let pending = null, idleTimer = null;
  const flush = () => {
    if (!pending) return;
    const el = pending; pending = null; clearTimeout(idleTimer);
    send('TYPE', el, fieldValue(el));
  };
  addEventListener('input', e => {
    const el = e.composedPath()[0];
    if (!el || el.tagName === 'SELECT' || ['checkbox', 'radio'].includes(el.type)) return;
    const field = el.isContentEditable ? (el.closest('[contenteditable]') || el) : el;
    if (pending && pending !== field) flush();
    pending = field; clearTimeout(idleTimer); idleTimer = setTimeout(flush, 3000);
  }, true);
  addEventListener('focusout', flush, true);
  addEventListener('pagehide', flush, true);
  addEventListener('change', e => {
    const el = e.target;
    if (el.tagName === 'SELECT') send('SELECT', el, clean(el.selectedOptions[0] ? el.selectedOptions[0].text : el.value));
  }, true);
  addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.isComposing && !e.shiftKey) { flush(); send('ENTER', e.composedPath()[0], ''); return; }
    const modifiers = [e.ctrlKey && 'ctrl', e.metaKey && 'cmd', e.altKey && 'alt', e.shiftKey && 'shift'].filter(Boolean);
    const isShortcut = (e.ctrlKey || e.metaKey || e.altKey) && !['Control', 'Meta', 'Alt', 'Shift'].includes(e.key);
    if (isShortcut) send('KEY', e.composedPath()[0], [...modifiers, e.key.toLowerCase()].join('+'));
  }, true);
  addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    flush();
    const target = e.composedPath()[0];
    send('CLICK', (target.closest && target.closest(INTERACTIVE)) || target, '');
  }, true);
})();
"""


def site_of(url: str) -> str:
    """booking.com -> booking. ponytail: last-two-labels rule, wrong for co.uk-style suffixes."""
    host = urlparse(url).hostname or ""
    labels = host.removeprefix("www.").split(".")
    return labels[-2] if len(labels) >= 2 else (host or "chrome")


def write_json_atomically(path: Path, payload: dict) -> None:
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, indent=2))
    os.replace(temporary_path, path)


class Recorder:
    def __init__(self, websocket, session_directory: Path):
        self.websocket = websocket
        self.session_directory = session_directory
        self.started_at = datetime.now().astimezone()
        self.started_monotonic = time.monotonic()
        self.message_ids = itertools.count(1)
        self.pending_replies: dict[int, asyncio.Future] = {}
        self.latest_frame_by_session: dict[str, str] = {}
        self.session_by_target: dict[str, str] = {}
        self.target_info: dict[str, dict] = {}
        self.action_count = self.screenshot_count = self.context_count = 0
        self.last_action_t = 0.0
        self.sites: set[str] = set()
        self.pending_lines: dict[int, str] = {}
        self.next_line_to_write = 0
        (session_directory / "traces").mkdir(parents=True)
        (session_directory / "screenshots").mkdir()
        self.dom_file = open(session_directory / "traces" / "dom.jsonl", "a")
        self.context_file = open(session_directory / "traces" / "context.jsonl", "a")

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started_monotonic, 3)

    async def send(self, method: str, params: dict | None = None, session_id: str | None = None) -> dict:
        message_id = next(self.message_ids)
        message = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        reply = self.pending_replies[message_id] = asyncio.get_running_loop().create_future()
        await self.websocket.send(json.dumps(message))
        return await reply

    def write_context(self, kind: str, **fields) -> None:
        event = {"t": self.elapsed(), "type": "context", "kind": kind, **fields}
        self.context_file.write(json.dumps(event) + "\n")
        self.context_file.flush()
        self.context_count += 1

    def write_manifest(self) -> None:
        write_json_atomically(self.session_directory / "manifest.json", {
            "schema_version": "0.2",
            "session_id": self.session_directory.name,
            "source": {"kind": "cdp_recorder", "recorder_version": RECORDER_VERSION},
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "duration_s": max(self.elapsed(), self.last_action_t),
            "timing": {"kind": "observed"},
            "streams": {
                "dom": {"path": "traces/dom.jsonl", "format": "jsonl", "count": self.action_count},
                "screenshots": {"path": "screenshots/", "format": "jpeg max 1280w", "count": self.screenshot_count},
                "accessibility": {"path": "traces/context.jsonl", "format": "jsonl", "count": self.context_count},
            },
            "counts": {"actions": self.action_count, "sites": len(self.sites)},
        })

    async def attach(self, target_id: str) -> None:
        try:
            session_id = (await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True}))["sessionId"]
            self.session_by_target[target_id] = session_id
            for method, params in [("Runtime.addBinding", {"name": "__rec"}),
                                   ("Runtime.enable", {}),
                                   ("Page.enable", {}),
                                   ("Page.addScriptToEvaluateOnNewDocument", {"source": PAGE_SCRIPT, "runImmediately": True}),
                                   ("Runtime.evaluate", {"expression": PAGE_SCRIPT}),
                                   ("Page.startScreencast", {"format": "jpeg", "quality": 60, "maxWidth": 1280,
                                                             "maxHeight": 4000})]:
                await self.send(method, params, session_id)
        except (KeyError, websockets.ConnectionClosed) as error:
            self.write_context("attach_failed", target_id=target_id, error=str(error))

    async def record_action(self, session_id: str, payload: dict) -> None:
        sequence_number = self.action_count
        self.action_count += 1  # claim the seq before awaiting, so concurrent actions stay ordered
        self.last_action_t = max(self.last_action_t, self.elapsed())
        action_t = self.last_action_t
        frame = self.latest_frame_by_session.get(session_id)
        if frame is None:  # no screencast frame yet (tab just opened): grab one now
            try:
                frame = (await self.send("Page.captureScreenshot", {"format": "jpeg", "quality": 60}, session_id))["data"]
            except KeyError:
                frame = None
        screenshot_path = None
        if frame:
            screenshot_path = f"screenshots/{sequence_number:05d}.jpg"
            (self.session_directory / screenshot_path).write_bytes(base64.b64decode(frame))
            self.screenshot_count += 1
        site = site_of(payload["url"])
        self.sites.add(site)
        action = {"seq": sequence_number, "t": action_t, "type": "action", "op": payload["op"],
                  "value": payload["value"], "site": site, "url": payload["url"], "app": "google-chrome",
                  "window": payload["title"], "element": payload["element"],
                  "screenshot": screenshot_path, "dom_snapshot": None}
        self.pending_lines[sequence_number] = json.dumps(action)
        while self.next_line_to_write in self.pending_lines:  # write in seq order even if a capture lagged
            self.dom_file.write(self.pending_lines.pop(self.next_line_to_write) + "\n")
            self.next_line_to_write += 1
        self.dom_file.flush()

    def handle_event(self, message: dict) -> None:
        method, params, session_id = message["method"], message.get("params", {}), message.get("sessionId")
        if method == "Page.screencastFrame":
            self.latest_frame_by_session[session_id] = params["data"]
            asyncio.ensure_future(self.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]}, session_id))
        elif method == "Runtime.bindingCalled" and params["name"] == "__rec":
            asyncio.ensure_future(self.record_action(session_id, json.loads(params["payload"])))
        elif method == "Target.targetCreated" and params["targetInfo"]["type"] == "page":
            info = params["targetInfo"]
            self.target_info[info["targetId"]] = info
            self.write_context("tab_opened", target_id=info["targetId"], url=info["url"], title=info["title"])
            asyncio.ensure_future(self.attach(info["targetId"]))
        elif method == "Target.targetInfoChanged" and params["targetInfo"]["type"] == "page":
            info = params["targetInfo"]
            previous = self.target_info.get(info["targetId"], {})
            if info["url"] != previous.get("url"):
                self.write_context("url_change", target_id=info["targetId"], url=info["url"], site=site_of(info["url"]))
            elif info["title"] != previous.get("title"):
                self.write_context("title_change", target_id=info["targetId"], title=info["title"])
            self.target_info[info["targetId"]] = info
        elif method == "Target.targetDestroyed" and params["targetId"] in self.target_info:
            self.write_context("tab_closed", target_id=params["targetId"])
            self.target_info.pop(params["targetId"])
            self.latest_frame_by_session.pop(self.session_by_target.pop(params["targetId"], ""), None)

    async def read_messages(self) -> None:
        async for raw_message in self.websocket:
            message = json.loads(raw_message)
            if "id" in message:
                reply = self.pending_replies.pop(message["id"], None)
                if reply and not reply.done():
                    reply.set_result(message.get("result", {}))
            else:
                self.handle_event(message)


async def record(port: int, output_root: Path) -> Path:
    for attempt in range(60):  # Chrome can take a while to open its DevTools port
        try:
            browser_websocket_url = json.load(urlopen(f"http://127.0.0.1:{port}/json/version"))["webSocketDebuggerUrl"]
            break
        except OSError:
            if attempt == 59:
                raise
            await asyncio.sleep(1)
    session_directory = output_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    async with websockets.connect(browser_websocket_url, max_size=None) as websocket:
        recorder = Recorder(websocket, session_directory)
        recorder.write_manifest()
        reader = asyncio.create_task(recorder.read_messages())
        await recorder.send("Target.setDiscoverTargets", {"discover": True})
        stop = asyncio.Event()
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(stop_signal, stop.set)
        while not stop.is_set() and not reader.done():
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                recorder.write_manifest()
        recorder.write_context("recorder_stopped", reason="browser closed" if reader.done() else "signal")
        recorder.write_manifest()
        reader.cancel()
    return session_directory


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--port", type=int, default=9222)
    argument_parser.add_argument("--out", type=Path, default=Path.home() / "eval" / "sessions")
    arguments = argument_parser.parse_args()
    print(asyncio.run(record(arguments.port, arguments.out)))


if __name__ == "__main__":
    main()
