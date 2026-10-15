"""Build a static HTML viewer for the benchmark sessions.

    python -m replicate.viewer            # -> viewer.html next to benchmark/
    python -m replicate.viewer --template dashboard.html --out dashboard.html   # performance + run explorer
    open viewer.html                      # works from file://, no server needed

Per session: a timeline of task segments coloured by ground-truth workflow
(one-offs and distractors in grey), predicted opportunities from results/ as
extra lanes, and a screenshot player that steps through every action with the
target element boxed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from PIL import Image

# Dashboard "Use case" facet: the business a run's repeated workflows come from, by Mind2Web domain.
USE_CASE_BY_DOMAIN = {"Travel": "Travel agency", "Shopping": "E-commerce", "Entertainment": "Events and entertainment",
                      "Info": "Research", "Service": "Customer service"}


def instance_label(workflow_instance: dict) -> str:
    """Mind2Web instances carry their task text; live ones only their parameter values."""
    if "task" in workflow_instance:
        return workflow_instance["task"]
    values = ", ".join(f"{name}={value}" for name, value in workflow_instance.get("values", {}).items())
    return f"{workflow_instance['task_id']}: {values}" if values else workflow_instance["task_id"]


def screenshot_size(path: Path) -> list[int]:
    """Live screenshots are the real viewport (1280 wide, any height); the page boxes targets against it."""
    try:
        with Image.open(path) as image:  # reads the header only
            return list(image.size)
    except OSError:
        return [1280, 800]


def session_data(bundle_path: Path, ground_truth: dict, predictions: dict, matches_by_run: dict,
                 screenshot_root: str, domain_by_task: dict) -> dict:
    manifest = json.loads((bundle_path / "manifest.json").read_text())
    events = [json.loads(line) for line in (bundle_path / "traces" / "dom.jsonl").read_text().splitlines()]
    opportunity_ids = [opportunity["opportunity_id"] for opportunity in ground_truth["opportunities"]]
    owner_by_sequence_number = {}
    for opportunity_index, opportunity in enumerate(ground_truth["opportunities"]):
        for workflow_instance in opportunity["instances"]:
            for sequence_number in workflow_instance.get("seqs", []):
                owner_by_sequence_number[sequence_number] = {"kind": "workflow", "opp": opportunity_index,
                                                             "task": instance_label(workflow_instance)}
    for negative in ground_truth["negatives"]:
        for sequence_number in negative.get("seqs", []):
            # Live negatives carry no site; the event's own site then stands.
            owner_by_sequence_number[sequence_number] = {"kind": negative["kind"], "task": negative["task"],
                                                         **({"site": negative["site"]} if "site" in negative else {})}

    def matched_opportunity_index(run_name: str, prediction_rank: int) -> int | None:
        # Matches come from replicate.score (action-level), not recomputed in the page.
        return next((opportunity_ids.index(matched_pair["opportunity_id"])
                     for matched_pair in matches_by_run.get(run_name, []) if matched_pair["rank"] == prediction_rank),
                    None)

    return {
        "id": bundle_path.name,
        "duration": manifest["duration_s"],
        # Dashboard facets: the business behind the repeated workflows, and who produced the actions.
        "use_cases": sorted({USE_CASE_BY_DOMAIN.get(domain_by_task[instance["task_id"]], domain_by_task[instance["task_id"]])
                             for opportunity in ground_truth["opportunities"] for instance in opportunity["instances"]
                             if instance["task_id"] in domain_by_task}
                            # Agent-generated sessions tag each workflow with its use case directly.
                            | {opportunity["use_case"] for opportunity in ground_truth["opportunities"] if opportunity.get("use_case")}),
        "source": "Agent generated" if "agent" in manifest["source"]["kind"]
        else "Agent generated" if manifest["source"]["kind"] == "cdp_recorder"
        else f"{manifest['source']['dataset'].split('/')[-1]} (human)" if manifest["source"].get("dataset") else "Human generated",
        "events": [{"seq": event["seq"], "t": event["t"], "site": event["site"], "op": event["op"],
                    "value": event["value"], "role": event["element"]["role"], "text": event["element"]["text"],
                    "bbox": event["element"]["bbox"],
                    "img": f"{screenshot_root}/{bundle_path.name}/{event['screenshot']}",
                    "size": screenshot_size(bundle_path / event["screenshot"]),
                    **owner_by_sequence_number.get(event["seq"], {})} for event in events],
        "gt": ground_truth,
        "preds": {run_name: [{"name": predicted_opportunity["name"],
                              "description": predicted_opportunity["description"],
                              "spans": [[[time_span["start_t"], time_span["end_t"]]
                                         for time_span in predicted_instance.get("segments") or [predicted_instance]]
                                        for predicted_instance in predicted_opportunity["instances"]],
                              "match": matched_opportunity_index(run_name, prediction_rank)}
                             for prediction_rank, predicted_opportunity
                             in enumerate(prediction.get("opportunities", [])[:10])]
                  for run_name, prediction in predictions.items()},
        # Live ground truth has no tier: a workflow occurrence split across spans means the tasks were interleaved.
        "tier": ground_truth.get("tier") or ("hard" if any(len(instance["spans"]) > 1
                                                          for opportunity in ground_truth["opportunities"]
                                                          for instance in opportunity["instances"]) else "easy"),
    }


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--bench", type=Path, default=Path("data"))
    argument_parser.add_argument("--results", type=Path, default=Path("data/results"))
    argument_parser.add_argument("--out", type=Path, default=Path("viewer.html"))
    argument_parser.add_argument("--template", default="template.html", help="template.html or dashboard.html")
    argument_parser.add_argument("--sessions", default="", help="comma-separated ids; default all")
    arguments = argument_parser.parse_args()
    screenshot_root = os.path.relpath(arguments.bench / "sessions", arguments.out.resolve().parent)
    scores_path = arguments.results / "scores.json"
    scores = json.loads(scores_path.read_text()) if scores_path.exists() else {}
    per_session_scores = scores.get("per_session", [])
    predictions_directory = arguments.results / "predictions"
    run_directories = sorted(predictions_directory.glob("*")) if predictions_directory.exists() else []
    tasks_path = arguments.bench / "index" / "tasks.jsonl"
    domain_by_task = {task["annotation_id"]: task["domain"] for task in map(json.loads, tasks_path.read_text().splitlines())} \
        if tasks_path.exists() else {}
    sessions = []
    bundle_paths = sorted(candidate for candidate in (arguments.bench / "sessions").iterdir()
                          if (candidate / "manifest.json").exists())
    for bundle_path in bundle_paths:
        if arguments.sessions and bundle_path.name not in arguments.sessions.split(","):
            continue
        ground_truth = json.loads((arguments.bench / "ground_truth" / f"{bundle_path.name}.json").read_text())
        if ground_truth.get("tier") == "easy":  # human single-task sessions are left out of the dashboard
            continue
        predictions = {}
        for run_directory in run_directories:
            prediction_path = run_directory / f"{bundle_path.name}.json"
            if prediction_path.exists():
                predictions[run_directory.name] = json.loads(prediction_path.read_text())
        matches_by_run = {f"{session_score['model']}__{session_score['condition']}": session_score["matches"]
                          for session_score in per_session_scores if session_score["session"] == bundle_path.name}
        sessions.append(session_data(bundle_path, ground_truth, predictions, matches_by_run, screenshot_root,
                                     domain_by_task))
    html = Path(__file__).with_name(arguments.template).read_text().replace("/*DATA*/null", json.dumps(sessions))
    html = html.replace("/*SCORES*/null", json.dumps(scores.get("table", [])))  # dashboard.html only
    arguments.out.write_text(html)
    print(f"wrote {arguments.out} ({len(sessions)} sessions)")


if __name__ == "__main__":
    main()
