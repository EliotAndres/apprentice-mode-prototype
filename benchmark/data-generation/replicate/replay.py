"""Execution check (PROTOCOL.md, Rule D): can a detected workflow be replayed from its own spec?

For each workflow a model detected in an agent session, an executing agent gets only the
model's steps and the parameter values of its first instance, and does the task live
(live/boat.sh with a generated scenario). The replay is then compared with the ground-truth run
that instance overlaps most: site order and typed values.

    python -m replicate.replay scenario <session> <prediction.json> --out replay.json
    live/boat.sh start <sandbox> replay.json  ...  live/boat.sh fetch <sandbox> replay.json replay-<session>
    python -m replicate.replay score <session> replay.json data/live/<sandbox>/log.txt <replay bundle>
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from replicate.live_ground_truth import read_log
from replicate.score import covered, match

SCENARIOS = Path("data-generation/live/scenarios")


def load_trace(bundle_path: Path) -> list[dict]:
    return [json.loads(line) for line in (bundle_path / "traces" / "dom.jsonl").read_text().splitlines()]


def source_scenario(ground_truth: dict) -> dict:
    """The scenario a session was recorded from: the one defining its workflows."""
    names = {opportunity["name"] for opportunity in ground_truth["opportunities"]}
    for path in sorted(SCENARIOS.glob("*.json")):
        scenario = json.loads(path.read_text())
        if names <= {workflow["name"] for workflow in scenario["workflows"]}:
            return scenario
    raise LookupError(f"no scenario defines {names}")


def build_scenario(session: str, prediction: dict, bench: Path = Path("data")) -> dict:
    ground_truth = json.loads((bench / "ground_truth" / f"{session}.json").read_text())
    predicted = prediction["opportunities"][:10]
    workflows, runs, script, targets = [], {}, [], {}
    for rank, true_index, _ in match(predicted, ground_truth):
        opportunity = predicted[rank]
        first_instance = covered(opportunity["instances"][:1], ground_truth["action_t"], 0)
        true_instance = max(ground_truth["opportunities"][true_index]["instances"],
                            key=lambda instance: len(first_instance & set(instance["seqs"])))
        workflow_id = f"R{len(workflows) + 1}"
        workflows.append({"id": workflow_id, "name": opportunity["name"], "apps": [opportunity["site"]],
                          "parameters": opportunity["parameters"],
                          # literal steps: braces in the model's text must survive str.format
                          "steps": [step.replace("{", "{{").replace("}", "}}") for step in opportunity["steps"]]})
        runs[f"{workflow_id}.1"] = {"workflow": workflow_id, "values": {},
                                    "note": f"Parameter values for this task: {opportunity['instances'][0]['values']}"}
        script.append({"run": f"{workflow_id}.1"})
        targets[workflow_id] = {"opportunity": true_index, "seqs": true_instance["seqs"]}
    return {"tabs": source_scenario(ground_truth)["tabs"], "workflows": workflows, "runs": runs, "noise": {},
            "script": script, "replay_of": {"session": session, "targets": targets}}


def lcs_length(first: list, second: list) -> int:
    table = [[0] * (len(second) + 1) for _ in range(len(first) + 1)]
    for i, first_item in enumerate(first):
        for j, second_item in enumerate(second):
            table[i + 1][j + 1] = table[i][j] + 1 if first_item == second_item else max(table[i][j + 1], table[i + 1][j])
    return table[-1][-1]


def site_order(actions: list[dict]) -> list[str]:
    sites = []
    for action in actions:
        if not sites or sites[-1] != action["site"]:
            sites.append(action["site"])
    return sites


def typed(actions: list[dict]) -> list[str]:
    return [action["value"].strip().lower() for action in actions if action["op"] == "TYPE" and action["value"]]


def compare(true_actions: list[dict], replay_actions: list[dict], finished: bool) -> dict:
    true_sites, true_values = site_order(true_actions), typed(true_actions)
    replay_values = set(typed(replay_actions))
    site_score = lcs_length(true_sites, site_order(replay_actions)) / len(true_sites) if true_sites else 0.0
    value_score = sum(value in replay_values for value in true_values) / len(true_values) if true_values else 1.0
    # Post hoc, reported separately: only the short inputs (parameters), not long notes or emails
    # whose text depends on what the page showed that day.
    short_values = [value for value in true_values if len(value) <= 40]
    short_score = sum(value in replay_values for value in short_values) / len(short_values) if short_values else 1.0
    return {"finished": finished, "site_order": round(site_score, 2), "typed_values": round(value_score, 2),
            "short_typed_values": round(short_score, 2),
            "success": finished and site_score >= 0.8 and value_score >= 0.5}


def score_replay(session: str, scenario: dict, log_text: str, replay_bundle: Path,
                 bench: Path = Path("data")) -> dict:
    original = load_trace(bench / "sessions" / session)
    replay = load_trace(replay_bundle)
    started_at = datetime.fromisoformat(json.loads((replay_bundle / "manifest.json").read_text())["started_at"])
    chunks = read_log(log_text)
    results = {}
    for chunk_number, chunk in enumerate(scenario["script"], 1):
        workflow_id = chunk["run"].split(".")[0]
        logged = chunks.get(chunk_number, {})
        if not (logged.get("start") and logged.get("end")):
            results[workflow_id] = {"finished": False, "success": False, "note": "not logged"}
            continue
        span = [(datetime.fromisoformat(logged[edge]) - started_at).total_seconds() for edge in ("start", "end")]
        replay_actions = [action for action in replay if span[0] <= action["t"] <= span[1]]
        target = scenario["replay_of"]["targets"][workflow_id]
        true_actions = [original[seq] for seq in target["seqs"]]
        results[workflow_id] = {"name": next(workflow["name"] for workflow in scenario["workflows"]
                                             if workflow["id"] == workflow_id),
                                **compare(true_actions, replay_actions, logged.get("ok", False)),
                                "reason": logged.get("reason", "")}
    return results


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    subcommands = argument_parser.add_subparsers(dest="command", required=True)
    scenario_parser = subcommands.add_parser("scenario")
    scenario_parser.add_argument("session")
    scenario_parser.add_argument("prediction", type=Path)
    scenario_parser.add_argument("--out", type=Path, required=True)
    score_parser = subcommands.add_parser("score")
    score_parser.add_argument("session")
    score_parser.add_argument("scenario", type=Path)
    score_parser.add_argument("log", type=Path)
    score_parser.add_argument("replay_bundle", type=Path)
    arguments = argument_parser.parse_args()
    if arguments.command == "scenario":
        scenario = build_scenario(arguments.session, json.loads(arguments.prediction.read_text()))
        arguments.out.write_text(json.dumps(scenario, indent=1, ensure_ascii=False))
        print(f"{len(scenario['workflows'])} workflows to replay")
    else:
        results = score_replay(arguments.session, json.loads(arguments.scenario.read_text()),
                               arguments.log.read_text(), arguments.replay_bundle)
        print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
