"""Write browser session bundles (schema v0.2) from composed plans + fetched data.

benchmark/sessions/<id>/
    manifest.json
    traces/dom.jsonl          one action per line (schemas/dom_event.schema.json)
    screenshots/<seq>.jpg     viewport frame before each action
    dom/<seq>.html.gz         cleaned HTML snapshot before each action
benchmark/ground_truth/<id>.json   kept outside the bundle so models never see it
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

RAW_DIRECTORY = Path("data/raw")
ACTION_REPR_PATTERN = re.compile(
    r"^\[(?P<role>[^\]]*)\]\s*(?P<text>.*?)\s*->\s*(?P<op>[A-Z]+)(?::\s*(?P<value>.*))?$", re.S)


def parse_repr(action_repr: str) -> dict:
    repr_match = ACTION_REPR_PATTERN.match(action_repr or "")
    if repr_match:
        return {"role": repr_match["role"], "text": repr_match["text"].strip()}
    return {"role": "", "text": (action_repr or "").strip()}


def export(plan: dict, tasks: dict, benchmark_directory: Path, with_dom: bool = True) -> Path:
    session_id = plan["session_id"]
    bundle_path = benchmark_directory / "sessions" / session_id
    if bundle_path.exists():
        shutil.rmtree(bundle_path)
    for subdirectory in ("traces", "screenshots") + (("dom",) if with_dom else ()):
        (bundle_path / subdirectory).mkdir(parents=True)
    dom_events = []
    for plan_event in plan["events"]:
        action = tasks[plan_event["task_id"]]["actions"][plan_event["action_index"]]
        action_uid, sequence_number = action["action_uid"], plan_event["seq"]
        action_metadata = json.loads((RAW_DIRECTORY / "meta" / f"{action_uid}.json").read_text())
        screenshot_path = f"screenshots/{sequence_number:05d}.jpg"
        shutil.copyfile(RAW_DIRECTORY / "frames" / f"{action_uid}.jpg", bundle_path / screenshot_path)
        dom_snapshot_path = None
        if with_dom:
            dom_snapshot_path = f"dom/{sequence_number:05d}.html.gz"
            shutil.copyfile(RAW_DIRECTORY / "dom" / f"{action_uid}.html.gz", bundle_path / dom_snapshot_path)
        element = parse_repr(action["repr"])
        dom_events.append({
            "seq": sequence_number, "t": plan_event["t"], "type": "action", "op": action["operation"]["op"],
            "value": action["operation"].get("value", ""), "site": tasks[plan_event["task_id"]]["website"],
            "url": None, "element": {"tag": action_metadata["tag"] or "", **element, "bbox": action_metadata["bbox"]},
            "screenshot": screenshot_path, "dom_snapshot": dom_snapshot_path})
    (bundle_path / "traces" / "dom.jsonl").write_text("".join(json.dumps(event) + "\n" for event in dom_events))
    manifest = {
        "schema_version": "0.2", "session_id": session_id,
        "source": {"kind": "chained_dataset", "dataset": "osunlp/Multimodal-Mind2Web", "license": "CC BY 4.0",
                   "composer_seed": plan["seed"], "tier": plan.get("tier", "easy")},
        "started_at": plan["started_at"], "duration_s": plan["duration_s"],
        "timing": {"kind": "synthetic", "note": "Mind2Web has no timing; per-action and pause durations are "
                   "sampled by replicate.compose. Time-savings derived from it are indicative only."},
        "streams": {"dom": {"path": "traces/dom.jsonl", "format": "jsonl", "count": len(dom_events)},
                    "screenshots": {"path": "screenshots/", "format": "jpeg 1280x800", "count": len(dom_events)},
                    **({"dom_snapshots": {"path": "dom/", "format": "html.gz", "count": len(dom_events)}}
                       if with_dom else {})},
        "counts": {"actions": len(dom_events), "sites": len({event["site"] for event in dom_events})},
    }
    (bundle_path / "manifest.json").write_text(json.dumps(manifest, indent=2))
    ground_truth_directory = benchmark_directory / "ground_truth"
    ground_truth_directory.mkdir(parents=True, exist_ok=True)
    (ground_truth_directory / f"{session_id}.json").write_text(json.dumps(plan["ground_truth"], indent=1))
    return bundle_path


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--index", type=Path, default=Path("data/index/tasks.jsonl"))
    argument_parser.add_argument("--plans", type=Path, default=Path("data/plans"))
    argument_parser.add_argument("--out", type=Path, default=Path("data"))
    argument_parser.add_argument("--no-dom", action="store_true", help="skip DOM snapshots (smaller bundles)")
    arguments = argument_parser.parse_args()
    tasks = {task["annotation_id"]: task for task in map(json.loads, arguments.index.open())}
    for plan_path in sorted(arguments.plans.glob("*.json")):
        bundle_path = export(json.loads(plan_path.read_text()), tasks, arguments.out, with_dom=not arguments.no_dom)
        print(f"wrote {bundle_path}")


if __name__ == "__main__":
    main()
