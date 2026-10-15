"""LLM client built on the `claude` CLI (`claude -p`), no API key needed.

- Structured output: pass a JSON Schema and the CLI validates the reply
  (`--json-schema`); the parsed object comes back as `data`.
- Images go inline as base64 content blocks over `--input-format stream-json`,
  so no tool calls are involved.
- Every call is cached on disk by a hash of (model, schema, prompt, images), so
  re-running is free and the checked-in cache reproduces results offline.
  REPLICATE_RUN=<n> (n > 0) adds a run index to the key, for repeated samples of
  the same call; run 0 is the plain key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

CACHE_DIRECTORY = Path(os.environ.get("REPLICATE_LLM_CACHE", "data/llm_cache"))
MODELS = ("haiku", "sonnet", "opus")  # CLI aliases


def _cache_key(model: str, prompt: str, schema: dict | None, image_paths: list[Path]) -> str:
    hasher = hashlib.sha256(f"{model}\0{json.dumps(schema, sort_keys=True)}\0{prompt}".encode())
    for image_path in image_paths:
        hasher.update(image_path.read_bytes())
    if os.environ.get("REPLICATE_RUN", "0") != "0":
        hasher.update(f"\0run{os.environ['REPLICATE_RUN']}".encode())
    return hasher.hexdigest()[:24]


def complete(prompt: str, model: str = "sonnet", schema: dict | None = None,
             images: list[Path] | None = None, offline: bool = False) -> dict:
    """Return {"text", "data", "model", "cost_usd", "seconds", "cached"}.

    `data` is the schema-validated object when `schema` is given, else None.
    With offline=True a cache miss raises instead of calling the CLI.
    """
    image_paths = [Path(image) for image in images or []]
    cache_path = CACHE_DIRECTORY / f"{_cache_key(model, prompt, schema, image_paths)}.json"
    if cache_path.exists():
        return {**json.loads(cache_path.read_text()), "cached": True}
    if offline or os.environ.get("REPLICATE_OFFLINE"):
        raise LookupError(f"LLM cache miss in offline mode: {cache_path.name}")
    started_at = time.time()
    cli_result = _run_cli(prompt, model, schema, image_paths)
    reply = {"text": cli_result.get("result", ""), "data": cli_result.get("structured_output"), "model": model,
             "cost_usd": cli_result.get("total_cost_usd"), "seconds": round(time.time() - started_at, 1)}
    if schema is not None and reply["data"] is None:
        raise RuntimeError(f"no structured output: {reply['text'][:300]}")
    CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(reply))
    return {**reply, "cached": False}


def _run_cli(prompt: str, model: str, schema: dict | None, image_paths: list[Path]) -> dict:
    content_blocks = []
    for image_path in image_paths:
        content_blocks.append({"type": "text", "text": f"[image {image_path.name}]"})
        content_blocks.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(image_path.read_bytes()).decode()}})
    content_blocks.append({"type": "text", "text": prompt})
    user_message = {"type": "user", "message": {"role": "user", "content": content_blocks}}
    # No user/project settings, plugins or MCP servers: they add instructions and ~$2 of context per call.
    command = ["claude", "-p", "--model", model, "--tools", "", "--verbose", "--setting-sources", "",
               "--strict-mcp-config", "--disable-slash-commands",
               "--input-format", "stream-json", "--output-format", "stream-json"]
    if schema is not None:
        command += ["--json-schema", json.dumps(schema)]
    error_message = ""
    for attempt_number in range(3):
        # Empty working directory: no CLAUDE.md/AGENTS.md gets pulled into the prompt.
        with tempfile.TemporaryDirectory() as empty_directory:
            completed_process = subprocess.run(command, input=json.dumps(user_message) + "\n",
                                               capture_output=True, text=True, cwd=empty_directory, timeout=1800)
        stream_events = [json.loads(line) for line in completed_process.stdout.splitlines()
                         if line.startswith("{")]  # skip any non-JSON notice line
        result_events = [event for event in stream_events if event.get("type") == "result"]
        if completed_process.returncode == 0 and result_events and not result_events[-1].get("is_error"):
            return result_events[-1]
        error_message = completed_process.stderr[-500:] or (
            result_events[-1].get("result", "") if result_events else completed_process.stdout[-500:])
        time.sleep(5 * (attempt_number + 1))
    raise RuntimeError(f"claude CLI failed rc={completed_process.returncode}: {error_message}")
