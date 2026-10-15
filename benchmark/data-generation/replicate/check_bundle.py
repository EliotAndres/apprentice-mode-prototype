"""Validate a browser session bundle (schema v0.2) and, optionally, its ground truth.

    python -m replicate.check_bundle benchmark/sessions/s001 [--gt benchmark/ground_truth/s001.json]

Exit code 0 when valid; every problem found is printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "schemas"


def _validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMA_DIRECTORY / schema_name).read_text()))


def check_bundle(bundle_path: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = bundle_path / "manifest.json"
    if not manifest_path.exists():
        return [f"{bundle_path}: missing manifest.json"]
    manifest = json.loads(manifest_path.read_text())
    errors += [f"manifest: {error.json_path}: {error.message}"
               for error in _validator("manifest.schema.json").iter_errors(manifest)]
    if errors:
        return errors
    dom_stream_path = bundle_path / manifest["streams"]["dom"]["path"]
    if not dom_stream_path.exists():
        return errors + [f"missing dom stream {dom_stream_path}"]
    event_validator = _validator("dom_event.schema.json")
    previous_time, event_count = -1.0, 0
    for line_index, line in enumerate(dom_stream_path.read_text().splitlines()):
        event = json.loads(line)
        event_count += 1
        line_label = f"dom.jsonl:{line_index + 1}"
        errors += [f"{line_label}: {error.json_path}: {error.message}" for error in event_validator.iter_errors(event)]
        if event.get("seq") != line_index:
            errors.append(f"{line_label}: seq {event.get('seq')} != line index {line_index}")
        if event.get("t", 0) < previous_time:
            errors.append(f"{line_label}: t goes backwards ({event['t']} < {previous_time})")
        previous_time = event.get("t", previous_time)
        if event.get("t", 0) > manifest["duration_s"]:
            errors.append(f"{line_label}: t beyond duration_s")
        for file_field in ("screenshot", "dom_snapshot"):
            if event.get(file_field) and not (bundle_path / event[file_field]).exists():
                errors.append(f"{line_label}: missing {file_field} file {event[file_field]}")
    for stream_name, stream in manifest["streams"].items():
        if ("count" in stream and stream_name in ("dom", "screenshots", "dom_snapshots")
                and stream["count"] != event_count):
            errors.append(f"manifest: streams.{stream_name}.count={stream['count']} "
                          f"but dom.jsonl has {event_count} events")
    return errors


def check_ground_truth(ground_truth: dict, bundle_path: Path) -> list[str]:
    errors = [f"ground truth: {error.json_path}: {error.message}"
              for error in _validator("ground_truth.schema.json").iter_errors(ground_truth)]
    manifest = json.loads((bundle_path / "manifest.json").read_text())
    event_count = manifest["streams"]["dom"]["count"]
    action_times = ground_truth.get("action_t", [])
    if len(action_times) != event_count:
        errors.append(f"action_t has {len(action_times)} entries, bundle has {event_count} events")
    else:
        dom_stream_path = bundle_path / manifest["streams"]["dom"]["path"]
        trace_times = [json.loads(line)["t"] for line in dom_stream_path.read_text().splitlines()]
        if trace_times != action_times:
            errors.append("action_t disagrees with traces/dom.jsonl timestamps")
    owner_by_sequence_number: dict[int, str] = {}
    all_instances = [workflow_instance for opportunity in ground_truth["opportunities"]
                     for workflow_instance in opportunity["instances"]] + ground_truth["negatives"]
    for task_instance in all_instances:
        for span_start, span_end in task_instance["spans"]:
            if not 0 <= span_start <= span_end <= manifest["duration_s"]:
                errors.append(f"{task_instance['task_id']}: bad span [{span_start}, {span_end}]")
        for sequence_number in task_instance.get("seqs", []):
            if not 0 <= sequence_number < event_count:
                errors.append(f"{task_instance['task_id']}: seq {sequence_number} out of range")
            elif sequence_number in owner_by_sequence_number:
                errors.append(f"seq {sequence_number} claimed by {owner_by_sequence_number[sequence_number]} "
                              f"and {task_instance['task_id']}")
            owner_by_sequence_number[sequence_number] = task_instance["task_id"]
    if owner_by_sequence_number and len(owner_by_sequence_number) != event_count:
        errors.append(f"{event_count - len(owner_by_sequence_number)} events belong to no instance")
    for opportunity in ground_truth["opportunities"]:
        if len({workflow_instance["task_id"] for workflow_instance in opportunity["instances"]}) < 2:
            errors.append(f"{opportunity['opportunity_id']}: fewer than 2 distinct instances")
    return errors


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("bundles", nargs="+", type=Path)
    argument_parser.add_argument("--gt-dir", type=Path, help="also check <gt-dir>/<session_id>.json")
    arguments = argument_parser.parse_args()
    failed_bundle_count = 0
    for bundle_path in arguments.bundles:
        errors = check_bundle(bundle_path)
        if arguments.gt_dir and not errors:
            ground_truth_path = arguments.gt_dir / f"{bundle_path.name}.json"
            errors += (check_ground_truth(json.loads(ground_truth_path.read_text()), bundle_path)
                       if ground_truth_path.exists() else [f"no {ground_truth_path}"])
        print(f"{'OK  ' if not errors else 'FAIL'} {bundle_path}")
        for error in errors[:50]:
            print(f"     {error}")
        failed_bundle_count += bool(errors)
    return 1 if failed_bundle_count else 0


if __name__ == "__main__":
    sys.exit(main())
