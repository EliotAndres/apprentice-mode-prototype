"""Scripted live sessions: render the agent's script, then build ground truth from its log.

The scenario (data-generation/live/scenarios/*.json) fixes the workflows, every run's values,
the noise and the order. The executing agent only logs `<iso time> start N` / `end N ok|failed ...`
per script chunk to log.txt, so the answer key can't drift from what was planned.

    python -m replicate.live_ground_truth prompt live/scenarios/<name>.json > script.md
    python -m replicate.live_ground_truth build live/scenarios/<name>.json log.txt --out ground_truth.json
    python -m replicate.live_ground_truth convert ground_truth.json <bundle> --out data/ground_truth/<id>.json

`build` writes the wall-clock format ({"workflows": [{"runs": [{"start", "end", "segments", "values"}]}],
"noise": [...]}); `convert` maps it onto a bundle's actions for replicate.score.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def render_script(scenario: dict) -> str:
    workflows = {workflow["id"]: workflow for workflow in scenario["workflows"]}
    lines = []
    for chunk_number, chunk in enumerate(scenario["script"], 1):
        if "pause" in chunk:
            lines.append(f"{chunk_number}. Pause: do nothing for {chunk['pause']} seconds (`sleep {chunk['pause']}`).")
        elif "noise" in chunk:
            lines.append(f"{chunk_number}. {scenario['noise'][chunk['noise']]}.")
        else:
            run = scenario["runs"][chunk["run"]]
            steps = workflows[run["workflow"]]["steps"]
            first_step, last_step = chunk.get("steps", [1, len(steps)])
            part = "" if (first_step, last_step) == (1, len(steps)) else f", steps {first_step}-{last_step} of {len(steps)}"
            lines.append(f"{chunk_number}. Task {chunk['run']}{part}:")
            lines += [f"   {step_number}. {steps[step_number - 1].format(**run['values'])}"
                      for step_number in range(first_step, last_step + 1)]
            if run.get("note") and first_step == 1:
                lines.append(f"   Note: {run['note']}.")
    return "\n".join(lines)


def read_log(log_text: str) -> dict[int, dict]:
    """chunk number -> {"start", "end", "ok", "reason"}."""
    chunks: dict[int, dict] = {}
    for line in log_text.splitlines():
        parts = line.split(maxsplit=4)
        if len(parts) < 3 or parts[1] not in ("start", "end"):
            continue
        chunk = chunks.setdefault(int(parts[2]), {})
        if parts[1] == "start":
            chunk["start"] = parts[0]
        else:
            chunk.update(end=parts[0], ok=len(parts) > 3 and parts[3] == "ok", reason=parts[4] if len(parts) > 4 else "")
    return chunks


def build(scenario: dict, log_text: str) -> dict:
    """Ground truth from the scenario plus what the log says actually happened.

    A run with any failed or unlogged chunk is not a run: its logged chunks go to noise as abandoned.
    """
    chunks = read_log(log_text)
    segments_by_item: dict[str, list] = {}
    failed_items: set[str] = set()
    for chunk_number, script_chunk in enumerate(scenario["script"], 1):
        item = script_chunk.get("run") or script_chunk.get("noise")
        if not item:
            continue
        logged = chunks.get(chunk_number, {})
        if logged.get("start") and logged.get("end"):
            segments_by_item.setdefault(item, []).append([logged["start"], logged["end"]])
        if not logged.get("ok"):
            failed_items.add(item)

    def timed(item: str) -> dict:
        segments = segments_by_item[item]
        return {"start": segments[0][0], "end": segments[-1][1], "segments": segments}

    workflows = []
    for workflow in scenario["workflows"]:
        run_ids = [run_id for run_id, run in scenario["runs"].items() if run["workflow"] == workflow["id"]]
        workflows.append({
            "name": workflow["name"], "use_case": workflow.get("use_case"), "apps": workflow["apps"],
            "parameters": workflow["parameters"],
            "steps": workflow["steps"],
            "runs": [{"id": run_id, **timed(run_id), "values": scenario["runs"][run_id]["values"]}
                     for run_id in run_ids if run_id not in failed_items and run_id in segments_by_item],
        })
    noise = [{"id": item, **timed(item), "description": scenario["noise"][item]}
             for item in scenario["noise"] if item in segments_by_item]
    noise += [{"id": item, **timed(item), "description": f"abandoned run of {item}"}
              for item in scenario["runs"] if item in failed_items and item in segments_by_item]
    return {"workflows": workflows, "noise": noise,
            "failed": sorted(failed_items), "log_reasons": {number: chunk["reason"] for number, chunk in chunks.items()
                                                           if chunk.get("reason")}}


def convert(live_ground_truth: dict, bundle_path: Path) -> dict:
    """Wall-clock runs -> action-level instances on the bundle's clock (replicate ground truth schema).

    Actions in no run and no noise item (moving between tasks, pauses) become one "unlabelled"
    one-off, so every action belongs to exactly one instance, as check_bundle expects.
    """
    manifest = json.loads((bundle_path / "manifest.json").read_text())
    session_start = datetime.fromisoformat(manifest["started_at"])
    action_times = [json.loads(line)["t"] for line in
                    (bundle_path / manifest["streams"]["dom"]["path"]).read_text().splitlines()]
    owned_actions: set[int] = set()

    def instance(task_id: str, item: dict, **fields) -> dict:
        spans = [[(datetime.fromisoformat(stamp) - session_start).total_seconds() for stamp in segment]
                 for segment in item.get("segments") or [[item["start"], item["end"]]]]
        seqs = [seq for seq, action_t in enumerate(action_times) if seq not in owned_actions
                and any(span_start <= action_t <= span_end for span_start, span_end in spans)]
        owned_actions.update(seqs)  # first claim wins if a run and a noise item overlap
        return {"task_id": task_id, "spans": spans, "seqs": seqs,
                "seconds": sum(span_end - span_start for span_start, span_end in spans), **fields}

    opportunities, single_runs = [], []
    for workflow_index, workflow in enumerate(live_ground_truth["workflows"]):
        opportunity_id = f"{bundle_path.name}-op{len(opportunities)}"
        if len(workflow["runs"]) < 2:  # done once is a one-off, not a repeated workflow
            single_runs += [{**run, "description": f"single run of {workflow['name']}"} for run in workflow["runs"]]
            continue
        instances = [instance(run.get("id", f"{opportunity_id}-run{run_index}"), run, values=run.get("values", {}))
                     for run_index, run in enumerate(workflow["runs"])]
        opportunities.append({
            "opportunity_id": opportunity_id, "template_id": opportunity_id, "name": workflow["name"],
            "use_case": workflow.get("use_case"),
            "description": " -> ".join(workflow["steps"]), "site": ", ".join(workflow.get("apps", [])),
            "parameters": workflow.get("parameters", []), "steps": workflow["steps"],
            "observed_seconds": sum(run["seconds"] for run in instances), "instances": instances,
        })
    negatives = [instance(item.get("id", f"noise{index}"), item, kind="one_off", task=item["description"])
                 for index, item in enumerate(live_ground_truth.get("noise", []) + single_runs)]
    unlabelled = [seq for seq in range(len(action_times)) if seq not in owned_actions]
    if unlabelled:
        negatives.append({"task_id": "unlabelled", "kind": "one_off", "task": "between tasks",
                          "spans": [[action_times[seq], action_times[seq]] for seq in unlabelled],
                          "seqs": unlabelled, "seconds": 0})
    return mark_ambiguous({"session_id": bundle_path.name, "action_t": action_times, "dontcare_inside_instances": True,
                           "opportunities": opportunities, "negatives": negatives})


def mark_ambiguous(ground_truth: dict) -> dict:
    """Runs of a workflow attempted 2+ times but completed fewer than twice become kind "ambiguous".

    The person visibly repeated the procedure, so calling it noise would punish a model for
    seeing it; calling it a workflow would reward one for a task never finished. Scoring ignores it.
    Run ids are "<WORKFLOW>.<n>"; noise ids ("N1") have no dot.
    """
    def workflow_of(task_id: str) -> str | None:
        return task_id.split(".")[0] if "." in task_id else None
    completed = {workflow_of(instance["task_id"]) for opportunity in ground_truth["opportunities"]
                 for instance in opportunity["instances"]}
    attempts: dict[str, int] = {}
    for negative in ground_truth["negatives"]:
        if workflow_of(negative["task_id"]):
            attempts[workflow_of(negative["task_id"])] = attempts.get(workflow_of(negative["task_id"]), 0) + 1
    for negative in ground_truth["negatives"]:
        workflow = workflow_of(negative["task_id"])
        if workflow and workflow not in completed and attempts[workflow] >= 2:
            negative["kind"] = "ambiguous"
    return ground_truth


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    subcommands = argument_parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("prompt").add_argument("scenario", type=Path)
    build_parser = subcommands.add_parser("build")
    build_parser.add_argument("scenario", type=Path)
    build_parser.add_argument("log", type=Path)
    build_parser.add_argument("--out", type=Path, required=True)
    convert_parser = subcommands.add_parser("convert")
    convert_parser.add_argument("live_ground_truth", type=Path)
    convert_parser.add_argument("bundle", type=Path)
    convert_parser.add_argument("--out", type=Path, required=True)
    arguments = argument_parser.parse_args()
    if arguments.command == "prompt":
        print(render_script(json.loads(arguments.scenario.read_text())))
    elif arguments.command == "build":
        ground_truth = build(json.loads(arguments.scenario.read_text()), arguments.log.read_text())
        arguments.out.write_text(json.dumps(ground_truth, indent=1, ensure_ascii=False))
        print("failed:", ground_truth["failed"] or "none")
    else:
        ground_truth = convert(json.loads(arguments.live_ground_truth.read_text()), arguments.bundle)
        arguments.out.write_text(json.dumps(ground_truth, indent=1, ensure_ascii=False))
        for opportunity in ground_truth["opportunities"]:
            print(opportunity["name"], [len(run["seqs"]) for run in opportunity["instances"]], "actions per run")


if __name__ == "__main__":
    main()
